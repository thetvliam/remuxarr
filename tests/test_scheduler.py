"""
scheduler.py — the two perpetual background tasks nobody watches.

Both loops run unattended for the life of the container, so every failure
here is a silent one: a scan that never fires, a scan that fires twice, or a
_scan_running flag left set, which wedges ALL scanning — manual and scheduled
— until the next restart. Nothing surfaces in the UI when any of that happens.

Time is faked by swapping scheduler.datetime for a stub whose now() is fixed
and whose strptime delegates to the real class, so the window arithmetic is
exercised for real. The module-level dedup guard is reset around every test;
leaving it set leaks into whatever runs next.

Verified by mutation: 29 mutations of scheduler.py, of which 28 are killed by
at least one test here. The one worth naming is dropping the `finally` that
clears _scan_running when no scan thread was started — a plausible tidy-up,
and the bug it reintroduces (scheduling enabled before scan paths are
configured permanently wedges all scanning at the first tick) is invisible
until someone notices the scan button has stopped working.

The 29th is equivalent, not a gap: removing `if not scan_times: return`
changes no behaviour, because `current_minute not in []` is always true and
the next check returns anyway. Removing BOTH guards together does fail
test_no_configured_times_never_scans, which is the evidence that the test
pins the contract rather than nothing at all. The first guard is a readability
early-exit, and no test can distinguish its presence.
"""
import asyncio
from datetime import datetime as _real_datetime

import pytest


# ── Harness ──────────────────────────────────────────────────────────────────

def _fake_datetime(hh_mm):
    """A datetime stand-in pinned to hh_mm, with strptime still real."""
    fixed = _real_datetime.strptime(hh_mm, "%H:%M")

    class _DT:
        @staticmethod
        def now():
            return fixed

        @staticmethod
        def strptime(s, fmt):
            return _real_datetime.strptime(s, fmt)

    return _DT


@pytest.fixture(autouse=True)
def _reset_guard():
    """The dedup guard is module state — isolate it."""
    import app.core.scheduler as sched

    before = sched._last_triggered_minute
    sched._last_triggered_minute = ""
    yield
    sched._last_triggered_minute = before


@pytest.fixture
def sched(monkeypatch):
    """scheduler with its session factory stubbed; settings set per test."""
    import app.core.scheduler as scheduler

    class _DB:
        def close(self):
            pass

    monkeypatch.setattr(scheduler, "SessionLocal", lambda: _DB())
    scheduler._cfg = {}
    monkeypatch.setattr(scheduler, "get_app_settings", lambda _db: scheduler._cfg)
    return scheduler


def _at(monkeypatch, sched, hh_mm):
    monkeypatch.setattr(sched, "datetime", _fake_datetime(hh_mm))


# ── _within_window ───────────────────────────────────────────────────────────

@pytest.mark.parametrize("now,expected", [
    ("01:59", False),
    ("02:00", True),    # inclusive lower bound
    ("04:00", True),
    ("06:00", True),    # inclusive upper bound
    ("06:01", False),
])
def test_a_normal_window_includes_both_endpoints(sched, monkeypatch, now, expected):
    _at(monkeypatch, sched, now)
    assert sched._within_window("02:00", "06:00") is expected


@pytest.mark.parametrize("now,expected", [
    ("21:59", False),
    ("22:00", True),
    ("23:59", True),
    ("00:30", True),    # after midnight, still inside
    ("02:00", True),
    ("02:01", False),
])
def test_a_window_spanning_midnight_wraps(sched, monkeypatch, now, expected):
    """
    start > end means the window crosses midnight. A naive `start <= now <= end`
    would be false for every minute of such a window, so the drain would never
    run at exactly the quiet hours it exists for.
    """
    _at(monkeypatch, sched, now)
    assert sched._within_window("22:00", "02:00") is expected


@pytest.mark.parametrize("start,end", [
    ("not a time", "06:00"),
    ("02:00", ""),
    (None, "06:00"),
    ("2:00 AM", "6:00 AM"),
])
def test_an_unparseable_window_is_closed_not_open(sched, monkeypatch, start, end):
    """
    Fails closed. Treating a malformed setting as "always open" would drain the
    whole backlog at full speed in the middle of the day — the exact burst the
    window exists to prevent.
    """
    _at(monkeypatch, sched, "04:00")
    assert sched._within_window(start, end) is False


