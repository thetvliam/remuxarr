"""
queue.py — the routes a user presses buttons on.

Two things here are worth more than the line coverage suggests.

THE SENTINEL INVARIANT
  Several routes dismiss a file: cancel one item, clear all pending, clear a
  dry-run batch. Every one of them must reset MediaFile.size/mtime to -1
  alongside the status, because the scanner's delta check compares ONLY
  size/mtime against the on-disk stat and has no awareness of .status at all.
  Without the reset, a dismissed file's unchanged bytes read as "nothing to
  do" and it is never re-evaluated by any delta scan — directly contradicting
  the frontend's own copy for all three actions ("they re-appear on the next
  scan").

  This has already been missed twice: clear_dry_run and history clear/delete
  reset the sentinels, cancel_item and clear_pending did not. So the invariant
  is tested as an invariant — test_every_dismissal_route_resets_the_sentinels
  drives each route through a shared table and fails on any that forgets,
  including one added later. tests/test_queue_lifecycle.py already covers the
  two individual routes; this is the generalisation, not a duplicate.

COUNTS THAT MEAN WHAT THEY SAY
  retry_all_failed's return values were wrong in a specific way: "retried"
  counted every item _process_file did not raise on, which is not the same as
  re-queued. A settings change that turned 40 of 50 failures into no-ops
  still reported 50, and the queue then showed 10. The counts are read
  straight into UI copy, so they are tested against the ScanStats outcomes
  rather than against the number of items looped over.

  It also used to re-queue cancelled rows along with the failed ones, though
  cancelled is what Skip, dismiss, Clear queue and Abort write. It takes
  failed rows only now. Widening the filter back to both survived the whole
  1518-test suite and is killed by test_cancelled_items_are_left_where_they_are.

Verified by mutation: 37 mutations of queue.py's cancel/clear/prioritise/retry/
serialise routes, every one killed by at least one test here. The invariant
test was checked the same way — adding a new dismissal route that sets status
but forgets the sentinels fails it by name.

That 37-mutation figure originally read as though it covered queue.py as a
whole. It did not: approve_manual_review was outside it, and an independent
audit found the endpoint had ZERO effective coverage — a test in
test_manual_review_refresh.py appeared to cover it but re-implemented the
inference in its own body and never called it. Four mutations of that endpoint
survived the entire suite. The approve tests below are the fix; the wording
above is now scoped to what it actually measured.

Also added from that audit: worker._claim_next, which nothing referenced at
all (both reversing the claim order and inverting the pending filter survived).
It is tested here rather than in a worker file because it is the consumption
end of the prioritize feature above — the ordering was pinned at the route
level and never verified where it is read.
"""
import json

import pytest
from fastapi import HTTPException


# ── Harness ──────────────────────────────────────────────────────────────────

REAL_SIZE  = 4_000_000_000
REAL_MTIME = 1_700_000_000.0


@pytest.fixture
def db():
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    from app.database.models import Base

    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)()


def _file(db, file_id=1, status="queued"):
    from app.database.models import MediaFile

    mf = MediaFile(id=file_id, path=f"/media/f{file_id}.mkv",
                   filename=f"f{file_id}.mkv", directory="/media",
                   size=REAL_SIZE, mtime=REAL_MTIME, status=status,
                   container="mkv", duration=1200.0)
    db.add(mf)
    db.commit()
    return mf


def _item(db, item_id=1, file_id=1, status="pending", **kw):
    from app.database.models import QueueItem

    qi = QueueItem(id=item_id, file_id=file_id, status=status, **kw)
    db.add(qi)
    db.commit()
    return qi


def _sentinels(db, file_id=1):
    from app.database.models import MediaFile

    m = db.get(MediaFile, file_id)
    return (m.size, m.mtime, m.status)


# ── The sentinel invariant ───────────────────────────────────────────────────

def _dismiss_via_cancel_item(db):
    from app.api.routes.queue import cancel_item
    _file(db)
    _item(db, status="pending")
    cancel_item(1, db)


def _dismiss_via_clear_pending(db):
    from app.api.routes.queue import clear_pending
    _file(db)
    _item(db, status="pending")
    clear_pending(db)


def _dismiss_via_clear_dry_run(db):
    from app.api.routes.queue import clear_dry_run
    _file(db)
    _item(db, status="dry_run")
    clear_dry_run(db)


def _dismiss_via_cancel_manual_review(db):
    from app.api.routes.queue import cancel_item
    _file(db)
    _item(db, status="manual_review")
    cancel_item(1, db)


DISMISSAL_ROUTES = {
    "cancel_item (pending)":        _dismiss_via_cancel_item,
    "cancel_item (manual_review)":  _dismiss_via_cancel_manual_review,
    "clear_pending":                _dismiss_via_clear_pending,
    "clear_dry_run":                _dismiss_via_clear_dry_run,
}


@pytest.mark.parametrize("label", sorted(DISMISSAL_ROUTES))
def test_every_dismissal_route_resets_the_sentinels(db, label):
    """
    The invariant, applied to every route that dismisses a file.

    A dismissal that sets status but leaves size/mtime matching the on-disk
    file is invisible to every delta scan, because the delta check reads only
    those two fields. The file then sits dismissed forever, or until a forced
    full scan happens to touch it.

    If a new dismissal route is added, add it to DISMISSAL_ROUTES — this test
    is the thing that will catch it forgetting the reset, which has already
    happened twice.
    """
    DISMISSAL_ROUTES[label](db)

    size, mtime, status = _sentinels(db)
    assert size == -1, f"{label}: size not reset — file invisible to delta scans"
    assert mtime == -1.0, f"{label}: mtime not reset"
    assert status == "skipped", f"{label}: media status left at '{status}'"


# ── clear_dry_run ────────────────────────────────────────────────────────────

def test_clearing_dry_runs_deletes_the_rows_outright(db):
    """
    Unlike a cancelled job, a discarded preview has no history worth keeping —
    so the rows go, rather than being marked cancelled.
    """
    from app.api.routes.queue import clear_dry_run
    from app.database.models import QueueItem

    _file(db)
    _item(db, status="dry_run")

    assert clear_dry_run(db) == {"cleared": 1}
    assert db.query(QueueItem).count() == 0


