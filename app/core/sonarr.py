"""
Sonarr API client — post-job notification.

Called after a successful Remuxarr job to trigger RescanSeries, which
makes Sonarr re-discover the processed file at its new path/extension
(critical when MKV → MP4 conversion changed the filename). Sonarr then
detects the old file as missing from disk and fires its EpisodeFileDelete
webhook — which Sonarr's own Plex connection uses to notify Plex.

Fire-and-forget: we POST the command and return immediately without
polling for completion. Sonarr runs the rescan in its own time, and the
downstream EpisodeFileDelete → Plex chain happens naturally once it does.
(Previously we polled until the command completed, but that was only
needed when we were also calling RenameFiles — which required waiting
for the rescan so Sonarr had the new file's ID. RenameFiles was removed
because it doesn't work when Sonarr's auto-rename is disabled.)

Uses only Python's stdlib urllib — no extra dependencies required.
Called via asyncio loop.run_in_executor() from worker.py.
"""

import logging
import urllib.error

from app.core.arr_client import arr_post

logger = logging.getLogger(__name__)


def notify_sonarr(base_url: str, api_key: str, series_id: int) -> None:
    """
    Fire-and-forget RescanSeries for the given series.

    Sonarr queues the rescan internally and runs it in its own time.
    When complete, it detects the replaced file as missing from disk and
    fires EpisodeFileDelete to its Plex connection — no further action
    required from Remuxarr.

    Best-effort — failures are logged but do not affect the Remuxarr
    job's recorded status (the file was already processed successfully).
    """
    logger.info("Sonarr: triggering RescanSeries for series %d", series_id)
    try:
        resp = arr_post(base_url, api_key, {"name": "RescanSeries", "seriesId": series_id})
        logger.debug(
            "Sonarr: RescanSeries queued (command id=%s)", resp.get("id", "?")
        )
    except urllib.error.HTTPError as exc:
        logger.error(
            "Sonarr: RescanSeries HTTP %d for series %d: %s",
            exc.code, series_id, exc.reason,
        )
    except Exception as exc:
        logger.error("Sonarr: RescanSeries failed for series %d: %s", series_id, exc)
