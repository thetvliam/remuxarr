"""
Engines a test makes for itself are disposed when the test ends
(tests/engine_disposal.py, wired in by conftest.py's autouse fixture).

Python 3.13 warns about every SQLite connection it garbage-collects without
close() having been called, and the suite printed several hundred such
warnings there, all from engines the tests built and never disposed. A
warning nobody reads is the kind that hides the next real one; pytest.ini
now fails a run on it.

Disposal happens after a test's body and every one of its own fixtures have
finished, so nothing inside a test can see it. The connections are checked
instead by a module-scoped fixture, whose teardown runs once the tests below
have been torn down, disposal included. Each test leaves a connection in
its engine's pool, where it stays open until the engine is disposed, and
checks that it is open before handing it over, so the check after teardown
cannot pass on a connection that was already closed.

One engine per kind of pool the suite's engines use, because dispose() is
the pool's to implement: StaticPool (memory_engine()), SingletonThreadPool
(a bare create_engine("sqlite://")), and QueuePool (a database file).

A thread the test starts is left blocked when it ends, and released only
after its teardown, so the engine has to be held for it. That situation is
real: disposing at once crashed test_a_finished_queue_item_does_not_block_a_revert
in 8 of 30 runs (see engine_disposal.py).

Verified by mutation, 12 applied, 12 killed on 3.12 and 3.13 alike. All are
of code nothing imported before this commit, so none is baseline evidence.
Against this file:

  • end() not disposing                       → killed, pool checks and held engine
  • memory_engine() not registering           → killed, pool checks and held engine
  • dispose_after_test() not registering      → killed, pool checks and held engine
  • registering outside a test accepted       → killed, the refusal
  • end() leaving the gap after a test open   → killed, the refusal
  • begin() not opening a test                → killed, every test that registers
  • running threads ignored, disposed at once → killed, the thread's read
  • held engines never released               → killed, pool checks and held engine
  • the threads before a test not recorded    → killed, pool checks and held engine
  • conftest's fixture not autouse            → killed, every test that registers
  • the fixture not calling begin()           → killed, every test that registers
  • the fixture not calling end()             → killed, pool checks, held engine
                                                 and the refusal

The checks after teardown fail at module teardown, which pytest reports
against the last test in the file.

The four that are wiring (memory_engine() registering, the fixture being
autouse, and its calls to begin() and end()) were also run against the
whole suite on 3.12, and each was killed there.
"""
import threading

import pytest
from sqlalchemy import create_engine, text

from tests import engine_disposal
from tests.conftest import memory_engine
from tests.engine_disposal import dispose_after_test


def _is_open(raw):
    try:
        raw.execute("select 1")
        return True
    except Exception:
        return False


def _pooled_connection(engine):
    """Use a connection and give it back: it stays open in the pool."""
    with engine.connect() as conn:
        conn.execute(text("select 1"))
        raw = conn.connection.dbapi_connection
    assert _is_open(raw), "closed before the test ended: nothing left to check"
    return raw


@pytest.fixture(scope="module")
def checked_after_the_tests():
    """Connections the tests leave behind, checked after their teardown."""
    left = []
    yield left
    still_open = [label for label, raw in left if _is_open(raw)]
    assert not still_open, f"not disposed when its test ended: {still_open}"


def test_an_engine_from_memory_engine_is_disposed(checked_after_the_tests):
    raw = _pooled_connection(memory_engine())
    checked_after_the_tests.append(("memory_engine() (StaticPool)", raw))


def test_a_registered_in_memory_engine_is_disposed(checked_after_the_tests):
    engine = dispose_after_test(create_engine("sqlite://"))
    raw = _pooled_connection(engine)
    checked_after_the_tests.append(('create_engine("sqlite://") (SingletonThreadPool)', raw))


def test_a_registered_file_engine_is_disposed(checked_after_the_tests, tmp_path):
    engine = dispose_after_test(create_engine(f"sqlite:///{tmp_path / 'test.db'}"))
    raw = _pooled_connection(engine)
    checked_after_the_tests.append(("a database file (QueuePool)", raw))


@pytest.fixture(scope="module")
def a_thread_left_running():
    """
    A thread the test below starts and leaves blocked, released only here,
    after that test has been torn down. It then reads the table the test
    made: an in-memory engine disposed in the meantime would have come back
    empty, and closing it during a query is what crashed the interpreter.
    """
    left = {}
    yield left
    left["go"].set()
    left["thread"].join(10)
    assert left["saw"] == [1], (
        "the engine was disposed while a thread its test started still used it"
    )
    engine_disposal.dispose_what_no_thread_is_using()
    assert not _is_open(left["raw"]), "never disposed once the thread had finished"


def test_an_engine_is_held_until_the_threads_its_test_started_finish(a_thread_left_running):
    engine = memory_engine()
    with engine.begin() as conn:
        conn.execute(text("CREATE TABLE t (x)"))
        conn.execute(text("INSERT INTO t VALUES (1)"))
    go = threading.Event()
    saw = []

    def later():
        go.wait(10)
        with engine.connect() as conn:
            saw.append(conn.execute(text("SELECT x FROM t")).scalar())

    thread = threading.Thread(target=later, daemon=True)
    thread.start()
    a_thread_left_running.update(go=go, thread=thread, saw=saw,
                                 raw=_pooled_connection(engine))


@pytest.fixture(scope="module")
def registered_outside_a_test():
    """
    What registering an engine says when no test is running. A module-scoped
    fixture is set up before the test that first asks for it has begun, which
    is exactly the case the refusal is there for.
    """
    engine = create_engine("sqlite://")
    try:
        dispose_after_test(engine)
    except RuntimeError as exc:
        return str(exc)
    finally:
        engine.dispose()
    return None


def test_an_engine_made_outside_a_test_is_refused(registered_outside_a_test):
    assert registered_outside_a_test is not None, (
        "an engine made outside a test was taken, and would have been "
        "disposed under whatever went on using it"
    )
    assert "outside a test" in registered_outside_a_test
