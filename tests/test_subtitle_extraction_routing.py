"""
worker.py — the two-pass subtitle extraction loop and its failure routing.

When a job has subtitle extractions and no other work, _run_job takes the
two-pass branch: extract each subtitle as its own command, then remux.
The loop and the routing that decides what a failed extraction means were
uncovered.

Two different failures land in the same place and must not be treated
alike. A text subtitle whose bytes are not valid UTF-8 is a decision for
a human — keep the track embedded or drop it — so the job goes to manual
review with the offending streams attached. Anything else (disk,
permissions, a missing stream) is a genuine failure and must fail the job
outright, or it strands a broken job in the subtitle-review queue under a
reason that does not describe what went wrong.

The routing has to stop the job as well as classify it. Extracted streams
are excluded from the muxed output, so remuxing after an extraction
failed removes those tracks from the file with no sidecar written to
replace them — the subtitle exists nowhere afterwards, and the job
records success. That is the silent loss this branch exists to prevent,
and it is what makes the return after flagging load-bearing rather than
tidy.

test_subtitle_extraction_failures.py already covers the two pieces this
sits between: the all-or-nothing staging guarantee, and
_is_subtitle_encoding_failure against both the standalone and combined
error shapes. Its docstring says of the routing itself that it "lives in
_run_job (async + DB, exercised in integration)" — which coverage does
not bear out; none of its 90 statements were reached. The classifier is
used for real here rather than faked, driven by the same verbatim FFmpeg
messages that file pins, so the seam between classifier and routing is
covered rather than assumed.

Confirmed unprotected before this file was written: ten mutations applied
to the loop and its routing, each run against the whole 1288-test suite,
all ten survived. Dropping the continue that collects failures across the
loop; treating every failure as an encoding failure; inverting the
classifier; removing the return after flagging, so the remux proceeds;
flagging review with no streams attached; numbering the progress labels
from zero; extracting the loop counter instead of the action's stream;
ignoring extraction failures entirely; dropping the stream number from
the hard-failure message; and storing the collected pair the wrong way
round. All ten are killed by the tests below.
"""
import asyncio
from types import SimpleNamespace

import pytest

import app.core.worker as worker
from app.core.decision import Action, ProcessingDecision


# FFmpeg's verbatim message for a text subtitle whose bytes are not valid
# UTF-8, in the standalone single-extraction form the two-pass path
# produces. Kept identical to the string test_subtitle_extraction_failures
# pins the classifier against — if the two drift, the classifier can keep
# passing there while the routing here stops firing.
ENCODING_ERROR = (
    "[srt @ 0x55d1c2a4b0c0] Invalid UTF-8 in decoded subtitles text; "
    "maybe missing -sub_charenc option\n"
    "Error while decoding stream #0:2: Invalid data found when "
    "processing input"
)

DISK_ERROR = "av_interleaved_write_frame(): No space left on device"


# ── Harness ──────────────────────────────────────────────────────────────────

class FakeWS:
    def __init__(self):
        self.sent = []

    async def broadcast_json(self, payload):
        self.sent.append(payload)


