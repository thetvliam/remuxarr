"""
An MP4's timecode track no longer stops a revert or AC3 Forge.

Many cameras write a timecode track (tmcd), a GoPro among them: the user's
GX010042.MP4 holds video, AAC, tmcd and GoPro telemetry, the video and the
tmcd track both carrying the timecode 12:59:45:12. FFmpeg's MP4 muxer
cannot copy a tmcd stream at all ("Could not find tag for codec none in
stream", mov_init in libavformat/movenc.c, FFmpeg 8.1.3), so a revert, which
maps every stream the original had, failed on every such file, and so did
Forge's add and undo, which copy every stream ("-map 0"). A job never maps
it and kept it anyway: the MP4 and QuickTime muxers write a tmcd track for
each video stream with a timecode, from the timecode FFmpeg's demuxer puts
on the video. The revert and Forge now leave an MP4's tmcd track out the
same way, with the chapter track (probe.rebuilt_track_indices), and the
muxer writes it again, with the same sample. Found on fixtures while
looking into data streams (PARKED 38).

A QuickTime file is left as it was: its muxer copies a tmcd track, and
keeps its place, which a track written again would not.

These run the real scan, worker, forge and revert against real files,
shaped like the GoPro's: 29.97 frames a second, its timecode, six seconds.

Verified by mutation, 5 applied, 5 killed. Each was first run against the
whole suite as it stood (2535 tests), and all 5 survived it:

  • the timecode track never found        → telling it apart, the probe,
                                            forge and the revert, MP4
  • found in a file with no video         → telling it apart
  • found in a QuickTime file too         → telling it apart, the probe and
                                            forge on QuickTime, and the
                                            QuickTime track keeping its place
  • the restore not leaving it out        → the revert, MP4
  • Forge not leaving it out              → forge add and undo, MP4

The kills are from this file, test_mp4_chapter_track.py and
test_quicktime_files.py, the files that share its rig.
"""
import asyncio
import hashlib
import subprocess

import pytest

from app.core.probe import probe_file, rebuilt_track_indices, timecode_track_indices
from tests import test_quicktime_files as _qt
from tests.test_quicktime_files import _WS, _run_job, needs_ffmpeg

# That file's fixture: a file-backed database every session points at,
# scratch folders and an empty library, English audio kept and revert
# points on. Bound by assignment, as test_forge_undo_identity.py does:
# pyflakes reads an imported fixture as unused.
rig = _qt.rig

SECONDS = 6
RATE = "30000/1001"
TIMECODE = "12:59:45:12"
TMCD = ("data", "tmcd", TIMECODE)


# ── Telling the timecode track apart ─────────────────────────────────────────

def _probe(brand, *streams):
    return {"format": {"tags": {"major_brand": brand}},
            "streams": [dict(s, index=i) for i, s in enumerate(streams)]}


_VIDEO = {"codec_type": "video", "codec_tag_string": "avc1"}
_AUDIO = {"codec_type": "audio", "codec_tag_string": "mp4a"}
_TC = {"codec_type": "data", "codec_tag_string": "tmcd"}
_GPMD = {"codec_type": "data", "codec_tag_string": "gpmd"}


@pytest.mark.parametrize("probe, expected", [
    (_probe("isom", _VIDEO, _AUDIO, _TC), [2]),
    (_probe("mp41", _VIDEO, _TC, _AUDIO, _GPMD), [1]),   # the GoPro's layout
    (_probe("isom", _VIDEO, _AUDIO, _GPMD), []),
    (_probe("isom", _AUDIO, _TC), []),     # no video to take the timecode from
    (_probe("qt  ", _VIDEO, _AUDIO, _TC), []),           # copied where it stands
], ids=["mp4", "gopro", "no-tmcd", "no-video", "quicktime"])
def test_the_timecode_track_is_told_apart(probe, expected):
    assert timecode_track_indices(probe) == expected


def test_both_tracks_the_muxer_writes_are_left_out():
    probe = _probe("isom", _VIDEO, _TC, _AUDIO,
                   {"codec_type": "data", "codec_name": "bin_data", "codec_tag_string": "text"})
    probe["chapters"] = [{"id": 0}]
    assert rebuilt_track_indices(probe) == [1, 3]


# ── Real files ───────────────────────────────────────────────────────────────

def _movie(path, fmt, audio, *, timecode_first=False):
    """
    An MP4 or QuickTime file with a timecode track, written by FFmpeg,
    whose muxer adds the track last. audio holds (language, channels) per
    AAC track. timecode_first moves it to just after the video, as a camera
    can write it (QuickTime only: the MP4 muxer cannot place one).
    """
    cmd = ["ffmpeg", "-v", "error", "-y",
           "-f", "lavfi", "-i", f"testsrc=size=64x36:rate={RATE}:duration={SECONDS}"]
    for n, _ in enumerate(audio):
        cmd += ["-f", "lavfi", "-i", f"sine=frequency={440 + 110 * n}:duration={SECONDS}"]
    cmd += ["-map", "0:v"]
    for n, (language, channels) in enumerate(audio):
        cmd += ["-map", f"{n + 1}:a", f"-ac:a:{n}", str(channels),
                f"-metadata:s:a:{n}", f"language={language}"]
    staged = path.with_name("staged" + path.suffix)
    cmd += ["-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p",
            "-c:a", "aac", "-timecode", TIMECODE, "-movflags", "+faststart",
            "-f", fmt, str(staged if timecode_first else path)]
    subprocess.run(cmd, check=True)
    if timecode_first:
        timecode = len(audio) + 1
        order = ["-map", "0:0", "-map", f"0:{timecode}"]
        for n in range(len(audio)):
            order += ["-map", f"0:{n + 1}"]
        subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", str(staged), *order,
                        "-c", "copy", "-movflags", "+faststart", "-f", fmt, str(path)],
                       check=True)
        staged.unlink()
    return path


