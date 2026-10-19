"""
An AC3 Forge run in progress when the app stops.

stop_worker stopped the claiming loop and the queue jobs, and nothing else.
The forge run was a local of the loop, so it carried on until the event
loop's own teardown cancelled it, after the lifespan had returned. FFmpeg
was killed and the temps went, but nothing updated the row, and the next
start failed it: "Interrupted by container restart or crash. The file may
or may not have been modified". Reproduced with real FFmpeg under
asyncio.run, as uvicorn runs the app, before this file existed; the file
was untouched.

stop_worker now cancels the forge run with the jobs and waits for it in
the same grace period. Cut off before it replaced the file, it goes back
to "pending", or "undo_pending" for an undo, and a revert point captured
for it is deleted. Cut off after, it records its outcome first, as a job
does: put back in the queue, an add that had already happened would run
again and append a second AC3.

Each test runs the real worker loop, start_worker to stop_worker, with
real FFmpeg, and holds the run at one point while the stop arrives.

Verified by mutation, 6 applied, 6 killed, each surviving the suite as it
stood (2037 tests) first:

  • stop_worker not stopping the forge run   → while FFmpeg runs (both),
                                               during the copy
  • no requeue when cut off                  → while FFmpeg runs (both),
                                               during the copy
  • an undo requeued as a plain add          → while FFmpeg runs (undo),
                                               during the copy
  • the requeue ignoring a finished row      → after the swap
  • the captured revert point kept           → during the copy
  • the bookkeeping after the swap awaited
    without the shield                       → after the swap (both)
"""
import asyncio
import hashlib
import json
import os
import shutil
import subprocess
import threading
from types import SimpleNamespace

import pytest


pytestmark = pytest.mark.skipif(
    shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None,
    reason="ffmpeg/ffprobe not available",
)

SECONDS = 10    # with -re, how long FFmpeg runs for


@pytest.fixture
def lib(tmp_path, monkeypatch):
    """Movie.mkv, ten seconds with one English AAC 5.1 track, scanned, with
    nothing for the queue to do and a forge add waiting."""
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    import app.core.forge as forge
    import app.core.worker as worker
    import app.database.session as session_module
    from app.config import settings as app_settings
    from app.core import scanner
    from app.database.models import AppSetting, Base, MediaFile, QueueItem

    engine = create_engine(f"sqlite:///{tmp_path / 'remuxarr.db'}",
                           connect_args={"check_same_thread": False, "timeout": 30})
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine)
    for module in (worker, forge, session_module):
        monkeypatch.setattr(module, "SessionLocal", factory)
    monkeypatch.setattr(app_settings, "TEMP_DIR", str(tmp_path / "tmp"))
    monkeypatch.setattr(app_settings, "RECYCLE_DIR", str(tmp_path / "recycle"))
    (tmp_path / "recycle").mkdir()
    monkeypatch.setattr(worker, "_pick_temp_dir", lambda _p: str(tmp_path))
    monkeypatch.setattr(worker, "_load_forge_plex_notify_data", lambda _id: None)
    monkeypatch.setattr(worker, "_forge_task", None)
    monkeypatch.setattr(worker, "_active_task_registry", {})

    library = tmp_path / "library"
    library.mkdir()
    video = library / "Movie.mkv"
    subprocess.run([
        "ffmpeg", "-v", "error", "-y",
        "-f", "lavfi", "-i", f"testsrc=size=64x36:rate=5:duration={SECONDS}",
        "-f", "lavfi", "-i", f"sine=frequency=440:duration={SECONDS}",
        "-map", "0:v", "-map", "1:a", "-ac", "6", "-metadata:s:a:0", "language=eng",
        "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-b:a", "64k", "-f", "matroska", str(video)], check=True)

    with factory() as db:
        for key, value in [("dry_run_mode", False), ("prefer_mp4_container", False),
                           ("auto_start_jobs", True)]:
            db.merge(AppSetting(key=key, value=json.dumps(value)))
        db.commit()
        scanner.scan_library(db, [str(library)], force_probe=True)
        assert db.query(QueueItem).filter(QueueItem.status == "pending").count() == 0, (
            "fixture gives the queue a job to run beside the forge")
        job_id = forge.queue_forge_job(db, db.query(MediaFile).one().id).id

    yield SimpleNamespace(Session=factory, worker=worker, forge=forge, video=video,
                          job_id=job_id, temp=tmp_path / f"forge_{job_id}.forge_tmp",
                          recycle=tmp_path / "recycle", tmp_path=tmp_path)
    engine.dispose()


def _set(lib, key, value):
    from app.database.models import AppSetting

    with lib.Session() as db:
        db.merge(AppSetting(key=key, value=json.dumps(value)))
        db.commit()


def _job(lib):
    from app.database.models import Ac3ForgeJob

    with lib.Session() as db:
        job = db.get(Ac3ForgeJob, lib.job_id)
        return SimpleNamespace(status=job.status, progress=job.progress,
                               started_at=job.started_at, error=job.error_message)


def _audio(path):
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "a",
         "-show_entries", "stream=codec_name", "-of", "json", str(path)],
        capture_output=True, text=True, check=True)
    return [s["codec_name"] for s in json.loads(out.stdout)["streams"]]


def _digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _ffmpeg_writing(path):
    """Processes whose command line names `path`."""
    found = []
    for pid in filter(str.isdigit, os.listdir("/proc")):
        try:
            with open(f"/proc/{pid}/cmdline", "rb") as f:
                args = f.read().split(b"\0")
        except OSError:
            continue
        if str(path).encode() in args:
            found.append(pid)
    return found


