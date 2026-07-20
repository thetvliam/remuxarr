"""
Regression tests for the Phase 1 review fixes (three genuine bugs).

Claim 1 — POST /api/scan/file blocked the event loop:
    scan_file was `async def` but called queue_single_file (ffprobe via
    subprocess + full decision/DB work) directly on the loop, freezing
    every other async task for its duration. It now offloads to the
    thread pool via run_in_executor with its own session, like its
    webhook/cleanup/orphan siblings. The test proves the blocking work
    runs OFF the event-loop thread.

Claim 2 — cancelled items had no completed_at:
    cancel_item and clear_pending set status="cancelled" without
    stamping completed_at, so they sank to the bottom of the
    completed_at-DESC history (NULLs last in SQLite) with "—"
    timestamps — the exact bug already fixed for skipped rows.

Claim 3 — abort_job didn't reset the delta-scan sentinels:
    every other cancel path resets MediaFile.size/mtime to -1/-1.0 so
    the file resurfaces on the next delta scan; abort_job set status but
    left the sentinels, stranding the aborted file until a forced full
    rescan.

Run from the project root:
    pytest tests/test_phase1_fixes.py -v
"""
import asyncio
import os
import sys
import threading
from types import SimpleNamespace

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))


def _fresh_db():
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    from app.database.models import Base

    engine = create_engine("sqlite://", connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)()


# ═══════════════════════════════════════════════════════════════════════════
# Claim 1 — scan_file offloads blocking work to the thread pool
# ═══════════════════════════════════════════════════════════════════════════

def test_scan_file_runs_blocking_work_off_the_event_loop(tmp_path, monkeypatch):
    """
    The blocking queue_single_file call must execute on a thread-pool
    thread, not the event-loop (main) thread. If it ran on the loop, the
    recorded thread would equal MainThread — which is the freeze this fix
    removes.
    """
    import app.api.routes.scan as scan

    real_file = tmp_path / "movie.mkv"
    real_file.write_text("x")

    recorded = {}

    def fake_queue_single_file(db, path):
        recorded["thread"] = threading.current_thread()
        return SimpleNamespace(id=42, reason="queued for test")

    async def fake_broadcast(payload):
        recorded["broadcast"] = payload

    # _scan_file_sync calls queue_single_file as a module global; patch it there.
    monkeypatch.setattr(scan, "queue_single_file", fake_queue_single_file)
    monkeypatch.setattr(scan.ws_manager, "broadcast_json", fake_broadcast)

    async def run():
        result = await scan.scan_file(SimpleNamespace(path=str(real_file)))
        return result, threading.current_thread()

    result, loop_thread = asyncio.run(run())

    assert result == {"queued": True, "queue_item_id": 42, "reason": "queued for test"}
    assert "thread" in recorded, "queue_single_file was never called"
    assert recorded["thread"] is not loop_thread, (
        "queue_single_file ran on the event-loop thread — it is still "
        "blocking the loop (claim 1)."
    )
    assert recorded["thread"] is not threading.main_thread()
    assert recorded["broadcast"]["queue_item_id"] == 42


def test_scan_file_reports_not_queued_when_nothing_to_do(tmp_path, monkeypatch):
    """When queue_single_file returns None (file already compliant), the
    route reports queued=False and broadcasts nothing."""
    import app.api.routes.scan as scan

    real_file = tmp_path / "movie.mkv"
    real_file.write_text("x")
    seen = {}

    monkeypatch.setattr(scan, "queue_single_file", lambda db, path: None)

    async def fake_broadcast(payload):
        seen["broadcast"] = True
    monkeypatch.setattr(scan.ws_manager, "broadcast_json", fake_broadcast)

    result = asyncio.run(scan.scan_file(SimpleNamespace(path=str(real_file))))
    assert result["queued"] is False
    assert "broadcast" not in seen


def test_scan_file_404s_on_missing_file():
    from fastapi import HTTPException

    import app.api.routes.scan as scan
    try:
        asyncio.run(scan.scan_file(SimpleNamespace(path="/does/not/exist.mkv")))
        assert False, "expected HTTPException for missing file"
    except HTTPException as e:
        assert e.status_code == 400


