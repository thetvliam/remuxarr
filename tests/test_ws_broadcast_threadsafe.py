"""
broadcast_threadsafe: reporting must not break the work it reports on
=====================================================================

Scan and revert both do their work in a background thread and broadcast
progress onto the event loop of the request that started them. The thread
can outlive that loop — a container shutting down, or a test client whose
loop closes while a daemon thread is still finishing — and when it does,
run_coroutine_threadsafe() raises.

Two separate failures come out of that, and only one is visible:

  * the exception has nowhere to go in a thread, so a revert that
    SUCCEEDED gets reported by threading's excepthook as a crashed thread;

  * the coroutine argument was already built before the call that raised,
    so it is left un-awaited. Python reports that from the garbage
    collector, meaning it surfaces against whatever test happens to be
    running when the GC fires, not the one that caused it.

The second is why this file exists. It is the one that got through: the
scan-side helper caught the exception but did not close the coroutine, so
the leak stayed and merely became silent. It then showed up as an
intermittent warning on an unrelated test, only under CI's timing, and the
test it was blamed on does not start a thread at all.

Run from the project root:
    pytest tests/test_ws_broadcast_threadsafe.py -v
"""
import asyncio
import gc
import threading
import warnings

from app.api.ws_manager import broadcast_threadsafe


def _dead_loop():
    """The loop a worker thread finds after its request's client went away."""
    loop = asyncio.new_event_loop()
    loop.close()
    return loop


def test_a_dead_loop_does_not_raise_into_the_calling_thread():
    """
    The caller is a bare thread with no exception handling above it, so
    anything raised here is an unhandled traceback on stderr reporting a
    job that in fact completed normally.
    """
    broadcast_threadsafe({"event": "revert_complete", "success": True},
                         _dead_loop())


def test_a_dead_loop_does_not_leak_the_coroutine():
    """
    The half that hides. Catching the scheduling error is not enough on its
    own — broadcast_json(...) is evaluated to build the argument before
    run_coroutine_threadsafe() is ever entered, so the coroutine exists
    whether or not the call succeeds and has to be closed explicitly.

    Asserted by collecting garbage inside a recording warnings context,
    because that is exactly how it reaches the test log in practice.
    """
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")

        broadcast_threadsafe({"event": "scan_completed"}, _dead_loop())
        gc.collect()

        leaked = [str(w.message) for w in caught
                  if "never awaited" in str(w.message)]

    assert leaked == [], (
        "the coroutine was left un-awaited; this surfaces later, from the "
        "garbage collector, against an unrelated test"
    )


def test_it_reaches_a_live_loop(monkeypatch):
    """
    The guard above must not have been bought by never broadcasting at all.
    A test that only proves failure is survivable passes just as happily
    against a helper whose body is `pass` — which is not hypothetical here:
    emptying the body did pass the entire suite until this test called the
    helper rather than the asyncio function underneath it.
    """
    from app.api import ws_manager as ws_module

    delivered = threading.Event()
    seen: list[dict] = []

    async def _record(data):
        seen.append(data)
        delivered.set()

    monkeypatch.setattr(ws_module.ws_manager, "broadcast_json", _record)

    loop = asyncio.new_event_loop()
    runner = threading.Thread(target=loop.run_forever, daemon=True)
    runner.start()
    try:
        payload = {"event": "revert_complete", "point_id": 7}
        # From a thread that is not the loop's own, which is the only
        # situation this helper exists for.
        worker = threading.Thread(
            target=lambda: broadcast_threadsafe(payload, loop), daemon=True,
        )
        worker.start()
        worker.join(timeout=5)

        assert delivered.wait(timeout=5), "the broadcast never reached the loop"
        assert seen == [payload]
    finally:
        loop.call_soon_threadsafe(loop.stop)
        runner.join(timeout=5)
        loop.close()
