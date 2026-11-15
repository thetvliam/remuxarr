"""
AC3 Forge keeps the date a file was made.

A job keeps it (test_creation_date.py), but Forge builds its own add and
undo commands, and FFmpeg takes creation_time out of the tags it copies
(copy_meta in fftools/ffmpeg_mux_init.c, FFmpeg 8.1.3). Reproduced with
real FFmpeg before this file existed: an add on a dated MP4 and on a dated
Matroska each gave back a file with no date.

load_forge_job_data now reads the date from the probe it already makes
(probe.creation_time), and both commands name it (forge._creation_time_args).
The real-file tests run the real scan, Forge queue and worker.

Verified by mutation, 7 applied, 7 killed. Each was first run against the
whole suite as it stood (2672 tests) with the change in place, and all 7
survived it:

  • the add not writing the date            → the add command, both real
                                              files
  • the undo not writing it                 → the undo command, both real
                                              files
  • either writing it with no date          → the commands
  • the job data not carrying it            → both real files
  • the worker not passing it to the add    → both real files
  • the worker not passing it to the undo   → both real files
  • the current time written, not the
    file's                                  → the commands, both real files
"""
import asyncio
import json
import subprocess

import pytest

from app.core.forge import build_add_ac3_command, build_undo_command
from tests import test_quicktime_files as _qt
from tests.test_quicktime_files import _WS, needs_ffmpeg

# That file's fixture: a file-backed database every session points at,
# scratch folders and an empty library, with the Forge module. Bound by
# assignment, as test_forge_undo_identity.py does: pyflakes reads an
# imported fixture as unused.
rig = _qt.rig

# Far from today, so that a date FFmpeg made up cannot pass for it.
MADE = "2014-03-02T10:20:30.000000Z"


# ── The commands ─────────────────────────────────────────────────────────────

def _add(**kwargs):
    return build_add_ac3_command("in.mp4", "out.tmp", 1, 1, container="mp4", **kwargs)


def _undo(**kwargs):
    return build_undo_command("in.mp4", "out.tmp", 1, container="mp4", **kwargs)


@pytest.mark.parametrize("build", [_add, _undo], ids=["add", "undo"])
def test_each_command_names_the_date_for_the_output(build):
    cmd = build(creation_time=MADE)

    at = cmd.index(f"creation_time={MADE}")
    assert cmd[at - 1] == "-metadata"
    assert at < cmd.index("-f")
    assert sum("creation_time" in arg for arg in cmd) == 1


@pytest.mark.parametrize("build", [_add, _undo], ids=["add", "undo"])
@pytest.mark.parametrize("given", [None, ""])
def test_neither_names_a_date_when_the_file_has_none(build, given):
    assert not [arg for arg in build(creation_time=given) if "creation_time" in arg]


# ── Real files ───────────────────────────────────────────────────────────────

def _file(path, muxer, *, made=MADE):
    """A download-like file with English AAC 5.1, a Forge candidate, and
    the creation date `made` (None for no date at all)."""
    cmd = ["ffmpeg", "-v", "error", "-y",
           "-f", "lavfi", "-i", "testsrc=size=64x36:rate=10:duration=1",
           "-f", "lavfi", "-i", "sine=frequency=440:duration=1",
           "-map", "0", "-map", "1", "-c:v", "libx264", "-preset", "ultrafast",
           "-pix_fmt", "yuv420p", "-c:a", "aac", "-ac", "6",
           "-metadata:s:a:0", "language=eng"]
    if made:
        cmd += ["-metadata", f"creation_time={made}"]
    subprocess.run(cmd + ["-f", muxer, str(path)], check=True)
    return path


def _date_and_audio(path):
    """The file's creation date, and its audio codecs in order."""
    probe = json.loads(subprocess.run(
        ["ffprobe", "-v", "error", "-print_format", "json",
         "-show_format", "-show_streams", str(path)],
        capture_output=True, text=True, check=True).stdout)
    return ((probe["format"].get("tags") or {}).get("creation_time"),
            [s["codec_name"] for s in probe["streams"] if s["codec_type"] == "audio"])


def _forge_then_undo(rig, path):
    """Run a Forge add on the file, then its undo. Returns the file's date
    and audio after each."""
    from app.core import scanner
    from app.database.models import Ac3ForgeJob, MediaFile

    with rig.Session() as db:
        scanner.scan_library(db, [str(rig.library)], force_probe=True)
        job_id = rig.forge.queue_forge_job(db, db.query(MediaFile).one().id).id
    assert asyncio.run(rig.worker._process_next_forge(_WS())) is True
    after_add = _date_and_audio(path)

    with rig.Session() as db:
        job = db.get(Ac3ForgeJob, job_id)
        assert job.status == "success", job.error_message
        job.status, job.is_undo, job.progress = "undo_pending", True, 0.0
        db.commit()
    assert asyncio.run(rig.worker._process_next_forge(_WS())) is True
    with rig.Session() as db:
        job = db.get(Ac3ForgeJob, job_id)
        assert job.status == "undone", job.error_message
    return after_add, _date_and_audio(path)


@needs_ffmpeg
@pytest.mark.parametrize("name, muxer", [("Film.mp4", "mp4"), ("Film.mkv", "matroska")])
def test_an_add_and_its_undo_keep_the_date(rig, name, muxer):
    source = _file(rig.library / name, muxer)
    assert _date_and_audio(source) == (MADE, ["aac"]), "fixture"

    after_add, after_undo = _forge_then_undo(rig, source)

    assert after_add == (MADE, ["aac", "ac3"])
    assert after_undo == (MADE, ["aac"])


@needs_ffmpeg
def test_a_file_with_no_date_is_not_given_one(rig):
    source = _file(rig.library / "Film.mp4", "mp4", made=None)

    after_add, after_undo = _forge_then_undo(rig, source)

    assert after_add == (None, ["aac", "ac3"])
    assert after_undo == (None, ["aac"])
