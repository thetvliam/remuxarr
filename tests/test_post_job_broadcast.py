"""
worker.py — _run_and_broadcast, the post-job dispatch wiring.

Every job that finishes leaves through this function. It wraps _run_job in
a try/finally and, from the finally, decides three things: what the
"job_completed" WebSocket event says, whether a job that came back still
marked "processing" gets forced to "failed", and which of the four
notification channels are dispatched. It held all 90 of its own statements
uncovered.

This is the layer directly above test_post_job_notify.py. That file pins
what each loader returns; nothing pinned whether the caller acts on it.
The distinction matters because the gates live here, not in the loaders:
_load_post_job_data computes Sonarr and Radarr data unconditionally and
documents that the caller is what restricts them to successful jobs, so a
lost gate here is invisible to every test of the loader itself.

Two tests in test_assorted_regressions.py look like they already cover
this — test_finally_broadcast_survives_single_cancel and
test_normal_completion_unaffected_by_abort_branch — but both build a local
replica of the try/except/finally shape and drive that. They pin the
asyncio contract the abort path depends on, which is worth pinning and is
not what this file does; they never import _run_and_broadcast, so no
change to the real function can fail them.

Confirmed unprotected before this file was written: ten mutations applied
to _run_and_broadcast, each run against the whole 1239-test suite, all ten
survived. Dropping the re-raise on abort; disabling the stuck-at-
processing safety net; widening the *arr gate to everything except
cancelled; narrowing email to successes only; sending Sonarr's payload
through notify_radarr; hardcoding the broadcast status; skipping the Plex
dispatch; skipping the broadcast entirely; dropping the defensive copy;
and passing None to _run_job in place of the ws_manager.

The ws_manager pass-down is mutated deliberately rather than only the
logic: _run_job is a fake in every test here, so this file supplies the
argument it also asserts on, which is the shape that cannot see a parent
dropping it. Asserting identity against the object the caller was handed
is what makes that mutant visible.

Nine of the ten are killed here. The tenth — deleting the `final =
final.copy()` before the forced-failure substitution — survives and is
recorded as equivalent rather than left open. post_job["final"] is read
exactly once, on the line above the safety net, and neither it nor
post_job is read again except for the "sonarr" and "radarr" keys; the dict
itself is a fresh literal built per call inside _load_post_job_data, so
nothing outside this function can observe it being written through. The
copy is defensive against a future caller that reuses the dict, and no
assertion available here can distinguish it from its absence without
pinning the identity of an object the function is free to replace.
"""
import asyncio
from types import SimpleNamespace

import pytest

import app.core.worker as worker


# Opaque to _run_and_broadcast — it passes both straight to a trigger
# without reading a single key, so the contents only need to be
# distinguishable.
SONARR_DATA = {"entity_id": 11, "url": "http://sonarr:8989", "api_key": "S-KEY"}
RADARR_DATA = {"entity_id": 22, "url": "http://radarr:7878", "api_key": "R-KEY"}
PLEX_DATA   = {"plex": "refresh"}
EMAIL_DATA  = {"email": "failure"}

# Stand in for the two notifier functions. Identity is the whole point:
# the notifier is chosen at the call site and handed to a trigger that
# swallows every exception, so a crossed pair produces no signal anywhere
# except in which object was passed.
SONARR_NOTIFIER = object()
RADARR_NOTIFIER = object()
SONARR_RESTORER = object()
RADARR_RESTORER = object()

FORCED_REASON = "Job did not complete cleanly (finalisation failed)"
FORCED_ERROR  = "Finalisation failed — check container logs"


# ── Harness ──────────────────────────────────────────────────────────────────

class FakeWS:
    def __init__(self):
        self.sent = []

    async def broadcast_json(self, payload):
        self.sent.append(payload)


def _recorder(sink):
    """
    A stand-in trigger that records its arguments when it is *called*
    rather than when its body runs.

    The three triggers are dispatched with asyncio.create_task, so the
    coroutine body does not execute until the loop next yields — after
    _run_and_broadcast has already returned. Capturing at construction
    time makes every assertion below independent of whether the task was
    ever scheduled, which is what lets these tests assert dispatch without
    sleeping for it and without a timing-sensitive failure when they do.

    A real coroutine is still returned, because create_task requires one
    and pytest.ini turns "coroutine was never awaited" into an error.
    """
    def record(*args):
        sink.append(args)

        async def _dispatched():
            pass

        return _dispatched()

    return record


