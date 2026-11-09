"""
Every stream keeps the flags it came with through a job, a Forge add and a
Forge undo (ffmpeg.KEEP_STREAM_FLAGS).

FFmpeg marks the first stream of a type default when two or more streams
of that type have none between them, unless the command sets a
disposition itself. None of these commands did, so copying an MKV with two
subtitles, neither on by default, turned the first one on. The sources
here have two audio tracks and two subtitles with no default among them,
and other flags (commentary, forced, hearing impaired) that must survive
beside them.

The AC3 a Forge add makes takes the flags of the AAC it is encoded from,
all but default, so a file never gains a second default audio track.
"""

import asyncio
import json
import os
import shutil
import subprocess

import pytest

from app.core.decision import analyze_file
from app.core.ffmpeg import KEEP_STREAM_FLAGS, build_ffmpeg_command
from app.core.forge import build_add_ac3_command, build_undo_command
from app.core.probe import extract_tracks, probe_file
from tests.conftest import make_file_info

ffmpeg_required = pytest.mark.skipif(
    shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None,
    reason="ffmpeg/ffprobe not available",
)

FLAGS = ("default", "forced", "hearing_impaired", "comment", "original")


def _ffmpeg(*args):
    subprocess.run(["ffmpeg", "-v", "error", "-y", *args], check=True)


def _srt(tmp_path, name):
    path = tmp_path / name
    path.write_text("1\n00:00:00,000 --> 00:00:01,500\n" + name + "\n")
    return str(path)


def _inputs(tmp_path):
    """Video, an AAC 5.1 and an AAC stereo, each a separate file."""
    video, surround, stereo = (str(tmp_path / n) for n in ("v.mkv", "a51.m4a", "a20.m4a"))
    _ffmpeg("-f", "lavfi", "-i", "testsrc=size=160x120:rate=10:duration=2",
            "-c:v", "libx264", "-pix_fmt", "yuv420p", video)
    _ffmpeg("-f", "lavfi", "-i", "sine=frequency=440:duration=2",
            "-ac", "6", "-c:a", "aac", surround)
    _ffmpeg("-f", "lavfi", "-i", "sine=frequency=550:duration=2",
            "-ac", "2", "-c:a", "aac", stereo)
    return video, surround, stereo


def _flags(path):
    """Each stream's set flags, in stream order."""
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-show_streams", "-of", "json", str(path)],
        capture_output=True, text=True, check=True,
    )
    return [
        (s["codec_type"], {f for f in FLAGS if (s.get("disposition") or {}).get(f)})
        for s in json.loads(out.stdout)["streams"]
    ]


# ── The commands ────────────────────────────────────────────────────────────

def test_each_command_keeps_the_streams_flags(settings):
    """All three commands carry the argument, whatever else they do."""
    tracks = [
        {"stream_index": 0, "track_type": "video", "codec": "h264"},
        {"stream_index": 1, "track_type": "audio", "codec": "aac", "language": "eng"},
    ]
    decision = analyze_file(
        make_file_info(path="/media/a.mkv", container="mkv"), tracks,
        {**settings, "prefer_mp4_container": True},
    )
    commands = [
        build_ffmpeg_command("/media/a.mkv", "/media/a.mp4", decision, tracks),
        build_add_ac3_command("/media/a.mkv", "/tmp/t", 1, 1),
        build_undo_command("/media/a.mkv", "/tmp/t", 1),
    ]
    for cmd in commands:
        at = [i for i in range(len(cmd)) if cmd[i:i + 2] == KEEP_STREAM_FLAGS]
        assert len(at) == 1, cmd


# ── A job ───────────────────────────────────────────────────────────────────

@ffmpeg_required
def test_a_job_leaves_every_kept_streams_flags_as_they_were(tmp_path, settings):
    """
    Two eng audio tracks (the second commentary) and two eng subtitles
    (forced, hearing impaired), none default; the job drops a ger subtitle
    and keeps the file as MKV.
    """
    from app.core.ffmpeg import execute_ffmpeg

    video, surround, stereo = _inputs(tmp_path)
    source = tmp_path / "source.mkv"
    _ffmpeg("-i", video, "-i", surround, "-i", stereo,
            "-i", _srt(tmp_path, "forced.srt"), "-i", _srt(tmp_path, "sdh.srt"),
            "-i", _srt(tmp_path, "ger.srt"),
            *[a for i in range(6) for a in ("-map", str(i))], "-c", "copy",
            "-metadata:s:a:0", "language=eng", "-metadata:s:a:1", "language=eng",
            "-metadata:s:s:0", "language=eng", "-metadata:s:s:1", "language=eng",
            "-metadata:s:s:2", "language=ger",
            "-disposition:v:0", "0", "-disposition:a:0", "0", "-disposition:a:1", "comment",
            "-disposition:s:0", "forced", "-disposition:s:1", "hearing_impaired",
            "-disposition:s:2", "0", str(source))
    before = _flags(source)
    assert before == [("video", set()), ("audio", set()), ("audio", {"comment"}),
                      ("subtitle", {"forced"}), ("subtitle", {"hearing_impaired"}),
                      ("subtitle", set())], "fixture changed"

    tracks = extract_tracks(probe_file(str(source)))
    decision = analyze_file(
        make_file_info(path=str(source), container="mkv"), tracks,
        {**settings, "prefer_mp4_container": False, "extract_text_subtitles_to_srt": False,
         "keep_audio_languages": ["eng"], "keep_subtitle_languages": ["eng"]},
    )
    assert decision.target_container == "mkv"
    assert [(a.action_type, a.stream_index) for a in decision.actions
            if a.action_type == "drop_track"] == [("drop_track", 5)], "fixture changed"

    result = asyncio.run(execute_ffmpeg(str(source), str(source), decision, tracks, job_id=1))
    assert result.success, result.error_message
    assert _flags(result.output_path) == before[:5]