def test_clearing_dry_runs_leaves_other_statuses_alone(db):
    """
    dry_run is a separate terminal status set by _finish_job. This endpoint
    exists precisely because clear_pending never touches it — the reverse must
    hold too.
    """
    from app.api.routes.queue import clear_dry_run
    from app.database.models import QueueItem

    for i, status in enumerate(["pending", "failed", "success", "manual_review"], 1):
        _file(db, file_id=i)
        _item(db, item_id=i, file_id=i, status=status)
    _file(db, file_id=99)
    _item(db, item_id=99, file_id=99, status="dry_run")

    assert clear_dry_run(db) == {"cleared": 1}
    assert db.query(QueueItem).count() == 4


def test_clearing_an_empty_dry_run_batch_is_a_no_op(db):
    from app.api.routes.queue import clear_dry_run

    assert clear_dry_run(db) == {"cleared": 0}


def test_a_dry_run_item_with_no_media_row_does_not_break_the_clear(db):
    """
    The `if item.media_file:` guard. One orphaned row must not abort the whole
    batch and leave the rest of the previews stranded.
    """
    from app.api.routes.queue import clear_dry_run
    from app.database.models import QueueItem

    _file(db, file_id=1)
    _item(db, item_id=1, file_id=1, status="dry_run")
    _item(db, item_id=2, file_id=1, status="dry_run")
    db.query(QueueItem).filter(QueueItem.id == 2).update({"file_id": 4242})
    db.commit()

    assert clear_dry_run(db) == {"cleared": 2}
    assert db.query(QueueItem).count() == 0


# ── clear_pending ────────────────────────────────────────────────────────────

def test_clearing_pending_only_touches_pending_items(db):
    from app.api.routes.queue import clear_pending
    from app.database.models import QueueItem

    for i, status in enumerate(["pending", "processing", "dry_run", "failed"], 1):
        _file(db, file_id=i)
        _item(db, item_id=i, file_id=i, status=status)

    assert clear_pending(db) == {"cancelled": 1}

    remaining = {q.id: q.status for q in db.query(QueueItem).all()}
    assert remaining == {1: "cancelled", 2: "processing", 3: "dry_run", 4: "failed"}


def test_clearing_pending_stamps_completed_at(db):
    """
    History orders by completed_at DESC and SQLite sorts NULLs last, so a
    cancelled row without it sinks to the bottom of the Failed tab regardless
    of recency and renders a "—" timestamp.
    """
    from app.api.routes.queue import clear_pending
    from app.database.models import QueueItem

    _file(db)
    _item(db, status="pending")

    clear_pending(db)

    assert db.get(QueueItem, 1).completed_at is not None


def test_clearing_pending_resets_every_affected_file(db):
    from app.api.routes.queue import clear_pending

    for i in (1, 2, 3):
        _file(db, file_id=i)
        _item(db, item_id=i, file_id=i, status="pending")

    assert clear_pending(db) == {"cancelled": 3}
    for i in (1, 2, 3):
        assert _sentinels(db, i) == (-1, -1.0, "skipped")


def test_clearing_an_empty_queue_is_a_no_op(db):
    from app.api.routes.queue import clear_pending

    assert clear_pending(db) == {"cancelled": 0}


# ── cancel_item guards ───────────────────────────────────────────────────────

def test_cancelling_a_missing_item_is_a_404(db):
    from app.api.routes.queue import cancel_item

    with pytest.raises(HTTPException) as exc:
        cancel_item(999, db)
    assert exc.value.status_code == 404


@pytest.mark.parametrize("status", ["processing", "success", "failed",
                                    "cancelled", "dry_run"])
def test_only_pending_and_manual_review_items_can_be_cancelled(db, status):
    """
    "processing" matters most: the worker owns that row, and cancelling it
    here would race the job rather than stopping it. Aborting a running job is
    a different operation (abort_job) with its own task-registry handling.
    """
    from app.api.routes.queue import cancel_item
    from app.database.models import QueueItem

    _file(db)
    _item(db, status=status)

    with pytest.raises(HTTPException) as exc:
        cancel_item(1, db)
    assert exc.value.status_code == 400

    assert db.get(QueueItem, 1).status == status
    assert _sentinels(db) == (REAL_SIZE, REAL_MTIME, "queued"), (
        "a rejected cancel still reset the file's delta sentinels"
    )


def test_cancelling_stamps_completed_at(db):
    from app.api.routes.queue import cancel_item
    from app.database.models import QueueItem

    _file(db)
    _item(db, status="pending")

    cancel_item(1, db)

    assert db.get(QueueItem, 1).completed_at is not None


# ── prioritize_item ──────────────────────────────────────────────────────────

def test_prioritising_moves_an_item_below_the_current_minimum(db):
    """The worker orders by priority ASC, so lower wins."""
    from app.api.routes.queue import prioritize_item

    for i in (1, 2, 3):
        _file(db, file_id=i)
        _item(db, item_id=i, file_id=i, status="pending", priority=5)

    assert prioritize_item(3, db) == {"id": 3, "priority": 4}


def test_prioritising_repeatedly_keeps_producing_a_deterministic_order(db):
    """
    Each call recalculates the minimum across all OTHER pending items, so
    pressing the button on several items in turn puts the last one first.
    """
    from app.api.routes.queue import prioritize_item

    for i in (1, 2, 3):
        _file(db, file_id=i)
        _item(db, item_id=i, file_id=i, status="pending", priority=5)

    first  = prioritize_item(1, db)["priority"]
    second = prioritize_item(2, db)["priority"]
    third  = prioritize_item(3, db)["priority"]

    assert second < first, "second press did not overtake the first"
    assert third < second, "third press did not overtake the second"


def test_prioritising_the_only_pending_item_resets_to_the_default(db):
    """
    With nothing to overtake there is no minimum to go below, so it returns to
    the default rather than drifting ever more negative on repeated presses.
    """
    from app.api.routes.queue import prioritize_item

    _file(db)
    _item(db, status="pending", priority=2)

    assert prioritize_item(1, db) == {"id": 1, "priority": 5}


def test_the_minimum_ignores_non_pending_items(db):
    """
    A finished item's priority is stale bookkeeping. Counting it would let a
    long-completed row dictate where a new one lands.
    """
    from app.api.routes.queue import prioritize_item

    _file(db, file_id=1)
    _item(db, item_id=1, file_id=1, status="success", priority=-50)
    _file(db, file_id=2)
    _item(db, item_id=2, file_id=2, status="pending", priority=5)
    _file(db, file_id=3)
    _item(db, item_id=3, file_id=3, status="pending", priority=5)

    assert prioritize_item(3, db)["priority"] == 4


def test_prioritising_a_missing_item_is_a_404(db):
    from app.api.routes.queue import prioritize_item

    with pytest.raises(HTTPException) as exc:
        prioritize_item(999, db)
    assert exc.value.status_code == 404