@pytest.fixture
def rig(monkeypatch):
    """
    _run_and_broadcast with every collaborator replaced by a recorder.

    The loaders are patched rather than backed by a database on purpose:
    what is under test is which of them get called and what happens to
    what they return, and test_post_job_notify.py already drives the real
    ones against real rows.
    """
    rig = SimpleNamespace(
        ws=FakeWS(),
        post_job=None,      # what _load_post_job_data hands back
        plex_data=None,
        email_data=None,
        during_job=None,    # awaited inside the fake _run_job
        run_job_args=[],
        emergency=[],
        arr=[],
        plex=[],
        email=[],
    )

    async def fake_run_job(job_id, ws_manager, loop):
        rig.run_job_args.append((job_id, ws_manager, loop))
        if rig.during_job is not None:
            await rig.during_job()

    monkeypatch.setattr(worker, "_run_job", fake_run_job)
    monkeypatch.setattr(worker, "_load_post_job_data",   lambda job_id: rig.post_job)
    monkeypatch.setattr(worker, "_load_plex_notify_data", lambda job_id: rig.plex_data)
    monkeypatch.setattr(worker, "_load_email_notify_data", lambda job_id: rig.email_data)
    monkeypatch.setattr(
        worker, "_emergency_fail_job",
        lambda job_id, reason: rig.emergency.append((job_id, reason)),
    )
    monkeypatch.setattr(worker, "_trigger_arr_notify",   _recorder(rig.arr))
    monkeypatch.setattr(worker, "_trigger_plex_notify",  _recorder(rig.plex))
    monkeypatch.setattr(worker, "_trigger_email_notify", _recorder(rig.email))
    monkeypatch.setattr(worker, "notify_sonarr", SONARR_NOTIFIER)
    monkeypatch.setattr(worker, "notify_radarr", RADARR_NOTIFIER)
    monkeypatch.setattr(worker, "restore_episode_quality", SONARR_RESTORER)
    monkeypatch.setattr(worker, "restore_movie_quality",   RADARR_RESTORER)
    return rig


def post_job(status="success", *, filename="Show.mkv", error=None,
             sonarr=None, radarr=None):
    """The shape _load_post_job_data returns."""
    return {
        "final": {
            "status":      status,
            "filename":    filename,
            "error":       error,
            "is_new_file": False,
            "output_path": "/media/Show.mkv",
        },
        "sonarr": sonarr,
        "radarr": radarr,
    }


def arr_calls(sink):
    """(data, notifier, restorer, service) per dispatch, with the loop dropped."""
    return [(args[0], args[2], args[3], args[4]) for args in sink]


def payloads(sink):
    return [args[0] for args in sink]


def run(rig, job_id=1):
    async def driver():
        loop = asyncio.get_running_loop()
        await worker._run_and_broadcast(job_id, rig.ws, loop)
        await asyncio.sleep(0)      # let the dispatched tasks finish

    asyncio.run(driver())


def run_and_abort(rig, job_id=1):
    """Cancel the task mid-job, the way abort_job does."""
    async def driver():
        loop = asyncio.get_running_loop()
        task = asyncio.create_task(worker._run_and_broadcast(job_id, rig.ws, loop))
        await asyncio.sleep(0.01)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        await asyncio.sleep(0)

    asyncio.run(driver())


# ── The broadcast ────────────────────────────────────────────────────────────

def test_a_finished_job_is_broadcast_with_its_final_state(rig):
    """
    Asserting the whole payload rather than a few keys: this dict is what
    every connected client renders the completed job from, so an extra
    key, a renamed one or a status that no longer tracks the job are all
    the same defect.
    """
    rig.post_job = post_job("failed", filename="Show.mkv", error="ffmpeg exited 1")

    run(rig)

    assert rig.ws.sent == [{
        "event":    "job_completed",
        "job_id":   1,
        "status":   "failed",
        "filename": "Show.mkv",
        "error":    "ffmpeg exited 1",
    }]


def test_the_job_is_run_with_the_ws_manager_it_was_given(rig):
    """
    The pass-down. _run_job broadcasts progress through this object, so
    losing it here would leave a job that runs correctly and reports
    nothing while it does.
    """
    rig.post_job = post_job()

    run(rig)

    assert len(rig.run_job_args) == 1
    job_id, ws_manager, loop = rig.run_job_args[0]
    assert job_id == 1
    assert ws_manager is rig.ws
    assert isinstance(loop, asyncio.AbstractEventLoop)


def test_a_job_whose_row_has_gone_broadcasts_nothing(rig):
    """
    _load_post_job_data returns None for a job deleted while it ran. The
    guard is what stops that becoming a TypeError on a background task
    with nobody awaiting it.
    """
    rig.post_job = None
    rig.plex_data = PLEX_DATA
    rig.email_data = EMAIL_DATA

    run(rig)

    assert rig.ws.sent == []
    assert (rig.arr, rig.plex, rig.email) == ([], [], [])


# ── The two abnormal exits ───────────────────────────────────────────────────

def test_a_crash_in_the_job_is_swallowed_and_still_broadcast(rig):
    """
    _run_job raising must not escape into the worker loop, and must not
    cost the client its completion event — without the broadcast the UI
    sits at whatever percentage the job died at.
    """
    async def boom():
        raise RuntimeError("ffmpeg blew up")

    rig.during_job = boom
    rig.post_job = post_job("failed", error="ffmpeg exited 1")

    run(rig)

    assert [m["status"] for m in rig.ws.sent] == ["failed"]


