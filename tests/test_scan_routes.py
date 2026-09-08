"""
The scan route: who owns the running flag, and what resets it.

scan_library is covered by test_scan_library.py. This is the wiring around
it — the flag that allows one scan at a time, the thread that carries it,
the cancel endpoint, and the progress and broadcast plumbing in _run_scan.
None of it had ever run in a test: trigger_scan and _run_scan were at zero,
and the module sat at 42%.

The failure this exists to prevent is not a crash. `_scan_running` is a
module-level bool with a documented single-owner lifecycle: whoever sets it
either hands ownership to a thread that actually started, or rolls it back
themselves on every other exit. When that contract breaks the app answers
409 "A scan is already in progress" to every scan, manual and scheduled,
until someone restarts the container — and there is no scan running to
cancel, because the flag is lying. The module's own comments record that
this shipped once already: the first version of the fix set the flag with
no rollback, so an empty scan_paths list raising the 400 left it stuck.

The cancel flag is the same shape one step along. It is read by
scan_library once per file and cleared by _run_scan at the start of each
run. Cancel with no scan running, or fail to clear it, and the flag
survives to kill the *next* scan a few files in — which looks like a scan
that stopped for no reason.

Fifteen mutants, all confirmed surviving the full suite before this file
existed:

  trigger    the 409 guard removed                            killed
             the flag never set                               killed
             the rollback removed from the finally            killed
             the thread-started flag never recorded           killed
             configured scan_paths ignored                    killed
             force_probe not passed to the thread             killed
  cancel     cancelling with no scan running allowed          killed
             the cancel flag not set                          killed
  the thread a stale cancel not cleared at the start          killed
             the progress counters not reset                  killed
             the worker paused on the wrong branch            killed
             the final progress broadcast never sent          killed
             the running flag not cleared in the finally      killed
             no completion broadcast when the scan raises     killed
             cancel_check wired to a constant False           killed

Each test names the one it closes.

The thread is never started here. trigger_scan is asserted on what it hands
to Thread(), and _run_scan is called directly, because starting a real
thread and waiting for it turns every assertion into a race and the two
halves are separately meaningful anyway.

Run from the project root:
    pytest tests/test_scan_routes.py -v
"""
import asyncio

import pytest
from fastapi import HTTPException

from app.api.routes import scan as scan_routes


@pytest.fixture(autouse=True)
def _clean_module_state():
    """
    _scan_running, _scan_progress and _scan_cancel_requested are module
    globals that outlive any one test.

    Reset on the way in as well as out: a test that leaves the running flag
    set makes every later trigger raise 409, and reading that failure as a
    fault in the test that reports it is exactly the wrong place to look.
    """
    def _reset():
        scan_routes._scan_running = False
        scan_routes._scan_cancel_requested = False
        scan_routes._scan_progress = {"scanned": 0, "total": 0}

    _reset()
    yield
    _reset()


@pytest.fixture
def thread(monkeypatch):
    """
    Capture what trigger_scan hands to Thread() instead of starting one.

    A real scan thread would race every assertion below, and what is being
    checked is the handover: the arguments, and whether the flag survives
    it.
    """
    captured = {}

    class _FakeThread:
        def __init__(self, target=None, args=(), name=None, daemon=None):
            captured.update(target=target, args=args, name=name,
                            daemon=daemon, started=False)

        def start(self):
            captured["started"] = True

    monkeypatch.setattr(scan_routes.threading, "Thread", _FakeThread)
    return captured


def _settings(monkeypatch, **cfg):
    monkeypatch.setattr(scan_routes, "get_app_settings", lambda _db: cfg)


def _trigger(paths=None, force_probe=False):
    body = scan_routes.ScanRequest(paths=paths, force_probe=force_probe)
    return asyncio.run(scan_routes.trigger_scan(body, db=object()))


# ── Starting a scan ───────────────────────────────────────────────────────────

def test_a_second_scan_is_refused_while_one_is_running(monkeypatch, thread):
    """
    Closes: the 409 guard removed. Two scans against the same database is
    what the flag exists to prevent, and the scheduler fires on a timer
    regardless of what anyone clicked.
    """
    _settings(monkeypatch, scan_paths=["/media"])
    _trigger()

    with pytest.raises(HTTPException) as raised:
        _trigger()

    assert raised.value.status_code == 409


def test_the_flag_is_set_before_the_thread_starts(monkeypatch, thread):
    """
    Closes: the flag never set.

    Set synchronously before any await, which is what closes the window
    between a manual trigger and a scheduled one. Asserted through the
    status endpoint rather than the global, since that is what the UI and
    the next caller both read.
    """
    _settings(monkeypatch, scan_paths=["/media"])
    _trigger()

    assert scan_routes.scan_status()["running"] is True


