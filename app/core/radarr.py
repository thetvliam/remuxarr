"""
Radarr API client — post-job notification.

Called after a successful Remuxarr job to trigger RescanMovie, which
makes Radarr re-discover the processed file at its new path/extension
(critical when MKV → MP4 conversion changed the filename). Radarr then
detects the old file as missing from disk and fires its MovieFileDelete
webhook — which Radarr's own Plex connection uses to notify Plex.

Fire-and-forget: we POST the command and return immediately without
polling for completion. Radarr runs the rescan in its own time.

Uses only Python's stdlib urllib — no extra dependencies required.
Called via asyncio loop.run_in_executor() from worker.py.
"""

import logging
import urllib.error

from app.core.arr_client import arr_post
from app.core.arr_quality import RADARR, restore_quality

logger = logging.getLogger(__name__)


def notify_radarr(base_url: str, api_key: str, movie_id: int) -> None:
    """
    Fire-and-forget RescanMovie for the given movie.

    Radarr queues the rescan internally and runs it in its own time.
    When complete, it detects the replaced file as missing from disk and
    fires MovieFileDelete to its Plex connection — no further action
    required from Remuxarr.

    Best-effort — failures are logged but do not affect the Remuxarr
    job's recorded status (the file was already processed successfully).
    """
    logger.info("Radarr: triggering RescanMovie for movie %d", movie_id)
    try:
        resp = arr_post(base_url, api_key, {"name": "RescanMovie", "movieId": movie_id})
        logger.debug(
            "Radarr: RescanMovie queued (command id=%s)", resp.get("id", "?")
        )
    except urllib.error.HTTPError as exc:
        logger.error(
            "Radarr: RescanMovie HTTP %d for movie %d: %s",
            exc.code, movie_id, exc.reason,
        )
    except Exception:
        logger.exception("Radarr: RescanMovie failed for movie %d", movie_id)


def restore_movie_quality(
    base_url: str, api_key: str, movie_id: int, path: str
) -> None:
    """
    Put back the quality Radarr re-parsed from the filename after the
    rescan above replaced the file record.

    Best-effort, like the notification: the file is already on disk and
    correct, so a failed metadata write must not mark the job failed. It
    is logged at error rather than swallowed, because the state it leaves
    behind is a file Radarr has flagged qualityCutoffNotMet and will try
    to replace.
    """
    try:
        restore_quality(RADARR, base_url, api_key, movie_id, path)
    except urllib.error.HTTPError as exc:
        logger.error(
            "Radarr: quality restore HTTP %d for movie %d (%s): %s",
            exc.code, movie_id, path, exc.reason,
        )
    except Exception:
        logger.exception(
            "Radarr: quality restore failed for movie %d (%s)", movie_id, path
        )
