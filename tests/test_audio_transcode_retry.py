"""
worker.py — the corrupt-audio transcode retry.

Some source files carry audio that cannot be stream-copied: either the
packet data itself is corrupt, or the packets have no usable timestamp at
all (common in older AVI files). Both fail the same way, at the muxer,
and both are fixed the same way — re-run with the audio transcoded to AAC
instead of copied, because the decoder then generates timestamps of its
own rather than trusting absent source ones.

Four pieces make that work and none of them was covered: two narrowly
scoped detectors, the predicate that ORs them, the rebuild that turns a
copy decision into a transcode decision, and the retry itself — which
exists twice, once in each execution branch, because the two branches
call different functions with different return shapes. Both call sites
are driven here. A retry that silently re-ran the original decision would
fail identically the second time and look exactly like a file that simply
cannot be processed.

The detectors are deliberately separate functions rather than one with
five conditions, and the tests keep them separate too: each must reject
the other's error. Collapsing them is a plausible tidy-up that would go
unnoticed, since the remediation is the same either way — until one of
the two conditions is loosened and the wrong failures start being
retried.

The narrowness is the point. Every condition exists to stop a retry that
cannot help: without the copy check a failed transcode is retried as
another transcode, and without the audio-stream check a video failure is
retried with the audio re-encoded. The retry is safe only because
"/copy" cannot appear in the retry's own error, which is what stops it
looping — so a detector that stops requiring "/copy" removes the loop
guard as well as the accuracy.

Ordering matters at the combined call site: the subtitle encoding check
runs first, and a subtitle cascade that also emits an aost#/copy line is
caught there and never reaches here. The two classifiers key on
disjoint substrings, and the errors used below are audio-only for that
reason.

Confirmed unprotected before this file was written: twelve mutations
applied across the four pieces, each run against the whole 1298-test
suite, all twelve survived. Dropping the copy condition; dropping the
audio-stream condition; dropping the condition that distinguishes the
timestamp detector from its sibling; retrying a failure with no error
text; retrying a successful run; requiring both root causes at once
rather than either; re-encoding every copied track including video;
rewriting every audio action including ones already transcoding;
targeting AC3 instead of AAC; adding a channel-count override; and
reusing the original decision for the retry at each of the two call
sites. All twelve are killed by the tests below.

A thirteenth was added afterwards and is recorded as a kill only: the
empty-error guard exists identically in both detectors, and the mutation
above touched one copy. Mutating the other confirms both are covered, but
it was never run against the unmutated suite, so it is evidence that the
duplicate is pinned now — not that it was exposed before.
"""
import asyncio
from types import SimpleNamespace

import pytest

import app.core.worker as worker
from app.core.decision import Action, ProcessingDecision


# FFmpeg's two shapes for audio that cannot be copied. Both carry the
# aost#/copy markers and the muxer rejection; they differ only in the
# final clause, which is what separates the two detectors.
CORRUPT_AUDIO = (
    "[out#0/matroska @ 0x561f2c] [aost#0:1/copy] Error submitting a packet "
    "to the muxer: Invalid data found when processing input"
)
UNKNOWN_TIMESTAMP = (
    "[out#0/avi @ 0x561f2c] [aost#0:1/copy] Error submitting a packet "
    "to the muxer: Can't write packet with unknown timestamp"
)
# Same failure shape but on a video stream, and the same failure shape on
# audio that was already being transcoded — neither is retryable.
VIDEO_COPY_FAILURE = CORRUPT_AUDIO.replace("aost#0:1", "vost#0:0")
AUDIO_TRANSCODE_FAILURE = CORRUPT_AUDIO.replace("/copy", "/aac")

DISK_FAILURE = "av_interleaved_write_frame(): No space left on device"


# ── The two detectors ────────────────────────────────────────────────────────

def test_corrupt_audio_is_recognised():
    assert worker._is_corrupt_audio_copy_failure(CORRUPT_AUDIO) is True


def test_unusable_timestamps_are_recognised():
    assert worker._is_unknown_timestamp_audio_failure(UNKNOWN_TIMESTAMP) is True


def test_neither_detector_answers_for_the_other_cause():
    """
    They are separate functions on purpose: same remediation, genuinely
    different root cause. Each rejecting the other's error is what keeps
    them from collapsing into one loose check.
    """
    assert worker._is_corrupt_audio_copy_failure(UNKNOWN_TIMESTAMP) is False
    assert worker._is_unknown_timestamp_audio_failure(CORRUPT_AUDIO) is False