def test_a_trigger_that_never_started_a_thread_releases_the_flag(monkeypatch,
                                                                 thread):
    """
    Closes: the rollback removed from the finally, and the thread-started
    flag never recorded.

    This is the recorded incident. The flag is set before the work that
    can fail, so an empty scan_paths list raising the 400 must not leave
    it set — that state answers 409 to every future scan, manual and
    scheduled, until the container restarts.
    """
    _settings(monkeypatch)                       # no scan_paths configured

    with pytest.raises(HTTPException) as raised:
        _trigger()

    assert raised.value.status_code == 400
    assert scan_routes.scan_status()["running"] is False, "the flag stuck"


def test_a_started_scan_keeps_the_flag_the_rollback_did_not_take_it(monkeypatch,
                                                                    thread):
    """
    Closes: the thread-started flag never recorded, which would roll the
    flag back under a scan that is genuinely running — so the UI reports
    idle and a second scan is allowed straight through.

    The other half of the test above: one says release it when the thread
    never started, this says keep it when it did. Either alone is passed
    by a version that always does the same thing.
    """
    _settings(monkeypatch, scan_paths=["/media"])
    _trigger()

    assert thread["started"] is True
    assert scan_routes.scan_status()["running"] is True


def test_the_configured_scan_paths_are_used_when_none_are_given(monkeypatch,
                                                                thread):
    """
    Closes: the configured paths ignored. The scheduler and the dashboard
    button both post an empty body, so this is the path almost every scan
    takes; ignoring settings turns them into a 400.
    """
    _settings(monkeypatch, scan_paths=["/media/tv", "/media/movies"])

    result = _trigger()

    assert result["paths"] == ["/media/tv", "/media/movies"]
    assert thread["args"][0] == ["/media/tv", "/media/movies"]


def test_a_requested_path_overrides_the_configured_ones(monkeypatch, thread):
    _settings(monkeypatch, scan_paths=["/media/tv"])

    result = _trigger(paths=["/media/one-off"])

    assert result["paths"] == ["/media/one-off"]
    assert thread["args"][0] == ["/media/one-off"]


def test_force_probe_reaches_the_scan_thread(monkeypatch, thread):
    """
    Closes: force_probe replaced by a literal in the thread arguments.

    A full scan that quietly runs as a delta scan finds nothing wrong and
    reports success, leaving files that changed without changing size or
    mtime on stale probe data indefinitely.
    """
    _settings(monkeypatch, scan_paths=["/media"])

    _trigger(force_probe=True)
    assert thread["args"][1] is True


# ── Cancelling ────────────────────────────────────────────────────────────────

def test_cancelling_with_no_scan_running_is_refused(monkeypatch):
    """
    Closes: the guard removed from cancel_scan.

    Without it the request succeeds and sets a flag nothing will clear
    until the next scan starts — which then stops a few files in, for a
    reason that happened before it began.
    """
    with pytest.raises(HTTPException) as raised:
        scan_routes.cancel_scan()

    assert raised.value.status_code == 400
    assert scan_routes._scan_cancel_requested is False


def test_cancelling_a_running_scan_sets_the_flag_the_scan_reads(monkeypatch,
                                                                thread):
    """
    Closes: the flag not set. The endpoint returns {"cancelling": True}
    either way, so the only thing separating a working cancel button from
    a decorative one is this global.
    """
    _settings(monkeypatch, scan_paths=["/media"])
    _trigger()

    assert scan_routes.cancel_scan() == {"cancelling": True}
    assert scan_routes._scan_cancel_requested is True


# ── The scan thread ───────────────────────────────────────────────────────────

class _Stats:
    queued = 3
    manual_review = 1
    errors = 0
    total = 4
    removed = 2
    cancelled = False


@pytest.fixture
def thread_rig(monkeypatch):
    """Run _run_scan for real, with its collaborators recorded."""
    rig = {"broadcasts": [], "paused": 0, "scan_kwargs": None}

    monkeypatch.setattr(scan_routes, "broadcast_threadsafe",
                        lambda data, loop: rig["broadcasts"].append(data))
    monkeypatch.setattr(scan_routes, "SessionLocal", lambda: _FakeSession())
    monkeypatch.setattr(scan_routes, "pause_worker",
                        lambda: rig.__setitem__("paused", rig["paused"] + 1))

    def _scan(db, paths, **kwargs):
        rig["scan_kwargs"] = kwargs
        return _Stats()

    monkeypatch.setattr(scan_routes, "scan_library", _scan)
    monkeypatch.setattr(scan_routes, "get_app_settings", lambda _db: {})
    return rig