# ── _tick: the guards before dispatch ────────────────────────────────────────

def _scan_stub(monkeypatch, sched):
    """Patch out the scan route and record thread dispatch."""
    import app.api.routes.scan as scan_route

    started = []
    monkeypatch.setattr(scan_route, "_scan_running", False, raising=False)
    monkeypatch.setattr(scan_route, "_run_scan",
                        lambda *a, **k: started.append(a), raising=False)

    import threading
    real_thread = threading.Thread

    class _Thread(real_thread):
        def start(self):
            started.append(self._args)

    monkeypatch.setattr(threading, "Thread", _Thread)
    return scan_route, started


def test_a_disabled_schedule_never_scans(sched, monkeypatch):
    _at(monkeypatch, sched, "03:00")
    _, started = _scan_stub(monkeypatch, sched)
    sched._cfg = {"scheduled_scan_enabled": False,
                  "scheduled_scan_times": ["03:00"],
                  "scan_paths": ["/media"]}

    asyncio.run(sched._tick(None))
    assert started == []


def test_no_configured_times_never_scans(sched, monkeypatch):
    _at(monkeypatch, sched, "03:00")
    _, started = _scan_stub(monkeypatch, sched)
    sched._cfg = {"scheduled_scan_enabled": True,
                  "scheduled_scan_times": [],
                  "scan_paths": ["/media"]}

    asyncio.run(sched._tick(None))
    assert started == []


def test_a_non_matching_minute_never_scans(sched, monkeypatch):
    _at(monkeypatch, sched, "03:01")
    _, started = _scan_stub(monkeypatch, sched)
    sched._cfg = {"scheduled_scan_enabled": True,
                  "scheduled_scan_times": ["03:00"],
                  "scan_paths": ["/media"]}

    asyncio.run(sched._tick(None))
    assert started == []


def test_a_matching_minute_dispatches_the_scan(sched, monkeypatch):
    _at(monkeypatch, sched, "03:00")
    _, started = _scan_stub(monkeypatch, sched)
    sched._cfg = {"scheduled_scan_enabled": True,
                  "scheduled_scan_times": ["01:00", "03:00"],
                  "scan_paths": ["/media/tv", "/media/films"]}

    asyncio.run(sched._tick(None))

    assert len(started) == 1
    paths, full, _loop = started[0]
    assert paths == ["/media/tv", "/media/films"]
    assert full is False, "scheduled scans are delta scans, not full rescans"


def test_the_same_minute_only_scans_once(sched, monkeypatch):
    """
    The loop wakes every 60s, so it can land in the same minute twice — most
    likely on a restart near a scheduled time. Without the guard that is two
    concurrent scans over the same library.
    """
    _at(monkeypatch, sched, "03:00")
    scan_route, started = _scan_stub(monkeypatch, sched)
    sched._cfg = {"scheduled_scan_enabled": True,
                  "scheduled_scan_times": ["03:00"],
                  "scan_paths": ["/media"]}

    asyncio.run(sched._tick(None))
    scan_route._scan_running = False      # pretend the first scan finished
    asyncio.run(sched._tick(None))

    assert len(started) == 1


def test_a_scan_already_running_is_not_doubled_up(sched, monkeypatch):
    """
    The other half of the same race: a manual scan started seconds earlier.
    Reads scan_route._scan_running as an ATTRIBUTE — importing the name would
    bind a stale copy and the check would always see False.
    """
    _at(monkeypatch, sched, "03:00")
    scan_route, started = _scan_stub(monkeypatch, sched)
    scan_route._scan_running = True
    sched._cfg = {"scheduled_scan_enabled": True,
                  "scheduled_scan_times": ["03:00"],
                  "scan_paths": ["/media"]}

    asyncio.run(sched._tick(None))

    assert started == []
    assert scan_route._scan_running is True, "clobbered a running scan's flag"


# ── _tick: the _scan_running lifecycle ───────────────────────────────────────

