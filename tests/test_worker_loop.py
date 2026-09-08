"""
worker.py — _loop, the concurrent job pool.

One iteration prunes finished tasks, reads the concurrency limit, gives
the AC3 forge first refusal on any free slot, then claims regular jobs
until the pool is full. Nothing else drives it, and none of its
scheduling body was covered.

The part worth the most here is the bookkeeping. A running job is tracked
in three places at once — the loop's local pool, the `_active_jobs` id
set, and the `_active_task_registry` that holds the Task object — and the
module comment above them says they are updated in lockstep so that
abort_job, called from an API route outside the loop, can find a specific
running job's Task and cancel it. Nothing enforced that. Dropping any one
of the three lines leaves a pool that looks correct from two angles and
is wrong from the third: a slot occupied forever, an id set that grows
without bound, or an abort button that silently does nothing because the
Task it needs was never registered or never removed.

Two of the mutations below are regressions this code has already had, and
both are recorded in the comments rather than in any test. Forge priority
once required the regular pool to be completely empty, which under a
continuous backlog never happens above max_concurrent_jobs=1 — the claim
loop refills a freed slot in the same iteration — so forge jobs starved
at every concurrency setting but one. And a started forge was once not
counted toward the total, so regular claims filled every slot on top of
it and the pool ran one over its limit. Both are reproduced as mutants
and both are now caught.

Speed note: _loop paces itself with a one-second sleep per iteration and
a five-second back-off after an error. Left real, this file would add
close to a minute to a suite that otherwise runs in well under one, so
the worker module's reference to asyncio is swapped for a proxy that
forwards everything except sleep. That is a change to the test's view of
the module, not to the loop's logic — the iteration count is controlled
by _running, exactly as it is in production.

Confirmed unprotected before this file was written: twelve mutations,
each run against the whole 1330-test suite, all twelve survived. Leaving
the finished task in the pool; leaking the id set; leaking the task
registry; never clearing a finished forge; dropping the lower bound on
the limit; ignoring the pause flag; requiring an empty pool before forge
runs; not counting a started forge; running one job over the limit; not
tracking a claimed job's id; not registering its Task; and letting one
failed iteration end the worker. All twelve are killed by the tests
below.
"""
import asyncio
from types import SimpleNamespace

import pytest

import app.api.ws_manager as ws_module
import app.core.worker as worker


class _NoWaitAsyncio:
    """
    Stands in for the asyncio module inside worker.py, with sleep reduced
    to a single yield.

    Everything else is forwarded to the real module, including
    CancelledError and create_task — _loop catches the former by
    reference off this same name, so it has to be the real class.
    """
    @staticmethod
    async def sleep(delay, *args, **kwargs):
        return await asyncio.sleep(0)

    def __getattr__(self, name):
        return getattr(asyncio, name)


class FakeWS:
    def __init__(self):
        self.sent = []

    async def broadcast_json(self, payload):
        self.sent.append(payload)


class _NullSession:
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


@pytest.fixture
def rig(monkeypatch):
    """
    _loop with its collaborators replaced and its iteration count under
    the test's control.

    The settings read happens once per iteration, before any scheduling,
    which makes it the natural place to count iterations and to switch
    _running off — the same flag stop_worker uses.
    """
    rig = SimpleNamespace(
        iterations=1,
        iteration=0,
        max_jobs=1,
        claims=[],
        jobs_finish=False,
        forge_pending=False,
        forge_pending_from=None,
        forge_finishes=False,
        failing_iterations=set(),
        started=[],
        forge_runs=0,
        forge_checks=[],
        ws=FakeWS(),
    )

    def fake_settings(db):
        rig.iteration += 1
        if rig.iteration >= rig.iterations:
            worker._running = False
        if rig.iteration in rig.failing_iterations:
            raise RuntimeError("settings read failed")
        return {"max_concurrent_jobs": rig.max_jobs}

    def fake_claim():
        return rig.claims.pop(0) if rig.claims else None

    def fake_has_pending_forge():
        rig.forge_checks.append(rig.iteration)
        if rig.forge_pending_from is not None:
            return rig.iteration >= rig.forge_pending_from
        return rig.forge_pending

    async def fake_run_and_broadcast(job_id, ws_manager, loop):
        rig.started.append(job_id)
        if not rig.jobs_finish:
            await asyncio.Event().wait()      # occupies its slot

    async def fake_process_next_forge(ws_manager):
        rig.forge_runs += 1
        if not rig.forge_finishes:
            await asyncio.Event().wait()
        return True

    monkeypatch.setattr(worker, "asyncio", _NoWaitAsyncio())
    monkeypatch.setattr(worker, "SessionLocal", lambda: _NullSession())
    monkeypatch.setattr(worker, "get_app_settings", fake_settings)
    monkeypatch.setattr(worker, "_claim_next", fake_claim)
    monkeypatch.setattr(worker, "_has_pending_forge", fake_has_pending_forge)
    monkeypatch.setattr(worker, "_run_and_broadcast", fake_run_and_broadcast)
    monkeypatch.setattr(worker, "_process_next_forge", fake_process_next_forge)
    monkeypatch.setattr(worker, "_paused", False)
    monkeypatch.setattr(ws_module, "ws_manager", rig.ws)

    # Module-level and shared between tests, so both are emptied on the
    # way in as well as the way out.
    worker._active_jobs.clear()
    worker._active_task_registry.clear()
    yield rig
    worker._running = False
    worker._active_jobs.clear()
    worker._active_task_registry.clear()


