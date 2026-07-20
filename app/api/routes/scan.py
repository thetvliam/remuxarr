"""
Scan Routes
===========
POST /api/scan/trigger          — kick off a library scan (background)
GET  /api/scan/status           — is a scan running?
POST /api/scan/file             — re-scan + re-queue a single file path
"""
import asyncio
import logging
import os
import threading

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.api.ws_manager import ws_manager
from app.core.scanner import scan_library, queue_single_file, cleanup_deleted_files, find_orphaned_media_files, remove_orphaned_media_files
from app.core.worker import pause_worker
from app.database.session import SessionLocal, get_app_settings, get_db

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/scan", tags=["scan"])

# Simple flag — only one scan at a time
_scan_running  = False
_scan_progress = {"scanned": 0, "total": 0}  # exposed via GET /status
_scan_cancel_requested = False


# ── Request models ─────────────────────────────────────────────────────────────

class ScanRequest(BaseModel):
    paths:       list[str] | None = None   # None → use configured scan_paths
    force_probe: bool = False              # True → full probe (ignores delta)


class FileScanRequest(BaseModel):
    path: str


class RemoveOrphanedRequest(BaseModel):
    file_ids: list[int]


# ── Endpoints ──────────────────────────────────────────────────────────────────

@router.post("/trigger")
async def trigger_scan(
    body: ScanRequest,
    db: Session = Depends(get_db),
):
    global _scan_running
    if _scan_running:
        raise HTTPException(409, "A scan is already in progress")

    # Set synchronously, immediately, before any await or thread creation —
    # closes a real TOCTOU window: a manual trigger and a scheduled trigger
    # (scheduler.py's _tick) landing in the gap between check and thread
    # start could both pass the check above and start two concurrent scans
    # against the same DB. No threading.Lock needed: both callers are
    # themselves single-threaded asyncio coroutines on the same event loop,
    # and this assignment has no await before or after it — asyncio only
    # switches coroutines at await points, so this check-and-set is already
    # atomic with respect to any other coroutine on the loop.
    #
    # The try/finally below is NOT optional: the first version of this fix
    # set the flag here but had no rollback, so any early exit before the
    # thread actually started — most easily an empty scan_paths list
    # raising the 400 below — left the flag stuck True forever, and every
    # future scan (manual or scheduled) got a 409 until the container
    # restarted. The flag's lifecycle contract: whoever sets it must
    # either hand ownership to a successfully-started scan thread (whose
    # own finally in _run_scan is then the sole reset point) or roll it
    # back themselves on every other exit path, exceptional or not.
    _scan_running = True
    scan_thread_started = False
    try:
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
        scan_thread_started = True
    finally:
        if not scan_thread_started:
            _scan_running = False

    return {"status": "started", "paths": paths, "force_probe": body.force_probe}


@router.get("/status")
def scan_status():
    return {
        "running": _scan_running,
        "scanned": _scan_progress["scanned"],
        "total":   _scan_progress["total"],
    }


@router.post("/cancel")
def cancel_scan():
    """
    Request the currently-running scan to stop.

    Doesn't stop anything immediately — sets a flag the scan loop checks
    once per file, right after that file finishes (see scan_library's
    cancel_check parameter). Whatever's already been processed stays
    exactly as committed; nothing about this can leave a partial or
    corrupt state, since each file is already committed individually as
    the scan goes.
    """
    global _scan_cancel_requested
    if not _scan_running:
        raise HTTPException(400, "No scan is currently running")

    _scan_cancel_requested = True
    return {"cancelling": True}


def _scan_file_sync(path: str):
    """
    Synchronous wrapper for thread-pool execution — mirrors the webhook
    handler's _queue_sync. queue_single_file runs ffprobe (subprocess,
    up to the probe timeout) plus full decision/DB work; on the event
    loop that would freeze WebSocket broadcasts, the worker's progress
    events, and every other async route for its whole duration. Every
    sibling here already offloads its blocking work (cleanup →
    _cleanup_sync, orphan removal → _remove_orphaned_sync, webhooks →
    _queue_sync); this route was the one that didn't. Opens its own
    SessionLocal on the executor thread rather than crossing the
    request-scoped session over a thread boundary. Returns the freshly
    -queried QueueItem (whose id/reason stay readable after the session
    closes because queue_single_file re-queries post-commit) or None.
    """
    db = SessionLocal()
    try:
        return queue_single_file(db, path)
    finally:
        db.close()


@router.post("/file")
async def scan_file(body: FileScanRequest):
    """
    Re-probe a single file and queue it if needed.
    Useful for manual testing or after fixing a misidentified file.

    No Depends(get_db): the blocking probe/queue work runs on the thread
    pool (see _scan_file_sync) with its own session, and the async route
    only awaits the broadcast afterward — so a single manual re-scan can
    no longer stall the event loop for the length of an ffprobe.
    """
    if not os.path.isfile(body.path):
        raise HTTPException(400, f"File not found: {body.path}")

    loop = asyncio.get_running_loop()
    qi = await loop.run_in_executor(None, _scan_file_sync, body.path)
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
        None, _cleanup_sync, scan_paths
    )

    await ws_manager.broadcast_json({
        "event":   "cleanup_completed",
        "removed": removed,
    })

    return {"removed": removed}


