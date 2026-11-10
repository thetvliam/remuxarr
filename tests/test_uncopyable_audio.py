"""
worker.py — audio that cannot be copied fails the job; it is not re-encoded.

Some source files carry audio that cannot be stream-copied: either the
packet data itself is corrupt, or the packets have no usable timestamp at
all (common in older AVI files). Both fail the same way, at the muxer.

Such a job used to be run again with every kept audio track re-encoded to
AAC. That was the one place the pipeline re-encoded anything, against what
the README promises ("Nothing is ever re-encoded - not video, not audio");
it re-encoded every kept audio track, not only the one FFmpeg had refused;
and the plan the job showed still said "copy". The retry has been removed.
The job now fails once, with a plain statement of why in front of FFmpeg's
own error (worker._explain_failure), and the file is left as it was, as
after any failed job.

What is covered: the two detectors, kept as separate functions because
the two causes are worded differently to the user; the explanation built
from them; that each of _run_job's two execution branches runs FFmpeg
once and records that explanation; that no command the pipeline builds
names an encoder; and, against real FFmpeg, that a file with corrupt AAC
fails with the error the detector reads and is not touched.

The detectors stay narrow for a new reason. They no longer guard a
retry; they decide whether a failure is explained as the audio's fault.
Without the copy and audio-stream conditions a video failure, or a disk
that filled up, would be reported as audio that cannot be copied.

Ordering matters at the combined call site: the subtitle encoding check
runs first, and a subtitle cascade that also emits an aost#/copy line is
caught there and never reaches here (test_subtitle_cascade_review.py).
The errors used below are audio-only for that reason.

Also here, because the rig is: what _run_job tells revert capture about
the streams a job removes (revert_capture._pins), and that each run is
handed the job's before_swap hook (see test_source_changed_mid_job.py).
"""
import asyncio
import hashlib
import json
import shutil
import subprocess
from types import SimpleNamespace

import pytest

import app.core.worker as worker
from app.core.decision import Action, ProcessingDecision, analyze_file
from app.core.ffmpeg import build_ffmpeg_command
from tests.conftest import make_file_info, make_track


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
# Same failure shape but on a video stream.
VIDEO_COPY_FAILURE = CORRUPT_AUDIO.replace("aost#0:1", "vost#0:0")
# And on audio FFmpeg was encoding, which the pipeline never asks for.
AUDIO_ENCODE_FAILURE = CORRUPT_AUDIO.replace("/copy", "/aac")

DISK_FAILURE = "av_interleaved_write_frame(): No space left on device"


# ── The two detectors ────────────────────────────────────────────────────────

def test_corrupt_audio_is_recognised():
    assert worker._is_corrupt_audio_copy_failure(CORRUPT_AUDIO) is True


def test_unusable_timestamps_are_recognised():
    assert worker._is_unknown_timestamp_audio_failure(UNKNOWN_TIMESTAMP) is True


def test_neither_detector_answers_for_the_other_cause():
    """
    They are separate functions on purpose: the same outcome, a genuinely
    different root cause, and the user is told which.
    """
    assert worker._is_corrupt_audio_copy_failure(UNKNOWN_TIMESTAMP) is False
    assert worker._is_unknown_timestamp_audio_failure(CORRUPT_AUDIO) is False


@pytest.mark.parametrize("detector", [
    worker._is_corrupt_audio_copy_failure,
    worker._is_unknown_timestamp_audio_failure,
])
@pytest.mark.parametrize("error", [None, ""])
def test_no_error_text_is_not_this_failure(detector, error):
    """A failure that said nothing cannot be diagnosed as this one."""
    assert detector(error) is False


def test_a_video_stream_failure_is_not_this_failure():
    assert worker._is_corrupt_audio_copy_failure(VIDEO_COPY_FAILURE) is False
    assert worker._is_unknown_timestamp_audio_failure(
        UNKNOWN_TIMESTAMP.replace("aost#0:1", "vost#0:0")) is False


def test_a_failure_while_encoding_is_not_this_failure():
    assert worker._is_corrupt_audio_copy_failure(AUDIO_ENCODE_FAILURE) is False
    assert worker._is_unknown_timestamp_audio_failure(
        UNKNOWN_TIMESTAMP.replace("/copy", "/aac")) is False


