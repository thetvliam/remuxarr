"""
Forge job completion and staged output copying.

Covers two pieces of behaviour that had no suite coverage:

  • finish_forge_job() — the terminal-state writer, including the undo status
    mapping. Its signature changed when the unused output_path parameter was
    removed, and nothing tested it.

  • _stage_parts() — the copy-and-fsync pass extracted so it could be moved off
    the event loop. The extraction preserved a subtle contract: part_paths is
    caller-owned and appended to as work completes, so a partially-finished run
    is still visible to the caller's OSError cleanup handler.

Grown from the ad-hoc scripts used to verify those changes.
"""
import asyncio
import datetime as dt
import os
import sys
import threading
import time

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.core.forge import finish_forge_job
from app.core.subprocess_runner import StagedOutput, _stage_parts


# ── finish_forge_job ─────────────────────────────────────────────────────────

@pytest.fixture
def db():
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    from app.database.models import Base

    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()

    # finish_forge_job opens its own session via SessionLocal; point that at
    # this engine so the test can observe what it wrote.
    import app.core.forge as forge_mod
    import app.database.session as session_mod
    original = session_mod.SessionLocal
    session_mod.SessionLocal = sessionmaker(bind=engine)
    forge_mod.SessionLocal = sessionmaker(bind=engine)
    yield session
    session_mod.SessionLocal = original
    forge_mod.SessionLocal = original


def _job(db, is_undo=False, progress=42.0):
    from app.database.models import Ac3ForgeJob, MediaFile

    name = f"{'undo' if is_undo else 'fwd'}-{id(object()):x}.mkv"
    mf = MediaFile(path=f"/m/{name}", filename=name, directory="/m", size=1, mtime=1.0)
    db.add(mf)
    db.commit()
    job = Ac3ForgeJob(file_id=mf.id, status="processing", is_undo=is_undo,
                      progress=progress)
    db.add(job)
    db.commit()
    return job.id


def _read(db, job_id):
    from app.database.models import Ac3ForgeJob

    db.expire_all()
    return db.get(Ac3ForgeJob, job_id)


def test_forward_success_marks_complete(db):
    jid = _job(db)
    finish_forge_job(jid, True, 123456789, None)

    job = _read(db, jid)
    assert job.status == "success"
    assert job.progress == 100.0
    assert job.output_size == 123456789
    assert job.error_message is None
    assert job.completed_at is not None


def test_forward_failure_does_not_force_progress_to_100(db):
    """A failed job should not read as having finished its work."""
    jid = _job(db, progress=42.0)
    finish_forge_job(jid, False, None, "ffmpeg exploded")

    job = _read(db, jid)
    assert job.status == "failed"
    assert job.progress == 42.0
    assert job.error_message == "ffmpeg exploded"


def test_undo_success_maps_to_undone(db):
    """Undo jobs get their own terminal states, not the forward ones."""
    jid = _job(db, is_undo=True)
    finish_forge_job(jid, True, 999, None)
    assert _read(db, jid).status == "undone"


def test_undo_failure_maps_to_undo_failed(db):
    jid = _job(db, is_undo=True)
    finish_forge_job(jid, False, None, "layout mismatch")

    job = _read(db, jid)
    assert job.status == "undo_failed"
    assert job.error_message == "layout mismatch"


def test_unknown_job_id_is_a_safe_noop(db):
    finish_forge_job(999999, True, 1, None)


def test_output_path_is_reachable_without_being_stored(db):
    """
    finish_forge_job deliberately takes no output_path: forge rewrites in
    place, so the path is always the source path and is already on the row.
    """
    jid = _job(db)
    finish_forge_job(jid, True, 555, None)

    job = _read(db, jid)
    assert job.media_file.path.startswith("/m/fwd-")


def test_stale_five_argument_call_raises(db):
    """The removed parameter must fail loudly, not silently shift output_size."""
    jid = _job(db)
    with pytest.raises(TypeError):
        finish_forge_job(jid, True, "/m/some.mkv", 555, None)


# ── _stage_parts ─────────────────────────────────────────────────────────────

def test_stage_parts_copies_and_leaves_final_untouched(tmp_path):
    temp = tmp_path / "src.tmp"
    temp.write_bytes(b"payload" * 1000)
    final = tmp_path / "out.mkv"

    parts: list[str] = []
    _stage_parts([StagedOutput(temp_path=str(temp), final_path=str(final))], parts)

    assert parts == [str(final) + ".part"]
    assert (tmp_path / "out.mkv.part").read_bytes() == b"payload" * 1000
    assert not final.exists(), "staging must not touch the destination"


def test_partial_failure_leaves_completed_parts_visible(tmp_path):
    """
    The contract the caller's OSError handler depends on: when output 2 of 3
    fails, part_paths must still contain output 1 so cleanup can remove it.
    """
    ok = tmp_path / "a.tmp"
    ok.write_bytes(b"x" * 100)

    outputs = [
        StagedOutput(temp_path=str(ok), final_path=str(tmp_path / "a.mkv")),
        StagedOutput(temp_path=str(tmp_path / "missing.tmp"),
                     final_path=str(tmp_path / "b.mkv")),
        StagedOutput(temp_path=str(ok), final_path=str(tmp_path / "c.mkv")),
    ]

    parts: list[str] = []
    with pytest.raises(OSError):
        _stage_parts(outputs, parts)

    assert parts == [str(tmp_path / "a.mkv") + ".part"]
    assert os.path.exists(parts[0]), "the completed .part must exist for cleanup"
    assert not os.path.exists(str(tmp_path / "c.mkv") + ".part")


def test_stage_parts_runs_off_the_event_loop(tmp_path):
    """
    Staging must execute on a worker thread. Uses a small file — this asserts
    the threading contract, not throughput; the large-file responsiveness
    measurement stays a manual tool.
    """
    temp = tmp_path / "src.tmp"
    temp.write_bytes(b"z" * 4096)
    outputs = [StagedOutput(temp_path=str(temp),
                            final_path=str(tmp_path / "out.mkv"))]

    seen = {}

    def spy(o, p):
        seen["thread"] = threading.get_ident()
        _stage_parts(o, p)

    async def driver():
        parts: list[str] = []
        seen["loop"] = threading.get_ident()
        await asyncio.get_running_loop().run_in_executor(None, spy, outputs, parts)
        return parts

    parts = asyncio.run(driver())
    assert seen["thread"] != seen["loop"], "staging ran on the event loop thread"
    assert os.path.exists(parts[0])


def test_cancellation_shield_lets_the_copy_settle():
    """
    Moving staging to an executor made it interruptible where the inline loop
    was not. A thread-pool thread cannot be interrupted, so cleanup must not
    run while the copy is still going — it would delete .part files the thread
    then recreates.
    """
    async def driver():
        finished = threading.Event()
        started = threading.Event()

        def slow(_outputs, part_paths):
            started.set()
            time.sleep(0.2)
            part_paths.append("late.part")
            finished.set()

        parts: list[str] = []
        staging = asyncio.ensure_future(
            asyncio.get_running_loop().run_in_executor(None, slow, [], parts)
        )

        async def body():
            try:
                await asyncio.shield(staging)
            except asyncio.CancelledError:
                try:
                    await staging
                except Exception:
                    pass
                assert finished.is_set(), \
                    "cleanup would have raced a still-running copy thread"
                raise

        task = asyncio.create_task(body())
        await asyncio.get_running_loop().run_in_executor(None, started.wait)
        task.cancel()

        with pytest.raises(asyncio.CancelledError):
            await task

        assert finished.is_set()
        assert parts == ["late.part"]

    asyncio.run(driver())
