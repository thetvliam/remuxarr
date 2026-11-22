"""
Engines a test makes for itself, disposed when the test ends.

Python 3.13 warns (ResourceWarning: unclosed database) about every SQLite
connection it garbage-collects without close() having been called; 3.12
says nothing. An engine whose session a test closes goes back to holding
its connection in the pool, and if the engine is never disposed, that
connection is left for the collector. A full run on 3.13 printed several
hundred of these warnings, and every one traced back to an engine a test had
built for itself: none came from the app's own engine.

Disposing at teardown, rather than in each fixture, also covers the helpers
that hand a test a bare session (db = _db()) and the tests that build an
engine in their own body, neither of which has a teardown to put it in.
conftest.py's autouse fixture calls begin() and end(). Being autouse in
conftest, it is set up before every other function-scoped fixture of a test
and torn down after all of them, so an engine is disposed once nothing in
the test can still be using it.

Not while a thread the test started is still running. A test can end with
work it set off still going: test_a_finished_queue_item_does_not_block_a_revert
starts a revert, whose thread goes on querying the test's engine after the
test has returned. Closing a SQLite connection under another thread's query
crashes the interpreter (Fatal Python error: Segmentation fault), which
disposing at once did in 8 of 30 runs of that test alone. So end() leaves
the engines of a test whose threads are still alive waiting, and a later
end() disposes them once every one of those threads has finished.

Only an engine made during a test is taken. A wider-scoped fixture that
registered one would see it disposed after its first test, and an in-memory
engine disposed comes back empty: the tests after the first would run
against no tables or rows, and one asserting that something is absent would
pass for that reason alone. Registering outside a test raises instead.

The state lives here rather than in conftest.py because conftest.py runs
twice in one session: pytest loads it as `conftest`, and the tests'
`from tests.conftest import ...` loads a second copy as `tests.conftest`.
Each copy would have had a registry of its own, the autouse fixture working
on one and memory_engine() registering in the other. Import this module as
`tests.engine_disposal` for the same reason; a copy loaded under another
name never sees begin(), and refuses every engine.
"""

import threading

# None outside a test; the engines registered so far while one runs.
_made_by_this_test: list | None = None
# The threads already running when the current test began.
_threads_before: set = set()
# (engines, threads): a finished test's engines, held until its threads end.
_waiting: list = []


def dispose_after_test(engine):
    """Return engine, to be disposed when the current test ends."""
    if _made_by_this_test is None:
        raise RuntimeError(
            "dispose_after_test() was called outside a test. An engine made "
            "outside a test (a module- or session-scoped fixture, say) must be "
            "disposed by whatever made it: disposing it after the first test "
            "would empty an in-memory database under the tests after it."
        )
    _made_by_this_test.append(engine)
    return engine


def begin():
    """A test is starting: take the engines it makes from here on."""
    global _made_by_this_test, _threads_before
    _made_by_this_test = []
    _threads_before = set(threading.enumerate())


def end():
    """
    The test has ended: dispose of every engine it made, or hold them until
    the threads it started have finished.
    """
    global _made_by_this_test
    engines, _made_by_this_test = _made_by_this_test, None
    still_running = [t for t in threading.enumerate()
                     if t not in _threads_before and t.is_alive()]
    if engines:
        _waiting.append((engines, still_running))
    dispose_what_no_thread_is_using()


def dispose_what_no_thread_is_using():
    """Dispose of the held engines whose tests' threads have all finished."""
    for entry in list(_waiting):
        engines, threads = entry
        if not any(t.is_alive() for t in threads):
            _waiting.remove(entry)
            for engine in engines:
                engine.dispose()
