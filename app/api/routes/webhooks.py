"""
Webhook Receivers — Sonarr & Radarr
=====================================
Both use the same debounce mechanism:

  1. A webhook arrives and its file paths are extracted, then IMMEDIATELY
     translated to Remuxarr's local filesystem view (see _translate_path).
  2. Each TRANSLATED path is registered in a debounce dict with a
     countdown task.
  3. If the same translated path arrives again before the timer expires,
     the existing task is cancelled and a new one starts. This collapses
     a burst of events for the same file — e.g. Sonarr commonly fires
     BOTH a "Download" and a "Rename" webhook for one ordinary import,
     each potentially carrying a slightly different raw path on Sonarr's
     own side that nonetheless maps to the exact same file once
     translated to Remuxarr's local view.
  4. When a timer fires, the file is probed and queued via scanner.queue_single_file().
  5. The new QueueItem ID is broadcast over WebSocket.

IMPORTANT: translation must happen BEFORE the debounce dict lookup, not
after. It used to happen later (inside _queue_sync, after the debounce
timer fired), keyed on the raw untranslated path — which meant a
Download+Rename pair with two different raw paths that both translate to
the same final file were NOT collapsed at all; they registered as two
separate dict entries and fired as two independent, un-deduplicated calls
into queue_single_file() moments apart. Whichever call ran second would
see the first one's already-committed MediaFile row and incorrectly
compute is_new_file=False for what was genuinely the file's first-ever
import — this is what caused a freshly-imported file to be misclassified
as a "reprocess" for Plex notification purposes instead of "new".

Supported event types
---------------------
Sonarr : Download, Rename  (Test returns 200 immediately)
Radarr : Download, Rename  (Test returns 200 immediately)
"""
import asyncio
import logging

from fastapi import APIRouter, HTTPException, Request

from app.api.ws_manager import ws_manager
from app.config import settings
from app.core.scanner import queue_single_file
from app.database.session import SessionLocal, get_app_settings

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/webhooks", tags=["webhooks"])

# {translated_path: asyncio.Task}
_pending: dict[str, asyncio.Task] = {}
_lock    = asyncio.Lock()


def _translate_path(path: str, remote_prefix: str, local_prefix: str) -> str:
    """
    Translate a path from Sonarr's container view to Remuxarr's view.

    Sonarr and Remuxarr often run in separate Docker containers with the
    same physical directory mounted at different paths. For example, both
    containers might mount /mnt/user/data/tv, but Sonarr maps it as /media
    while Remuxarr maps it as /media/tv. Without translation, the path in
    Sonarr's webhook payload points to a file that doesn't exist from
    Remuxarr's perspective, causing silent queue failures.

    Both prefixes must be non-empty for translation to apply — if either
    is blank, the path is returned unchanged so unconfigured setups
    (where both containers already agree on the path) work out of the box.
    """
    remote = remote_prefix.rstrip("/")
    local  = local_prefix.rstrip("/")
    if not remote or not local:
        return path
    if path.startswith(remote + "/") or path == remote:
        return local + path[len(remote):]
    return path


def _resolve_translated_path_sync(
    path: str,
    series_id:       int | None,
    radarr_movie_id: int | None,
) -> str:
    """
    Synchronous helper: reads the appropriate path-prefix settings and
    returns the translated (Remuxarr-local) path. Run via
    loop.run_in_executor() so the async webhook handler never blocks on
    a DB read directly.
    """
    db = SessionLocal()
    try:
        cfg = get_app_settings(db)
        if series_id:
            remote = cfg.get("sonarr_path_prefix_remote", "")
            local  = cfg.get("sonarr_path_prefix_local",  "")
        elif radarr_movie_id:
            remote = cfg.get("radarr_path_prefix_remote", "")
            local  = cfg.get("radarr_path_prefix_local",  "")
        else:
            remote = local = ""
        translated = _translate_path(path, remote, local)
        if translated != path:
            logger.info("Path translated: %s → %s", path, translated)
        return translated
    finally:
        db.close()


# ── Endpoints ──────────────────────────────────────────────────────────────────

@router.post("/sonarr")
async def sonarr_webhook(request: Request):
    payload    = await _parse_body(request)
    event_type = payload.get("eventType", "")
    logger.info("Sonarr webhook: %s", event_type)

    if event_type == "Test":
        return {"status": "ok", "message": "Sonarr connection test successful"}

    if event_type not in ("Download", "Rename"):
        return {"status": "ignored", "event": event_type}

    paths     = _sonarr_paths(payload)
    series_id = _sonarr_series_id(payload)
    await _debounce_all(paths, series_id)
    return {"status": "accepted", "files": len(paths)}


@router.post("/radarr")
async def radarr_webhook(request: Request):
    payload    = await _parse_body(request)
    event_type = payload.get("eventType", "")
    logger.info("Radarr webhook: %s", event_type)

    if event_type == "Test":
        return {"status": "ok", "message": "Radarr connection test successful"}
    if event_type not in ("Download", "Rename"):
        return {"status": "ignored", "event": event_type}

    paths    = _radarr_paths(payload)
    movie_id = _radarr_movie_id(payload)
    await _debounce_all(paths, radarr_movie_id=movie_id)
    return {"status": "accepted", "files": len(paths)}


# ── Debounce engine ────────────────────────────────────────────────────────────