# ═══════════════════════════════════════════════════════════════════════════
# Claim 2 — cancel_item / clear_pending stamp completed_at
# ═══════════════════════════════════════════════════════════════════════════

def _seed_pending(db, item_id, file_id):
    from app.database.models import MediaFile, QueueItem
    if db.get(MediaFile, file_id) is None:
        db.add(MediaFile(id=file_id, path=f"/m/{file_id}.mkv", filename=f"{file_id}.mkv",
                         directory="/m", size=100, mtime=1.0, status="queued"))
    db.add(QueueItem(id=item_id, file_id=file_id, status="pending"))
    db.commit()


def test_cancel_item_stamps_completed_at():
    from app.api.routes.queue import cancel_item
    from app.database.models import QueueItem

    db = _fresh_db()
    _seed_pending(db, 1, 1)
    assert db.get(QueueItem, 1).completed_at is None

    cancel_item(1, db)

    item = db.get(QueueItem, 1)
    assert item.status == "cancelled"
    assert item.completed_at is not None, (
        "cancel_item left completed_at NULL — the row will sink to the "
        "bottom of the Failed tab with a '—' timestamp (claim 2)."
    )


def test_clear_pending_stamps_completed_at_on_all():
    from app.api.routes.queue import clear_pending
    from app.database.models import QueueItem

    db = _fresh_db()
    _seed_pending(db, 1, 1)
    _seed_pending(db, 2, 2)

    result = clear_pending(db)
    assert result == {"cancelled": 2}

    for iid in (1, 2):
        item = db.get(QueueItem, iid)
        assert item.status == "cancelled"
        assert item.completed_at is not None, (
            f"clear_pending left completed_at NULL on item {iid} (claim 2)."
        )


# ═══════════════════════════════════════════════════════════════════════════
# Claim 3 — abort_job resets the delta-scan sentinels
# ═══════════════════════════════════════════════════════════════════════════

def test_abort_job_resets_delta_sentinels():
    """
    A successful abort must reset MediaFile.size/mtime to the sentinels
    (like every other cancel path) so the aborted file is re-evaluated
    by the next delta scan, not stranded until a forced full rescan.

    abort_job uses the module-level SessionLocal, so this seeds and
    asserts through that same session and registers a live task in the
    active-task registry to satisfy the "is it actually running?" gate.
    """
    import app.core.worker as worker
    from app.database.models import Base, MediaFile, QueueItem
    from app.database.session import SessionLocal, engine

    Base.metadata.create_all(engine)

    async def run():
        db = SessionLocal()
        try:
            db.query(QueueItem).delete()
            db.query(MediaFile).delete()
            db.add(MediaFile(id=1, path="/media/abort.mkv", filename="abort.mkv",
                             directory="/media", size=987654, mtime=555.5,
                             status="processing"))
            db.add(QueueItem(id=1, file_id=1, status="processing"))
            db.commit()
        finally:
            db.close()

        # A real, not-yet-done task so abort_job's registry gate passes.
        task = asyncio.create_task(asyncio.sleep(30))
        worker._active_task_registry[1] = task
        try:
            result = worker.abort_job(1)
        finally:
            worker._active_task_registry.pop(1, None)
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
            worker.resume_worker()  # undo pause_worker() so other tests are unaffected

        assert result is True, "abort_job should report the task was cancelled"

        db = SessionLocal()
        try:
            m = db.get(MediaFile, 1)
            assert m.status == "skipped"
            assert (m.size, m.mtime) == (-1, -1.0), (
                f"abort_job left sentinels at ({m.size}, {m.mtime}) — the "
                "aborted file is invisible to delta scans (claim 3)."
            )
            job = db.get(QueueItem, 1)
            assert job.status == "cancelled"
            assert job.completed_at is not None  # abort_job already stamped this
        finally:
            # clean up so the shared file DB doesn't leak into other tests
            db.query(QueueItem).delete()
            db.query(MediaFile).delete()
            db.commit()
            db.close()

    asyncio.run(run())
