"""
A file that changes on disk while AC3 Forge is running on it.

Jobs stop when their source changes under them (test_source_changed_mid_job.py)
and so does a revert, but a forge run never checked. Sonarr or Radarr
importing an upgrade at the same name while FFmpeg ran ended in a success
that overwrote the upgrade with the forged old file, and a file deleted
mid-run was written back; an undo did the same. Reproduced with real
FFmpeg through the worker before this file existed, with the change at
either point below. A deletion during the encode was refused, but only
because the tag check could not probe the missing file, so the reason given
was the wrong one.

_run_forge_job now records the file's size and mtime when the run starts
and checks them first thing in the staging hook, before the tag check and
the capture, and again just before the swap. A change fails the run
(undo_failed for an undo) with nothing written.

Each test runs the worker's forge path with real FFmpeg and a file-backed
database (test_forge_shutdown.py's fixture), and changes the file at a fixed
point rather than racing it: "during the encode" is the first FFmpeg
progress message, before the staging hook runs; "before the swap" is inside
the revert capture, after both of the staging hook's checks and before the
output is copied beside the file.

9 of the 11 fail against the code before the check: each run ended in
success with the file overwritten or written back, and the deletion during
the encode failed with the tag check's reason. The 2 controls pass on both.

Verified by mutation, 8 applied, each run first against the suite as it
stood (2709 tests):

  • run_forge_command not passing before_swap on    survived → killed here
  • _forge_run not passing it on                    survived → killed here
  • the swap hook always answering None             survived → killed here
  • the staging hook's check removed                survived → killed by
                                                      the deletion during
                                                      the encode
  • that check made after the tag check             survived → killed by
                                                      the same
  • the identity reduced to size alone              survived → killed by
                                                      the edit that keeps
                                                      the size
  • the identity reduced to mtime alone             survived → killed by
                                                      the upgrade carrying
                                                      the old mtime
  • a refused run keeping its captured sidecar      already killed by
                                                      test_forge_orchestration
                                                      (a failed run discards
                                                      its sidecar); killed
                                                      here too

With the staging hook's check removed, an upgrade during the encode is
still caught at the swap. What that check adds is the right reason for a
file deleted during the encode, which the tag check would otherwise refuse
as a file it could not probe.
"""
import asyncio
import hashlib
import os
import shutil
import subprocess

import pytest

from tests import test_forge_shutdown as _shutdown
from tests.test_forge_shutdown import (
    WITH_A_SUBTITLE, _audio, _forge_sidecar, _forged_then_undo, _job,
    _revert_files, _set,
)

pytestmark = pytest.mark.skipif(
    shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None,
    reason="ffmpeg/ffprobe not available",
)

# That file's fixture: Movie.mkv with one English AAC 5.1 track, scanned,
# a forge add waiting. Bound by assignment, not imported by name: pyflakes
# reads an imported fixture as unused and every test's parameter as a
# redefinition of it.
lib = _shutdown.lib

ENCODE, SWAP = "during the encode", "before the swap"


class _WS:
    """Calls on_encode at the first progress message FFmpeg itself sends,
    which comes before the staging hook runs."""

    def __init__(self, on_encode=None):
        self.on_encode = on_encode

    async def broadcast_json(self, data):
        from app.core.subprocess_runner import STAGING_ACTION

        if (data.get("event") == "forge_job_progress" and self.on_encode
                and data["current_action"] != STAGING_ACTION):
            on_encode, self.on_encode = self.on_encode, None
            on_encode()


@pytest.fixture
def upgrade(tmp_path):
    """A different release of the film: another size and other bytes."""
    path = tmp_path / "upgrade.mkv"
    subprocess.run([
        "ffmpeg", "-v", "error", "-y",
        "-f", "lavfi", "-i", "testsrc=size=96x54:rate=5:duration=3",
        "-f", "lavfi", "-i", "sine=frequency=880:duration=3",
        "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-ac", "2", "-f", "matroska", str(path)], check=True)
    return path