def test_an_unrelated_failure_is_not_this_failure():
    assert worker._is_corrupt_audio_copy_failure(DISK_FAILURE) is False
    assert worker._is_unknown_timestamp_audio_failure(DISK_FAILURE) is False


# ── The explanation ──────────────────────────────────────────────────────────

@pytest.mark.parametrize("error, cause", [
    (CORRUPT_AUDIO, "An audio track has corrupt frames."),
    (UNKNOWN_TIMESTAMP, "An audio track has packets with no timestamps."),
])
def test_audio_that_cannot_be_copied_is_explained(error, cause):
    """
    The plain statement first, then which of the two causes, then
    FFmpeg's own text, which names the stream.
    """
    assert worker._explain_failure(error) == (
        f"{worker.UNCOPYABLE_AUDIO} {cause}\n\nFFmpeg: {error}"
    )


def test_the_explanation_says_the_file_was_not_re_encoded_or_changed():
    assert "does not re-encode audio" in worker.UNCOPYABLE_AUDIO
    assert "left as it was" in worker.UNCOPYABLE_AUDIO


@pytest.mark.parametrize("error", [DISK_FAILURE, VIDEO_COPY_FAILURE,
                                   AUDIO_ENCODE_FAILURE, "", None])
def test_any_other_failure_keeps_its_own_error(error):
    assert worker._explain_failure(error) == error


# ── The job ──────────────────────────────────────────────────────────────────

def copy_audio(stream_index=1):
    return Action(action_type="copy_track", description="keep english audio",
                  track_type="audio", stream_index=stream_index)


def copy_video():
    return Action(action_type="copy_track", description="keep video",
                  track_type="video", stream_index=0)


def extract_subtitle(stream_index=3):
    return Action(action_type="extract_subtitle", description="extract eng",
                  track_type="subtitle", stream_index=stream_index,
                  external_path="/media/Show.eng.srt")


def decision_with(*action_list):
    return ProcessingDecision(should_process=True, reason="remux",
                              actions=list(action_list), target_container="mkv")


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
    other work alone takes the two-pass path. Both are exercised: they
    call different functions with different return shapes.
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



def _set_up(rig, path):
    """Point the rig at one execution branch; returns that branch's calls."""
    if path == "two-pass":
        rig.actions = [copy_video(), copy_audio()]
        return rig.ffmpeg_calls
    rig.actions = [copy_audio(), extract_subtitle()]
    return rig.combined_calls


@pytest.mark.parametrize("path", ["two-pass", "combined"])
@pytest.mark.parametrize("error", [CORRUPT_AUDIO, UNKNOWN_TIMESTAMP])
def test_the_job_fails_once_and_says_why(rig, path, error):
    """
    One run, no second with the audio re-encoded: the rig has a second,
    successful result waiting, and it must be left there.
    """
    calls = _set_up(rig, path)
    rig.results = [ffmpeg_result(rig, False, error), ffmpeg_result(rig, True)]

    run(rig)

    assert len(calls) == 1
    assert len(rig.ffmpeg_calls) + len(rig.combined_calls) == 1
    assert len(rig.results) == 1, "a second run took the waiting result"
    assert len(rig.finish_calls) == 1
    finished = rig.finish_calls[0]
    assert finished[1] is False
    assert finished[4] == worker._explain_failure(error)
    assert finished[4].startswith(worker.UNCOPYABLE_AUDIO)


@pytest.mark.parametrize("path", ["two-pass", "combined"])
def test_the_run_is_asked_to_copy_the_audio(rig, path):
    """The decision handed to FFmpeg is the job's own, audio copied."""
    calls = _set_up(rig, path)
    rig.results = [ffmpeg_result(rig, False, CORRUPT_AUDIO)]

    run(rig)

    audio = [a for a in calls[0]["decision"].actions if a.track_type == "audio"]
    assert [a.action_type for a in audio] == ["copy_track"]


@pytest.mark.parametrize("path", ["two-pass", "combined"])
def test_an_unrelated_failure_is_recorded_with_its_own_error(rig, path):
    calls = _set_up(rig, path)
    rig.results = [ffmpeg_result(rig, False, DISK_FAILURE)]

    run(rig)

    assert len(calls) == 1
    assert rig.finish_calls[0][1] is False
    assert rig.finish_calls[0][4] == DISK_FAILURE


