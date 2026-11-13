"""
An MP4's chapter track is written once, however often the file is rewritten.

FFmpeg reads an MP4's or QuickTime file's chapters from its chapter track,
and shows that track too, as a data stream of codec bin_data. AC3 Forge's
add and undo copy every stream ("-map 0"), and so did a revert for a stream
the original had, so the track was copied as a junk data track (bin_data,
tagged "gpmd") while the muxer wrote the chapters as a new chapter track.
Every Forge run and every revert on an MP4 with chapters added one, and
in a QuickTime file the copy made FFmpeg fail ("Tag text incompatible with
output codec id"), so neither could be run on a .mov with chapters. The
explorer reported it as checks 11, 21 and 24 (PARKED 13 and 16): 64 of its
84 findings. Plain ffmpeg -map 0 -c copy does the same.

probe.chapter_track_indices now tells the chapter track apart, from a probe
that asks for chapters, and the Forge commands and the restore leave it out.

These run the real scan, worker, forge and revert against real files.

Verified by mutation, 13 applied, 13 killed. Each was first run against the
whole suite as it stood (2480 tests), and all 13 survived it:

  • the probe not asking for chapters       → the probe, forge and revert
                                              tests, both formats
  • the track found without chapters,       → telling it apart, one case
    with any sample entry, or of any codec    each
  • the add, or the undo, copying it        → forge add and undo, both
  • Forge's job data never finding it       → the same
  • titles not skipping it                  → titles skip it
  • the worker not passing it to the add,
    or to the undo                          → forge add and undo, both
  • the restore copying it                  → the revert, both formats
  • the restore skipping a sidecar stream
    at the same index                       → the revert (MP4)
  • the restore not being told it           → the revert, both formats
"""
import asyncio
import json
import subprocess

import pytest

from app.core.forge import titles_after_forge
from app.core.probe import chapter_track_indices, probe_file
from tests import test_quicktime_files as _qt
from tests.test_quicktime_files import _WS, _run_job, needs_ffmpeg

# That file's fixture: a file-backed database every session points at,
# scratch folders and an empty library, English audio kept and revert
# points on. Bound by assignment, as test_forge_undo_identity.py does:
# pyflakes reads an imported fixture as unused.
rig = _qt.rig

# Six seconds, as the testlib files are: with the second chapter starting at
# a second or less, FFmpeg passed no packet to the copied chapter track, the
# muxer dropped it as empty, and the duplication never showed.
SECONDS = 6
CHAPTERS = [(0.0, 3.0, "Opening"), (3.0, 6.0, "Main")]


# ── Telling the chapter track apart ──────────────────────────────────────────

def _stream(index, kind, codec, tag):
    return {"index": index, "codec_type": kind, "codec_name": codec, "codec_tag_string": tag}


@pytest.mark.parametrize("streams, chapters, expected", [
    ([_stream(0, "video", "h264", "avc1"), _stream(1, "data", "bin_data", "text")], 2, [1]),
    ([_stream(0, "video", "h264", "avc1"), _stream(1, "data", "bin_data", "tx3g")], 2, [1]),
    # No chapters, no chapter track.
    ([_stream(0, "video", "h264", "avc1"), _stream(1, "data", "bin_data", "text")], 0, []),
    # A GoPro track, or the junk an earlier run left: copied as it is.
    ([_stream(0, "video", "h264", "avc1"), _stream(1, "data", "bin_data", "gpmd")], 2, []),
    # A subtitle with the same sample entry is a subtitle.
    ([_stream(0, "video", "h264", "avc1"), _stream(1, "subtitle", "mov_text", "tx3g")], 2, []),
    # A timecode track is data, but not bin_data.
    ([_stream(0, "video", "h264", "avc1"), _stream(1, "data", "none", "text")], 2, []),
])
def test_the_chapter_track_is_told_apart(streams, chapters, expected):
    probe = {"streams": streams, "chapters": [{"id": n} for n in range(chapters)]}
    assert chapter_track_indices(probe) == expected


def test_titles_skip_the_chapter_track():
    """Forge leaves the chapter track out, so every titled stream after it
    moves down one, and down one more past an undone audio track."""
    tracks = [
        {"stream_index": 0, "track_type": "video", "title": None},
        {"stream_index": 1, "track_type": "audio", "title": "Surround"},
        {"stream_index": 2, "track_type": "audio", "title": "Stereo"},
        {"stream_index": 4, "track_type": "subtitle", "title": "Signs"},
    ]

    assert titles_after_forge(tracks, rebuilt_tracks=[3]) == {
        1: "Surround", 2: "Stereo", 3: "Signs"}
    assert titles_after_forge(tracks, 0, rebuilt_tracks=[3]) == {1: "Stereo", 2: "Signs"}


# ── Real files ───────────────────────────────────────────────────────────────