@pytest.mark.parametrize("status", ["processing", "success", "failed",
                                    "manual_review", "dry_run"])
def test_only_pending_items_can_be_prioritised(db, status):
    from app.api.routes.queue import prioritize_item

    _file(db)
    _item(db, status=status, priority=5)

    with pytest.raises(HTTPException) as exc:
        prioritize_item(1, db)
    assert exc.value.status_code == 400


# ── retry_all_failed ─────────────────────────────────────────────────────────

@pytest.fixture
def retry(monkeypatch):
    """
    Stub _process_file and the settings lookup, and drive ScanStats directly —
    what is under test is which counts come back, not the decision engine.
    """
    import app.api.routes.queue as q

    calls = []
    outcomes = {}

    def _process_file(db, path, cfg, **kw):
        calls.append({"path": path, **kw})
        outcome = outcomes.get(path, "queued")
        if outcome == "raise":
            raise ValueError(f"unknown container info for {path}")
        setattr(kw["stats"], outcome, getattr(kw["stats"], outcome) + 1)

    monkeypatch.setattr(q, "_process_file", _process_file)
    monkeypatch.setattr(q, "get_app_settings", lambda _db: {})
    monkeypatch.setattr(q, "_current_dry_run_mode", lambda _db: False)
    monkeypatch.setattr(q.os.path, "exists", lambda p: p != "/media/gone.mkv")

    q._calls = calls
    q._outcomes = outcomes
    return q


def test_retrying_with_nothing_failed_is_a_no_op(db, retry):
    from app.api.routes.queue import retry_all_failed

    assert retry_all_failed(db) == {"retried": 0, "skipped": 0}
    assert retry._calls == []


def test_failed_items_are_retried(db, retry):
    from app.api.routes.queue import retry_all_failed

    _file(db)
    _item(db, status="failed")

    assert retry_all_failed(db)["retried"] == 1


def test_cancelled_items_are_left_where_they_are(db, retry):
    """
    Cancelled is what Skip in Review, dismissing a queued item, Clear queue
    and Abort all write, so it records a removal the user asked for. Retry All
    used to re-queue these alongside the failures, so one press sent every
    skipped file back to Review and every cleared file back to the queue.

    Mixed with a failed row on purpose: the failed one still has to be
    retried, so this cannot pass by retrying nothing at all. The cancelled
    row must survive untouched, because retry deletes each row it takes
    before re-running it.
    """
    from app.api.routes.queue import retry_all_failed
    from app.database.models import QueueItem

    _file(db, file_id=1)
    _item(db, item_id=1, file_id=1, status="failed")
    _file(db, file_id=2)
    _item(db, item_id=2, file_id=2, status="cancelled")

    result = retry_all_failed(db)

    assert result["retried"] == 1
    assert [c["path"] for c in retry._calls] == ["/media/f1.mkv"]
    assert db.get(QueueItem, 2).status == "cancelled"


@pytest.mark.parametrize("status", ["pending", "processing", "success",
                                    "manual_review", "dry_run"])
def test_unfinished_and_successful_items_are_left_alone(db, retry, status):
    from app.api.routes.queue import retry_all_failed

    _file(db)
    _item(db, status=status)

    assert retry_all_failed(db) == {"retried": 0, "skipped": 0}


def test_every_retry_forces_a_fresh_probe(db, retry):
    """
    The point of retry: pick up settings changes, code fixes, or on-disk
    changes since the failure. A cached probe would replay the same decision.
    """
    from app.api.routes.queue import retry_all_failed

    _file(db)
    _item(db, status="failed")

    retry_all_failed(db)

    assert retry._calls[0]["force_probe"] is True


def test_arr_ids_survive_the_retry(db, retry):
    """
    Preserved so the notification chain fires after the re-processed job
    completes. Previously "Retry All" dropped them, so webhook-originated
    failures produced jobs that never fired RescanSeries/RescanMovie —
    even though single-item retry preserved them correctly.
    """
    from app.api.routes.queue import retry_all_failed

    _file(db)
    _item(db, status="failed", sonarr_series_id=17, radarr_movie_id=42)

    retry_all_failed(db)

    assert retry._calls[0]["sonarr_series_id"] == 17
    assert retry._calls[0]["radarr_movie_id"] == 42


def test_a_file_gone_from_disk_is_skipped_not_retried(db, retry):
    from app.api.routes.queue import retry_all_failed
    from app.database.models import MediaFile

    _file(db)
    db.query(MediaFile).filter(MediaFile.id == 1).update({"path": "/media/gone.mkv"})
    db.commit()
    _item(db, status="failed")

    assert retry_all_failed(db)["skipped"] == 1
    assert retry._calls == []


def test_the_retried_count_reflects_requeued_work_not_items_looped_over(db, retry):
    """
    The bug this counting was changed for. "retried" used to mean "every item
    _process_file did not raise on", so a settings change that turned most
    failures into no-ops still reported all of them as requeued and the queue
    then showed a fraction of that.
    """
    from app.api.routes.queue import retry_all_failed

    for i in (1, 2, 3, 4):
        _file(db, file_id=i)
        _item(db, item_id=i, file_id=i, status="failed")
    retry._outcomes["/media/f2.mkv"] = "skipped"
    retry._outcomes["/media/f3.mkv"] = "skipped"
    retry._outcomes["/media/f4.mkv"] = "manual_review"

    result = retry_all_failed(db)

    assert result["retried"] == 1
    assert result["skipped"] == 2
    assert result["manual_review"] == 1


def test_manual_review_outcomes_are_reported_separately(db, retry):
    """
    Not finished — waiting on the user. Folding them into either other count
    would hide that, and a retry that moved items to Review used to look like
    it had done nothing.
    """
    from app.api.routes.queue import retry_all_failed

    _file(db)
    _item(db, status="failed")
    retry._outcomes["/media/f1.mkv"] = "manual_review"

    result = retry_all_failed(db)

    assert result["manual_review"] == 1
    assert result["retried"] == 0
    assert result["skipped"] == 0


def test_one_bad_file_does_not_abandon_the_rest_of_the_batch(db, retry):
    """
    Mirrors scan_library's own per-file protection. Without it, one raising
    file kills the request with a 500 and silently abandons every item queued
    behind it, with no indication of where the batch stopped.
    """
    from app.api.routes.queue import retry_all_failed

    for i in (1, 2, 3):
        _file(db, file_id=i)
        _item(db, item_id=i, file_id=i, status="failed")
    retry._outcomes["/media/f2.mkv"] = "raise"

    result = retry_all_failed(db)

    assert [c["path"] for c in retry._calls] == [
        "/media/f1.mkv", "/media/f2.mkv", "/media/f3.mkv"
    ], "the batch stopped at the failing file"
    assert result["retried"] == 2
    assert len(result["errors"]) == 1
    assert result["errors"][0]["path"] == "/media/f2.mkv"