def test_an_aborted_job_stays_cancelled_and_still_broadcasts(rig):
    """
    The real function, against the contract test_assorted_regressions.py
    pins on a replica. Both halves matter and they pull against each
    other: the task must still end cancelled, so the except branch has to
    re-raise, and the finally must still broadcast, so every other client
    learns the job stopped rather than only the one that clicked abort.
    """
    rig.during_job = lambda: asyncio.sleep(10)
    rig.post_job = post_job("cancelled")

    run_and_abort(rig)

    assert [m["status"] for m in rig.ws.sent] == ["cancelled"]


# ── The stuck-at-processing safety net ───────────────────────────────────────

def test_a_job_left_processing_is_forced_to_failed(rig):
    """
    A job still marked "processing" after _run_job returned means
    finalisation did not commit. Without this the row never leaves
    "processing" and the frontend holds at 100% forever.
    """
    rig.post_job = post_job("processing")

    run(rig)

    assert rig.emergency == [(1, FORCED_REASON)]
    assert rig.ws.sent == [{
        "event":    "job_completed",
        "job_id":   1,
        "status":   "failed",
        "filename": "Show.mkv",
        "error":    FORCED_ERROR,
    }]


def test_the_forced_failure_reaches_email_but_not_the_arrs(rig):
    """
    The substitution happens before the notification gates read the
    status, so a forced failure is treated as a failure throughout: no
    rescan is triggered for a job that did not finish, and the failure
    still reaches the email breaker. Reading the gates off the original
    "processing" would silently do neither.
    """
    rig.post_job = post_job("processing", sonarr=SONARR_DATA, radarr=RADARR_DATA)
    rig.plex_data = PLEX_DATA
    rig.email_data = EMAIL_DATA

    run(rig)

    assert rig.arr == []
    assert rig.plex == []
    assert payloads(rig.email) == [EMAIL_DATA]


# ── Which notifications go out ───────────────────────────────────────────────

def test_each_arr_is_dispatched_through_its_own_notifier(rig):
    """
    The crossed-wire guard, one layer above the identical one in
    test_post_job_notify.py. The notifier is chosen here and handed to a
    trigger that swallows every exception, so Sonarr's payload sent with
    notify_radarr produces no error anywhere — the rescan simply never
    happens.
    """
    rig.post_job = post_job("success", sonarr=SONARR_DATA, radarr=RADARR_DATA)

    run(rig)

    assert arr_calls(rig.arr) == [
        (SONARR_DATA, SONARR_NOTIFIER, SONARR_RESTORER, "Sonarr"),
        (RADARR_DATA, RADARR_NOTIFIER, RADARR_RESTORER, "Radarr"),
    ]


def test_an_arr_the_loader_declined_is_not_dispatched(rig):
    """None from the loader means unconfigured or not applicable to this
    job, and is the only signal the caller gets."""
    rig.post_job = post_job("success", sonarr=None, radarr=RADARR_DATA)

    run(rig)

    assert arr_calls(rig.arr) == [
        (RADARR_DATA, RADARR_NOTIFIER, RADARR_RESTORER, "Radarr"),
    ]


def test_plex_is_dispatched_when_the_loader_returns_data(rig):
    rig.post_job = post_job("success")
    rig.plex_data = PLEX_DATA

    run(rig)

    assert payloads(rig.plex) == [PLEX_DATA]


def test_plex_is_not_dispatched_when_the_loader_declines(rig):
    rig.post_job = post_job("success")
    rig.plex_data = None

    run(rig)

    assert rig.plex == []


@pytest.mark.parametrize("status", ["failed", "cancelled", "dry_run"])
def test_only_a_success_reaches_the_arrs_and_plex(rig, status):
    """
    The gate that _load_post_job_data's docstring delegates here: it
    builds Sonarr and Radarr data for every job and states that the caller
    is what restricts them to successes. Telling Sonarr to rescan after a
    failed remux points it at a file that was never replaced.
    """
    rig.post_job = post_job(status, sonarr=SONARR_DATA, radarr=RADARR_DATA)
    rig.plex_data = PLEX_DATA

    run(rig)

    assert rig.arr == []
    assert rig.plex == []


@pytest.mark.parametrize("status", ["success", "failed", "dry_run"])
def test_email_runs_for_successes_and_failures_alike(rig, status):
    """
    Email is the one channel that fires on both outcomes, because the
    consecutive-failure breaker needs the successes too — a success resets
    it. Narrowing this to failures would leave the breaker latched after
    the first bad job and mail on every subsequent one.
    """
    rig.post_job = post_job(status)
    rig.email_data = EMAIL_DATA

    run(rig)

    assert payloads(rig.email) == [EMAIL_DATA]


def test_a_cancelled_job_sends_no_email(rig):
    """A user aborting a job is not a failure and must not move the
    breaker in either direction."""
    rig.post_job = post_job("cancelled")
    rig.email_data = EMAIL_DATA

    run(rig)

    assert rig.email == []