def _streams(path):
    """Each stream's type, sample entry and timecode, in order."""
    return [(s["codec_type"], s.get("codec_tag_string"), (s.get("tags") or {}).get("timecode"))
            for s in probe_file(str(path))["streams"]]


def _sample(path):
    """The timecode track's one sample, as bytes: the frame it starts at."""
    index = [s[1] for s in _streams(path)].index("tmcd")
    data = subprocess.run(["ffmpeg", "-v", "error", "-i", str(path), "-map", f"0:{index}",
                           "-c", "copy", "-f", "data", "-"],
                          capture_output=True, check=True).stdout
    assert data, "the timecode track holds no sample"
    return hashlib.sha256(data).hexdigest()


def _forge_add_and_undo(rig, movie):
    """Forge's add, then its undo, each through the worker: the streams after
    each."""
    from app.core import scanner
    from app.database.models import Ac3ForgeJob, MediaFile

    with rig.Session() as db:
        scanner.scan_library(db, [str(rig.library)], force_probe=True)
        job_id = rig.forge.queue_forge_job(db, db.query(MediaFile).one().id).id
    assert asyncio.run(rig.worker._process_next_forge(_WS())) is True
    with rig.Session() as db:
        job = db.get(Ac3ForgeJob, job_id)
        assert job.status == "success", job.error_message
    after_add = _streams(movie)
    sample_after_add = _sample(movie)

    with rig.Session() as db:
        job = db.get(Ac3ForgeJob, job_id)
        job.status, job.is_undo, job.progress = "undo_pending", True, 0.0
        db.commit()
    assert asyncio.run(rig.worker._process_next_forge(_WS())) is True
    with rig.Session() as db:
        job = db.get(Ac3ForgeJob, job_id)
        assert job.status == "undone", job.error_message
    return after_add, sample_after_add


FORMATS = pytest.mark.parametrize("fmt, name", [("mp4", "GoPro.mp4"), ("mov", "Camera.mov")])


@needs_ffmpeg
@FORMATS
def test_the_probe_finds_only_an_mp4s_timecode_track(rig, fmt, name):
    movie = _movie(rig.library / name, fmt, [("eng", 2)])
    assert _streams(movie) == [("video", "avc1", TIMECODE), ("audio", "mp4a", None), TMCD]
    assert timecode_track_indices(probe_file(str(movie))) == ([2] if fmt == "mp4" else [])


@needs_ffmpeg
@FORMATS
def test_forge_add_and_undo_keep_the_timecode(rig, fmt, name):
    movie = _movie(rig.library / name, fmt, [("eng", 6)])
    before, sample = _streams(movie), _sample(movie)
    assert before == [("video", "avc1", TIMECODE), ("audio", "mp4a", None), TMCD]

    after_add, sample_after_add = _forge_add_and_undo(rig, movie)

    # The AC3 is appended. An MP4's timecode track is written again, after
    # it; a QuickTime file's is copied where it stood.
    ac3 = ("audio", "ac-3", None)
    assert after_add == ([*before[:2], ac3, TMCD] if fmt == "mp4" else [*before, ac3])
    assert sample_after_add == sample
    assert _streams(movie) == before
    assert _sample(movie) == sample


@needs_ffmpeg
@FORMATS
def test_a_revert_gives_back_the_timecode(rig, fmt, name):
    """The job drops the German track; the revert puts it back, and the
    timecode track with its sample. German, not French: the QuickTime
    muxer writes only languages in the Macintosh table, which spells
    French fra, not fre (PARKED 42)."""
    from app.core.revert_restore import restore_revert_point
    from app.database.models import RevertPoint

    movie = _movie(rig.library / name, fmt, [("eng", 2), ("ger", 2)])
    before, sample = _streams(movie), _sample(movie)

    assert "drop_track" in _run_job(rig, movie)
    assert _streams(movie) == [before[0], before[1], TMCD]

    with rig.Session() as db:
        point_id = db.query(RevertPoint).one().id
    outcome = asyncio.run(restore_revert_point(point_id))
    assert outcome.success, outcome.error

    assert _streams(movie) == before
    assert _sample(movie) == sample


@needs_ffmpeg
def test_a_quicktime_timecode_track_keeps_its_place(rig):
    """
    Copied, not written again: a track written again would go last. Forge
    on a QuickTime file whose timecode track comes before its audio, as a
    camera can write it.
    """
    movie = _movie(rig.library / "Camera.mov", "mov", [("eng", 6)], timecode_first=True)
    before, sample = _streams(movie), _sample(movie)
    assert before == [("video", "avc1", TIMECODE), TMCD, ("audio", "mp4a", None)]

    after_add, _ = _forge_add_and_undo(rig, movie)

    assert after_add == [*before, ("audio", "ac-3", None)]
    assert _streams(movie) == before
    assert _sample(movie) == sample