def run(rig):
    """Run the loop for rig.iterations iterations, then clean up."""
    async def driver():
        worker._running = True
        try:
            await worker._loop()
        finally:
            pending = [t for t in asyncio.all_tasks()
                       if t is not asyncio.current_task()]
            for task in pending:
                task.cancel()
            await asyncio.gather(*pending, return_exceptions=True)

    asyncio.run(driver())


def started_events(rig):
    return [m["job_id"] for m in rig.ws.sent if m.get("event") == "job_started"]


# ── Tracking a running job ───────────────────────────────────────────────────

def test_a_claimed_job_is_tracked_where_abort_can_find_it(rig):
    """
    abort_job runs from an API route, outside the loop, and needs the
    Task object to cancel. It reads _active_task_registry; the pause
    logic reads _active_jobs. Both are written here and neither has any
    other writer on this path.
    """
    rig.claims = [7]

    run(rig)

    assert worker._active_jobs == {7}
    assert list(worker._active_task_registry) == [7]
    assert isinstance(worker._active_task_registry[7], asyncio.Task)


def test_a_finished_job_is_released_from_all_three_places(rig):
    """
    The lockstep invariant. Claiming a second job at all proves the local
    pool was pruned, since the limit is one; the two module registries
    are asserted directly. Any one of the three left behind is a
    different bug and none of them raises.
    """
    rig.iterations = 3
    rig.jobs_finish = True
    rig.claims = [1, 2]

    run(rig)

    assert rig.started == [1, 2]
    assert worker._active_jobs == set()
    assert worker._active_task_registry == {}


# ── The concurrency limit ────────────────────────────────────────────────────

def test_no_more_than_the_limit_runs_at_once(rig):
    rig.max_jobs = 2
    rig.claims = [1, 2, 3]

    run(rig)

    assert rig.started == [1, 2]


def test_a_zero_limit_still_runs_one_job(rig):
    """
    max_concurrent_jobs is user-editable. A zero or negative value should
    slow the worker to one job, not stop it dead with no indication of
    why nothing is processing.
    """
    rig.max_jobs = 0
    rig.claims = [1]

    run(rig)

    assert rig.started == [1]


def test_a_paused_worker_claims_nothing(rig, monkeypatch):
    """
    Pause has to stop the claim before it happens: a claimed job is
    already marked processing in the database, so claiming while paused
    would leave a row nothing is working on.
    """
    monkeypatch.setattr(worker, "_paused", True)
    rig.claims = [1]

    run(rig)

    assert rig.started == []
    assert rig.forge_checks == []


# ── Forge priority ───────────────────────────────────────────────────────────

def test_forge_takes_a_free_slot_ahead_of_regular_jobs(rig):
    """
    Forge work is queued by hand and would otherwise never run under a
    continuous main-queue backlog, since the claim loop takes every slot
    the moment one frees.
    """
    rig.forge_pending = True
    rig.claims = [1]

    run(rig)

    assert rig.forge_runs == 1
    assert rig.started == []


def test_forge_does_not_have_to_wait_for_an_empty_pool(rig):
    """
    The starvation regression. Requiring the regular pool to be empty
    reads as equivalent and is not: with a job still running and a slot
    free, forge must take the slot. Under a backlog the pool is never
    empty, so the old condition meant forge effectively never ran above
    max_concurrent_jobs=1.
    """
    rig.iterations = 2
    rig.max_jobs = 2
    rig.claims = [1]                 # one job runs, one slot stays free
    rig.forge_pending_from = 2

    run(rig)

    assert rig.started == [1]
    assert rig.forge_runs == 1


def test_a_forge_slot_still_leaves_room_for_the_others(rig):
    """
    Forge taking one of three slots should leave two for regular jobs,
    not zero. The count has to include the forge task started this same
    iteration, or the pool runs over its limit.
    """
    rig.max_jobs = 3
    rig.forge_pending = True
    rig.claims = [1, 2, 3, 4]

    run(rig)

    assert rig.forge_runs == 1
    assert rig.started == [1, 2]


def test_only_one_forge_runs_at_a_time(rig):
    rig.iterations = 2
    rig.max_jobs = 3
    rig.forge_pending = True

    run(rig)

    assert rig.forge_runs == 1
    assert rig.forge_checks == [1]      # not even asked again while busy


def test_a_finished_forge_frees_the_forge_slot(rig):
    """Without the clear, one forge job would be the last one ever run."""
    rig.iterations = 2
    rig.max_jobs = 3
    rig.forge_pending = True
    rig.forge_finishes = True

    run(rig)

    assert rig.forge_runs == 2


# ── Announcements and resilience ─────────────────────────────────────────────

def test_every_started_job_is_announced(rig):
    rig.max_jobs = 2
    rig.claims = [4, 9]

    run(rig)

    assert started_events(rig) == [4, 9]


def test_a_failed_iteration_does_not_stop_the_worker(rig):
    """
    The loop is the only thing draining the queue and nothing restarts
    it. A transient database error must cost one iteration, not the
    worker.
    """
    rig.iterations = 2
    rig.failing_iterations = {1}
    rig.claims = [1]

    run(rig)

    assert rig.started == [1]
