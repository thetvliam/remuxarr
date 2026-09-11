"""
worker.py — _load_job_data, the pre-execution re-decision gate.

Between a file being queued and the worker picking it up, the settings
that produced the queued plan can change: a threshold moves, an override
lands, a language gets corrected. _load_job_data is where that is caught.
It re-runs analyze_file against the current settings and then applies
whichever of three outcomes comes back — gate the job for manual review,
mark it skipped, or refresh the stored plan and hand execution the data it
needs. All 90 statements of its body were uncovered.

The two no-op outcomes are the interesting half, because both were once
unhandled: any decision was returned for execution unconditionally, so an
item that had become a no-op executed a decision carrying
target_container=None and failed with "Unsupported output container None"
instead of being marked skipped, and an item that had become
manual-review executed as though a human had already approved it. Neither
outcome produces an exception, so nothing here fails loudly if the gate
goes: the job runs, and the review it was supposed to wait for never
happens.

Three existing tests — in test_revert_capture.py and
test_source_file_preservation.py — monkeypatch _load_job_data out entirely
and hand _run_job a fixed tuple. That is right for what they test, and it
means the real function had no coverage from them at all.

analyze_file is faked here rather than driven for real. What is under test
is what the caller does with a decision, and the decision engine has its
own suite; the tests that matter for the seam assert what analyze_file was
*called with*, since a fake that this file supplies cannot otherwise see
the caller stop passing something down.

Confirmed unprotected before this file was written: twelve mutations
applied to _load_job_data, each run against the whole 1257-test suite, all
twelve survived. Dropping the on-disk existence check; recording a missing
file as a success; leaving the media row un-gated on manual review;
serialising flagged subtitles unconditionally; hoisting the language-flag
upsert above the manual-review return, which is the documented hazard the
current ordering exists to avoid; removing the skip gate; skipping without
a completion timestamp; leaving stale PlannedActions in place; writing
every action at position zero; dropping target_language; crossing the
audio and subtitle language overrides on the way into the decision engine;
and probing faststart for every container rather than only mp4. All
twelve are killed by the tests below — no survivors, and none recorded as
equivalent.
"""
import json
import os
from types import SimpleNamespace

import pytest

import app.core.worker as worker
from app.core.decision import Action, ProcessingDecision
from app.database.models import MediaFile, PlannedAction, QueueItem, Track


# ── Harness ──────────────────────────────────────────────────────────────────

@pytest.fixture
def rig(monkeypatch, tmp_path):
    """
    A real in-memory database and a real file on disk, with the decision
    engine and the three writers it triggers replaced by recorders.

    The database is real because the outcomes under test are almost
    entirely row transitions — status, reason, completed_at, the
    PlannedAction set — and asserting those against real rows is what
    makes a lost commit visible. Only analyze_file, _finish_job and
    _upsert_language_flags are faked: the first to drive each outcome
    deterministically, the other two because they open their own sessions
    and are covered elsewhere.
    """
    from sqlalchemy.orm import sessionmaker

    from tests.conftest import memory_engine

    from app.database.models import Base

    engine = memory_engine()
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    monkeypatch.setattr(worker, "SessionLocal", lambda: session)

    video = tmp_path / "Show.mkv"
    video.write_bytes(b"not really a video")

    rig = SimpleNamespace(
        db=session,
        path=str(video),
        decision=proceeding(),
        analyze_calls=[],
        finish_calls=[],
        upsert_calls=[],
        faststart_calls=[],
    )

    def fake_analyze(file_info, tracks, app_cfg, **kwargs):
        rig.analyze_calls.append(
            SimpleNamespace(file_info=file_info, tracks=tracks,
                            app_cfg=app_cfg, kwargs=kwargs)
        )
        return rig.decision

    def fake_faststart(path):
        rig.faststart_calls.append(path)
        return True

    monkeypatch.setattr(worker, "analyze_file", fake_analyze)
    monkeypatch.setattr(worker, "is_faststart_mp4", fake_faststart)
    monkeypatch.setattr(worker, "_finish_job",
                        lambda *args: rig.finish_calls.append(args))
    monkeypatch.setattr(worker, "_upsert_language_flags",
                        lambda db, media, decision: rig.upsert_calls.append(decision))
    return rig