async def _debounce_all(
    paths: list[str],
    series_id:      int | None = None,
    radarr_movie_id: int | None = None,
) -> None:
    loop = asyncio.get_running_loop()
    for raw_path in paths:
        # Translate BEFORE using the path as a debounce key — this is the
        # fix. A Download event and a Rename event for the same ordinary
        # import can carry two different raw paths on Sonarr's side that
        # both resolve to the identical final Remuxarr-local file; keying
        # on the untranslated path would treat them as unrelated and let
        # both fire independently.
        path = await loop.run_in_executor(
            None, _resolve_translated_path_sync, raw_path, series_id, radarr_movie_id,
        )
        async with _lock:
            if path in _pending:
                _pending[path].cancel()
                logger.debug("Debounce reset: %s", path)
            task = asyncio.create_task(
                _delayed_queue(path, series_id, radarr_movie_id)
            )
            _pending[path] = task


async def _delayed_queue(
    path: str,
    series_id:      int | None = None,
    radarr_movie_id: int | None = None,
) -> None:
    """
    Wait debounce_seconds, then probe-and-queue the file.
    `path` here is already the TRANSLATED (Remuxarr-local) path — see
    _debounce_all, which resolves it before this task is even created.
    """
    try:
        await asyncio.sleep(settings.WEBHOOK_DEBOUNCE_SECONDS)
        logger.info("Debounce fired — queuing: %s", path)

        loop = asyncio.get_running_loop()
        qi   = await loop.run_in_executor(
            None, _queue_sync, path, series_id, radarr_movie_id
        )

        if qi:
            await ws_manager.broadcast_json({
                "event":         "file_queued",
                "file_path":     path,
                "queue_item_id": qi.id,
                "reason":        qi.reason,
            })
        else:
            logger.info("File skipped (no changes needed): %s", path)

    except asyncio.CancelledError:
        pass   # debounce was reset — do nothing
    finally:
        async with _lock:
            # Only remove the entry if it's still THIS task. cancel() is
            # cooperative — this finally block doesn't run the instant
            # cancel() is called, it runs whenever the event loop gets
            # back to unwinding this coroutine, which in practice is
            # often AFTER _debounce_all has already registered a NEWER
            # task in _pending[path] for a subsequent event on the same
            # path. An unconditional pop here would remove that newer
            # task's entry instead of this (already-cancelled) one's own,
            # orphaning it from tracking — if a THIRD event then arrives
            # for the same path, _debounce_all finds nothing to cancel
            # and starts a fully independent timer, defeating the whole
            # point of debouncing for that path.
            if _pending.get(path) is asyncio.current_task():
                _pending.pop(path, None)


def _queue_sync(
    path: str,
    series_id:      int | None = None,
    radarr_movie_id: int | None = None,
):
    """
    Synchronous wrapper for thread-pool execution.
    `path` is already translated by the time this runs — see _debounce_all.
    """
    db = SessionLocal()
    try:
        return queue_single_file(
            db, path,
            sonarr_series_id=series_id,
            radarr_movie_id=radarr_movie_id,
        )
    except Exception:
        logger.exception("Failed to queue %s", path)
        return None
    finally:
        db.close()


# ── Payload parsers ────────────────────────────────────────────────────────────

def _sonarr_series_id(payload: dict) -> int | None:
    """Extract the Sonarr series ID from a webhook payload."""
    try:
        return int(payload["series"]["id"])
    except (KeyError, TypeError, ValueError):
        return None


def _sonarr_paths(payload: dict) -> list[str]:
    paths: list[str] = []

    # v3 Download event
    ef = payload.get("episodeFile", {})
    if ef.get("path"):
        paths.append(ef["path"])

    # v3 Rename event — renamedEpisodeFiles array
    for item in payload.get("renamedEpisodeFiles", []):
        if item.get("path"):
            paths.append(item["path"])

    # v3 import/upgrade — episodeFiles array
    for item in payload.get("episodeFiles", []):
        if item.get("path"):
            paths.append(item["path"])

    return list(dict.fromkeys(paths))   # dedupe while preserving order


def _radarr_movie_id(payload: dict) -> int | None:
    """Extract the Radarr movie ID from a webhook payload."""
    try:
        return int(payload["movie"]["id"])
    except (KeyError, TypeError, ValueError):
        return None


def _radarr_paths(payload: dict) -> list[str]:
    paths: list[str] = []

    mf = payload.get("movieFile", {})
    if mf.get("path"):
        paths.append(mf["path"])

    # Rename event — renamedMovieFiles is a LIST (mirrors Sonarr's
    # renamedEpisodeFiles), each element extending WebhookMovieFile. The
    # previous code read "renamedMovieFile" (singular object), a field
    # Radarr never emits, so Rename events matched nothing and queued no
    # files — Download still worked via movieFile above. Confirmed
    # against Radarr's WebhookRenamePayload / WebhookRenamedMovieFile
    # source, not just the field name: same array shape the correctly
    # -handled Sonarr sibling already uses. Caught by independent review.
    #
    # Each element also carries "previousPath" (the pre-rename full
    # path). We deliberately take only "path" (the NEW location), exactly
    # as _sonarr_paths does: after a rename the file no longer exists at
    # previousPath, so queuing it would just probe-fail on a missing
    # file. The stale MediaFile row still sitting at the old path is
    # cleaned up by the scanner's own orphan/delete handling, not here.
    for item in payload.get("renamedMovieFiles", []):
        if item.get("path"):
            paths.append(item["path"])

    return list(dict.fromkeys(paths))


# ── Helpers ────────────────────────────────────────────────────────────────────

async def _parse_body(request: Request) -> dict:
    try:
        return await request.json()
    except Exception:
        raise HTTPException(400, "Invalid JSON payload")