@pytest.mark.parametrize("detector", [
    worker._is_corrupt_audio_copy_failure,
    worker._is_unknown_timestamp_audio_failure,
])
@pytest.mark.parametrize("error", [None, ""])
def test_no_error_text_is_not_a_retryable_failure(detector, error):
    """A failure that said nothing cannot be diagnosed as this one."""
    assert detector(error) is False


def test_a_video_stream_failure_is_not_retryable():
    """
    The retry re-encodes audio. A video failure with the identical muxer
    message would be retried into exactly the same failure.
    """
    assert worker._is_corrupt_audio_copy_failure(VIDEO_COPY_FAILURE) is False


def test_a_failure_while_already_transcoding_is_not_retryable():
    """
    This is also the loop guard. The retry runs with -c:a aac, so its own
    errors can never contain "/copy" — which is precisely why requiring
    "/copy" means a retry cannot retry itself.
    """
    assert worker._is_corrupt_audio_copy_failure(AUDIO_TRANSCODE_FAILURE) is False


def test_an_unrelated_failure_is_not_retryable():
    assert worker._is_corrupt_audio_copy_failure(DISK_FAILURE) is False
    assert worker._is_unknown_timestamp_audio_failure(DISK_FAILURE) is False


# ── The shared predicate ─────────────────────────────────────────────────────

def outcome(success, error):
    return SimpleNamespace(success=success, error=error)


@pytest.mark.parametrize("error", [CORRUPT_AUDIO, UNKNOWN_TIMESTAMP])
def test_either_root_cause_alone_triggers_the_retry(error):
    """
    Either, not both. The two causes never co-occur — each error carries
    one final clause — so requiring both would mean never retrying at
    all.
    """
    assert worker._needs_audio_transcode_retry(outcome(False, error)) is True


def test_a_successful_run_is_never_retried():
    """
    The error text of a successful run is not necessarily empty, and a
    retry here would re-run a remux that already landed.
    """
    assert worker._needs_audio_transcode_retry(outcome(True, CORRUPT_AUDIO)) is False


def test_an_unrelated_failure_is_not_retried():
    assert worker._needs_audio_transcode_retry(outcome(False, DISK_FAILURE)) is False


# ── Rebuilding the decision ──────────────────────────────────────────────────

def copy_audio(stream_index=1):
    return Action(action_type="copy_track", description="keep english audio",
                  track_type="audio", stream_index=stream_index)


def copy_video():
    return Action(action_type="copy_track", description="keep video",
                  track_type="video", stream_index=0)


def decision_with(*action_list):
    return ProcessingDecision(should_process=True, reason="remux",
                              actions=list(action_list), target_container="mkv")


def test_a_copied_audio_track_becomes_an_aac_transcode():
    rebuilt = worker._make_audio_transcode_decision(decision_with(copy_audio()))

    action = rebuilt.actions[0]
    assert action.action_type == "transcode_track"
    assert action.output_codec == "aac"
    assert action.stream_index == 1
    assert "AAC" in action.description


def test_the_source_channel_layout_is_left_alone():
    """
    Empty options mean build_ffmpeg_command emits no -ac override, so
    stereo stays stereo and 5.1 stays 5.1. Forcing a channel count here
    would silently downmix or upmix every retried file — and the AC3
    path that does force 5.1 sets its own options and is unaffected.
    """
    rebuilt = worker._make_audio_transcode_decision(decision_with(copy_audio()))

    assert rebuilt.actions[0].output_codec_options == {}


def test_video_is_still_copied():
    """The retry re-encodes audio and nothing else."""
    rebuilt = worker._make_audio_transcode_decision(
        decision_with(copy_video(), copy_audio())
    )

    assert rebuilt.actions[0] == copy_video()


def test_audio_that_was_already_transcoding_is_left_untouched():
    """
    An AC3 forge action arrives here as an audio transcode with its own
    codec and options. Rewriting it to AAC would discard both.
    """
    forged = Action(action_type="transcode_track", description="forge AC3",
                    track_type="audio", stream_index=2,
                    output_codec="ac3",
                    output_codec_options={"b:a": "640k", "ac": "6"})

    rebuilt = worker._make_audio_transcode_decision(decision_with(forged))

    assert rebuilt.actions[0] == forged


def test_the_original_decision_is_not_modified():
    original = decision_with(copy_audio())

    worker._make_audio_transcode_decision(original)

    assert original.actions[0].action_type == "copy_track"


# ── The retry at both call sites ─────────────────────────────────────────────

class FakeWS:
    def __init__(self):
        self.sent = []

    async def broadcast_json(self, payload):
        self.sent.append(payload)