# ── approve_manual_review ────────────────────────────────────────────────────
#
# These replace a tautological test that lived in test_manual_review_refresh.py
# and re-implemented this endpoint's inference in its own body — it never
# called approve_manual_review, so it passed regardless of what queue.py did.
# Three mutations of the endpoint (invert the inference, never set the flag,
# drop the status guard) all survived the entire suite before these existed.

def _review_item(db, review_subtitles=None, status="manual_review"):
    """A manual-review item whose media row has a video and an audio track."""
    from app.database.models import Track

    _file(db)
    db.add_all([
        Track(file_id=1, stream_index=0, track_type="video", codec="h264"),
        Track(file_id=1, stream_index=1, track_type="audio", codec="aac",
              language="eng", channels=2),
    ])
    db.commit()
    return _item(db, status=status, review_subtitles=review_subtitles)


def test_approving_a_threshold_review_acknowledges_the_gate(db):
    """
    review_subtitles being NULL is the established signal that this review
    came from the undefined-audio threshold gate rather than the image-
    subtitle one. That gate has no per-track override, so without persisting
    the exemption the fresh analyze_file() below would re-trigger the
    identical gate immediately — a track's language tag never changes on its
    own, so the item would bounce straight back into manual review forever.
    """
    from app.api.routes.queue import approve_manual_review
    from app.database.models import MediaFile

    _review_item(db, review_subtitles=None)

    approve_manual_review(1, db)

    assert db.get(MediaFile, 1).und_audio_threshold_acknowledged is True


def test_approving_a_subtitle_review_does_not_acknowledge_the_audio_gate(db):
    """
    The provenance collision, tested against the real endpoint this time.

    A subtitle-encoding review carries a non-null review_subtitles. Flipping
    the acknowledgement flag for it would permanently exempt the file from a
    threshold check it never tripped — a silent, permanent loss of a safety
    gate, on a file the user only meant to approve some subtitles for.
    """
    from app.api.routes.queue import approve_manual_review
    from app.database.models import MediaFile

    _review_item(db, review_subtitles=json.dumps([{"stream_index": 2}]))

    approve_manual_review(1, db)

    assert db.get(MediaFile, 1).und_audio_threshold_acknowledged is False, (
        "approving a SUBTITLE review acknowledged the undefined-audio "
        "threshold gate — the file is now permanently exempt from a check "
        "it never tripped"
    )


def test_approving_re_runs_the_decision_engine(db, monkeypatch):
    """
    Not just a status flip. The worker recomputes its own decision at pickup,
    so processing was never wrong — but the reason text and Planned Actions
    shown in the UI stayed stale, still describing why the file needed review,
    for as long as it sat in the queue.
    """
    import app.api.routes.queue as q

    _review_item(db, review_subtitles=None)

    seen = {}
    real_analyze = q.analyze_file

    def _spy(file_info, tracks, cfg, **kw):
        seen["called"] = True
        seen["acknowledged"] = file_info.get("und_audio_threshold_acknowledged")
        return real_analyze(file_info, tracks, cfg, **kw)

    monkeypatch.setattr(q, "analyze_file", _spy)

    q.approve_manual_review(1, db)

    assert seen.get("called"), "approve did not re-run the decision engine"


def test_the_exemption_is_visible_to_the_fresh_decision(db, monkeypatch):
    """
    Ordering, which is the whole point of setting the flag first. If the
    exemption were persisted after analyze_file ran, the fresh decision would
    still see the un-acknowledged file, re-trigger the gate, and leave the
    item in manual_review — the flag would be set but useless until the next
    scan.
    """
    import app.api.routes.queue as q

    _review_item(db, review_subtitles=None)

    seen = {}
    real_analyze = q.analyze_file

    def _spy(file_info, tracks, cfg, **kw):
        seen["acknowledged"] = file_info.get("und_audio_threshold_acknowledged")
        return real_analyze(file_info, tracks, cfg, **kw)

    monkeypatch.setattr(q, "analyze_file", _spy)

    q.approve_manual_review(1, db)

    assert seen["acknowledged"] is True, (
        "analyze_file saw the file as un-acknowledged — the exemption was "
        "applied after the decision instead of before it"
    )


def test_the_decision_outcome_is_applied_to_the_item(db, monkeypatch):
    """
    The fresh decision has to reach the row. Computing it and discarding it
    would leave the item in manual_review with its stale reason — which is
    exactly the pre-fix behaviour the endpoint's docstring describes.
    """
    import app.api.routes.queue as q
    from app.database.models import QueueItem

    _review_item(db, review_subtitles=None)

    applied = []
    real_apply = q._apply_decision_to_item
    monkeypatch.setattr(
        q, "_apply_decision_to_item",
        lambda db_, item, media, decision: (
            applied.append(decision), real_apply(db_, item, media, decision))[1],
    )

    q.approve_manual_review(1, db)

    assert applied, "the fresh decision was computed and then discarded"
    assert db.get(QueueItem, 1).status != "manual_review", (
        "the item stayed in manual_review after approval"
    )


def test_approving_a_missing_item_is_a_404(db):
    from app.api.routes.queue import approve_manual_review

    with pytest.raises(HTTPException) as exc:
        approve_manual_review(999, db)
    assert exc.value.status_code == 404


@pytest.mark.parametrize("status", ["pending", "processing", "success",
                                    "failed", "cancelled", "dry_run"])
def test_only_manual_review_items_can_be_approved(db, status):
    """
    "processing" matters most: the worker owns that row, and re-running the
    decision engine underneath it would rewrite the planned actions of a job
    already executing them.
    """
    from app.api.routes.queue import approve_manual_review
    from app.database.models import MediaFile

    _review_item(db, review_subtitles=None, status=status)

    with pytest.raises(HTTPException) as exc:
        approve_manual_review(1, db)
    assert exc.value.status_code == 400

    assert db.get(MediaFile, 1).und_audio_threshold_acknowledged is False, (
        "a rejected approval still acknowledged the threshold gate"
    )