@pytest.mark.parametrize("path", ["two-pass", "combined"])
def test_a_run_that_succeeds_is_recorded_as_one(rig, path):
    calls = _set_up(rig, path)
    rig.results = [ffmpeg_result(rig, True)]

    run(rig)

    assert len(calls) == 1
    assert rig.finish_calls[0][1] is True


@pytest.mark.parametrize("path", ["two-pass", "combined"])
def test_each_run_is_checked_at_the_swap(rig, path):
    """
    Every run is handed the job's before_swap hook — the check that the
    source has not changed under the job before the output replaces it.
    """
    calls = _set_up(rig, path)
    rig.results = [ffmpeg_result(rig, True)]

    run(rig)

    assert callable(calls[0]["before_swap"])


# ── Nothing the pipeline builds re-encodes ───────────────────────────────────

def test_the_job_command_copies_every_stream_it_keeps(settings):
    """
    The command for a job that keeps video, two audio tracks and a subtitle,
    converting to MP4: every codec argument is "copy". The Action a decision
    is made of has nowhere to name an encoder either.
    """
    tracks = [
        make_track(stream_index=0, track_type="video", codec="h264"),
        make_track(stream_index=1, codec="eac3", language="eng", channels=6,
                   channel_layout="5.1"),
        make_track(stream_index=2, codec="aac", language="eng"),
        make_track(stream_index=3, codec="dts", language="ger"),
        make_track(stream_index=4, track_type="subtitle", codec="mov_text", language="eng"),
    ]
    decision = analyze_file(
        make_file_info(path="/m/Film.mkv", container="mkv", video_codec="h264"), tracks,
        {**settings, "prefer_mp4_container": True, "keep_audio_languages": ["eng"],
         "extract_text_subtitles_to_srt": False},
    )
    assert decision.target_container == "mp4", "fixture changed"

    cmd = build_ffmpeg_command("/m/Film.mkv", "/m/Film.mp4", decision, tracks)

    codecs = {arg: cmd[i + 1] for i, arg in enumerate(cmd) if arg.startswith("-c:")}
    assert codecs == {"-c:v": "copy", "-c:a:0": "copy", "-c:a:1": "copy", "-c:s": "copy"}
    assert not {"output_codec", "output_codec_options"} & set(Action.__dataclass_fields__)


# ── Real FFmpeg ──────────────────────────────────────────────────────────────

ffmpeg_required = pytest.mark.skipif(
    shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None,
    reason="ffmpeg/ffprobe not available",
)


def _adts_header(frame_len, sample_rate_index):
    """A 7-byte ADTS header: AAC-LC, stereo, no CRC."""
    return bytes([
        0xFF, 0xF1,
        (1 << 6) | (sample_rate_index << 2),
        (2 << 6) | ((frame_len >> 11) & 0x03),
        (frame_len >> 3) & 0xFF,
        ((frame_len & 0x07) << 5) | 0x1F,
        0xFC,
    ])


def _corrupt_first_audio_track(path, bad_packet=20):
    """
    Damage two AAC packets so a stream copy fails inside the muxer, as
    tools/testlib/corrupt_aac.py does for the generated library. The MP4
    and Matroska muxers add the aac_adtstoasc filter when a stream's first
    packet starts with an ADTS sync word, and the filter then rejects a
    later packet whose ADTS header is invalid (sample-rate index 15).
    Packet sizes do not change, so the file still probes normally.
    """
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "a:0",
         "-show_entries", "packet=pos,size", "-of", "json", str(path)],
        check=True, capture_output=True, text=True).stdout
    packets = json.loads(out)["packets"]
    data = bytearray(path.read_bytes())
    for packet, sample_rate_index in ((packets[0], 4), (packets[bad_packet], 15)):
        offset, size = int(packet["pos"]), int(packet["size"])
        data[offset:offset + 7] = _adts_header(size, sample_rate_index)
    path.write_bytes(bytes(data))