@pytest.fixture
def rig(monkeypatch, tmp_path):
    """
    _run_job driven down the two-pass branch.

    The branch is chosen by the decision's contents: extract actions and
    no other work means use_combined is False. File size is left at zero
    so the disk pre-flight skips itself, since it is covered separately
    and only adds fakes here.

    _is_subtitle_encoding_failure is deliberately NOT faked — it is the
    hinge the routing turns on, and using it for real is what makes these
    tests cover the seam rather than restate the branch.
    """
    media = tmp_path / "Show.mkv"
    media.write_bytes(b"media")

    rig = SimpleNamespace(
        ws=FakeWS(),
        path=str(media),
        actions=[],
        results={},          # stream_index -> (success, error)
        extraction_calls=[],
        ffmpeg_calls=[],
        flag_calls=[],
        finish_calls=[],
        progress_calls=[],
    )

    def fake_load(job_id):
        decision = ProcessingDecision(
            should_process=True, reason="extract subtitles",
            actions=list(rig.actions), target_container="mkv",
        )
        return (
            {"id": job_id, "is_dry_run": False},
            {"id": 1, "path": rig.path, "filename": "Show.mkv", "size": 0},
            [{"stream_index": 2, "track_type": "subtitle"}],
            {},
            decision,
        )

    async def fake_extraction(*, input_path, stream_index, output_srt_path, job_id):
        rig.extraction_calls.append((stream_index, output_srt_path))
        success, error = rig.results.get(stream_index, (True, None))
        return SimpleNamespace(success=success, error=error, output_path=output_srt_path)

    async def fake_ffmpeg(**kwargs):
        rig.ffmpeg_calls.append(kwargs)
        return SimpleNamespace(success=True, output_path=rig.path,
                               output_size=123, error=None)

    monkeypatch.setattr(worker, "_load_job_data", fake_load)
    monkeypatch.setattr(worker, "determine_output_path",
                        lambda path, decision: rig.path)
    monkeypatch.setattr(worker, "execute_subtitle_extraction", fake_extraction)
    monkeypatch.setattr(worker, "execute_ffmpeg", fake_ffmpeg)
    monkeypatch.setattr(worker, "_flag_subtitle_encoding_review",
                        lambda *args: rig.flag_calls.append(args))
    monkeypatch.setattr(worker, "_finish_job",
                        lambda *args: rig.finish_calls.append(args))
    monkeypatch.setattr(worker, "_update_progress",
                        lambda *args: rig.progress_calls.append(args))
    return rig


def extraction(stream_index, path):
    return Action(action_type="extract_subtitle",
                  description=f"extract stream {stream_index}",
                  track_type="subtitle", stream_index=stream_index,
                  external_path=path)


def run(rig, job_id=1):
    """
    asyncio.run shuts the default executor down on the way out, waiting
    for submitted work — so the fire-and-forget _update_progress writes
    inside the loop have all completed by the time this returns, and
    asserting on them is not a race.
    """
    async def driver():
        loop = asyncio.get_running_loop()
        await worker._run_job(job_id, rig.ws, loop)

    asyncio.run(driver())


def labels(rig):
    return [p["current_action"] for p in rig.ws.sent
            if p.get("event") == "job_progress"]


def failure_message(rig):
    assert len(rig.finish_calls) == 1, rig.finish_calls
    _, success, _, _, error = rig.finish_calls[0]
    assert success is False
    return error


# ── The loop itself ──────────────────────────────────────────────────────────

def test_each_subtitle_is_extracted_from_its_own_stream(rig):
    """
    Each action carries the stream to read and the path to write. Reading
    the loop counter instead would extract the wrong track into the right
    filename — an .srt whose contents belong to another subtitle, with
    nothing failing.
    """
    rig.actions = [extraction(2, "/media/Show.eng.srt"),
                   extraction(5, "/media/Show.fre.srt")]

    run(rig)

    assert rig.extraction_calls == [
        (2, "/media/Show.eng.srt"),
        (5, "/media/Show.fre.srt"),
    ]


def test_each_extraction_reports_its_place_in_the_run(rig):
    """Counting from zero would show "0/2" as the first of two."""
    rig.actions = [extraction(2, "/a.srt"), extraction(5, "/b.srt")]

    run(rig)

    assert labels(rig)[:2] == [
        "Extracting subtitle to SRT (1/2)",
        "Extracting subtitle to SRT (2/2)",
    ]
    assert [args[2] for args in rig.progress_calls] == labels(rig)[:2]


def test_a_clean_run_goes_on_to_the_remux(rig):
    rig.actions = [extraction(2, "/a.srt")]

    run(rig)

    assert len(rig.ffmpeg_calls) == 1
    assert rig.flag_calls == []
    assert rig.finish_calls[0][1] is True


