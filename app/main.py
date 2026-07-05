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


# ── Lifespan ───────────────────────────────────────────────────────────────────

@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("━━━ %s starting ━━━", settings.APP_NAME)
    init_db()
    _cleanup_orphaned_temp_files()
    await start_worker()
    from app.api.ws_manager import ws_manager
    asyncio.create_task(run_scheduler(ws_manager))
    asyncio.create_task(run_plex_backlog_drain())
    yield
    await stop_worker()
    logger.info("━━━ %s stopped ━━━", settings.APP_NAME)


def _cleanup_orphaned_temp_files() -> None:
    """
    Remove any .remuxarr_tmp or .forge_tmp files left behind in TEMP_DIR by
    jobs that were interrupted mid-stream (container restart, SIGKILL,
    'No space left on device' failures, thread-pool starvation kills, etc.).

    These files live in RAM on Unraid (tmpfs) and silently accumulate until
    the RAM filesystem fills up, causing 'No space left on device' for
    subsequent jobs even when the array has plenty of space.
    """
    temp_dir = settings.TEMP_DIR
    try:
        orphans = (
            glob.glob(os.path.join(temp_dir, "*.remuxarr_tmp"))
            + glob.glob(os.path.join(temp_dir, "*.forge_tmp"))
        )
        if not orphans:
            return
        total_bytes = 0
        for f in orphans:
            try:
                size = os.path.getsize(f)
                os.remove(f)
                total_bytes += size
                logger.debug("Removed orphaned temp file: %s", f)
            except OSError as exc:
                logger.warning("Could not remove orphaned temp file %s: %s", f, exc)
        logger.info(
            "Startup cleanup: removed %d orphaned temp file(s) (%.1f MB freed from %s)",
            len(orphans), total_bytes / 1024 / 1024, temp_dir,
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

from app.api.routes import queue, history, webhooks, settings as settings_routes, scan, forge, worker as worker_routes, logs as logs_routes, plex as plex_routes, notifications as notifications_routes, audio_language as audio_language_routes

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
            # Serve real static files at the dist root (favicon.ico, etc.)
            candidate = _static_dir / path.lstrip("/")
            if candidate.is_file():
                return FileResponse(str(candidate))

            # Fall back to the SPA entry point for client-side routes
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
