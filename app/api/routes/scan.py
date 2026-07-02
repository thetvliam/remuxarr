"""
Scan Routes
===========
POST /api/scan/trigger          — kick off a library scan (background)
GET  /api/scan/status           — is a scan running?
POST /api/scan/file             — re-scan + re-queue a single file path
"""
import asyncio
import logging
import threading

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.api.ws_manager import ws_manager
from app.core.scanner import scan_library, queue_single_file, cleanup_deleted_files
from app.core.worker import pause_worker
from app.database.session import SessionLocal, get_app_settings, get_db

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/scan", tags=["scan"])

# Simple flag — only one scan at a time
_scan_running  = False
_scan_progress = {"scanned": 0, "total": 0}  # exposed via GET /status


# ── Request models ─────────────────────────────────────────────────────────────

class ScanRequest(BaseModel):
    paths:       list[str] | None = None   # None → use configured scan_paths
    force_probe: bool = False              # True → full probe (ignores delta)


class FileScanRequest(BaseModel):
    path: str


# ── Endpoints ──────────────────────────────────────────────────────────────────

@router.post("/trigger")
async def trigger_scan(
    body: ScanRequest,
    db: Session = Depends(get_db),
):
    global _scan_running
    if _scan_running:
        raise HTTPException(409, "A scan is already in progress")

    app_cfg = get_app_settings(db)
    paths   = body.paths or app_cfg.get("scan_paths", [])

    if not paths:
        raise HTTPException(
            400,
            "No scan paths configured. Add paths via Settings or pass them in the request body.",
        )

    # Capture the running event loop here, in the async context, so the
    # scan thread can safely schedule WebSocket broadcasts back onto it.
    loop = asyncio.get_running_loop()

    # Launch the scan on a DEDICATED daemon thread rather than via
    # FastAPI's BackgroundTasks (which uses the shared default thread pool).
    # The shared pool is also used by every sync route handler in the app;
    # a long-running scan that blocks on hundreds of ffprobe subprocesses
    # exhausts the pool and causes HTTP requests to hang.  A dedicated thread
    # is completely outside the pool and never competes with route handlers.
    t = threading.Thread(
        target  = _run_scan,
        args    = (paths, body.force_probe, loop),
        name    = "remuxarr-scanner",
        daemon  = True,   # exits automatically if the container shuts down
    )
    t.start()

    return {"status": "started", "paths": paths, "force_probe": body.force_probe}


@router.get("/status")
def scan_status():
    return {
        "running": _scan_running,
        "scanned": _scan_progress["scanned"],
        "total":   _scan_progress["total"],
    }


@router.post("/file")
async def scan_file(body: FileScanRequest, db: Session = Depends(get_db)):
    """
    Re-probe a single file and queue it if needed.
    Useful for manual testing or after fixing a misidentified file.
    """
    if not os.path.isfile(body.path):
        raise HTTPException(400, f"File not found: {body.path}")

    qi = queue_single_file(db, body.path)
    if qi:
        await ws_manager.broadcast_json({
            "event":         "file_queued",
            "file_path":     body.path,
            "queue_item_id": qi.id,
            "reason":        qi.reason,
        })
        return {"queued": True, "queue_item_id": qi.id, "reason": qi.reason}

    return {"queued": False, "reason": "File already meets all criteria or is already queued"}


@router.post("/cleanup")
async def run_cleanup(db: Session = Depends(get_db)):
    """
    Remove database entries for files that no longer exist on disk.
    Scoped to the configured scan_paths — files outside them are never touched.
    Files with an active processing job are skipped.
    """
    app_cfg    = get_app_settings(db)
    scan_paths = app_cfg.get("scan_paths", [])

    if not scan_paths:
        raise HTTPException(
            400,
            "No scan paths configured — nothing to clean up.",
        )

    loop = asyncio.get_running_loop()
    removed = await loop.run_in_executor(
        None, cleanup_deleted_files, db, scan_paths
    )

    await ws_manager.broadcast_json({
        "event":   "cleanup_completed",
        "removed": removed,
    })

    return {"removed": removed}


# ── Background task ────────────────────────────────────────────────────────────

def _run_scan(
    paths:       list[str],
    force_probe: bool,
    loop:        asyncio.AbstractEventLoop,
) -> None:
    """
    Executed by FastAPI's BackgroundTasks in a thread-pool worker.

    We receive the event loop captured in the async endpoint above so that
    asyncio.run_coroutine_threadsafe() can reliably schedule WebSocket
    broadcasts back onto the main event loop from this sync thread.
    """
    global _scan_running, _scan_progress
    _scan_running  = True
    _scan_progress = {"scanned": 0, "total": 0}

    def _broadcast(data: dict) -> None:
        """Fire-and-forget WebSocket broadcast from a sync thread."""
        try:
            asyncio.run_coroutine_threadsafe(
                ws_manager.broadcast_json(data), loop
            )
        except Exception:
            pass   # Never let a broadcast failure abort the scan

    _broadcast({"event": "scan_started"})

    db = SessionLocal()
    try:
        app_cfg = get_app_settings(db)

        # If auto_start_jobs is disabled, pause the worker BEFORE scanning so
        # jobs that get queued won't be picked up automatically.  The user
        # must click Resume on the dashboard when they're ready to process.
        auto_start = app_cfg.get("auto_start_jobs", True)
        if not auto_start:
            pause_worker()

        # Broadcast progress every PROGRESS_INTERVAL files so the UI can
        # show X/Y without flooding the WebSocket on large libraries.
        PROGRESS_INTERVAL = 10
        _last_broadcast: list[int] = [0]  # mutable cell to capture in closure

        def _on_progress(scanned: int, total: int) -> None:
            _scan_progress["scanned"] = scanned
            _scan_progress["total"]   = total
            if scanned - _last_broadcast[0] >= PROGRESS_INTERVAL or scanned == total:
                _last_broadcast[0] = scanned
                _broadcast({
                    "event":   "scan_progress",
                    "scanned": scanned,
                    "total":   total,
                })

        stats = scan_library(db, paths, force_probe=force_probe,
                             progress_callback=_on_progress)
        _broadcast({
            "event":         "scan_completed",
            "queued":        stats.queued,
            "manual_review": stats.manual_review,
            "errors":        stats.errors,
            "total":         stats.total,
            "removed":       stats.removed,
        })
    except Exception:
        logger.exception("Scan failed")
        _broadcast({"event": "scan_completed", "queued": 0,
                    "manual_review": 0, "errors": 1, "total": 0})
    finally:
        db.close()
        _scan_running = False