def test_approving_an_item_whose_media_row_is_gone_is_a_404(db):
    from app.api.routes.queue import approve_manual_review
    from app.database.models import QueueItem

    _review_item(db, review_subtitles=None)
    db.query(QueueItem).filter(QueueItem.id == 1).update({"file_id": 4242})
    db.commit()

    with pytest.raises(HTTPException) as exc:
        approve_manual_review(1, db)
    assert exc.value.status_code == 404


# ── _serialize ───────────────────────────────────────────────────────────────

def test_a_serialised_item_carries_its_file_details(db):
    from app.api.routes.queue import _serialize

    _file(db)
    item = _item(db, status="pending", reason="drop 2 audio tracks")

    out = _serialize(item)

    assert out["id"] == 1
    assert out["status"] == "pending"
    assert out["reason"] == "drop 2 audio tracks"
    assert out["file"]["filename"] == "f1.mkv"
    assert out["file"]["container"] == "mkv"


def test_an_item_whose_media_row_is_gone_serialises_with_a_null_file(db):
    """
    The UI renders this. Raising here would take out the whole list endpoint
    because of one orphaned row.
    """
    from app.api.routes.queue import _serialize
    from app.database.models import QueueItem

    _file(db)
    _item(db, status="pending")
    db.query(QueueItem).filter(QueueItem.id == 1).update({"file_id": 4242})
    db.commit()

    out = _serialize(db.get(QueueItem, 1))

    assert out["file"] is None


def test_flagged_subtitles_are_parsed_from_json(db):
    from app.api.routes.queue import _serialize

    _file(db)
    item = _item(db, status="manual_review",
                 review_subtitles=json.dumps([{"stream_index": 3,
                                               "codec": "hdmv_pgs_subtitle"}]))

    out = _serialize(item)

    assert out["flagged_subtitles"] == [{"stream_index": 3,
                                         "codec": "hdmv_pgs_subtitle"}]


def test_unparseable_flagged_subtitles_degrade_to_null(db):
    """
    Corrupt bookkeeping must not break the queue view — the item still needs
    to be visible so the user can dismiss it.
    """
    from app.api.routes.queue import _serialize

    _file(db)
    item = _item(db, status="manual_review", review_subtitles="{not json")

    assert _serialize(item)["flagged_subtitles"] is None


def test_planned_actions_are_omitted_unless_asked_for(db):
    """
    The list endpoints serialise every item; loading each one's actions would
    be a query per row.
    """
    from app.api.routes.queue import _serialize

    _file(db)
    item = _item(db, status="pending")

    assert "planned_actions" not in _serialize(item)
    assert _serialize(item, include_actions=True)["planned_actions"] == []


def test_planned_actions_are_returned_in_order(db):
    from app.api.routes.queue import _serialize
    from app.database.models import PlannedAction

    _file(db)
    item = _item(db, status="pending")
    for order in (2, 0, 1):
        db.add(PlannedAction(queue_item_id=1, order=order,
                             action_type="drop_track",
                             description=f"step {order}",
                             track_type="audio", stream_index=order))
    db.commit()
    db.refresh(item)

    actions = _serialize(item, include_actions=True)["planned_actions"]

    assert [a["order"] for a in actions] == [0, 1, 2]


def test_missing_timestamps_serialise_as_null_rather_than_raising(db):
    from app.api.routes.queue import _serialize

    _file(db)
    item = _item(db, status="pending")

    out = _serialize(item)

    assert out["started_at"] is None
    assert out["completed_at"] is None
    assert out["created_at"] is not None


# ── worker._claim_next ───────────────────────────────────────────────────────
#
# Found by an independent mutation audit (Phase 1): `grep -rn "_claim_next"
# tests/` returned nothing, and both reversing the claim order and inverting
# the pending filter survived the entire 662-test suite.
#
# This lives here rather than in a worker test file because it is the
# consumption end of the prioritize feature tested above: prioritize_item's
# effect on ordering was pinned at the route level and then never verified at
# the point where the ordering is actually read.

@pytest.fixture
def claim(db, monkeypatch):
    """Bind worker.SessionLocal to the test's own session, unclosed."""
    from sqlalchemy.orm import sessionmaker

    import app.core.worker as worker

    monkeypatch.setattr(worker, "SessionLocal",
                        sessionmaker(bind=db.get_bind()))
    return worker


def test_an_empty_queue_claims_nothing(claim):
    assert claim._claim_next() is None


def test_only_pending_items_are_claimable(claim, db):
    """
    The serious one. Claiming a "processing" item re-runs a job already
    executing; claiming "success" reprocesses a finished file; claiming
    "manual_review" bypasses the gate the user has not answered yet.
    """
    for i, status in enumerate(["processing", "success", "failed",
                                "manual_review", "cancelled", "dry_run"], 1):
        _file(db, file_id=i)
        _item(db, item_id=i, file_id=i, status=status)

    assert claim._claim_next() is None, (
        "the worker claimed a job that was not pending — it will re-run "
        "finished or in-flight work"
    )


def test_the_lowest_priority_number_is_claimed_first(claim, db):
    """
    Priority ascends: lower wins. This is what prioritize_item manipulates,
    and reversing it would make "move to top" mean "move to bottom".
    """
    for i, priority in enumerate([5, 1, 9], 1):
        _file(db, file_id=i)
        _item(db, item_id=i, file_id=i, status="pending", priority=priority)

    assert claim._claim_next() == 2


def test_ties_are_broken_by_the_oldest_item(claim, db):
    """FIFO within a priority band, so equal-priority work cannot starve."""
    from datetime import datetime, timedelta

    base = datetime(2026, 1, 1, 12, 0, 0)
    for i, offset in enumerate([2, 0, 1], 1):
        _file(db, file_id=i)
        _item(db, item_id=i, file_id=i, status="pending", priority=5,
              created_at=base + timedelta(minutes=offset))

    assert claim._claim_next() == 2


def test_priority_outranks_age(claim, db):
    """A prioritised item jumps the queue even if it is the newest."""
    from datetime import datetime, timedelta

    base = datetime(2026, 1, 1, 12, 0, 0)
    _file(db, file_id=1)
    _item(db, item_id=1, file_id=1, status="pending", priority=5,
          created_at=base)
    _file(db, file_id=2)
    _item(db, item_id=2, file_id=2, status="pending", priority=4,
          created_at=base + timedelta(hours=1))

    assert claim._claim_next() == 2


def test_claiming_marks_the_item_processing_and_stamps_started_at(claim, db):
    """
    The claim has to be recorded, or the next tick claims the same row again
    and two workers process one file concurrently.
    """
    from app.database.models import QueueItem

    _file(db)
    _item(db, status="pending")

    claim._claim_next()

    db.expire_all()
    row = db.get(QueueItem, 1)
    assert row.status == "processing"
    assert row.started_at is not None


