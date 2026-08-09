"""
Background task lifecycle: registration, death reporting, and shutdown.

These cover app/main.py's _spawn / _on_task_done / _cancel_background_tasks,
which had no suite coverage despite being the fix for two production bugs:
long-lived services being garbage-collected mid-await, and those same services
dying without a log line.

Grown from the ad-hoc script used to verify the original change. Only the fast,
deterministic checks are here — the large-file event-loop timing check stays a
manual tool, since a multi-hundred-megabyte copy has no place in a suite that
runs in seconds.
"""
import asyncio
import logging

import pytest


import app.main as m


@pytest.fixture(autouse=True)
def _clean_registry():
    """Never let one test's tasks leak into the next."""
    m._background_tasks.clear()
    yield
    m._background_tasks.clear()


# ── Registration ─────────────────────────────────────────────────────────────

def test_spawn_holds_a_strong_reference():
    """
    The whole point of the registry: asyncio only weakly references running
    tasks, so a task with no other referent can be collected mid-await.
    """
    async def driver():
        started = asyncio.Event()

        async def forever():
            started.set()
            await asyncio.sleep(3600)

        task = m._spawn(forever(), name="probe")
        await started.wait()
        assert task in m._background_tasks
        assert task.get_name() == "probe"

        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

    asyncio.run(driver())


def test_registry_discards_completed_tasks():
    """Must not grow for the life of the process."""
    async def driver():
        async def quick():
            return 1

        task = m._spawn(quick(), name="quick")
        await task
        await asyncio.sleep(0)          # let the done-callback run
        assert task not in m._background_tasks
        assert not m._background_tasks

    asyncio.run(driver())


# ── Death reporting ──────────────────────────────────────────────────────────

def test_task_that_raises_is_logged_with_traceback(caplog):
    """
    A perpetual service that raises must report at the moment it dies. Without
    retrieving the exception here, asyncio emits "Task exception was never
    retrieved" at some later garbage collection, detached from the failure and
    easy to dismiss as noise.
    """
    async def driver():
        async def crashes():
            await asyncio.sleep(0)
            raise RuntimeError("Plex API returned garbage")

        m._spawn(crashes(), name="plex-backlog-drain")
        await asyncio.sleep(0.05)

    with caplog.at_level(logging.ERROR):
        asyncio.run(driver())

    errors = [r for r in caplog.records if r.levelno >= logging.ERROR]
    assert errors, "a task that raised produced no ERROR log"
    rec = errors[0]
    assert "plex-backlog-drain" in rec.getMessage(), "log does not name the task"
    assert rec.exc_info is not None, "log carries no traceback"
    assert isinstance(rec.exc_info[1], RuntimeError)


def test_cancelled_task_is_not_logged_as_an_error(caplog):
    """Cancellation is the expected shutdown outcome, not a failure."""
    async def driver():
        async def forever():
            await asyncio.sleep(3600)

        m._spawn(forever(), name="scheduler")
        await asyncio.sleep(0)
        await m._cancel_background_tasks()

    with caplog.at_level(logging.ERROR):
        asyncio.run(driver())

    assert not [r for r in caplog.records if r.levelno >= logging.ERROR], \
        "cancellation during shutdown was logged as an error"


def test_clean_completion_is_silent(caplog):
    async def driver():
        async def ok():
            return "fine"

        m._spawn(ok(), name="oneshot")
        await asyncio.sleep(0.02)

    with caplog.at_level(logging.ERROR):
        asyncio.run(driver())

    assert not [r for r in caplog.records if r.levelno >= logging.ERROR]


# ── Shutdown ─────────────────────────────────────────────────────────────────

def test_cancel_runs_task_cleanup_and_drains_registry():
    """Each service's own `finally` must actually run, not be torn down with the loop."""
    async def driver():
        ran = []

        async def service():
            try:
                await asyncio.sleep(3600)
            finally:
                ran.append("cleanup")

        m._spawn(service(), name="svc")
        await asyncio.sleep(0)
        await m._cancel_background_tasks()

        assert ran == ["cleanup"], "task cleanup path did not run"
        assert not m._background_tasks, "registry not drained"

    asyncio.run(driver())


def test_outer_cancellation_propagates():
    """
    The reason shutdown uses gather(return_exceptions=True) rather than
    awaiting each task in a try/except: awaiting individually cannot tell
    "the task I cancelled has unwound" from "this shutdown coroutine is itself
    being cancelled", and swallows both. Shutdown must stay interruptible.
    """
    async def driver():
        async def slow_unwind():
            try:
                await asyncio.sleep(3600)
            except asyncio.CancelledError:
                await asyncio.sleep(0.5)      # deliberately slow cleanup
                raise

        m._spawn(slow_unwind(), name="slow")
        shutdown = asyncio.create_task(m._cancel_background_tasks())
        await asyncio.sleep(0.05)
        shutdown.cancel()

        with pytest.raises(asyncio.CancelledError):
            await shutdown

    asyncio.run(driver())


def test_cancel_on_empty_registry_is_a_noop():
    asyncio.run(m._cancel_background_tasks())


def test_already_finished_tasks_are_not_recancelled():
    """A task that completed before shutdown must not break the drain."""
    async def driver():
        async def quick():
            return 1

        async def forever():
            await asyncio.sleep(3600)

        done = m._spawn(quick(), name="done")
        m._spawn(forever(), name="running")
        await done
        await m._cancel_background_tasks()
        assert not m._background_tasks

    asyncio.run(driver())