def media_row(rig, *, file_id=1, container="mkv", **fields):
    fields.setdefault("video_codec", "h264")
    fields.setdefault("und_audio_threshold_acknowledged", False)
    rig.db.add(MediaFile(
        id=file_id, path=rig.path, filename=os.path.basename(rig.path),
        directory=os.path.dirname(rig.path), size=18, mtime=1.0,
        container=container, **fields,
    ))
    rig.db.commit()


def queue_row(rig, *, job_id=1, file_id=1, **fields):
    fields.setdefault("status", "pending")
    rig.db.add(QueueItem(id=job_id, file_id=file_id, **fields))
    rig.db.commit()


def track_row(rig, *, track_id, stream_index, track_type, file_id=1, **fields):
    rig.db.add(Track(id=track_id, file_id=file_id, stream_index=stream_index,
                     track_type=track_type, **fields))
    rig.db.commit()


def job(rig, job_id=1):
    return rig.db.get(QueueItem, job_id)


def file_row(rig, file_id=1):
    return rig.db.get(MediaFile, file_id)


def actions(rig, job_id=1):
    return (
        rig.db.query(PlannedAction)
        .filter(PlannedAction.queue_item_id == job_id)
        .order_by(PlannedAction.id)
        .all()
    )


# ── Decision builders ────────────────────────────────────────────────────────
# Each stands for one of the three outcomes the function branches on.

def proceeding(action_list=()):
    return ProcessingDecision(
        should_process=True, reason="remux to mkv",
        actions=list(action_list), target_container="mkv",
    )


def manual_review(reason="image-based subtitles", flagged=None):
    return ProcessingDecision(
        should_process=False, reason=reason,
        is_manual_review=True, flagged_subtitles=flagged,
    )


def nothing_to_do(reason="already compliant"):
    return ProcessingDecision(should_process=False, reason=reason)


# ── The two ways a job never loads ───────────────────────────────────────────

def test_a_job_that_no_longer_exists_loads_nothing(rig):
    assert worker._load_job_data(404) is None
    assert rig.finish_calls == []


def test_a_job_whose_media_row_is_gone_is_failed_rather_than_loaded(rig):
    queue_row(rig, file_id=99)

    assert worker._load_job_data(1) is None
    assert rig.finish_calls == [(1, False, None, None, "File not found on disk")]


def test_a_file_deleted_from_disk_is_failed_rather_than_loaded(rig):
    """
    The row surviving its file is the ordinary case — something moved or
    deleted the media between the scan and the pickup. Without the on-disk
    check the job proceeds to ffmpeg and fails there instead, with an
    error describing the wrong problem.
    """
    media_row(rig)
    queue_row(rig)
    os.remove(rig.path)

    assert worker._load_job_data(1) is None
    assert rig.finish_calls == [(1, False, None, None, "File not found on disk")]
    assert job(rig).status == "pending"      # left for _finish_job to settle


# ── Outcome one: manual review ───────────────────────────────────────────────

def test_a_manual_review_decision_gates_both_the_job_and_the_file(rig):
    """
    Both rows have to move. The queue item is what the worker looks at and
    the media row is what Language Review lists from, so gating only one
    leaves the file either invisible to review or eligible to be queued
    again by the next scan.
    """
    media_row(rig)
    queue_row(rig)
    rig.decision = manual_review(reason="two image-based subtitle tracks")

    assert worker._load_job_data(1) is None
    assert job(rig).status == "manual_review"
    assert job(rig).reason == "two image-based subtitle tracks"
    assert file_row(rig).status == "manual_review"


def test_a_review_stores_the_tracks_it_flagged(rig):
    media_row(rig)
    queue_row(rig)
    flagged = [{"stream_index": 3, "language": "eng", "codec": "hdmv_pgs_subtitle"}]
    rig.decision = manual_review(flagged=flagged)

    worker._load_job_data(1)

    assert json.loads(job(rig).review_subtitles) == flagged