def test_a_claimed_item_is_not_claimed_twice(claim, db):
    _file(db)
    _item(db, status="pending")

    assert claim._claim_next() == 1
    assert claim._claim_next() is None, "the same job was claimed twice"


# ── approve_manual_review: the three outcomes ────────────────────────────────
#
# Merged from the Phase 4 audit's tests/test_approve_manual_review.py. Its
# guard and provenance tests duplicated the ones above (it was written against
# the pre-fix tree and could not see them), so only the genuinely new material
# is taken: what the item actually BECOMES once the fresh decision runs.
#
# The tests above pin that the decision is re-run and applied; these pin the
# outcomes it produces. Note the useful gotcha the audit recorded: building a
# genuinely "no changes needed" file needs an MP4 whose path is NOT on disk,
# because is_faststart_mp4 returns None ("undeterminable") for an unreadable
# file, which unlike False raises no add_faststart action.

def _reviewable(db, *, path="/m/Show.mkv", container="mkv", tracks):
    """A manual_review item with an explicit track list."""
    from app.database.models import MediaFile, QueueItem, Track

    mf = MediaFile(id=1, path=path, filename=path.rsplit("/", 1)[-1],
                   directory="/m", size=1, mtime=1.0, container=container,
                   video_codec="h264")
    db.add(mf)
    db.commit()
    for si, tt, codec, lang in tracks:
        db.add(Track(file_id=1, stream_index=si, track_type=tt, codec=codec,
                     language=lang, channels=2, is_default=False,
                     is_forced=False, is_hearing_impaired=False, is_dub=False))
    db.commit()
    qi = QueueItem(id=1, file_id=1, status="manual_review", is_dry_run=False,
                   reason="needs review", review_subtitles=None)
    db.add(qi)
    db.commit()
    return mf, qi


_UND_PAIR_PLUS_FRENCH = [
    (0, "video", "h264", None),
    (1, "audio", "eac3", "und"),
    (2, "audio", "eac3", "und"),
    (3, "audio", "eac3", "fre"),   # gives the engine something to drop
]


def test_an_approved_item_needing_work_moves_to_pending_with_fresh_actions(db):
    from app.api.routes.queue import approve_manual_review
    from app.database.models import PlannedAction

    media, item = _reviewable(db, tracks=_UND_PAIR_PLUS_FRENCH)

    approve_manual_review(1, db)

    db.expire_all()
    assert item.status == "pending"
    assert media.status == "queued"
    assert db.query(PlannedAction).filter_by(queue_item_id=1).count() > 0, (
        "moved to pending with no planned actions to show the user"
    )


def test_stale_planned_actions_are_replaced_not_appended(db):
    """
    The re-run regenerates the action list. Leaving the old rows in place shows
    a Planned Actions panel describing two different decisions at once.
    """
    from app.api.routes.queue import approve_manual_review
    from app.database.models import PlannedAction

    _reviewable(db, tracks=_UND_PAIR_PLUS_FRENCH)
    db.add(PlannedAction(queue_item_id=1, order=0, action_type="stale",
                         description="from the previous decision"))
    db.commit()

    approve_manual_review(1, db)

    db.expire_all()
    kinds = {a.action_type
             for a in db.query(PlannedAction).filter_by(queue_item_id=1).all()}
    assert "stale" not in kinds, "the previous decision's actions survived the re-run"


def test_an_approved_file_needing_no_changes_is_skipped_and_stamped(db):
    """
    completed_at matters: the Skipped tab orders by it DESC and SQLite sorts
    NULLs last, so an unstamped row sinks to the bottom and renders "—".
    """
    from app.api.routes.queue import approve_manual_review

    media, item = _reviewable(
        db, path="/m/Clean.mp4", container="mp4",
        tracks=[(0, "video", "h264", None), (1, "audio", "aac", "eng")],
    )

    approve_manual_review(1, db)

    db.expire_all()
    assert item.status == "skipped"
    assert media.status == "skipped"
    assert item.completed_at is not None


def test_an_item_tripping_a_second_gate_stays_in_review_with_a_fresh_reason(db):
    """
    Approving the audio-threshold gate must not push an item past an unrelated
    image-subtitle gate that still applies.

    The audit's version of this test hedged with an if/else covering both
    outcomes, which cannot fail meaningfully — status is always one of them.
    The behaviour is deterministic and asserted as such: the item stays in
    manual_review, and its reason is regenerated to describe the gate that is
    NOW blocking it rather than the one that was.
    """
    from app.api.routes.queue import approve_manual_review
    from app.database.models import QueueItem

    _reviewable(db, path="/m/Subs.mkv", tracks=[
        (0, "video", "h264", None),
        (1, "audio", "eac3", "eng"),
        (2, "subtitle", "dvd_subtitle", "eng"),
    ])
    db.query(QueueItem).filter(QueueItem.id == 1).update(
        {"review_subtitles": json.dumps([{"stream_index": 2}])})
    db.commit()

    approve_manual_review(1, db)

    db.expire_all()
    item = db.get(QueueItem, 1)
    assert item.status == "manual_review"
    assert "image-based subtitle" in item.reason, (
        f"reason not regenerated for the gate now blocking it: {item.reason!r}"
    )


def test_approving_returns_the_serialised_item_with_its_actions(db):
    """The response feeds the modal directly, so it must carry the new actions."""
    from app.api.routes.queue import approve_manual_review

    _reviewable(db, tracks=_UND_PAIR_PLUS_FRENCH)

    payload = approve_manual_review(1, db)

    assert payload["id"] == 1
    assert "planned_actions" in payload
    assert payload["planned_actions"], "returned no actions to render"


# ── resolve_subtitles: the extract answer ────────────────────────────────────
#
# "extract" joins keep and remove. The endpoint refuses it where it would be
# accepted and then not carried out: on a stream the extraction branch cannot
# take, and on a review raised by a failed extraction, where extracting again
# repeats the failure and sends the job straight back to review.
#
# Six mutants, each run against the whole 1520-test suite before these tests
# existed, and all six survived: rejecting extract outright, dropping the
# codec check, checking image codecs instead of extractable ones, accepting a
# stream with no stored track, dropping the encoding-review check, and
# applying that check to keep and remove as well. Each is killed below.

_EXTRACTABLE_REVIEW = [
    (0, "video", "h264", None),
    (1, "audio", "aac", "eng"),
    (2, "subtitle", "ass", "eng"),
]