class _FakeSession:
    def close(self):
        pass


def _events(rig):
    return [b["event"] for b in rig["broadcasts"]]


def test_the_running_flag_is_released_when_the_scan_ends(thread_rig):
    """
    Closes: the flag not cleared in the finally.

    Once the thread is running this is the sole reset point, so losing it
    means every later scan is refused with 409 and the cancel endpoint
    accepts requests for a scan that finished.
    """
    scan_routes._scan_running = True

    scan_routes._run_scan(["/media"], False, object())

    assert scan_routes.scan_status()["running"] is False


def test_a_scan_that_raises_still_releases_the_flag_and_says_so(thread_rig,
                                                               monkeypatch):
    """
    Closes: the completion broadcast dropped from the failure path.

    The dashboard starts a spinner on scan_started and stops it on
    scan_completed. Without one on failure the spinner runs until the page
    is reloaded, which reads as a scan that is still going.
    """
    def _boom(*a, **kw):
        raise RuntimeError("ffprobe exploded")

    monkeypatch.setattr(scan_routes, "scan_library", _boom)
    scan_routes._scan_running = True

    scan_routes._run_scan(["/media"], False, object())

    assert _events(thread_rig) == ["scan_started", "scan_completed"]
    assert scan_routes.scan_status()["running"] is False


def test_a_stale_cancel_does_not_kill_the_next_scan(thread_rig):
    """
    Closes: the cancel flag not cleared at the start of a run.

    A cancel that arrives as a scan is finishing leaves the flag set with
    nothing left to read it. The next scan then stops on its first file,
    and nothing in the UI explains why.
    """
    scan_routes._scan_cancel_requested = True

    scan_routes._run_scan(["/media"], False, object())

    assert scan_routes._scan_cancel_requested is False


def test_the_cancel_flag_is_what_the_scan_actually_checks(thread_rig):
    """
    Closes: cancel_check wired to a constant. The endpoint would set the
    flag, the UI would report cancelling, and the scan would run to
    completion regardless.
    """
    scan_routes._run_scan(["/media"], False, object())
    check = thread_rig["scan_kwargs"]["cancel_check"]

    assert check() is False
    scan_routes._scan_cancel_requested = True
    assert check() is True


def test_the_progress_counters_start_from_zero(thread_rig):
    """
    Closes: the progress reset removed. The status endpoint would serve
    the previous scan's numbers until the new one caught up, so a scan of
    a small folder after a large one shows a bar going backwards.
    """
    scan_routes._scan_progress = {"scanned": 900, "total": 1000}

    scan_routes._run_scan(["/media"], False, object())
    on_progress = thread_rig["scan_kwargs"]["progress_callback"]

    assert scan_routes.scan_status()["scanned"] == 0
    on_progress(1, 50)
    assert scan_routes.scan_status() == {"running": False, "scanned": 1,
                                         "total": 50}


def test_the_last_file_always_gets_a_progress_broadcast(thread_rig):
    """
    Closes: the scanned == total condition dropped from the throttle.

    Progress is throttled to one broadcast per ten files so a large
    library does not flood the socket, which means a scan of 34 files
    would otherwise send its last at 30 and leave the bar at 30/34 for
    good.
    """
    scan_routes._run_scan(["/media"], False, object())
    on_progress = thread_rig["scan_kwargs"]["progress_callback"]

    for scanned in range(1, 35):
        on_progress(scanned, 34)

    progress = [b for b in thread_rig["broadcasts"]
                if b["event"] == "scan_progress"]
    assert progress[-1] == {"event": "scan_progress", "scanned": 34,
                            "total": 34}


def test_the_worker_is_paused_only_when_auto_start_is_off(thread_rig,
                                                          monkeypatch):
    """
    Closes: the pause moved to the wrong branch.

    Inverted, a scan with auto-start enabled pauses the worker and nothing
    resumes it, so the queue fills and nothing is processed until someone
    finds the Resume button.
    """
    monkeypatch.setattr(scan_routes, "get_app_settings",
                        lambda _db: {"auto_start_jobs": True})
    scan_routes._run_scan(["/media"], False, object())
    assert thread_rig["paused"] == 0

    monkeypatch.setattr(scan_routes, "get_app_settings",
                        lambda _db: {"auto_start_jobs": False})
    scan_routes._run_scan(["/media"], False, object())
    assert thread_rig["paused"] == 1
