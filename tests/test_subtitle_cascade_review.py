"""
worker.py — the combined-pass subtitle encoding cascade.

The combined pass muxes the file and extracts its subtitles in one FFmpeg
invocation. When a text subtitle cannot be decoded, that single failure
kills every output in the command, including the video and audio that
were fine. Falling back to a remux-only run would drop the subtitle
without saying so, which is why the job is routed to manual review
instead — the user chooses to keep the track embedded or drop it.

This is the twin of the two-pass routing covered in
test_subtitle_extraction_routing.py, and the pair is deliberately
asymmetric. A combined command's cascade cannot be attributed to a
particular stream, so this path flags every extraction stream; the
two-pass path runs each stream as its own command and flags only the ones
that actually failed. The asymmetry is the sort that looks like an
inconsistency to a later reader, so it is pinned here rather than left to
the comment.

Ordering matters and is not obvious from either branch alone. This check
sits above the corrupt-audio retry, and a subtitle cascade frequently
also emits an aost#/copy muxer line — the audio detector's docstring says
so explicitly. The same error therefore satisfies both classifiers, and
which one wins is decided purely by position in the function. Retrying it
as an audio problem would re-run the whole command, fail identically, and
still never tell the user about the subtitle.

Also covered here: _flag_subtitle_encoding_review's commit failure path.
The flagger is the last step of both routing branches, and it is what
moves the job out of "processing". A commit that fails silently leaves
the job wedged there with the caller believing review was raised.

Confirmed unprotected before this file was written: seven mutations, each
run against the whole 1322-test suite, all seven survived. Flagging a
successful run; disabling the cascade gate so the error falls through to
the audio retry; flagging and then continuing down the branch; raising
review with no streams attached; building the stream/path pairs the wrong
way round; swallowing a failed commit; and leaving the failed
transaction un-rolled-back. All seven are killed by the tests below.

An eighth — dropping the classifier so every combined failure is routed
to review — was already killed at baseline, by
test_audio_transcode_retry.py's combined-path test, since the audio
failure it drives would be diverted into review. It is recorded here as
already covered rather than counted as this file's work.
"""
import asyncio
from types import SimpleNamespace

import pytest

import app.core.worker as worker
from app.core.decision import Action, ProcessingDecision
from app.database.models import MediaFile, QueueItem


# FFmpeg's combined-command form for a subtitle that cannot be decoded.
# The sist# marker is what identifies it as a subtitle input stream.
CASCADE = (
    "Error while decoding sist#0:2 -> #1:0/srt: Invalid data found when "
    "processing input"
)
# The same cascade as it usually appears in the wild: the subtitle failure
# takes the muxer down with it, so an audio copy line is emitted too. This
# string satisfies BOTH classifiers, which is the case the ordering exists
# to resolve.
CASCADE_WITH_AUDIO_NOISE = (
    "[aost#0:1/copy] Error submitting a packet to the muxer: Invalid data "
    "found when processing input\n"
    + CASCADE
)
DISK_FAILURE = "av_interleaved_write_frame(): No space left on device"


# ── Harness ──────────────────────────────────────────────────────────────────

class FakeWS:
    def __init__(self):
        self.sent = []

    async def broadcast_json(self, payload):
        self.sent.append(payload)


SUBTITLE_TRACKS = [
    {"stream_index": 2, "track_type": "subtitle", "language": "eng",
     "codec": "mov_text"},
    {"stream_index": 3, "track_type": "subtitle", "language": "fre",
     "codec": "mov_text"},
]


@pytest.fixture
def rig(monkeypatch, tmp_path):
    """
    _run_job forced down the combined branch: extract actions alongside
    other work is what selects it.
    """
    media = tmp_path / "Show.mkv"
    media.write_bytes(b"media")

    rig = SimpleNamespace(
        ws=FakeWS(),
        path=str(media),
        actions=[],
        results=[],
        combined_calls=[],
        flag_calls=[],
        finish_calls=[],
    )

    def fake_load(job_id):
        decision = ProcessingDecision(
            should_process=True, reason="remux and extract",
            actions=list(rig.actions), target_container="mkv",
        )
        return (
            {"id": job_id, "is_dry_run": False},
            {"id": 1, "path": rig.path, "filename": "Show.mkv", "size": 0},
            SUBTITLE_TRACKS,
            {},
            decision,
        )

    async def fake_combined(**kwargs):
        rig.combined_calls.append(kwargs)
        return rig.results.pop(0), []

    monkeypatch.setattr(worker, "_load_job_data", fake_load)
    monkeypatch.setattr(worker, "determine_output_path",
                        lambda path, decision: rig.path)
    monkeypatch.setattr(worker, "execute_ffmpeg_combined", fake_combined)
    monkeypatch.setattr(worker, "_flag_subtitle_encoding_review",
                        lambda *args: rig.flag_calls.append(args))
    monkeypatch.setattr(worker, "_finish_job",
                        lambda *args: rig.finish_calls.append(args))
    return rig


def copy_audio():
    return Action(action_type="copy_track", description="keep audio",
                  track_type="audio", stream_index=1)


def extraction(stream_index, path):
    return Action(action_type="extract_subtitle",
                  description=f"extract stream {stream_index}",
                  track_type="subtitle", stream_index=stream_index,
                  external_path=path)


def outcome(rig, success, error):
    return SimpleNamespace(success=success, error=error,
                           output_path=rig.path, output_size=99)