def test_an_extract_answer_is_stored_and_queues_the_extraction(db):
    from app.api.routes.queue import SubtitleOverridesRequest, resolve_subtitles
    from app.database.models import PlannedAction

    media, item = _reviewable(db, tracks=_EXTRACTABLE_REVIEW)

    resolve_subtitles(1, SubtitleOverridesRequest(overrides={2: "extract"}), db)

    db.expire_all()
    assert json.loads(media.subtitle_overrides) == {"2": "extract"}
    assert item.status == "pending"
    planned = db.query(PlannedAction).filter(PlannedAction.queue_item_id == 1).all()
    assert ("extract_subtitle", 2) in [(a.action_type, a.stream_index) for a in planned]


@pytest.mark.parametrize("stream_index", [3, 4, 9],
                         ids=["pgs", "webvtt", "no-such-stream"])
def test_extract_is_refused_where_there_is_nothing_to_extract(db, stream_index):
    """
    PGS is a bitmap, webvtt is text the extraction branch does not handle,
    and stream 9 does not exist. The decision engine would ignore all three
    answers, so accepting one would report a choice that was never applied.
    """
    from app.api.routes.queue import SubtitleOverridesRequest, resolve_subtitles

    _reviewable(db, tracks=_EXTRACTABLE_REVIEW + [
        (3, "subtitle", "hdmv_pgs_subtitle", "eng"),
        (4, "subtitle", "webvtt", "eng"),
    ])

    with pytest.raises(HTTPException) as exc:
        resolve_subtitles(
            1, SubtitleOverridesRequest(overrides={stream_index: "extract"}), db,
        )
    assert exc.value.status_code == 400


def _encoding_review(db):
    """A review raised by a failed extraction, on a track that is otherwise extractable."""
    media, item = _reviewable(db, tracks=[
        (0, "video", "h264", None),
        (1, "audio", "aac", "eng"),
        (2, "subtitle", "subrip", "eng"),
    ])
    item.review_reason = "subtitle_encoding"
    db.commit()
    return media, item


def test_extract_is_refused_on_a_review_raised_by_a_failed_extraction(db):
    """
    subrip passes the codec check, so only the review's cause refuses this.
    The job would run the same extraction, fail the same way, and come
    straight back here.
    """
    from app.api.routes.queue import SubtitleOverridesRequest, resolve_subtitles

    _encoding_review(db)

    with pytest.raises(HTTPException) as exc:
        resolve_subtitles(1, SubtitleOverridesRequest(overrides={2: "extract"}), db)
    assert exc.value.status_code == 400


@pytest.mark.parametrize("choice", ["keep", "remove"])
def test_keep_and_remove_still_answer_a_failed_extraction(db, choice):
    """The only two ways out of that review, so refusing Extract must not reach them."""
    from app.api.routes.queue import SubtitleOverridesRequest, resolve_subtitles

    media, _ = _encoding_review(db)

    resolve_subtitles(1, SubtitleOverridesRequest(overrides={2: choice}), db)

    db.expire_all()
    assert json.loads(media.subtitle_overrides) == {"2": choice}


# ── Stats: the Review badge's backlog ────────────────────────────────────────
#
# The Review tab badge was review.length — manual-review QueueItems only —
# so it read zero while the Audio and Subtitle Language Review sections had
# rows waiting. A file can carry a language flag without ever entering
# manual review: one undefined audio track under a threshold of two, or an
# undefined subtitle that gets extracted. queue_stats now reports that
# backlog so the badge can include it.

def _audio_flag(db, flag_id=1, file_id=1, stream_index=1):
    from app.database.models import AudioLanguageFlag

    f = AudioLanguageFlag(id=flag_id, file_id=file_id,
                          stream_index=stream_index, detected_language="und")
    db.add(f)
    db.commit()
    return f


def _subtitle_flag(db, flag_id=1, file_id=1, stream_index=2):
    from app.database.models import SubtitleLanguageFlag

    f = SubtitleLanguageFlag(id=flag_id, file_id=file_id,
                             stream_index=stream_index, detected_language="und")
    db.add(f)
    db.commit()
    return f


def test_stats_reports_the_language_review_backlog(db):
    from app.api.routes.queue import queue_stats

    _file(db)
    _audio_flag(db)
    _subtitle_flag(db, flag_id=2)
    _subtitle_flag(db, flag_id=3, stream_index=3)

    stats = queue_stats(db=db)

    assert stats["language_review"] == {"audio": 1, "subtitle": 2}


def test_stats_counts_flags_not_the_files_they_sit_on(db):
    """Three flags on one file are three answers the user still owes."""
    from app.api.routes.queue import queue_stats

    _file(db)
    _subtitle_flag(db, flag_id=1, stream_index=2)
    _subtitle_flag(db, flag_id=2, stream_index=3)
    _subtitle_flag(db, flag_id=3, stream_index=4)

    assert queue_stats(db=db)["language_review"]["subtitle"] == 3


def test_stats_reports_an_empty_backlog_as_zero_not_a_missing_key(db):
    """
    The UI adds these to review.length. A missing key would read as NaN and
    blank the badge, which is the failure this endpoint exists to prevent.
    """
    from app.api.routes.queue import queue_stats

    stats = queue_stats(db=db)

    assert stats["language_review"] == {"audio": 0, "subtitle": 0}


def test_stats_keeps_queue_statuses_out_of_the_language_review_key(db):
    """
    Statuses stay top-level and the backlog stays nested, so a caller
    walking this dict as a status map cannot mistake a flag count for one.
    """
    from app.api.routes.queue import queue_stats

    _file(db)
    _item(db, status="manual_review")
    _audio_flag(db)

    stats = queue_stats(db=db)

    assert stats["manual_review"] == 1
    assert "audio" not in stats
    assert "subtitle" not in stats
    assert stats["language_review"]["audio"] == 1


# ── Acknowledged undefined-audio thresholds ──────────────────────────────────
#
# Approving a threshold review sets und_audio_threshold_acknowledged and
# nothing ever set it back. The file is exempt from the gate for good, and
# until these endpoints there was no way to see that, let alone undo it.
#
# It matters because the Approve button used to claim it would process the
# file, which was wrong whenever nothing else needed doing. So an unknown
# number of these were given on a false description.

def _ack_file(db, file_id, path, acknowledged=True, status="skipped"):
    from app.database.models import MediaFile

    m = MediaFile(id=file_id, path=path, filename=path.rsplit("/", 1)[-1],
                  directory="/media/tv", size=5000, mtime=1_700_000_000.0,
                  container="mkv", status=status,
                  und_audio_threshold_acknowledged=acknowledged)
    db.add(m)
    db.commit()
    return m