# ── An encoding failure: collect, review, and stop ───────────────────────────

def test_an_encoding_failure_does_not_stop_the_other_extractions(rig):
    """
    Subtitles in one file usually share a charset, so the tracks after a
    bad one are very likely bad too. Stopping at the first would resolve
    one track per review visit instead of all of them in one.
    """
    rig.actions = [extraction(2, "/a.srt"), extraction(5, "/b.srt")]
    rig.results = {2: (False, ENCODING_ERROR)}

    run(rig)

    assert [call[0] for call in rig.extraction_calls] == [2, 5]


def test_the_streams_that_failed_are_collected_into_one_review(rig):
    """
    The pair is (stream, path) in that order and the review reads both:
    the stream to render a Keep/Remove choice against, the path to know
    what a Keep would have written.
    """
    rig.actions = [extraction(2, "/a.srt"), extraction(5, "/b.srt")]
    rig.results = {2: (False, ENCODING_ERROR), 5: (False, ENCODING_ERROR)}

    run(rig)

    assert len(rig.flag_calls) == 1
    job_id, pairs, tracks = rig.flag_calls[0]
    assert job_id == 1
    assert pairs == [(2, "/a.srt"), (5, "/b.srt")]
    assert tracks == [{"stream_index": 2, "track_type": "subtitle"}]


def test_a_flagged_job_never_reaches_the_remux(rig):
    """
    The load-bearing return. Extracted streams are dropped from the muxed
    output, so remuxing after flagging removes the subtitle from the file
    with no sidecar written to replace it — gone from both places, job
    recorded as done.
    """
    rig.actions = [extraction(2, "/a.srt")]
    rig.results = {2: (False, ENCODING_ERROR)}

    run(rig)

    assert rig.ffmpeg_calls == []
    assert rig.finish_calls == []      # left in review, not settled here


def test_a_partial_encoding_failure_still_blocks_the_remux(rig):
    """One bad track out of two is still a track that would vanish."""
    rig.actions = [extraction(2, "/a.srt"), extraction(5, "/b.srt")]
    rig.results = {5: (False, ENCODING_ERROR)}

    run(rig)

    assert rig.ffmpeg_calls == []
    assert rig.flag_calls[0][1] == [(5, "/b.srt")]


# ── Any other failure: fail the job ──────────────────────────────────────────

def test_a_non_encoding_failure_fails_the_job_immediately(rig):
    """
    A full disk is not a question for a human about subtitle tracks.
    Routing it to review would park a genuinely failed job in the wrong
    queue under a reason that does not describe it.
    """
    rig.actions = [extraction(2, "/a.srt"), extraction(5, "/b.srt")]
    rig.results = {2: (False, DISK_ERROR)}

    run(rig)

    assert rig.flag_calls == []
    assert rig.ffmpeg_calls == []
    assert "stream 2" in failure_message(rig)
    assert "No space left on device" in failure_message(rig)


def test_a_hard_failure_abandons_the_remaining_extractions(rig):
    """Immediately means immediately — the second stream is never tried."""
    rig.actions = [extraction(2, "/a.srt"), extraction(5, "/b.srt")]
    rig.results = {2: (False, DISK_ERROR)}

    run(rig)

    assert [call[0] for call in rig.extraction_calls] == [2]


def test_a_hard_failure_after_an_encoding_failure_still_fails_the_job(rig):
    """
    The two outcomes are not equal partners: collecting an encoding
    failure must not turn a later disk failure into a review. The job
    fails, and the streams collected so far go nowhere.
    """
    rig.actions = [extraction(2, "/a.srt"), extraction(5, "/b.srt")]
    rig.results = {2: (False, ENCODING_ERROR), 5: (False, DISK_ERROR)}

    run(rig)

    assert rig.flag_calls == []
    assert "stream 5" in failure_message(rig)