def run(rig, job_id=1):
    async def driver():
        loop = asyncio.get_running_loop()
        await worker._run_job(job_id, rig.ws, loop)

    asyncio.run(driver())


def combined_job(rig, *extractions):
    rig.actions = [copy_audio(), *extractions]


# ── Routing a cascade to review ──────────────────────────────────────────────

def test_a_subtitle_cascade_sends_the_job_to_review(rig):
    combined_job(rig, extraction(2, "/media/Show.eng.srt"))
    rig.results = [outcome(rig, False, CASCADE)]

    run(rig)

    assert len(rig.flag_calls) == 1
    job_id, pairs, tracks = rig.flag_calls[0]
    assert job_id == 1
    assert pairs == [(2, "/media/Show.eng.srt")]
    assert tracks == SUBTITLE_TRACKS


def test_every_extraction_stream_is_flagged_not_only_the_guilty_one(rig):
    """
    The deliberate asymmetry with the two-pass path. One command produced
    one error, and nothing in it says which subtitle caused the cascade,
    so every stream that was being extracted is offered for review.
    Guessing one would leave the actual offender embedded and unreviewed.
    """
    combined_job(rig,
                 extraction(2, "/media/Show.eng.srt"),
                 extraction(3, "/media/Show.fre.srt"))
    rig.results = [outcome(rig, False, CASCADE)]

    run(rig)

    assert rig.flag_calls[0][1] == [
        (2, "/media/Show.eng.srt"),
        (3, "/media/Show.fre.srt"),
    ]


def test_a_flagged_job_does_not_continue_down_the_branch(rig):
    """
    Everything below this point assumes a result worth acting on. Falling
    through would run the audio-retry check against the cascade and then
    settle the job as failed, on top of the review just raised.
    """
    combined_job(rig, extraction(2, "/media/Show.eng.srt"))
    rig.results = [outcome(rig, False, CASCADE)]

    run(rig)

    assert len(rig.combined_calls) == 1
    assert rig.finish_calls == []


def test_a_cascade_that_also_reads_as_corrupt_audio_still_goes_to_review(rig):
    """
    The ordering case. A subtitle decode failure usually takes the muxer
    down with it, so the same error satisfies the audio detector too —
    its own docstring says a cascade "is already caught there and never
    reaches here". Which classifier wins is decided by position alone,
    and nothing else pins it.
    """
    assert worker._is_corrupt_audio_copy_failure(CASCADE_WITH_AUDIO_NOISE) is True

    combined_job(rig, extraction(2, "/media/Show.eng.srt"))
    rig.results = [outcome(rig, False, CASCADE_WITH_AUDIO_NOISE)]

    run(rig)

    assert len(rig.flag_calls) == 1
    assert len(rig.combined_calls) == 1      # not retried as an audio problem


# ── What must not be routed to review ────────────────────────────────────────

def test_a_successful_run_is_not_sent_to_review(rig):
    """
    FFmpeg writes plenty to stderr on a run that succeeded. Matching the
    text without checking the outcome would send finished jobs to review.
    """
    combined_job(rig, extraction(2, "/media/Show.eng.srt"))
    rig.results = [outcome(rig, True, CASCADE)]

    run(rig)

    assert rig.flag_calls == []
    assert rig.finish_calls[0][1] is True


def test_an_unrelated_failure_is_not_sent_to_review(rig):
    combined_job(rig, extraction(2, "/media/Show.eng.srt"))
    rig.results = [outcome(rig, False, DISK_FAILURE)]

    run(rig)

    assert rig.flag_calls == []
    assert rig.finish_calls[0][1] is False


# ── Committing the review ────────────────────────────────────────────────────

@pytest.fixture
def db(monkeypatch):
    """A real session for _flag_subtitle_encoding_review to write through."""
    from sqlalchemy.orm import sessionmaker

    from tests.conftest import memory_engine

    from app.database.models import Base

    engine = memory_engine()
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    monkeypatch.setattr(worker, "SessionLocal", lambda: session)

    session.add(MediaFile(id=1, path="/media/Show.mkv", filename="Show.mkv",
                          directory="/media", size=1, mtime=1.0))
    session.add(QueueItem(id=1, file_id=1, status="processing"))
    session.commit()
    return session


def test_a_failed_commit_is_not_reported_as_a_raised_review(db, monkeypatch):
    """
    The caller returns straight after this and treats the job as handed
    to review. Swallowing the error leaves it stuck in "processing" with
    nothing to show for it — the state the stuck-job safety net exists to
    catch, reached silently.
    """
    def boom():
        raise RuntimeError("database is locked")

    monkeypatch.setattr(db, "commit", boom)

    with pytest.raises(RuntimeError):
        worker._flag_subtitle_encoding_review(1, [(2, "/a.srt")], SUBTITLE_TRACKS)


def test_a_failed_commit_rolls_the_transaction_back(db, monkeypatch):
    """
    The session is returned to a pool. Leaving a failed transaction open
    hands the next caller a connection that fails on its first statement,
    somewhere unrelated to here.
    """
    rolled_back = []
    real_rollback = db.rollback

    def boom():
        raise RuntimeError("database is locked")

    def record_rollback():
        rolled_back.append(True)
        real_rollback()

    monkeypatch.setattr(db, "commit", boom)
    monkeypatch.setattr(db, "rollback", record_rollback)

    with pytest.raises(RuntimeError):
        worker._flag_subtitle_encoding_review(1, [(2, "/a.srt")], SUBTITLE_TRACKS)

    assert rolled_back == [True]