# ── AC3 Forge ───────────────────────────────────────────────────────────────

def _forge_source(tmp_path, with_ac3=False):
    """
    Video, an eng AAC 5.1 (default and original), an eng AAC stereo
    commentary and two eng subtitles (forced, hearing impaired), none of
    them default. with_ac3 appends the AC3 5.1 an add would have made, for
    an undo to remove.
    """
    video, surround, stereo = _inputs(tmp_path)
    source = tmp_path / "source.mkv"
    ac3 = ["-map", "1", "-c:a:2", "ac3", "-ac:a:2", "6",
           "-metadata:s:a:2", "language=eng", "-disposition:a:2", "0"] if with_ac3 else []
    _ffmpeg("-i", video, "-i", surround, "-i", stereo,
            "-i", _srt(tmp_path, "forced.srt"), "-i", _srt(tmp_path, "sdh.srt"),
            *[a for i in range(5) for a in ("-map", str(i))], *ac3, "-c:v", "copy",
            "-c:a:0", "copy", "-c:a:1", "copy", "-c:s", "copy",
            "-metadata:s:a:0", "language=eng", "-metadata:s:a:1", "language=eng",
            "-metadata:s:s:0", "language=eng", "-metadata:s:s:1", "language=eng",
            "-disposition:v:0", "0", "-disposition:a:0", "default+original",
            "-disposition:a:1", "comment",
            "-disposition:s:0", "forced", "-disposition:s:1", "hearing_impaired",
            str(source))
    return source


def _run_forge(monkeypatch, source, is_undo, container="mkv"):
    """worker._run_forge_job on a real file; returns finish_forge_job's verdict."""
    import app.core.forge as forge
    import app.core.worker as worker

    job_data = {
        "job_id": 1, "file_id": 1, "is_undo": is_undo,
        "file_path": str(source), "filename": source.name, "container": container,
        "aac_stream_index": 1, "audio_track_count": 2,
        "undo_audio_output_index": 2 if is_undo else None,
        "titles": {}, "original_size": os.path.getsize(source),
        "job_timeout_minutes": 5, "add_faststart": False,
        "app_cfg": {"revert_enabled": False}, "cover_art": ([], 0),
    }
    finished = {}

    def finish(job_id, success, size, error, **kwargs):
        finished.update(success=success, error=error)

    class _WS:
        async def broadcast_json(self, *_a, **_k):
            pass

    async def go():
        await worker._run_forge_job(1, _WS(), asyncio.get_running_loop())

    with monkeypatch.context() as m:
        m.setattr(forge, "load_forge_job_data", lambda _id: dict(job_data))
        m.setattr(forge, "finish_forge_job", finish)
        m.setattr(forge, "load_forge_final_state", lambda _id: None)
        asyncio.run(go())
    return finished


@ffmpeg_required
def test_a_forge_add_leaves_the_copied_streams_flags_as_they_were(tmp_path, monkeypatch):
    source = _forge_source(tmp_path)
    before = _flags(source)
    finished = _run_forge(monkeypatch, source, is_undo=False)
    assert finished["success"], finished["error"]
    after = _flags(source)
    assert [t for t, _ in after] == [t for t, _ in before] + ["audio"]
    assert after[:-1] == before
    assert before[1] == ("audio", {"default", "original"}), "fixture changed"
    assert after[-1] == ("audio", {"original"})


@ffmpeg_required
def test_a_forge_add_to_an_mp4_leaves_one_default_audio_track(tmp_path, monkeypatch):
    """
    In an MP4 a second default audio track gave every track its own
    alternate group, both audio tracks enabled. ffprobe reads an MP4
    track's enabled flag as default.
    """
    video, surround, stereo = _inputs(tmp_path)
    source = tmp_path / "source.mp4"
    _ffmpeg("-i", video, "-i", surround, "-i", stereo, "-map", "0", "-map", "1", "-map", "2",
            "-c", "copy", "-metadata:s:a:0", "language=eng", "-metadata:s:a:1", "language=fre",
            "-disposition:a:0", "default", "-disposition:a:1", "0", str(source))
    before = _flags(source)
    assert before == [("video", {"default"}), ("audio", {"default"}), ("audio", set())], \
        "fixture changed"
    finished = _run_forge(monkeypatch, source, is_undo=False, container="mp4")
    assert finished["success"], finished["error"]
    assert _flags(source) == before + [("audio", set())]


@ffmpeg_required
def test_a_forge_undo_leaves_every_streams_flags_as_they_were(tmp_path, monkeypatch):
    source = _forge_source(tmp_path, with_ac3=True)
    before = _flags(source)
    assert [t for t, _ in before] == ["video", "audio", "audio", "subtitle", "subtitle", "audio"], \
        "fixture changed"
    finished = _run_forge(monkeypatch, source, is_undo=True)
    assert finished["success"], finished["error"]
    assert _flags(source) == before[:5]