def _revert_files(lib):
    from app.database.models import RevertPoint

    with lib.Session() as db:
        rows = db.query(RevertPoint).count()
    return rows, sorted(p.name for p in lib.recycle.rglob("*") if p.is_file())


async def _until(condition, what, timeout=30.0):
    for _ in range(int(timeout / 0.05)):
        if condition():
            return
        await asyncio.sleep(0.05)
    raise AssertionError(f"timed out waiting for {what}")


def _forged_then_undo(lib):
    """A real forge add, then the undo the undo route would queue for it."""
    from app.database.models import Ac3ForgeJob

    assert asyncio.run(lib.worker._process_next_forge(_FakeWS())) is True
    assert _job(lib).status == "success"
    assert _audio(lib.video) == ["aac", "ac3"]
    with lib.Session() as db:
        job = db.get(Ac3ForgeJob, lib.job_id)
        job.status, job.is_undo, job.progress = "undo_pending", True, 0.0
        db.commit()


def _real_time(lib, monkeypatch):
    """FFmpeg reads its input at the speed it plays (-re), so the run lasts
    SECONDS rather than a fraction of one."""
    for name in ("build_add_ac3_command", "build_undo_command"):
        real = getattr(lib.forge, name)
        monkeypatch.setattr(lib.forge, name,
                            lambda *a, _real=real, **k: [(c := _real(*a, **k))[0], "-re", *c[1:]])


@pytest.mark.parametrize("undo", [False, True], ids=["add", "undo"])
def test_a_run_stopped_while_ffmpeg_runs_goes_back_in_the_queue(lib, monkeypatch, undo):
    expected = "pending"
    if undo:
        _forged_then_undo(lib)
        expected = "undo_pending"
    _real_time(lib, monkeypatch)
    before = _digest(lib.video)

    async def go():
        await lib.worker.start_worker()
        await _until(lambda: _job(lib).progress > 0, "FFmpeg to report progress")
        assert _ffmpeg_writing(lib.temp), "FFmpeg is not running"
        await lib.worker.stop_worker()
        # Checked before the event loop's own teardown, which used to be
        # the only thing that stopped the run.
        return _job(lib), _ffmpeg_writing(lib.temp)

    job, still_running = asyncio.run(go())

    # Not the progress figure: a progress write already on its way to the
    # database can land after the requeue, as it can for a queue job.
    assert (job.status, job.started_at) == (expected, None)
    assert still_running == []
    assert not lib.temp.exists()
    assert _digest(lib.video) == before


def test_a_run_stopped_during_the_copy_drops_its_revert_point(lib, monkeypatch):
    """
    Held at the first progress update of the copy into place: FFmpeg has
    finished and the revert point for its output has been captured. An
    undo, because an add loses nothing and so captures nothing; the AC3 an
    undo removes is in the file it was handed, which with no earlier point
    counts as the original.
    """
    from app.api.ws_manager import ws_manager
    from app.core.subprocess_runner import STAGING_ACTION

    _forged_then_undo(lib)
    _set(lib, "revert_enabled", True)
    before = _digest(lib.video)
    copying = threading.Event()
    seen = {}

    async def broadcast(message):
        if message.get("current_action") == STAGING_ACTION:
            seen["revert"] = _revert_files(lib)
            copying.set()
            await asyncio.Event().wait()        # until the stop cancels it
    monkeypatch.setattr(ws_manager, "broadcast_json", broadcast)

    async def go():
        await lib.worker.start_worker()
        await _until(copying.is_set, "the copy to start")
        await lib.worker.stop_worker()
        return _job(lib)

    job = asyncio.run(go())

    assert seen["revert"][1], "fixture changed: nothing was captured to drop"
    assert job.status == "undo_pending"
    assert _revert_files(lib) == (0, [])
    assert _digest(lib.video) == before


@pytest.mark.parametrize("undo", [False, True], ids=["add", "undo"])
def test_a_run_stopped_after_the_swap_is_recorded_as_done(lib, monkeypatch, undo):
    """
    Held in the track re-read: the file has been rewritten and, for the
    undo, its revert point recorded, and the job is not yet marked
    finished. The add is the case that matters most, as one run again
    appends a second AC3; the undo is the one that records a revert point.
    """
    from app.database.models import Track

    if undo:
        _forged_then_undo(lib)
    _set(lib, "revert_enabled", True)
    reached, release = threading.Event(), threading.Event()
    real_probe = lib.forge.probe_tracks_after_forge

    def held_probe(path):
        reached.set()
        release.wait(30)
        return real_probe(path)
    monkeypatch.setattr(lib.forge, "probe_tracks_after_forge", held_probe)

    async def go():
        await lib.worker.start_worker()
        await _until(reached.is_set, "the track re-read")
        stop = asyncio.create_task(lib.worker.stop_worker())
        await asyncio.sleep(0.5)                # the cancellation has landed
        held = (_job(lib).status, stop.done())
        release.set()
        await stop
        return held

    held = asyncio.run(go())

    assert held == ("processing", False), (
        "the stop did not wait for the run to record what it had done")
    audio = ["aac"] if undo else ["aac", "ac3"]
    assert _job(lib).status == ("undone" if undo else "success")
    assert _audio(lib.video) == audio
    with lib.Session() as db:
        assert sorted(t.codec for t in db.query(Track)
                      .filter(Track.track_type == "audio")) == audio
    rows, files = _revert_files(lib)
    assert (rows, bool(files)) == ((1, True) if undo else (0, False))


class _FakeWS:
    async def broadcast_json(self, *args, **kwargs):
        pass
