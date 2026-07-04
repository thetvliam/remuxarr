"""
Scheduled Scan Scheduler
========================
Runs as a perpetual asyncio background task, waking every 60 seconds and
dispatching a library scan when the current local time matches any of the
times in the `scheduled_scan_times` setting (HH:MM, 24-hour format).

Uses the system's local time, which respects the TZ environment variable
on the container — the standard way Docker handles timezone configuration.

A module-level dedup guard (_last_triggered_minute) prevents the scan from
being triggered twice if the loop fires more than once within the same
minute (e.g. on startup near a scheduled time).  The guard resets on a
container restart, which is acceptable — a redundant scan on restart is
harmless.
"""

import asyncio
import logging
import os
from datetime import datetime

from app.core.plex import notify_plex_reprocessed_file
from app.database.models import PlexAnalyzeBacklog
from app.database.session import SessionLocal, get_app_settings

logger = logging.getLogger(__name__)

# Minute string ("HH:MM") of the most recently triggered scheduled scan.
# Resets on restart — intentional.
_last_triggered_minute: str = ""

# Seconds between each backlog item processed during the analyze window.
# Not user-configurable — a sensible fixed pace that drains a few hundred
# items comfortably within a multi-hour window without bursting Plex with
# requests. At this rate a 4-hour window can clear roughly 1,500 items.
PLEX_BACKLOG_DRAIN_INTERVAL_SECONDS = 8


async def run_scheduler(ws_manager) -> None:
    """
    Perpetual asyncio task. Call via asyncio.create_task() at startup.
    ws_manager is passed through to the scan dispatch so scheduled scans
    broadcast WebSocket events identically to manual scans.
    """
    logger.info("Scheduler started — checking for scheduled scans every 60 s")
    while True:
        try:
            await _tick(ws_manager)
        except Exception:
            logger.exception("Scheduler tick raised an unexpected error")
        await asyncio.sleep(60)


def _within_window(start: str, end: str) -> bool:
    """
    True if the current local time falls within [start, end], both "HH:MM".
    Handles windows that span midnight (e.g. start=22:00, end=02:00).
    """
    try:
        s = datetime.strptime(start, "%H:%M").time()
        e = datetime.strptime(end,   "%H:%M").time()
    except (ValueError, TypeError):
        return False

    now = datetime.now().time()   # respects TZ env var
    if s <= e:
        return s <= now <= e
    else:
        # Window wraps past midnight
        return now >= s or now <= e


async def run_plex_backlog_drain() -> None:
    """
    Perpetual asyncio task — drains the Plex Analyze backlog one item at a
    time, only while the current local time is within the configured
    plex_analyze_window_start/end window.

    This exists because reprocessed files (RE-PROCESS, retry, or a file
    replaced in place) need an explicit Analyze call to force Plex to
    re-read stream metadata, and firing hundreds of these immediately
    during a large backfill would burst Plex with requests — each of
    which also requires fetching the full library section listing to find
    the matching item. Queuing and draining slowly within a quiet-hours
    window avoids that, and matches the timing pattern Plex itself uses
    for its own nightly maintenance.
    """
    logger.info(
        "Plex backlog drain started — checking every %d s within the "
        "configured window",
        PLEX_BACKLOG_DRAIN_INTERVAL_SECONDS,
    )
    while True:
        try:
            await _drain_tick()
        except Exception:
            logger.exception("Plex backlog drain tick raised an unexpected error")
        await asyncio.sleep(PLEX_BACKLOG_DRAIN_INTERVAL_SECONDS)


async def _drain_tick() -> None:
    db = SessionLocal()
    try:
        cfg = get_app_settings(db)
        if not cfg.get("plex_enabled", False):
            return

        window_start = cfg.get("plex_analyze_window_start", "02:00")
        window_end   = cfg.get("plex_analyze_window_end",   "06:00")
        if not _within_window(window_start, window_end):
            return

        entry = (
            db.query(PlexAnalyzeBacklog)
            .order_by(PlexAnalyzeBacklog.created_at.asc())
            .first()
        )
        if not entry:
            return

        media = entry.media_file
        # Capture everything needed for the network call before deleting
        # the row — deletion happens regardless of outcome (best-effort,
        # matching the rest of the Plex integration's philosophy).
        local_path        = media.path if media else None
        expected_language = entry.expected_language
        db.delete(entry)
        db.commit()

        if not media or not local_path or not os.path.exists(local_path):
            logger.debug("Plex backlog: skipping missing/deleted file (entry %d)", entry.id)
            return

        url      = (cfg.get("plex_url") or "").rstrip("/")
        token    = cfg.get("plex_token") or ""
        mappings = cfg.get("plex_path_mappings", [])
        if not url or not token or not mappings:
            return
    finally:
        db.close()

    loop = asyncio.get_running_loop()
    try:
        await loop.run_in_executor(
            None, notify_plex_reprocessed_file,
            url, token, mappings, local_path, expected_language,
        )
    except Exception:
        logger.exception("Plex backlog: analyze failed for %s", local_path)


async def _tick(ws_manager) -> None:
    """
    Called once per minute.  Checks settings and fires a scan if needed.
    """
    global _last_triggered_minute

    db = SessionLocal()
    try:
        cfg = get_app_settings(db)
    finally:
        db.close()

    if not cfg.get("scheduled_scan_enabled", False):
        return

    scan_times: list[str] = cfg.get("scheduled_scan_times", [])
    if not scan_times:
        return

    current_minute = datetime.now().strftime("%H:%M")   # respects TZ env var

    # Dedup: don't trigger twice in the same minute window
    if current_minute == _last_triggered_minute:
        return

    if current_minute not in scan_times:
        return

    # Time matches — dispatch the scan
    _last_triggered_minute = current_minute
    logger.info("Scheduler: triggering scheduled scan at %s", current_minute)

    # Import here to avoid circular imports (scan.py imports scanner.py;
    # scheduler.py is imported by main.py which also imports scan.py).
    from app.api.routes.scan import _run_scan, _scan_running

    if _scan_running:
        logger.info("Scheduler: scan already running — skipping this trigger")
        return

    db2 = SessionLocal()
    try:
        cfg2       = get_app_settings(db2)
        scan_paths = cfg2.get("scan_paths", [])
    finally:
        db2.close()

    if not scan_paths:
        logger.warning("Scheduler: no scan paths configured — skipping")
        return

    # Dispatch on a background thread, exactly like the manual scan button.
    # We capture the running loop and pass ws_manager's broadcast function
    # the same way the scan route does.
    loop = asyncio.get_running_loop()
    import threading
    t = threading.Thread(
        target  = _run_scan,
        args    = (scan_paths, False, loop),
        name    = "remuxarr-scheduled-scanner",
        daemon  = True,
    )
    t.start()