def _cleanup_sync(scan_paths: list[str]) -> int:
    """
    Sync wrapper around cleanup_deleted_files, opening/closing its own
    SessionLocal() rather than reusing the request-scoped Session from
    run_cleanup's own Depends(get_db).

    That Session is bound to the request's own lifecycle and isn't
    intended to cross thread boundaries — it happened to work here only
    because of check_same_thread=False, but this was the one place in the
    codebase doing this; every other executor helper (_queue_sync,
    _load_job_data, etc.) already opens its own session for exactly this
    reason. Caught by independent review.
    """
    db = SessionLocal()
    try:
        return cleanup_deleted_files(db, scan_paths)
    finally:
        db.close()


@router.get("/orphaned")
def list_orphaned(db: Session = Depends(get_db)):
    """
    List every MediaFile row whose path falls outside every currently
    configured scan path.

    This is a real gap in the regular cleanup mechanism, not a bug in it —
    cleanup_deleted_files() is deliberately scoped to only ever touch
    scan_paths, so a file scanned under a path that's since been removed
    from configuration becomes permanently invisible to it, regardless of
    whether the underlying file still exists. This surfaces those rows
    directly so they're visible instead of silently accumulating.

    on_disk in the response is informational only — it does not affect
    whether a row is considered orphaned, only whether the file happens
    to still physically exist at that no-longer-configured path.
    """
    app_cfg    = get_app_settings(db)
    scan_paths = app_cfg.get("scan_paths", [])

    orphaned = find_orphaned_media_files(db, scan_paths)
    return {
        "total": len(orphaned),
        "items": [
            {
                "id":       m.id,
                "filename": m.filename,
                "path":     m.path,
                "size":     m.size,
                "on_disk":  os.path.exists(m.path),
            }
            for m in orphaned
        ],
    }


def _remove_orphaned_sync(file_ids: list[int]) -> int:
    """
    Sync wrapper around remove_orphaned_media_files, opening/closing its
    own SessionLocal() on the executor thread — the exact pattern (and
    rationale) of _cleanup_sync above. When that function was fixed, its
    docstring declared it "the one place in the codebase doing this";
    this endpoint was doing the identical thing four routes down —
    passing the request-scoped Depends(get_db) Session into
    run_in_executor — and was missed. It only ever worked because of
    check_same_thread=False. Caught by independent review.
    """
    db = SessionLocal()
    try:
        return remove_orphaned_media_files(db, file_ids)
    finally:
        db.close()


@router.post("/orphaned/remove")
async def remove_orphaned(body: RemoveOrphanedRequest):
    """
    Remove specific orphaned MediaFile rows by ID (and every row across
    the codebase that references them — see
    scanner._delete_media_file_and_related).

    Deliberately does not re-check scan_paths membership or disk
    existence here — the row was already surfaced via GET /orphaned as
    being outside the configured library, which is the only thing that
    matters for this action. Removing the database row never touches
    the actual file on disk, regardless of whether it still exists.

    No Depends(get_db) — all database work happens on the executor
    thread inside _remove_orphaned_sync's own session; a request-scoped
    session here would have nothing to do except tempt the next edit
    into passing it across the thread boundary again.
    """
    if not body.file_ids:
        raise HTTPException(400, "No file IDs provided")

    loop = asyncio.get_running_loop()
    removed = await loop.run_in_executor(None, _remove_orphaned_sync, body.file_ids)
    return {"removed": removed}


# ── Background task ────────────────────────────────────────────────────────────

def _run_scan(
    paths:       list[str],
    force_probe: bool,
    loop:        asyncio.AbstractEventLoop,
) -> None:
    """
    Executed on a dedicated daemon thread (threading.Thread), NOT via
    FastAPI's BackgroundTasks — see trigger_scan's own comment for why:
    BackgroundTasks uses the shared default thread pool, which every sync
    route handler also draws from, and a long-running scan blocking on
    hundreds of ffprobe subprocesses would exhaust it and hang unrelated
    HTTP requests. A dedicated thread stays completely outside that pool.

    We receive the event loop captured in the async endpoint above so that
    asyncio.run_coroutine_threadsafe() can reliably schedule WebSocket
    broadcasts back onto the main event loop from this sync thread.
    """
    global _scan_running, _scan_progress, _scan_cancel_requested
    # NOTE: _scan_running is deliberately NOT set here. Both launchers
    # (trigger_scan above, and scheduler._tick) set it synchronously
    # BEFORE starting this thread — that ordering is what closes the
    # double-start race — and each rolls it back itself on any exit where
    # this thread never actually started. Once this thread IS running, the
    # finally at the bottom of this function is the sole reset point. A
    # re-set here would be harmless but would blur that single-owner
    # lifecycle; anyone adding a new launcher must follow the same
    # set-before-start + rollback-on-failure contract.
    _scan_progress = {"scanned": 0, "total": 0}
    _scan_cancel_requested = False

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

        def _check_cancel() -> bool:
            return _scan_cancel_requested

        stats = scan_library(db, paths, force_probe=force_probe,
                             progress_callback=_on_progress,
                             cancel_check=_check_cancel)
        _broadcast({
            "event":         "scan_completed",
            "queued":        stats.queued,
            "manual_review": stats.manual_review,
            "errors":        stats.errors,
            "total":         stats.total,
            "removed":       stats.removed,
            "cancelled":     stats.cancelled,
        })
    except Exception:
        logger.exception("Scan failed")
        _broadcast({"event": "scan_completed", "queued": 0,
                    "manual_review": 0, "errors": 1, "total": 0})
    finally:
        db.close()
        _scan_running = False