def test_a_review_with_nothing_flagged_stores_no_subtitles(rig):
    """
    Not every manual review comes from subtitles. The column has to be
    left empty rather than holding the JSON text "null", which the UI
    would read as a list of flagged tracks and fail to render.
    """
    media_row(rig)
    queue_row(rig)
    rig.decision = manual_review(flagged=None)

    worker._load_job_data(1)

    assert job(rig).review_subtitles is None


def test_a_manual_review_leaves_the_language_flags_alone(rig):
    """
    The ordering guard the docstring is mostly about. A manual-review
    decision returns before mismatch detection runs, so its mismatch
    fields are always None — upserting on it would read those Nones as
    "no mismatch" and clear flags that are still valid. The upsert sits
    below this branch for that reason, and nothing else records that it
    has to.
    """
    media_row(rig)
    queue_row(rig)
    rig.decision = manual_review()

    worker._load_job_data(1)

    assert rig.upsert_calls == []


# ── Outcome two: nothing to do ───────────────────────────────────────────────

def test_a_no_op_decision_is_skipped_rather_than_executed(rig):
    """
    A no-op decision carries target_container=None, which
    build_ffmpeg_command hard-rejects. Executing one produces
    "Unsupported output container None" — a failure that describes the
    plumbing rather than the fact there was simply nothing left to do.
    """
    media_row(rig)
    queue_row(rig)
    rig.decision = nothing_to_do(reason="already mkv with the wanted tracks")

    assert worker._load_job_data(1) is None
    assert job(rig).status == "skipped"
    assert job(rig).reason == "already mkv with the wanted tracks"
    assert job(rig).review_subtitles is None
    assert file_row(rig).status == "skipped"


def test_a_skipped_job_is_given_a_completion_time(rig):
    """Nothing else sets it on this path, and the history view orders by
    it — a skipped job without one does not appear as finished."""
    media_row(rig)
    queue_row(rig)
    rig.decision = nothing_to_do()

    worker._load_job_data(1)

    assert job(rig).completed_at is not None


def test_a_no_op_decision_still_updates_the_language_flags(rig):
    """
    The other side of the ordering guard: skipping is a real outcome of a
    completed analysis, so its mismatch fields are meaningful and a
    correction resolved via Approve should show up in Language Review
    without waiting for the next full scan.
    """
    media_row(rig)
    queue_row(rig)
    rig.decision = nothing_to_do()

    worker._load_job_data(1)

    assert rig.upsert_calls == [rig.decision]


# ── Outcome three: proceeding ────────────────────────────────────────────────

def test_a_proceeding_job_returns_plain_data_for_execution(rig):
    """
    Everything returned crosses a thread boundary into the executor, so
    none of it can be an ORM object bound to the session this function
    closes on the way out.
    """
    media_row(rig)
    queue_row(rig, is_dry_run=True)
    track_row(rig, track_id=1, stream_index=0, track_type="video", codec="h264")

    loaded = worker._load_job_data(1)

    assert loaded is not None
    job_dict, file_dict, tracks, app_cfg, decision = loaded
    assert job_dict == {"id": 1, "is_dry_run": True}
    assert file_dict == {"id": 1, "path": rig.path,
                         "filename": "Show.mkv", "size": 18}
    assert all(isinstance(t, dict) for t in tracks)
    assert isinstance(app_cfg, dict)
    assert decision is rig.decision


def test_the_stale_plan_is_replaced_rather_than_added_to(rig):
    """
    The plan is rewritten because the decision was recomputed and may no
    longer match the one queued. Leaving the old rows means the UI shows a
    plan the job is not running — which is what happened before this
    refresh existed.
    """
    media_row(rig)
    queue_row(rig)
    rig.db.add(PlannedAction(queue_item_id=1, order=0, action_type="drop_track",
                             description="stale row from scan time"))
    rig.db.commit()
    rig.decision = proceeding([
        Action(action_type="copy_track", description="keep english audio",
               track_type="audio", stream_index=1),
    ])

    worker._load_job_data(1)

    assert [a.description for a in actions(rig)] == ["keep english audio"]