@pytest.fixture
def rig(monkeypatch, tmp_path):
    """
    _run_job with both executors faked and a queue of results.

    Which branch runs is decided by the decision's contents:
    extract actions alongside other work takes the combined path,
    other work alone takes the two-pass path. Both are exercised, because
    the retry is written out twice — the shared predicate is hoisted but
    the call, the log wording and the rebuild are not.
    """
    media = tmp_path / "Show.mkv"
    media.write_bytes(b"media")

    rig = SimpleNamespace(
        ws=FakeWS(),
        path=str(media),
        actions=[],
        results=[],
        ffmpeg_calls=[],
        combined_calls=[],
        finish_calls=[],
    )

    def fake_load(job_id):
        return (
            {"id": job_id, "is_dry_run": False},
            {"id": 1, "path": rig.path, "filename": "Show.mkv", "size": 0},
            [],
            {},
            decision_with(*rig.actions),
        )

    async def fake_ffmpeg(**kwargs):
        rig.ffmpeg_calls.append(kwargs)
        return rig.results.pop(0)

    async def fake_combined(**kwargs):
        rig.combined_calls.append(kwargs)
        return rig.results.pop(0), []

    monkeypatch.setattr(worker, "_load_job_data", fake_load)
    monkeypatch.setattr(worker, "determine_output_path",
                        lambda path, decision: rig.path)
    monkeypatch.setattr(worker, "execute_ffmpeg", fake_ffmpeg)
    monkeypatch.setattr(worker, "execute_ffmpeg_combined", fake_combined)
    monkeypatch.setattr(worker, "execute_subtitle_extraction", None)
    monkeypatch.setattr(worker, "_finish_job",
                        lambda *args: rig.finish_calls.append(args))
    return rig


def ffmpeg_result(rig, success=True, error=None):
    return SimpleNamespace(success=success, error=error,
                           output_path=rig.path, output_size=99)


def run(rig, job_id=1):
    async def driver():
        loop = asyncio.get_running_loop()
        await worker._run_job(job_id, rig.ws, loop)

    asyncio.run(driver())


def audio_actions(call):
    return [a for a in call["decision"].actions if a.track_type == "audio"]


def test_the_two_pass_retry_re_runs_with_the_audio_transcoded(rig):
    rig.actions = [copy_video(), copy_audio()]
    rig.results = [ffmpeg_result(rig, False, CORRUPT_AUDIO),
                   ffmpeg_result(rig, True)]

    run(rig)

    assert len(rig.ffmpeg_calls) == 2
    first, retry = rig.ffmpeg_calls
    assert audio_actions(first)[0].action_type == "copy_track"
    assert audio_actions(retry)[0].action_type == "transcode_track"
    assert audio_actions(retry)[0].output_codec == "aac"


def test_the_combined_retry_re_runs_with_the_audio_transcoded(rig):
    """
    The second call site. It calls a different function with a different
    return shape, which is exactly why the retry is written out twice and
    why covering only one of them would leave the other free to drift.
    """
    rig.actions = [
        copy_audio(),
        Action(action_type="extract_subtitle", description="extract eng",
               track_type="subtitle", stream_index=3,
               external_path="/media/Show.eng.srt"),
    ]
    rig.results = [ffmpeg_result(rig, False, UNKNOWN_TIMESTAMP),
                   ffmpeg_result(rig, True)]

    run(rig)

    assert len(rig.combined_calls) == 2
    first, retry = rig.combined_calls
    assert audio_actions(first)[0].action_type == "copy_track"
    assert audio_actions(retry)[0].action_type == "transcode_track"


def test_the_retry_decides_the_outcome_the_job_records(rig):
    """
    The job is settled from the retry's result, not the first attempt's —
    otherwise a successful retry would still be recorded as a failure.
    """
    rig.actions = [copy_audio()]
    rig.results = [ffmpeg_result(rig, False, CORRUPT_AUDIO),
                   ffmpeg_result(rig, True)]

    run(rig)

    assert rig.finish_calls[0][1] is True


def test_a_retry_that_also_fails_reports_its_own_error(rig):
    rig.actions = [copy_audio()]
    rig.results = [ffmpeg_result(rig, False, CORRUPT_AUDIO),
                   ffmpeg_result(rig, False, DISK_FAILURE)]

    run(rig)

    assert rig.finish_calls[0][1] is False
    assert rig.finish_calls[0][4] == DISK_FAILURE


def test_an_unrelated_failure_runs_ffmpeg_only_once(rig):
    rig.actions = [copy_audio()]
    rig.results = [ffmpeg_result(rig, False, DISK_FAILURE)]

    run(rig)

    assert len(rig.ffmpeg_calls) == 1
    assert rig.finish_calls[0][4] == DISK_FAILURE