@ffmpeg_required
def test_a_file_with_corrupt_audio_fails_and_is_left_as_it_was(tmp_path, settings):
    """
    The real command on a file whose English AAC has a corrupt packet,
    dropping a German track. FFmpeg's error is the one the detector reads,
    the explanation is built from it, the source is byte for byte what it
    was, and no temp file is left beside it.
    """
    from app.core.ffmpeg import execute_ffmpeg
    from app.core.probe import extract_tracks, probe_file

    source = tmp_path / "Film.mp4"
    subprocess.run(
        ["ffmpeg", "-v", "error", "-y",
         "-f", "lavfi", "-i", "testsrc=size=160x120:rate=10:duration=2",
         "-f", "lavfi", "-i", "sine=frequency=440:duration=2",
         "-f", "lavfi", "-i", "sine=frequency=550:duration=2",
         "-map", "0", "-map", "1", "-map", "2",
         "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", "-ac", "2",
         "-metadata:s:a:0", "language=eng", "-metadata:s:a:1", "language=ger",
         str(source)], check=True)
    _corrupt_first_audio_track(source)
    before = hashlib.sha256(source.read_bytes()).hexdigest()

    tracks = extract_tracks(probe_file(str(source)))
    decision = analyze_file(
        make_file_info(path=str(source), container="mp4", video_codec="h264"), tracks,
        {**settings, "keep_audio_languages": ["eng"], "keep_default_audio": False},
    )
    assert [a.stream_index for a in decision.actions
            if a.action_type == "drop_track"] == [2], "fixture changed"

    result = asyncio.run(execute_ffmpeg(str(source), str(source), decision, tracks, job_id=1))

    assert result.success is False
    assert worker._is_corrupt_audio_copy_failure(result.error), result.error
    assert worker._explain_failure(result.error).startswith(
        worker.UNCOPYABLE_AUDIO + " An audio track has corrupt frames.")
    assert hashlib.sha256(source.read_bytes()).hexdigest() == before
    assert sorted(p.name for p in tmp_path.iterdir()) == ["Film.mp4"]


# ── What a job tells revert capture ──────────────────────────────────────────
#
# Capture is told which input streams the job leaves out, so it follows each
# kept track to where the job put it rather than matching it by metadata the
# job may have rewritten (revert_capture._pins).

@pytest.fixture
def capturing_rig(rig, monkeypatch):
    """
    rig, with each fake executor running the staging hook as the real ones
    do — only for a run that succeeded — and capture replaced by a recorder.
    """
    import app.core.revert_capture as capture_mod

    rig.captures = []

    async def capture(**kwargs):
        rig.captures.append(kwargs)
        return None, None

    monkeypatch.setattr(capture_mod, "capture", capture)

    async def staging_ffmpeg(**kwargs):
        rig.ffmpeg_calls.append(kwargs)
        result = rig.results.pop(0)
        if result.success:
            await kwargs["before_staging"]("/produced.mkv")
        return result

    async def staging_combined(**kwargs):
        rig.combined_calls.append(kwargs)
        result = rig.results.pop(0)
        if result.success:
            await kwargs["before_staging"]("/produced.mkv")
        return result, []

    monkeypatch.setattr(worker, "execute_ffmpeg", staging_ffmpeg)
    monkeypatch.setattr(worker, "execute_ffmpeg_combined", staging_combined)
    return rig



def _drop_audio(stream_index):
    return Action(action_type="drop_track", description="drop ger",
                  track_type="audio", stream_index=stream_index)


def test_capture_is_told_what_the_job_removes(capturing_rig):
    rig = capturing_rig
    rig.actions = [copy_video(), copy_audio(stream_index=1), _drop_audio(2)]
    rig.results = [ffmpeg_result(rig, True)]

    run(rig)

    assert [c["removed"] for c in rig.captures] == [frozenset({2})]


def test_capture_is_told_about_extracted_subtitles_too(capturing_rig):
    rig = capturing_rig
    rig.actions = [copy_audio(stream_index=1), _drop_audio(2), extract_subtitle(3)]
    rig.results = [ffmpeg_result(rig, True)]

    run(rig)

    assert [c["removed"] for c in rig.captures] == [frozenset({2, 3})]


def test_a_job_whose_audio_cannot_be_copied_captures_nothing(capturing_rig):
    """The staging hook runs only for a run that succeeded."""
    rig = capturing_rig
    rig.actions = [copy_video(), copy_audio(stream_index=1), _drop_audio(2)]
    rig.results = [ffmpeg_result(rig, False, CORRUPT_AUDIO)]

    run(rig)

    assert rig.captures == []