def test_the_fresh_plan_is_stored_in_the_order_it_will_run(rig):
    """
    Position in the list is what decides the stored order, not the
    Action's own order field — every action here carries order=9 and none
    of them is stored that way. The UI renders the plan by this column, so
    losing it turns an ordered plan into an arbitrary one.
    """
    media_row(rig)
    queue_row(rig)
    rig.decision = proceeding([
        Action(action_type="copy_track", description="first", order=9),
        Action(action_type="drop_track", description="second", order=9),
        Action(action_type="change_container", description="third", order=9),
    ])

    worker._load_job_data(1)

    assert [(a.order, a.description) for a in actions(rig)] == [
        (0, "first"), (1, "second"), (2, "third"),
    ]


def test_an_action_keeps_the_language_it_targets(rig):
    """
    target_language is read back much later, by the Plex backlog drain,
    to work out whether Plex has already picked the change up. Dropping
    it here costs nothing visible until that drain has nothing to compare
    against.
    """
    media_row(rig)
    queue_row(rig)
    rig.decision = proceeding([
        Action(action_type="copy_track", description="set audio to english",
               track_type="audio", stream_index=1, target_language="eng"),
    ])

    worker._load_job_data(1)

    stored = actions(rig)[0]
    assert stored.target_language == "eng"
    assert (stored.action_type, stored.track_type, stored.stream_index) == (
        "copy_track", "audio", 1,
    )


# ── What the decision is recomputed from ─────────────────────────────────────

def test_the_decision_is_recomputed_from_the_stored_file_and_tracks(rig):
    media_row(rig, container="mkv", video_codec="hevc",
              und_audio_threshold_acknowledged=True, font_attachments=3)
    queue_row(rig)
    track_row(rig, track_id=1, stream_index=0, track_type="video", codec="hevc")
    track_row(rig, track_id=2, stream_index=1, track_type="audio",
              codec="ac3", language="eng")

    worker._load_job_data(1)

    assert len(rig.analyze_calls) == 1
    call = rig.analyze_calls[0]
    assert call.file_info == {
        "path": rig.path,
        "container": "mkv",
        "video_codec": "hevc",
        "font_attachments": 3,
        "und_audio_threshold_acknowledged": True,
    }
    assert [(t["stream_index"], t["track_type"]) for t in call.tracks] == [
        (0, "video"), (1, "audio"),
    ]


def test_each_language_override_set_reaches_its_own_parameter(rig):
    """
    Two same-shaped dicts read from two columns and passed as two
    adjacent keyword arguments. Crossed, they produce a decision that
    relabels audio from the subtitle corrections and vice versa — no
    error, just the wrong languages written into the file.
    """
    media_row(
        rig,
        audio_language_overrides=json.dumps({"1": "eng"}),
        subtitle_language_overrides=json.dumps({"2": "fre"}),
        subtitle_overrides=json.dumps({"3": "keep"}),
    )
    queue_row(rig)

    worker._load_job_data(1)

    kwargs = rig.analyze_calls[0].kwargs
    assert kwargs["audio_language_overrides"] == {1: "eng"}
    assert kwargs["subtitle_language_overrides"] == {2: "fre"}
    assert kwargs["subtitle_overrides"] == {3: "keep"}


@pytest.mark.parametrize("container", ["mkv", "avi", None])
def test_faststart_is_not_probed_for_anything_but_mp4(rig, container):
    """
    is_faststart_mp4 opens and reads the file. Running it for every
    container costs a read per job on formats where the answer is
    meaningless, and None — not False — is what tells the decision engine
    the question does not apply.
    """
    media_row(rig, container=container)
    queue_row(rig)

    worker._load_job_data(1)

    assert rig.faststart_calls == []
    assert rig.analyze_calls[0].kwargs["has_faststart"] is None


def test_faststart_is_probed_for_mp4(rig):
    media_row(rig, container="MP4")      # case-insensitive by design
    queue_row(rig)

    worker._load_job_data(1)

    assert rig.faststart_calls == [rig.path]
    assert rig.analyze_calls[0].kwargs["has_faststart"] is True