def _movie(path, fmt, audio):
    """
    An MP4 or QuickTime file with two chapters, written by FFmpeg, whose
    muxer gives it a chapter track. audio holds (language, codec, channels)
    per track.
    """
    meta = path.with_suffix(".chapters")
    meta.write_text(";FFMETADATA1\n" + "".join(
        f"[CHAPTER]\nTIMEBASE=1/1000\nSTART={int(a * 1000)}\nEND={int(b * 1000)}\ntitle={t}\n"
        for a, b, t in CHAPTERS))
    cmd = ["ffmpeg", "-v", "error", "-y",
           "-f", "lavfi", "-i", f"testsrc=size=64x36:rate=5:duration={SECONDS}"]
    for n, _ in enumerate(audio):
        cmd += ["-f", "lavfi", "-i", f"sine=frequency={440 + 110 * n}:duration={SECONDS}"]
    cmd += ["-i", str(meta), "-map_chapters", str(len(audio) + 1), "-map", "0:v"]
    for n, (language, codec, channels) in enumerate(audio):
        cmd += ["-map", f"{n + 1}:a", f"-c:a:{n}", codec, f"-ac:a:{n}", str(channels),
                f"-metadata:s:a:{n}", f"language={language}"]
    cmd += ["-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p",
            "-movflags", "+faststart", "-f", fmt, str(path)]
    subprocess.run(cmd, check=True)
    meta.unlink()
    return path


def _streams(path):
    """Each stream's type, codec and sample entry: a junk chapter copy shows
    as an extra data stream tagged "gpmd"."""
    probe = probe_file(str(path))
    return [(s["codec_type"], s["codec_name"], s.get("codec_tag_string"))
            for s in probe["streams"]]


def _chapters(path):
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-print_format", "json", "-show_chapters", str(path)],
        capture_output=True, text=True, check=True).stdout
    return [(round(float(c["start_time"]), 2), round(float(c["end_time"]), 2),
             (c.get("tags") or {}).get("title")) for c in json.loads(out)["chapters"]]


FORMATS = pytest.mark.parametrize("fmt, name", [("mp4", "Movie.mp4"), ("mov", "Camera.mov")])


@needs_ffmpeg
@FORMATS
def test_the_probe_reads_chapters_and_finds_the_track(rig, fmt, name):
    movie = _movie(rig.library / name, fmt, [("eng", "aac", 2)])
    probe = probe_file(str(movie))

    assert len(probe["chapters"]) == len(CHAPTERS)
    assert chapter_track_indices(probe) == [2]
    assert _streams(movie)[2] == ("data", "bin_data", "text")


@needs_ffmpeg
@FORMATS
def test_forge_add_and_undo_write_the_chapter_track_once(rig, fmt, name):
    from app.core import scanner
    from app.database.models import Ac3ForgeJob, MediaFile

    movie = _movie(rig.library / name, fmt, [("eng", "aac", 6)])
    before = _streams(movie)
    assert before == [("video", "h264", "avc1"), ("audio", "aac", "mp4a"),
                      ("data", "bin_data", "text")], "fixture changed"
    with rig.Session() as db:
        scanner.scan_library(db, [str(rig.library)], force_probe=True)
        job_id = rig.forge.queue_forge_job(db, db.query(MediaFile).one().id).id

    assert asyncio.run(rig.worker._process_next_forge(_WS())) is True
    with rig.Session() as db:
        job = db.get(Ac3ForgeJob, job_id)
        assert job.status == "success", job.error_message
    after_add = _streams(movie)
    assert after_add.count(("data", "bin_data", "text")) == 1
    assert [s for s in after_add if s[0] == "data"] == [("data", "bin_data", "text")]
    assert len(after_add) == len(before) + 1
    assert _chapters(movie) == CHAPTERS

    with rig.Session() as db:
        job = db.get(Ac3ForgeJob, job_id)
        job.status, job.is_undo, job.progress = "undo_pending", True, 0.0
        db.commit()
    assert asyncio.run(rig.worker._process_next_forge(_WS())) is True

    assert _streams(movie) == before
    assert _chapters(movie) == CHAPTERS


@needs_ffmpeg
@FORMATS
def test_a_revert_gives_back_one_chapter_track(rig, fmt, name):
    """
    The job drops three tracks; the revert puts them back, and the
    original's single chapter track and its chapters with it.

    Three, so the sidecar holds a stream at the index the processed file's
    chapter track has (2: video, English, chapters). Only the processed
    file's is left out; the sidecar's has to come back.
    """
    from app.core.revert_restore import restore_revert_point
    from app.database.models import RevertPoint

    movie = _movie(rig.library / name, fmt, [("eng", "aac", 2), ("ger", "aac", 2),
                                             ("fre", "aac", 2), ("spa", "aac", 2)])
    before = _streams(movie)

    assert "drop_track" in _run_job(rig, movie)
    assert [s for s in _streams(movie) if s[0] == "data"] == [("data", "bin_data", "text")]

    with rig.Session() as db:
        point_id = db.query(RevertPoint).one().id
    outcome = asyncio.run(restore_revert_point(point_id))
    assert outcome.success, outcome.error

    assert _streams(movie) == before
    assert _chapters(movie) == CHAPTERS
