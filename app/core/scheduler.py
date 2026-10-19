"""
Scheduled Scan Scheduler
========================
Runs as a perpetual asyncio background task, waking every 60 seconds and
dispatching a library scan when the current local time matches any of the
times in the `scheduled_scan_times` setting (HH:MM, 24-hour format).

Uses the system's local time, which respects the TZ environment variable
on the container — the standard way Docker handles timezone configuration.

A scheduled time fires when it came round since the previous check
(_last_checked), not only when the check lands on its exact minute. Each
cycle is the tick, the retention sweep and a 60-second sleep, so a cycle
runs a little over a minute and the checked minute skipped one every so
often: 12 times a day at half a second per cycle, 102 at five, and a scan
due in a skipped minute did not run that day. Remembering the last check
also keeps a time from firing twice. The reference resets on a container
restart, after which only the current minute counts, as before.
"""

import asyncio
import logging
import os
from datetime import datetime, timedelta

from app.core.plex import notify_plex_reprocessed_file
from app.core.recycle import sweep_retention
from app.database.models import PlexAnalyzeBacklog
from app.database.session import SessionLocal, get_app_settings

logger = logging.getLogger(__name__)

# When _tick last looked at the clock. None until the first tick after a
# start — intentional, see _due_time.
_last_checked: datetime | None = None

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

        # Retention runs from the loop rather than from _tick, which
        # returns early whenever scheduled scans are switched off. Tucked
        # in there, the recycle bin would grow without limit for every
        # user who does not use scheduled scans — which is most of them.
        #
        # Off the event loop: the sweep unlinks files on what may be a
        # spun-down array, and blocking here stalls every other background
        # task including job progress.
        try:
            await asyncio.get_running_loop().run_in_executor(None, sweep_retention)
        except Exception:
            logger.exception("Retention sweep raised an unexpected error")

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
    db = SessionLocal()
    try:
        cfg = get_app_settings(db)
    finally:
        db.close()

    if cfg.get("plex_enabled", False) and cfg.get("plex_analyze_backlog_enabled", False):
        logger.info(
            "Plex backlog drain started — %ds between real analyzes, %ds "
            "between everything else, within the configured window",
            PLEX_BACKLOG_DRAIN_INTERVAL_SECONDS, PLEX_BACKLOG_SKIP_INTERVAL_SECONDS,
        )
    else:
        # This task always starts at boot regardless of the setting — the
        # loop below checks fresh on every tick via _drain_tick(), so
        # toggling the setting later takes effect immediately without a
        # restart. This message only reflects the setting's state at THIS
        # exact moment, though: if it's later turned on, this won't print
        # again, since the task doesn't restart — it was already right
        # here, quietly polling. Worded separately from the message above
        # specifically so the log doesn't describe active analyze timing
        # for a task that will actually do nothing right now.
        logger.info(
            "Plex backlog drain task started, but idle — plex_enabled and/or "
            "plex_analyze_backlog_enabled is currently off, so no analyzes "
            "will be sent until both are enabled in Settings > Plex Analyze "
            "Backlog."
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
        if not cfg.get("plex_analyze_backlog_enabled", False):
            # Feature is off — leave any leftover entries untouched rather
            # than draining them. Re-enabling resumes exactly where the
            # queue left off, in the same oldest-first order as always.
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


def _due_time(scan_times: list[str], since: datetime | None,
              now: datetime) -> str | None:
    """
    A scheduled "HH:MM" that came round after `since` and at or before
    `now`, or None. At most one: a gap covering several times gives one
    scan, which covers them all.

    With no `since`, the first tick after a start, only the current minute
    counts, so a restart does not fire for times earlier in the day. A clock
    that went back fires nothing and the next tick measures from here. A gap
    of a day or more, the host asleep for one, has passed every time, and
    the scan runs once on waking, as a missed one should.
    """
    if since is None:
        current = now.strftime("%H:%M")
        return current if current in scan_times else None
    if now <= since:
        return None
    for scheduled in scan_times:
        try:
            hour, minute = (int(part) for part in scheduled.split(":"))
            # Today's and the previous check's day: a gap over midnight.
            occurrences = [day.replace(hour=hour, minute=minute, second=0, microsecond=0)
                           for day in (since, now)]
        except ValueError:
            continue
        if now - since >= timedelta(days=1):
            return scheduled
        if any(since < when <= now for when in occurrences):
            return scheduled
    return None


async def _tick(ws_manager) -> None:
    """
    Called about once a minute. Checks settings and fires a scan if a
    scheduled time came round since the previous call.
    """
    global _last_checked

    # Moved on every call, including the early returns below: switching
    # scheduled scans on must not fire for times that passed while they
    # were off.
    now = datetime.now()   # respects TZ env var
    since, _last_checked = _last_checked, now

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

    due = _due_time(scan_times, since, now)
    if due is None:
        return

    # Time matches — dispatch the scan. The time is used up even if a scan
    # is already running (below): one running then satisfies it.
    logger.info("Scheduler: triggering scheduled scan for %s", due)

    # Deferred (in-function) import to avoid circular imports: scan.py
    # imports scanner.py; scheduler.py is imported by main.py, which also
    # imports scan.py.
    #
    # Imports the MODULE itself here, not `_scan_running` as a name — a
    # `from app.api.routes.scan import _scan_running` binds a local copy
    # of whatever the value was at import time; reassigning that local
    # name would never actually mutate scan.py's own module-level
    # variable, so trigger_scan's own check would still see False and
    # this fix would silently fail to close the race it's meant to close
    # (confirmed directly: this exact gotcha reproduces with a minimal
    # two-module test). Reading/writing scan_route._scan_running as an
    # attribute is what actually shares the same underlying value across
    # both files.
    import app.api.routes.scan as scan_route
    from app.api.routes.scan import _run_scan

    if scan_route._scan_running:
        logger.info("Scheduler: scan already running — skipping this trigger")
        return

    # Set synchronously, immediately, before any await — see trigger_scan's
    # own comment for the full rationale (same fix, same reasoning, applied
    # on this side of the same race), including the flag's lifecycle
    # contract this try/finally implements. The first version of this fix
    # set the flag but had no rollback, so an empty scan_paths list here
    # (completely plausible: scheduling enabled before paths configured)
    # permanently wedged all scanning at the first scheduled tick.
    #
    # scan_paths comes from `cfg`, already loaded at the top of this
    # function — the previous version opened a SECOND session and
    # re-fetched settings here, after setting the flag, which was most of
    # the window in which an exception could leak it. Reusing the
    # already-loaded value doesn't just shrink that window, it removes it:
    # nothing between the set and the thread start can fail except thread
    # creation itself, and the finally covers that too.
    scan_route._scan_running = True
    scan_thread_started = False
    try:
        scan_paths = cfg.get("scan_paths", [])
        if not scan_paths:
            logger.warning("Scheduler: no scan paths configured — skipping")
            return

        # Dispatch on a background thread, exactly like the manual scan
        # button. We capture the running loop and pass ws_manager's
        # broadcast function the same way the scan route does.
        loop = asyncio.get_running_loop()
        import threading
        t = threading.Thread(
            target  = _run_scan,
            args    = (scan_paths, False, loop),
            name    = "remuxarr-scheduled-scanner",
            daemon  = True,
        )
        t.start()
        scan_thread_started = True
    finally:
        if not scan_thread_started:
            scan_route._scan_running = False