def test_no_scan_paths_releases_the_flag(sched, monkeypatch):
    """
    The regression this finally exists for. Scheduling enabled before scan
    paths are configured is entirely plausible, and the first version set the
    flag with no rollback — which permanently wedged ALL scanning, manual
    included, at the first scheduled tick.
    """
    _at(monkeypatch, sched, "03:00")
    scan_route, started = _scan_stub(monkeypatch, sched)
    sched._cfg = {"scheduled_scan_enabled": True,
                  "scheduled_scan_times": ["03:00"],
                  "scan_paths": []}

    asyncio.run(sched._tick(None))

    assert started == []
    assert scan_route._scan_running is False, (
        "_scan_running left set — all scanning is now wedged until restart"
    )


def test_a_thread_that_fails_to_start_releases_the_flag(sched, monkeypatch):
    """
    The only other thing between setting the flag and handing off. It must not
    leak either, which is why scan_thread_started is set AFTER t.start().
    """
    _at(monkeypatch, sched, "03:00")
    scan_route, _ = _scan_stub(monkeypatch, sched)
    sched._cfg = {"scheduled_scan_enabled": True,
                  "scheduled_scan_times": ["03:00"],
                  "scan_paths": ["/media"]}

    import threading

    class _Boom(threading.Thread):
        def start(self):
            raise RuntimeError("can't start new thread")

    monkeypatch.setattr(threading, "Thread", _Boom)

    with pytest.raises(RuntimeError):
        asyncio.run(sched._tick(None))

    assert scan_route._scan_running is False


def test_a_dispatched_scan_keeps_the_flag_set(sched, monkeypatch):
    """
    The finally must NOT clear the flag on the happy path — the scan thread
    owns it from here and clears it when it finishes.
    """
    _at(monkeypatch, sched, "03:00")
    scan_route, started = _scan_stub(monkeypatch, sched)
    sched._cfg = {"scheduled_scan_enabled": True,
                  "scheduled_scan_times": ["03:00"],
                  "scan_paths": ["/media"]}

    asyncio.run(sched._tick(None))

    assert len(started) == 1
    assert scan_route._scan_running is True


def test_an_already_running_scan_still_consumes_the_scheduled_minute(sched, monkeypatch):
    """
    _last_triggered_minute is set BEFORE the _scan_running check, so a trigger
    skipped because a scan is already in flight still burns that minute.

    That ordering is deliberate rather than an oversight. The scheduled scan's
    intent is "the library gets scanned around 03:00", and a scan running at
    03:00 satisfies it. Deferring the guard until after the check would mean a
    manual scan finishing mid-minute lets the scheduler immediately start a
    second pass over the same library for the same scheduled time.
    """
    _at(monkeypatch, sched, "03:00")
    scan_route, started = _scan_stub(monkeypatch, sched)
    scan_route._scan_running = True
    sched._cfg = {"scheduled_scan_enabled": True,
                  "scheduled_scan_times": ["03:00"],
                  "scan_paths": ["/media"]}

    asyncio.run(sched._tick(None))
    assert sched._last_triggered_minute == "03:00"

    scan_route._scan_running = False      # the manual scan finishes
    asyncio.run(sched._tick(None))

    assert started == [], "re-scanned the same library for the same scheduled time"


# ── _drain_tick: the guards ──────────────────────────────────────────────────

class _Entry:
    def __init__(self, path="/media/ep.mkv", language="eng"):
        self.id = 1
        self.expected_language = language
        self.media_file = type("MF", (), {"path": path})() if path else None


def _drain_stub(monkeypatch, sched, entry=None, exists=True):
    """Stub the session's query chain and os.path.exists for the drain."""
    deleted = []

    class _Q:
        def order_by(self, *_a):
            return self

        def first(self):
            return entry

    class _DB:
        def query(self, *_a):
            return _Q()

        def delete(self, obj):
            deleted.append(obj)

        def commit(self):
            pass

        def close(self):
            pass

    monkeypatch.setattr(sched, "SessionLocal", lambda: _DB())
    monkeypatch.setattr(sched.os.path, "exists", lambda _p: exists)
    return deleted


def _full_cfg(**over):
    cfg = {
        "plex_enabled": True,
        "plex_analyze_backlog_enabled": True,
        "plex_analyze_window_start": "02:00",
        "plex_analyze_window_end":   "06:00",
        "plex_url": "http://plex:32400/",
        "plex_token": "tok",
        "plex_path_mappings": [{"from": "/media", "to": "/data"}],
    }
    cfg.update(over)
    return cfg


