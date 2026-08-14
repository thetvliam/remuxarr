"""
Regression tests for attachment loss on remux.

The bug: build_ffmpeg_command's -map arguments are built from
extract_tracks(), which returns only video, audio and subtitle streams.
No attachment was ever named in a map, FFmpeg's default stream selection
does not pick them up, and the mux reports success. Every remux —
including a pure language re-tag that changes nothing else — silently
destroyed the file's fonts, cover art and posters.

Nothing surfaced it. The job succeeded, the sizes looked right, and the
only symptom was styled subtitles rendering in a fallback typeface at
some later point, with no way back to the original.

The argv tests below are the cheap half. The load-bearing ones run real
FFmpeg against a real file with a real attachment and check the
attachment is in the output, because the bug was invisible at exactly
the level argv assertions operate at — the command looked correct, and
was correct, for the streams it knew about.

Verified by mutation, 4 applied, 4 killed:

  • The attachment map removed entirely     → killed (the original bug)
  • "0:t?" → "0:t" (optional marker dropped)→ killed, real FFmpeg exits
                                              non-zero on the majority of
                                              files, which have none
  • The out_fmt gate removed, mapping into
    every container                          → killed, real MP4 mux fails
                                              at header write
  • Gate widened to include "mp4"            → killed, same

No equivalent mutants.
"""
import json
import shutil
import subprocess

import pytest

from app.core.decision import analyze_file
from app.core.ffmpeg import build_ffmpeg_command
from tests.conftest import make_file_info, make_track


ffmpeg_required = pytest.mark.skipif(
    shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None,
    reason="ffmpeg/ffprobe not available",
)


def _drop_foreign_audio(path, container, settings):
    """A file needing only a track drop — no container change involved."""
    tracks = [
        make_track(stream_index=0, track_type="video", codec="h264"),
        make_track(stream_index=1, track_type="audio", codec="aac",
                   language="eng", is_default=True),
        make_track(stream_index=2, track_type="audio", codec="aac",
                   language="fre"),
    ]
    file_info = make_file_info(path=path, container=container, video_codec="h264")
    decision = analyze_file(file_info, tracks, settings)
    assert decision.should_process, "fixture no longer produces work"
    return decision, tracks


# ── argv ─────────────────────────────────────────────────────────────────────

def test_matroska_output_maps_attachments(settings):
    settings["prefer_mp4_container"] = False
    decision, tracks = _drop_foreign_audio("/media/Show.mkv", "mkv", settings)

    cmd = build_ffmpeg_command("/media/Show.mkv", "/media/out.mkv",
                               decision, tracks)

    assert "0:t?" in cmd, "attachments are not mapped; they will be destroyed"


def test_attachment_map_is_optional(settings):
    """
    Most files have no attachments at all. Without the "?" FFmpeg treats
    an unmatched map as an error and every one of those jobs fails.
    """
    settings["prefer_mp4_container"] = False
    decision, tracks = _drop_foreign_audio("/media/Show.mkv", "mkv", settings)

    cmd = build_ffmpeg_command("/media/Show.mkv", "/media/out.mkv",
                               decision, tracks)

    assert "0:t" not in cmd, "the non-optional form would fail on most files"


def test_mp4_output_does_not_map_attachments(settings):
    """
    Not a defensive skip. MP4 cannot store an attachment and the mux
    fails at header write, so mapping one turns a working job into a
    failing one.
    """
    settings["prefer_mp4_container"] = True
    decision, tracks = _drop_foreign_audio("/media/Show.mp4", "mp4", settings)

    cmd = build_ffmpeg_command("/media/Show.mp4", "/media/out.mp4",
                               decision, tracks)

    assert "0:t?" not in cmd


# ── Against real FFmpeg ──────────────────────────────────────────────────────

