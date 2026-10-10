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

A Rename also moves each renamed file's record from its old name to its
new one before anything is queued — see _move_renamed and
scanner.move_renamed_file.

Surviving a stop
----------------
The timer lives in memory, and the webhook has already been answered
"accepted" when it starts. A stop or a crash before it fired used to lose
the file: Sonarr or Radarr had nothing to resend, and the file waited for a
scan, if one was scheduled at all, which has no IDs to give it. So each translated path is also written to webhook_intake before
the webhook is answered, and the row is deleted once the queue attempt has
run (_queue_sync), whatever its outcome. A row still there at the next
start is replayed through the same debounce with the IDs it was stored
with (replay_webhook_intake, called from main.lifespan).

Each timer carries the id of the row it was armed with and deletes only
that row. A second event for the same file replaces the row before it
cancels the first timer, so a first attempt already running in a thread
when the second event arrives cannot delete the newer row. A cancelled
timer deletes nothing: on a debounce reset the newer row has replaced its
own, and on a stop the row is what the next start needs.

If the row cannot be written, the timer is armed anyway, which is how
every webhook behaved before this existed. The webhook is not failed for
it.
"""
import asyncio
import logging

from fastapi import APIRouter, HTTPException, Request

from app.api.ws_manager import ws_manager
from app.config import settings
from app.core.scanner import move_renamed_file, queue_single_file
from app.core.pathmap import translate_path
from app.database.models import WebhookIntake
from app.database.session import SessionLocal, get_app_settings

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/webhooks", tags=["webhooks"])

# {translated_path: asyncio.Task}
_pending: dict[str, asyncio.Task] = {}
_lock    = asyncio.Lock()


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
        translated = translate_path(path, remote, local)
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
    await _move_renamed(_sonarr_renames(payload), series_id=series_id)
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
    await _move_renamed(_radarr_renames(payload), radarr_movie_id=movie_id)
    await _debounce_all(paths, radarr_movie_id=movie_id)
    return {"status": "accepted", "files": len(paths)}


# ── Renames ────────────────────────────────────────────────────────────────────

async def _move_renamed(
    pairs: list[tuple[str, str]],
    series_id:       int | None = None,
    radarr_movie_id: int | None = None,
) -> None:
    """
    Move each renamed file's record to its new name, at receipt.

    Before the debounce rather than inside it: the debounce waits ten
    seconds by default, and a scan in that window would give the new name
    a record of its own and could delete the old one along with its
    history. Both paths are translated the same way the queued path is. A
    move that fails is logged and the webhook still goes on to queue the
    new name, which is what happened to every rename before this existed.
    """
    loop = asyncio.get_running_loop()
    for raw_old, raw_new in pairs:
        old = await loop.run_in_executor(
            None, _resolve_translated_path_sync, raw_old, series_id, radarr_movie_id,
        )
        new = await loop.run_in_executor(
            None, _resolve_translated_path_sync, raw_new, series_id, radarr_movie_id,
        )
        outcome = await loop.run_in_executor(None, _move_sync, old, new)
        logger.info("Rename %s -> %s: %s", old, new, outcome)


def _move_sync(old: str, new: str) -> str:
    db = SessionLocal()
    try:
        return move_renamed_file(db, old, new)
    except Exception:
        db.rollback()
        logger.exception("Could not move the record for %s to %s", old, new)
        return "failed"
    finally:
        db.close()


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
        # Written before the timer is armed and before the webhook is
        # answered, so "accepted" means it is on disk. See the module
        # docstring, "Surviving a stop".
        intake_id = await loop.run_in_executor(
            None, _remember_sync, path, series_id, radarr_movie_id,
        )
        await _arm(path, series_id, radarr_movie_id, intake_id)


async def _arm(
    path: str,
    series_id:       int | None,
    radarr_movie_id: int | None,
    intake_id:       int | None,
) -> None:
    """Start the debounce timer for an already-translated path."""
    async with _lock:
        if path in _pending:
            _pending[path].cancel()
            logger.debug("Debounce reset: %s", path)
        task = asyncio.create_task(
            _delayed_queue(path, series_id, radarr_movie_id, intake_id)
        )
        _pending[path] = task


async def replay_webhook_intake() -> None:
    """
    Re-arm every webhook the last run accepted but never queued.

    Called from main.lifespan on start. Each row goes back through the
    normal debounce with the IDs it was stored with, so an event for the
    same file arriving just after the start still collapses into it. A
    file that was queued just before the stop is handled as a second
    webhook for it would be: a job still waiting only has its IDs
    refreshed (scanner._process_file).
    """
    loop = asyncio.get_running_loop()
    rows = await loop.run_in_executor(None, _intake_rows_sync)
    for intake_id, path, series_id, radarr_movie_id in rows:
        logger.info("Replaying a webhook received before the last stop: %s", path)
        await _arm(path, series_id, radarr_movie_id, intake_id)


def _remember_sync(
    path: str,
    series_id:       int | None,
    radarr_movie_id: int | None,
) -> int | None:
    """Record the path, replacing any earlier row for it. Returns the row id."""
    db = SessionLocal()
    try:
        db.query(WebhookIntake).filter(WebhookIntake.path == path).delete()
        row = WebhookIntake(path=path, sonarr_series_id=series_id,
                            radarr_movie_id=radarr_movie_id)
        db.add(row)
        db.commit()
        return row.id
    except Exception:
        db.rollback()
        logger.warning(
            "Could not record the webhook for %s. It will still be queued, "
            "but not if Remuxarr stops before then.", path, exc_info=True,
        )
        return None
    finally:
        db.close()


def _forget_sync(intake_id: int) -> None:
    """Delete one intake row by id; a newer row for the same path stays."""
    db = SessionLocal()
    try:
        db.query(WebhookIntake).filter(WebhookIntake.id == intake_id).delete()
        db.commit()
    except Exception:
        db.rollback()
        logger.warning("Could not clear the webhook record %d", intake_id,
                       exc_info=True)
    finally:
        db.close()


def _intake_rows_sync() -> list[tuple]:
    db = SessionLocal()
    try:
        return [(row.id, row.path, row.sonarr_series_id, row.radarr_movie_id)
                for row in db.query(WebhookIntake).order_by(WebhookIntake.id)]
    finally:
        db.close()


async def _delayed_queue(
    path: str,
    series_id:      int | None = None,
    radarr_movie_id: int | None = None,
    intake_id:      int | None = None,
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
            None, _queue_sync, path, series_id, radarr_movie_id, intake_id
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
        # A debounce reset or a stop. The intake row is left alone either
        # way: a reset has already replaced it, and a stop needs it.
        pass
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
    intake_id:      int | None = None,
):
    """
    Synchronous wrapper for thread-pool execution.
    `path` is already translated by the time this runs — see _debounce_all.

    The intake row is deleted here, in the thread, once the attempt has
    run, whatever came of it. A failure is logged and not retried, as it
    was before the row existed; keeping it would retry the same failure at
    every start.
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
        if intake_id is not None:
            _forget_sync(intake_id)


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