@pytest.mark.parametrize("off", ["plex_enabled", "plex_analyze_backlog_enabled"])
def test_the_drain_is_inert_while_disabled(sched, monkeypatch, off):
    """
    Checked fresh every tick, so toggling the setting takes effect without a
    restart. Backlog entries are left untouched rather than drained, so
    re-enabling resumes in the same oldest-first order.
    """
    _at(monkeypatch, sched, "04:00")
    deleted = _drain_stub(monkeypatch, sched, entry=_Entry())
    sched._cfg = _full_cfg(**{off: False})

    sent = []
    monkeypatch.setattr(sched, "notify_plex_reprocessed_file",
                        lambda *a: sent.append(a) or True)

    assert asyncio.run(sched._drain_tick()) is False
    assert sent == []
    assert deleted == [], "drained an entry while the feature was off"


def test_outside_the_window_nothing_drains(sched, monkeypatch):
    _at(monkeypatch, sched, "12:00")
    deleted = _drain_stub(monkeypatch, sched, entry=_Entry())
    sched._cfg = _full_cfg()

    sent = []
    monkeypatch.setattr(sched, "notify_plex_reprocessed_file",
                        lambda *a: sent.append(a) or True)

    assert asyncio.run(sched._drain_tick()) is False
    assert sent == []
    assert deleted == []


def test_an_empty_backlog_is_a_quiet_no_op(sched, monkeypatch):
    _at(monkeypatch, sched, "04:00")
    _drain_stub(monkeypatch, sched, entry=None)
    sched._cfg = _full_cfg()

    assert asyncio.run(sched._drain_tick()) is False


def test_incomplete_plex_config_sends_nothing(sched, monkeypatch):
    """URL, token and mappings are all required to address an item."""
    _at(monkeypatch, sched, "04:00")
    _drain_stub(monkeypatch, sched, entry=_Entry())
    sched._cfg = _full_cfg(plex_path_mappings=[])

    sent = []
    monkeypatch.setattr(sched, "notify_plex_reprocessed_file",
                        lambda *a: sent.append(a) or True)

    assert asyncio.run(sched._drain_tick()) is False
    assert sent == []


# ── _drain_tick: processing one entry ────────────────────────────────────────

def test_a_drained_entry_reports_a_real_analyze(sched, monkeypatch):
    """
    True is what makes the caller wait the full 8s interval. Reporting False
    for a real analyze would pace the expensive call at 1s and burst Plex.
    """
    _at(monkeypatch, sched, "04:00")
    deleted = _drain_stub(monkeypatch, sched, entry=_Entry())
    sched._cfg = _full_cfg()

    sent = []
    monkeypatch.setattr(sched, "notify_plex_reprocessed_file",
                        lambda *a: sent.append(a) or True)

    assert asyncio.run(sched._drain_tick()) is True
    assert len(deleted) == 1

    url, token, mappings, path, lang = sent[0]
    assert url == "http://plex:32400", "trailing slash not stripped"
    assert token == "tok"
    assert path == "/media/ep.mkv"
    assert lang == "eng"


def test_a_skipped_analyze_reports_false(sched, monkeypatch):
    """
    notify_plex_reprocessed_file returns falsy when the item was already
    correct or only needed a cheap refresh — neither is worth an 8s pause.
    """
    _at(monkeypatch, sched, "04:00")
    _drain_stub(monkeypatch, sched, entry=_Entry())
    sched._cfg = _full_cfg()
    monkeypatch.setattr(sched, "notify_plex_reprocessed_file", lambda *a: False)

    assert asyncio.run(sched._drain_tick()) is False


def test_a_missing_file_is_dropped_from_the_backlog(sched, monkeypatch):
    """
    The row is deleted before the network call, so a file that no longer
    exists doesn't wedge the head of the queue forever.
    """
    _at(monkeypatch, sched, "04:00")
    deleted = _drain_stub(monkeypatch, sched, entry=_Entry(), exists=False)
    sched._cfg = _full_cfg()

    sent = []
    monkeypatch.setattr(sched, "notify_plex_reprocessed_file",
                        lambda *a: sent.append(a) or True)

    assert asyncio.run(sched._drain_tick()) is False
    assert len(deleted) == 1, "missing file left at the head of the queue"
    assert sent == []


