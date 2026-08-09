"""
Remuxarr — FastAPI application entry point.
"""
import asyncio
import glob
import logging
import os
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from app.config import settings
from app.core.scheduler import run_scheduler, run_plex_backlog_drain
from app.core.worker import start_worker, stop_worker
from app.database.session import init_db

logging.basicConfig(
    level=logging.DEBUG if settings.DEBUG else logging.INFO,
    format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger(__name__)

# ── In-memory log handler (for the UI log viewer) ──────────────────────────
# Registered here, immediately after basicConfig, so it captures all logs
# from startup onwards.  uvicorn.access is excluded to prevent every call
# to GET /api/logs from creating its own log entry (infinite noise loop).

from app.core.log_handler import get_handler as _get_log_handler

class _NoUvicornAccess(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        return record.name != "uvicorn.access"

_mem_handler = _get_log_handler()
_mem_handler.setLevel(logging.INFO)
_mem_handler.setFormatter(logging.Formatter("%(message)s"))
_mem_handler.addFilter(_NoUvicornAccess())
logging.getLogger().addHandler(_mem_handler)

# Resolve the frontend build directory (relative to this file)
FRONTEND_DIR = Path(__file__).parent.parent / "frontend" / "dist"
FRONTEND_DEV  = Path(__file__).parent.parent / "frontend"


# ── Background task registry ──────────────────────────────────────────────────
# asyncio holds only a WEAK reference to a running task. A task with no other
# referent can therefore be garbage-collected mid-await — silently, with no
# exception raised and nothing written to the log. Both long-lived services
# below (the scan scheduler and the Plex backlog drain) were previously
# created with a bare asyncio.create_task() whose return value was discarded,
# which is exactly that situation: the failure mode is "scheduled scans just
# stopped happening at some point and nothing says why".
#
# Holding a strong reference for the task's lifetime removes the possibility.
# The done-callback discards it again so this set tracks only live tasks
# rather than growing for the life of the process.

_background_tasks: set[asyncio.Task] = set()


def _on_task_done(task: asyncio.Task) -> None:
    """
    Drop a finished task from the registry, and report it if it died.

    The discard alone was not enough. These are perpetual services: if
    run_scheduler() raises, the task completes, the callback removes it, and
    nothing ever retrieves the exception — asyncio then emits "Task exception
    was never retrieved" at some arbitrary later garbage-collection, detached
    from the moment of failure and easy to dismiss as noise.

    That is the same class of bug the strong reference above fixes. Holding a
    reference stops a service vanishing silently; retrieving the exception here
    stops it *dying* silently. A crashed scheduler now logs, with a traceback,
    at the instant it crashes.
    """
    _background_tasks.discard(task)
    if task.cancelled():
        return                      # expected during shutdown
    exc = task.exception()
    if exc is not None:
        logger.error(
            "Background task %s died and will not restart", task.get_name(),
            exc_info=exc,
        )


def _spawn(coro, name: str) -> asyncio.Task:
    """Start a long-lived background task and keep it referenced."""
    task = asyncio.create_task(coro, name=name)
    _background_tasks.add(task)
    task.add_done_callback(_on_task_done)
    return task


async def _cancel_background_tasks() -> None:
    """
    Cancel every registered background task and wait for it to unwind.

    Uses gather(return_exceptions=True) rather than awaiting each task in a
    try/except. The distinction matters: awaiting individually and catching
    CancelledError cannot tell "the task I just cancelled has unwound" from
    "this shutdown coroutine is itself being cancelled", and swallows both.
    gather returns a child's cancellation as a result object while still
    letting a cancellation aimed at *us* propagate, so shutdown stays
    interruptible.

    The list() snapshot is load-bearing twice over — the done-callback discards
    from _background_tasks as each task finishes, so the set mutates across the
    await, and the snapshot is what lets results be paired back to task names
    for logging.

    Previously these tasks were never cancelled at all — the lifespan stopped
    the worker and returned, leaving both services to be torn down with the
    loop. Cancelling explicitly means their own cleanup paths (the `finally`
    blocks in scheduler.py) actually run.
    """
    tasks = list(_background_tasks)
    for task in tasks:
        if not task.done():
            task.cancel()

    results = await asyncio.gather(*tasks, return_exceptions=True)

    for task, result in zip(tasks, results):
        # CancelledError is the expected outcome here, not a failure.
        if isinstance(result, BaseException) and not isinstance(result, asyncio.CancelledError):
            # exc_info=result, not logger.exception(): there is no active
            # exception outside an except block, so exception() would log
            # "NoneType: None" where the traceback should be.
            logger.error(
                "Background task %s raised during shutdown", task.get_name(),
                exc_info=result,
            )


# ── Lifespan ───────────────────────────────────────────────────────────────────

@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("━━━ %s starting ━━━", settings.APP_NAME)
    init_db()
    _cleanup_orphaned_temp_files()
    await start_worker()
    from app.api.ws_manager import ws_manager
    _spawn(run_scheduler(ws_manager), name="remuxarr-scheduler")
    _spawn(run_plex_backlog_drain(), name="remuxarr-plex-backlog-drain")
    yield
    await stop_worker()
    await _cancel_background_tasks()
    logger.info("━━━ %s stopped ━━━", settings.APP_NAME)


def _cleanup_orphaned_temp_files() -> None:
    """
    Remove work-in-progress files left behind by jobs that were interrupted
    mid-stream (container restart, SIGKILL, 'No space left on device'
    failures, thread-pool starvation kills, etc.).

    Two locations, both necessary:

    1. TEMP_DIR — *.remuxarr_tmp and *.forge_tmp. On Unraid this is tmpfs, so
       orphans consume RAM and eventually cause 'No space left on device' for
       later jobs even when the array has plenty of space.

    2. The configured scan_paths — *.part plus the same two temp suffixes.
       This sweep was missing entirely, and it covers the cases most likely to
       accumulate large files:

         • _stage_parts() writes "<final_path>.part" NEXT TO THE TARGET, i.e.
           inside the media library. A crash during the copy leaves a
           multi-gigabyte "Movie.mkv.part" there permanently. Nothing surfaces
           it: ".part" is not in MEDIA_EXTENSIONS, so the scanner skips it.

         • _pick_temp_dir() falls back to os.path.dirname(reference_path) when
           TEMP_DIR is short on space — again the media directory. That
           fallback fires precisely when disk is already tight, which is
           exactly when leaked temps hurt most.

    Deleting a .part is safe: it is a staged copy that has not yet been
    os.replace()'d into position, so the original file is still intact and
    nothing is lost by removing it.

    Called from lifespan BEFORE start_worker(), so none of this can race a job
    of our own. The mtime guard below covers the one case ordering does not:
    a second instance pointed at the same library, mid-copy right now.
    """
    import time

    temp_dir = settings.TEMP_DIR
    # A .part being actively written by another process would be recently
    # modified. Anything older than this is not in flight.
    MIN_AGE_SECONDS = 300
    now = time.time()

    def _remove(paths: list[str], label: str) -> tuple[int, int]:
        removed = total = 0
        for f in paths:
            try:
                st = os.stat(f)
                if now - st.st_mtime < MIN_AGE_SECONDS:
                    logger.info(
                        "Skipping recently-modified orphan %s (%.0fs old) — it may "
                        "belong to another running instance", f, now - st.st_mtime,
                    )
                    continue
                os.remove(f)
                removed += 1
                total += st.st_size
                logger.debug("Removed orphaned %s file: %s", label, f)
            except OSError as exc:
                logger.warning("Could not remove orphaned file %s: %s", f, exc)
        return removed, total

    try:
        found: set[str] = set()

        # 1. The temp directory (flat — nothing nests there).
        for pattern in ("*.remuxarr_tmp", "*.forge_tmp"):
            found.update(glob.glob(os.path.join(temp_dir, pattern)))

        # 2. The media library. Walked rather than globbed because libraries
        #    nest arbitrarily. followlinks stays at its default of False: a
        #    symlink cycle inside a library would otherwise loop forever, and
        #    a symlinked directory is not somewhere this should be deleting.
        try:
            from app.database.session import SessionLocal, get_app_settings

            with SessionLocal() as db:
                scan_paths = get_app_settings(db).get("scan_paths") or []
        except Exception as exc:
            scan_paths = []
            logger.warning("Could not read scan_paths for orphan cleanup: %s", exc)

        suffixes = (".part", ".remuxarr_tmp", ".forge_tmp")
        for root_path in scan_paths:
            if not root_path or not os.path.isdir(root_path):
                continue
            for dirpath, _dirnames, filenames in os.walk(root_path):
                for name in filenames:
                    if name.endswith(suffixes):
                        found.add(os.path.join(dirpath, name))

        if not found:
            return

        removed, total_bytes = _remove(sorted(found), "temp/part")
        if removed:
            logger.info(
                "Startup cleanup: removed %d orphaned file(s), %.1f MB freed",
                removed, total_bytes / 1024 / 1024,
            )
    except Exception as exc:
        logger.warning("Orphaned temp file cleanup failed: %s", exc)


# ── App factory ────────────────────────────────────────────────────────────────

app = FastAPI(
    title       = "Remuxarr",
    description = "Automatic media remuxer — strip tracks, fix audio, convert containers.",
    version     = "0.1.0",
    lifespan    = lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins     = ["*"],   # tighten via reverse-proxy in production
    allow_credentials = True,
    allow_methods     = ["*"],
    allow_headers     = ["*"],
)

# ── Routers ────────────────────────────────────────────────────────────────────

from app.api.routes import queue, history, webhooks, settings as settings_routes, scan, forge, worker as worker_routes, logs as logs_routes, plex as plex_routes, notifications as notifications_routes, audio_language as audio_language_routes, backup as backup_routes, subtitle_language as subtitle_language_routes

app.include_router(queue.router)
app.include_router(history.router)
app.include_router(webhooks.router)
app.include_router(settings_routes.router)
app.include_router(scan.router)
app.include_router(forge.router)
app.include_router(worker_routes.router)
app.include_router(logs_routes.router)
app.include_router(plex_routes.router)
app.include_router(notifications_routes.router)
app.include_router(audio_language_routes.router)
app.include_router(backup_routes.router)
app.include_router(subtitle_language_routes.router)


# ── Health ─────────────────────────────────────────────────────────────────────

@app.get("/api/health", tags=["system"])
def health():
    return {"status": "ok", "app": settings.APP_NAME, "version": "0.1.0"}


# ── WebSocket ──────────────────────────────────────────────────────────────────

@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    from app.api.ws_manager import ws_manager
    await ws_manager.connect(websocket)
    try:
        while True:
            msg = await websocket.receive_text()
            if msg.strip() == "ping":
                await websocket.send_text("pong")
    except WebSocketDisconnect:
        ws_manager.disconnect(websocket)


# ── Static frontend ────────────────────────────────────────────────────────────
# Serve the built Vite output from /frontend/dist if present,
# otherwise fall back to the raw /frontend folder (dev / no-build mode).

_static_dir = FRONTEND_DIR if FRONTEND_DIR.is_dir() else (
    FRONTEND_DEV if FRONTEND_DEV.is_dir() else None
)

if _static_dir:
    # Mount assets at /assets so Vite hashed filenames work
    _assets = _static_dir / "assets"
    if _assets.is_dir():
        app.mount("/assets", StaticFiles(directory=str(_assets)), name="assets")

    from fastapi import Request
    from fastapi.responses import JSONResponse
    from starlette.exceptions import HTTPException as StarletteHTTPException

    # Resolved once at import: the containment check below compares against it
    # on every 404, and re-resolving per request costs syscalls for a value
    # that cannot change. resolve() also collapses any symlink in the
    # configured path itself, so the comparison is symlink-stable.
    _static_root = _static_dir.resolve()

    def _safe_static_path(url_path: str):
        """
        Map a URL path to a file inside the static root, or None.

        This exists because the obvious form — `_static_dir / path.lstrip("/")` —
        was an unauthenticated arbitrary-file-read. pathlib's `/` operator does
        not sanitise anything: it happily builds `<root>/../../etc/hostname`,
        and `.is_file()` then confirms it, and `FileResponse` serves it.

        The important detail, and the one that makes this worse than it looks:
        the attack does NOT need the literal `../` form. Starlette
        percent-decodes before populating `request.url.path`, so a plain
        `GET /%2e%2e/%2e%2e/config/remuxarr.db` arrives here already decoded to
        `/../../config/remuxarr.db`. The literal form is actually the one that
        fails, because browsers and curl collapse `..` client-side before
        sending — which means an ordinary HTTP client, or any scanner probing
        for traversal, reaches this with the encoded form as a matter of course.
        No raw socket required.

        The target that matters is the config volume: remuxarr.db stores
        app_settings in plaintext, including plex_token, sonarr_api_key,
        radarr_api_key and email_password. The app has no authentication of its
        own, so reachability of the port is the only precondition.

        resolve() collapses `..` and symlinks, and relative_to() then asserts
        the result is still inside the root. Note this deliberately rejects a
        symlink inside the static directory pointing outside it — a Vite build
        contains no such thing, and "the link target is outside the root" is
        exactly the case being defended against.
        """
        candidate = (_static_root / url_path.lstrip("/")).resolve()
        try:
            candidate.relative_to(_static_root)
        except ValueError:
            return None
        return candidate

    @app.exception_handler(StarletteHTTPException)
    async def spa_fallback(request: Request, exc: StarletteHTTPException):
        """
        Serve index.html for any 404 on a non-API, non-asset route so the
        React SPA can handle client-side routing on full-page loads/refreshes.

        This is an EXCEPTION HANDLER, not a catch-all route. That distinction
        matters: a catch-all `/{path:path}` route gives Starlette a FULL
        match for EVERY path — including `/api/queue` — which short-circuits
        its built-in redirect_slashes logic before it can redirect
        `/api/queue` → `/api/queue/`. By only acting after normal routing
        (including redirect_slashes) has already failed with a 404, API
        routes resolve correctly regardless of trailing slash, and only
        genuinely-unmatched frontend routes fall through to index.html.
        """
        path = request.url.path

        if (
            exc.status_code == 404
            and not path.startswith("/api")
            and not path.startswith("/assets")
            and path != "/ws"
        ):
            # Serve real static files at the dist root (favicon.ico, etc.),
            # but only ones that actually resolve inside the static root.
            candidate = _safe_static_path(path)
            if candidate and candidate.is_file():
                return FileResponse(str(candidate))

            # Fall back to the SPA entry point for client-side routes.
            # A traversal attempt lands here too, so it is answered with the
            # ordinary SPA response rather than anything that confirms whether
            # the requested file exists.
            index = _static_dir / "index.html"
            if index.is_file():
                return FileResponse(str(index))

        # Not an SPA route (or frontend missing) — preserve the original 404
        return JSONResponse(
            {"detail": exc.detail},
            status_code=exc.status_code,
            headers=exc.headers,
        )
else:
    logger.warning(
        "No frontend directory found at %s. "
        "The API is running but the UI is not being served. "
        "Run 'cd frontend && npm run build' to build the UI.",
        FRONTEND_DIR,
    )