def _sonarr_renames(payload: dict) -> list[tuple[str, str]]:
    """(previousPath, path) for each file a Sonarr Rename event moved."""
    return _renames(payload.get("renamedEpisodeFiles", []))


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
    # -handled Sonarr sibling already uses.
    #
    # Each element also carries "previousPath" (the pre-rename full
    # path). Only "path" (the NEW location) is queued, exactly as
    # _sonarr_paths does: after a rename the file no longer exists at
    # previousPath, so queuing it would just probe-fail on a missing
    # file. previousPath is read by _radarr_renames instead, which moves
    # the file's record from the old name to the new one.
    for item in payload.get("renamedMovieFiles", []):
        if item.get("path"):
            paths.append(item["path"])

    return list(dict.fromkeys(paths))


def _radarr_renames(payload: dict) -> list[tuple[str, str]]:
    """(previousPath, path) for each file a Radarr Rename event moved."""
    return _renames(payload.get("renamedMovieFiles", []))


def _renames(items) -> list[tuple[str, str]]:
    """
    Both services' renamed-file elements carry the old full path as
    previousPath beside the new one as path (WebhookRenamedEpisodeFile and
    WebhookRenamedMovieFile, checked in their source). An element missing
    either is skipped: without both there is nothing to move.
    """
    pairs = []
    for item in items or []:
        old, new = item.get("previousPath"), item.get("path")
        if old and new:
            pairs.append((old, new))
    return list(dict.fromkeys(pairs))


# ── Helpers ────────────────────────────────────────────────────────────────────

async def _parse_body(request: Request) -> dict:
    try:
        return await request.json()
    except Exception:
        raise HTTPException(400, "Invalid JSON payload")