def test_an_entry_whose_media_row_is_gone_is_dropped(sched, monkeypatch):
    _at(monkeypatch, sched, "04:00")
    deleted = _drain_stub(monkeypatch, sched, entry=_Entry(path=None))
    sched._cfg = _full_cfg()

    sent = []
    monkeypatch.setattr(sched, "notify_plex_reprocessed_file",
                        lambda *a: sent.append(a) or True)

    assert asyncio.run(sched._drain_tick()) is False
    assert len(deleted) == 1
    assert sent == []


def test_a_failing_analyze_does_not_take_the_loop_down(sched, monkeypatch):
    """
    Best-effort, like the rest of the Plex integration. Returns False so the
    next tick comes quickly rather than waiting the full interval for a call
    that may never have been sent.
    """
    _at(monkeypatch, sched, "04:00")
    deleted = _drain_stub(monkeypatch, sched, entry=_Entry())
    sched._cfg = _full_cfg()

    def _boom(*_a):
        raise ConnectionError("plex unreachable")

    monkeypatch.setattr(sched, "notify_plex_reprocessed_file", _boom)

    assert asyncio.run(sched._drain_tick()) is False
    assert len(deleted) == 1, "entry re-queued after a failure — will retry forever"


# ── Loop resilience ──────────────────────────────────────────────────────────

def test_a_raising_tick_does_not_kill_the_scheduler(sched, monkeypatch):
    """
    Both loops are perpetual and unsupervised. An escaping exception ends the
    task silently, and scheduled scans simply never happen again — with
    nothing in the UI to say so.
    """
    ticks = []

    async def _boom(_ws):
        ticks.append(1)
        raise RuntimeError("bad tick")

    class _Stop(Exception):
        pass

    async def _sleep(_s):
        if len(ticks) >= 2:
            raise _Stop
        return None

    monkeypatch.setattr(sched, "_tick", _boom)
    monkeypatch.setattr(sched.asyncio, "sleep", _sleep)

    with pytest.raises(_Stop):
        asyncio.run(sched.run_scheduler(None))

    assert len(ticks) == 2, "loop stopped at the first raising tick"


def test_the_drain_paces_real_analyzes_slower_than_skips(sched, monkeypatch):
    """
    The whole point of the bool _drain_tick returns. A real analyze is the
    expensive operation on Plex's side; everything else moves on almost
    immediately.
    """
    slept = []
    outcomes = [True, False]

    async def _tick():
        return outcomes.pop(0)

    class _Stop(Exception):
        pass

    async def _sleep(s):
        slept.append(s)
        if not outcomes:
            raise _Stop
        return None

    sched._cfg = _full_cfg()
    monkeypatch.setattr(sched, "_drain_tick", _tick)
    monkeypatch.setattr(sched.asyncio, "sleep", _sleep)

    with pytest.raises(_Stop):
        asyncio.run(sched.run_plex_backlog_drain())

    assert slept == [sched.PLEX_BACKLOG_DRAIN_INTERVAL_SECONDS,
                     sched.PLEX_BACKLOG_SKIP_INTERVAL_SECONDS]
    assert (sched.PLEX_BACKLOG_SKIP_INTERVAL_SECONDS
            < sched.PLEX_BACKLOG_DRAIN_INTERVAL_SECONDS)


def test_a_raising_drain_tick_waits_the_short_interval(sched, monkeypatch):
    """
    After a failure the outcome is unknown, so it must not be treated as a
    real analyze and pause for the full interval.
    """
    slept = []

    async def _tick():
        raise RuntimeError("boom")

    class _Stop(Exception):
        pass

    async def _sleep(s):
        slept.append(s)
        raise _Stop

    sched._cfg = _full_cfg()
    monkeypatch.setattr(sched, "_drain_tick", _tick)
    monkeypatch.setattr(sched.asyncio, "sleep", _sleep)

    with pytest.raises(_Stop):
        asyncio.run(sched.run_plex_backlog_drain())

    assert slept == [sched.PLEX_BACKLOG_SKIP_INTERVAL_SECONDS]