# The list endpoint goes through TestClient rather than being called
# directly, following test_subtitle_language_review: FastAPI applies Query()
# constraints while parsing a REQUEST, and a direct Python call bypasses
# them entirely — the bound on `limit` would be untested no matter what the
# signature said. That bound is the point of paginating this at all.

def _threadsafe_db():
    """
    The `db` fixture's plain sqlite:// engine cannot be shared with
    TestClient: the request runs on another thread, takes its own
    connection, and finds no tables. conftest.memory_engine exists for this.
    """
    from sqlalchemy.orm import sessionmaker

    from app.database.models import Base
    from tests.conftest import memory_engine

    engine = memory_engine()
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)()


def _ack_client(db):
    from fastapi import FastAPI
    from starlette.testclient import TestClient

    from app.api.routes import queue as queue_routes

    app = FastAPI()
    app.include_router(queue_routes.router)
    # Keyed on the object the route closed over, not on an import of get_db
    # from app.database.session — see the note in
    # test_subtitle_language_review, where the equivalent-looking version
    # silently ran against the real database.
    app.dependency_overrides[queue_routes.get_db] = lambda: db
    return TestClient(app)


def test_acknowledged_lists_only_exempted_files():
    db = _threadsafe_db()
    _ack_file(db, 1, "/media/tv/A.mkv", acknowledged=True)
    _ack_file(db, 2, "/media/tv/B.mkv", acknowledged=False)

    out = _ack_client(db).get("/api/queue/acknowledged").json()

    assert out["total"] == 1
    assert [f["path"] for f in out["files"]] == ["/media/tv/A.mkv"]


def test_acknowledged_reports_the_full_total_not_the_page():
    """The count drives a UI control, so it must describe the backlog."""
    db = _threadsafe_db()
    for i in range(1, 6):
        _ack_file(db, i, f"/media/tv/{i}.mkv")

    out = _ack_client(db).get("/api/queue/acknowledged?limit=2").json()

    assert out["total"] == 5
    assert len(out["files"]) == 2


def test_acknowledged_rejects_an_unbounded_limit():
    """
    Unbounded, limit=-1 reaches SQLAlchemy's .limit(), which reads a
    negative as no limit at all — one request returning every exempted file
    in the library. The same hole the language lists already had.
    """
    db = _threadsafe_db()
    _ack_file(db, 1, "/media/tv/A.mkv")

    assert _ack_client(db).get("/api/queue/acknowledged?limit=-1").status_code == 422
    assert _ack_client(db).get("/api/queue/acknowledged?limit=99999").status_code == 422


def test_clearing_lets_the_file_face_the_threshold_again(db):
    from app.api.routes.queue import ClearAcknowledgedRequest, clear_acknowledged
    from app.database.models import MediaFile

    _ack_file(db, 1, "/media/tv/A.mkv")

    out = clear_acknowledged(ClearAcknowledgedRequest(file_ids=[1]), db=db)

    assert out["cleared"] == 1
    assert db.get(MediaFile, 1).und_audio_threshold_acknowledged is False


def test_clearing_also_invalidates_the_scan_stamp(db):
    """
    The half that is easy to forget. Clearing the column alone changes
    nothing a user can see: the file's bytes are untouched, so the delta
    scan finds size and mtime identical, returns early, and analyze_file is
    never called. The acknowledgement would be gone and the file would
    still never come back.
    """
    from app.api.routes.queue import ClearAcknowledgedRequest, clear_acknowledged
    from app.database.models import MediaFile

    _ack_file(db, 1, "/media/tv/A.mkv")

    clear_acknowledged(ClearAcknowledgedRequest(file_ids=[1]), db=db)

    media = db.get(MediaFile, 1)
    assert media.size == -1
    assert media.mtime == -1.0
    # The same check scanner.py makes, against the real on-disk values.
    assert not (media.size == 5000 and abs(media.mtime - 1_700_000_000.0) < 1.0)


def test_clearing_touches_only_the_files_named(db):
    """
    A mutant that ignores file_ids and clears the table passes any test that
    only checks the named file, so the untouched one is asserted too.
    """
    from app.api.routes.queue import ClearAcknowledgedRequest, clear_acknowledged
    from app.database.models import MediaFile

    _ack_file(db, 1, "/media/tv/A.mkv")
    _ack_file(db, 2, "/media/tv/B.mkv")

    clear_acknowledged(ClearAcknowledgedRequest(file_ids=[1]), db=db)

    other = db.get(MediaFile, 2)
    assert other.und_audio_threshold_acknowledged is True
    assert other.size == 5000
    assert other.mtime == 1_700_000_000.0


def test_clearing_an_unknown_id_does_not_fail_the_rest(db):
    """
    Driven by a multi-select whose list may have moved under the user.
    Failing the whole call because one row went stale is worse than saying
    which one did.
    """
    from app.api.routes.queue import ClearAcknowledgedRequest, clear_acknowledged
    from app.database.models import MediaFile

    _ack_file(db, 1, "/media/tv/A.mkv")

    out = clear_acknowledged(ClearAcknowledgedRequest(file_ids=[1, 999]), db=db)

    assert out["cleared"] == 1
    assert out["missing"] == [999]
    assert db.get(MediaFile, 1).und_audio_threshold_acknowledged is False


def test_clearing_a_file_that_was_never_acknowledged_counts_nothing(db):
    """"cleared" describes work done, not ids received."""
    from app.api.routes.queue import ClearAcknowledgedRequest, clear_acknowledged

    _ack_file(db, 1, "/media/tv/A.mkv", acknowledged=False)

    out = clear_acknowledged(ClearAcknowledgedRequest(file_ids=[1]), db=db)

    assert out["cleared"] == 0
    assert out["missing"] == []


def test_acknowledged_is_not_swallowed_by_the_item_id_route():
    """
    /{item_id} is declared in the same router and FastAPI matches in
    declaration order, so with /acknowledged below it the path resolves to
    the item lookup, which tries to read "acknowledged" as an int and
    returns 422. The OpenAPI schema lists the route either way — it is
    registered, just unreachable — so nothing but a real request catches it.
    """
    db = _threadsafe_db()
    _ack_file(db, 1, "/media/tv/A.mkv")

    r = _ack_client(db).get("/api/queue/acknowledged")

    assert r.status_code == 200
    assert "total" in r.json()