def _digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _run(lib, monkeypatch, when, change):
    """Run the waiting forge job with `change` made to the file `when`."""
    import app.core.revert_capture as capture_mod

    ws = _WS(change if when == ENCODE else None)
    if when == SWAP:
        real = capture_mod.capture

        async def capture_then_change(**kw):
            out = await real(**kw)
            change()
            return out
        monkeypatch.setattr(capture_mod, "capture", capture_then_change)
    assert asyncio.run(lib.worker._process_next_forge(ws)) is True
    return _job(lib)


def _replace_with(lib, upgrade):
    return lambda: shutil.copyfile(upgrade, lib.video)


@pytest.mark.parametrize("undo", [False, True], ids=["add", "undo"])
def test_an_untouched_file_is_forged_as_before(lib, monkeypatch, undo):
    if undo:
        _forged_then_undo(lib)
    job = _run(lib, monkeypatch, SWAP, lambda: None)
    assert (job.status, job.error) == ("undone" if undo else "success", None)
    assert _audio(lib.video) == (["aac"] if undo else ["aac", "ac3"])


@pytest.mark.parametrize("when", [ENCODE, SWAP])
@pytest.mark.parametrize("undo", [False, True], ids=["add", "undo"])
def test_an_upgrade_landing_mid_run_is_left_as_it_is(lib, monkeypatch, upgrade, undo, when):
    if undo:
        _forged_then_undo(lib)
    job = _run(lib, monkeypatch, when, _replace_with(lib, upgrade))
    assert job.status == ("undo_failed" if undo else "failed")
    assert job.error == ("Replaced or modified on disk while AC3 Forge was "
                         "running. Nothing was written.")
    assert _digest(lib.video) == _digest(upgrade)


@pytest.mark.parametrize("when", [ENCODE, SWAP])
def test_a_file_deleted_mid_run_stays_deleted(lib, monkeypatch, when):
    # During the encode this was refused before, by the tag check, which
    # could not probe the missing file: the reason is what is pinned there.
    job = _run(lib, monkeypatch, when, lambda: os.remove(lib.video))
    assert job.status == "failed"
    assert job.error == ("Deleted or renamed on disk while AC3 Forge was "
                         "running. Nothing was written.")
    assert not lib.video.exists()


def test_an_edit_that_keeps_the_size_is_noticed(lib, monkeypatch):
    before = _digest(lib.video)
    st = os.stat(lib.video)
    job = _run(lib, monkeypatch, SWAP,
               lambda: os.utime(lib.video, (st.st_atime, st.st_mtime + 60)))
    assert job.status == "failed"
    assert job.error.startswith("Replaced or modified on disk")
    assert _digest(lib.video) == before


def test_an_upgrade_carrying_the_old_mtime_is_noticed(lib, monkeypatch, upgrade):
    st = os.stat(lib.video)

    def replace_keeping_mtime():
        shutil.copyfile(upgrade, lib.video)
        os.utime(lib.video, (st.st_atime, st.st_mtime))
    job = _run(lib, monkeypatch, SWAP, replace_keeping_mtime)
    assert job.status == "failed"
    assert job.error.startswith("Replaced or modified on disk")
    assert _digest(lib.video) == _digest(upgrade)


@WITH_A_SUBTITLE
def test_a_revert_point_captured_before_the_change_is_discarded(lib, monkeypatch, upgrade):
    # A file a job has processed has a revert point, so the undo's capture
    # writes a sidecar of its own; the change lands just after it.
    _set(lib, "revert_enabled", True)
    _forged_then_undo(lib, processed=True)
    before = _revert_files(lib)
    assert _forge_sidecar(lib) not in before[1]
    seen = []

    def replace_once_captured():
        seen.append(_forge_sidecar(lib) in _revert_files(lib)[1])
        shutil.copyfile(upgrade, lib.video)
    job = _run(lib, monkeypatch, SWAP, replace_once_captured)
    assert seen == [True], "the capture wrote no sidecar to discard"
    assert job.status == "undo_failed"
    assert _revert_files(lib) == before
    assert _digest(lib.video) == _digest(upgrade)
