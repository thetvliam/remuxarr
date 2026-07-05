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

# Seconds between each backlog item processed during the analyze window,
# when that item actually triggers a real Analyze call — the genuinely
# expensive operation on Plex's side. Not user-configurable — a sensible
# fixed pace that drains a few hundred real analyzes comfortably within a
# multi-hour window without bursting Plex with requests.
PLEX_BACKLOG_DRAIN_INTERVAL_SECONDS = 8

# Seconds to wait after an item that did NOT trigger a real Analyze —
# already correct (Plex's own maintenance got there first), a plain
# refresh fallback, or nothing to send at all. None of these involve the
# expensive operation the interval above exists to pace, so there's no
# reason to wait as long between them. Kept at a small non-zero value
# rather than 0 as cheap insurance — a skip usually costs nothing beyond
# a local cache lookup, but if that section's cache happens to need
# refreshing at that exact moment, it's still one real (lightweight)
# request to Plex, and a back-to-back flood of even lightweight requests
# is worth avoiding on principle.
PLEX_BACKLOG_SKIP_INTERVAL_SECONDS = 1


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

    _drain_tick() reports back whether it actually sent a real Analyze
    call for the item it just processed. Only that outcome uses the full
    interval — an item that was already correct (or had nothing to send
    at all) moves on almost immediately, since none of those involve the
    expensive operation the interval exists to pace.
    """
    logger.info(
        "Plex backlog drain started — %ds between real analyzes, %ds "
        "between everything else, within the configured window",
        PLEX_BACKLOG_DRAIN_INTERVAL_SECONDS, PLEX_BACKLOG_SKIP_INTERVAL_SECONDS,
    )
    while True:
        try:
            analyzed = await _drain_tick()
        except Exception:
            logger.exception("Plex backlog drain tick raised an unexpected error")
            analyzed = False
        await asyncio.sleep(
            PLEX_BACKLOG_DRAIN_INTERVAL_SECONDS if analyzed
            else PLEX_BACKLOG_SKIP_INTERVAL_SECONDS
        )


async def _drain_tick() -> bool:
    """
    Process at most one backlog entry, if the window is open and one exists.

    Returns True only if a real Analyze call was sent for that entry —
    False for every other outcome (idle, missing file, missing config, or
    any skip/fallback inside notify_plex_reprocessed_file itself). The
    caller uses this to decide how long to wait before the next tick.
    """
    db = SessionLocal()
    try:
        cfg = get_app_settings(db)
        if not cfg.get("plex_enabled", False):
            return False

        window_start = cfg.get("plex_analyze_window_start", "02:00")
        window_end   = cfg.get("plex_analyze_window_end",   "06:00")
        if not _within_window(window_start, window_end):
            return False

        entry = (
            db.query(PlexAnalyzeBacklog)
            .order_by(PlexAnalyzeBacklog.created_at.asc())
            .first()
        )
        if not entry:
            return False

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
            return False

        url      = (cfg.get("plex_url") or "").rstrip("/")
        token    = cfg.get("plex_token") or ""
        mappings = cfg.get("plex_path_mappings", [])
        if not url or not token or not mappings:
            return False
    finally:
        db.close()

    loop = asyncio.get_running_loop()
    try:
        analyzed = await loop.run_in_executor(
            None, notify_plex_reprocessed_file,
            url, token, mappings, local_path, expected_language,
        )
        return bool(analyzed)
    except Exception:
        logger.exception("Plex backlog: analyze failed for %s", local_path)
        # Unknown whether a real analyze was sent before the failure —
        # assume not, so the next tick comes quickly rather than
        # potentially waiting the full interval for no reason.
        return False


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