def _source_with_attachment(tmp_path, container="mkv"):
    """A file with video, two audio languages, and a font attachment."""
    video = tmp_path / "v.mkv"
    subprocess.run(
        ["ffmpeg", "-v", "error", "-y", "-f", "lavfi",
         "-i", "testsrc=size=160x120:rate=10:duration=2",
         "-c:v", "libx264", "-pix_fmt", "yuv420p", str(video)],
        check=True,
    )
    audio = tmp_path / "a.m4a"
    subprocess.run(
        ["ffmpeg", "-v", "error", "-y", "-f", "lavfi",
         "-i", "sine=frequency=440:duration=2", "-c:a", "aac", str(audio)],
        check=True,
    )
    font = tmp_path / "Roboto.ttf"
    font.write_bytes(b"\x00\x01\x02\x03" * 256)

    source = tmp_path / f"source.{container}"
    subprocess.run(
        ["ffmpeg", "-v", "error", "-y", "-i", str(video), "-i", str(audio),
         "-i", str(audio),
         "-map", "0:v", "-map", "1:a", "-map", "2:a",
         "-metadata:s:a:0", "language=eng", "-metadata:s:a:1", "language=fre",
         "-c", "copy", "-attach", str(font),
         "-metadata:s:t", "mimetype=application/x-truetype-font",
         "-f", "matroska", str(source)],
        check=True,
    )
    return source


def _streams(path):
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-show_streams", "-of", "json", str(path)],
        capture_output=True, text=True,
    )
    assert out.returncode == 0, f"{path} is unreadable"
    return json.loads(out.stdout)["streams"]


@ffmpeg_required
def test_real_remux_preserves_the_attachment(tmp_path, settings):
    """
    The reproduction, run through the production command builder rather
    than a hand-written command — the point being that the builder itself
    was what dropped it.
    """
    settings["prefer_mp4_container"] = False
    source = _source_with_attachment(tmp_path)
    assert any(s["codec_type"] == "attachment" for s in _streams(source))

    decision, tracks = _drop_foreign_audio(str(source), "mkv", settings)
    out = tmp_path / "out.mkv"
    subprocess.run(build_ffmpeg_command(str(source), str(out), decision, tracks),
                   check=True)

    kinds = [s["codec_type"] for s in _streams(out)]
    assert "attachment" in kinds, "the attachment was destroyed by the remux"
    assert kinds.count("audio") == 1, "fixture should still have dropped the fre track"

    attachment = next(s for s in _streams(out) if s["codec_type"] == "attachment")
    assert attachment["tags"]["filename"] == "Roboto.ttf"


@ffmpeg_required
def test_real_remux_of_a_file_without_attachments_still_succeeds(tmp_path, settings):
    """
    The majority case, and what the "?" is for. A non-optional map turns
    every ordinary file into a failed job.
    """
    settings["prefer_mp4_container"] = False
    plain = tmp_path / "plain.mkv"
    subprocess.run(
        ["ffmpeg", "-v", "error", "-y", "-f", "lavfi",
         "-i", "testsrc=size=160x120:rate=10:duration=2",
         "-f", "lavfi", "-i", "sine=frequency=440:duration=2",
         "-f", "lavfi", "-i", "sine=frequency=880:duration=2",
         "-map", "0:v", "-map", "1:a", "-map", "2:a",
         "-metadata:s:a:0", "language=eng", "-metadata:s:a:1", "language=fre",
         "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac",
         "-f", "matroska", str(plain)],
        check=True,
    )

    decision, tracks = _drop_foreign_audio(str(plain), "mkv", settings)
    out = tmp_path / "out.mkv"
    subprocess.run(build_ffmpeg_command(str(plain), str(out), decision, tracks),
                   check=True)

    assert [s["codec_type"] for s in _streams(out)] == ["video", "audio"]


@ffmpeg_required
def test_real_mp4_output_from_an_attachment_bearing_source_succeeds(tmp_path, settings):
    """
    Why the map is gated on the output format. The source has an
    attachment and the target is MP4, which cannot hold one — mapping it
    fails the whole mux at header write, so the gate is the difference
    between a working conversion and a broken one. The attachment is
    genuinely lost here, which is a property of MP4 rather than a
    regression.
    """
    settings["prefer_mp4_container"] = True
    source = _source_with_attachment(tmp_path)
    decision, tracks = _drop_foreign_audio(str(source), "mkv", settings)
    decision.target_container = "mp4"

    out = tmp_path / "out.mp4"
    subprocess.run(build_ffmpeg_command(str(source), str(out), decision, tracks),
                   check=True)

    kinds = [s["codec_type"] for s in _streams(out)]
    assert "attachment" not in kinds
    assert kinds.count("video") == 1
