"""
The WebSocket hub: connection bookkeeping and fan-out
=====================================================

Every event the backend produces for the UI — job_progress, scan_progress,
revert_complete, all of them — leaves through WebSocketManager.broadcast_json
and arrives at a browser tab that the /ws endpoint in main.py registered.

Eleven test modules broadcast something in the course of checking something
else, and all eleven substitute a double for broadcast_json. So what was
covered was every producer against a fake hub, and the hub itself had never
run in a test: connect, disconnect and broadcast_json sat at zero coverage,
as did the endpoint that calls them. test_ws_broadcast_threadsafe.py covers
the module-level broadcast_threadsafe and monkeypatches broadcast_json on
its way past, so it does not reach this either.

Nothing here fails loudly when it is wrong, which is what makes it worth
pinning:

  * A fan-out that lets the first dead socket raise stops every tab after it
    in the list receiving anything, mid-job, with nothing raised on the
    producer side.
  * A disconnect filtering on `is` rather than `is not` unregisters every
    tab except the one that actually left.
  * A payload built with str() instead of json.dumps() arrives as a Python
    repr. useWebSocket.js hands it to JSON.parse, the parse throws, and its
    handler logs a console.warn and drops the message — so the dashboard
    quietly stops updating while the backend reports success throughout.
  * An endpoint that accepts the socket without registering it leaves the
    tab reporting connected, because the frontend sets that from onopen, and
    receiving nothing for the life of the page.

Ten mutants, each confirmed to survive the full suite before this file
existed:

  connect       drop self._connections.append(ws)            killed
                drop await ws.accept()                       killed
  disconnect    `c is not ws` becomes `c is ws`              killed
                the filter becomes []                        killed
  broadcast     json.dumps(data) becomes str(data)           killed
                drop the try/except around send_text         killed
                dead.append(ws) becomes pass                 killed
  the endpoint  connect() becomes a bare websocket.accept()  killed
                drop disconnect() on WebSocketDisconnect     killed
                drop the "pong" reply                        killed

Each test names the one it closes. Two further mutants were applied and are
deliberately left alive rather than papered over:

  * Removing `if not self._connections: return`. With nobody connected the
    loop body never runs and the prune list is empty, so the only input that
    tells the two versions apart is a payload json.dumps cannot serialise,
    and nothing broadcasts one. A test would pin an accident — that
    broadcasting garbage to nobody happens to be silent — rather than a
    behaviour anyone chose.
  * Iterating self._connections instead of list(self._connections).
    disconnect() rebinds the attribute rather than mutating the list, and
    pruning runs after the loop has finished, so the snapshot changes
    nothing unless a socket connects while a broadcast is in flight.
    Whether that socket should receive the in-flight message is a question
    this project has never answered, and asserting either way would invent
    an answer.

Run from the project root:
    pytest tests/test_ws_manager.py -v
"""
import asyncio
import json
import threading

import pytest

from app.api.ws_manager import WebSocketManager, ws_manager

try:
    from starlette.testclient import TestClient
except (ImportError, RuntimeError):  # pragma: no cover - missing httpx
    TestClient = None


class FakeSocket:
    """
    A browser tab, as broadcast_json sees one.

    Records rather than asserts, so each test states what it wants out of
    the recording. `attempts` counts every send the hub made including the
    ones that raised, which is the only way to see a socket that should
    have been pruned still being written to.
    """

    def __init__(self, *, fails: bool = False) -> None:
        self.accepted = False
        self.sent: list[str] = []
        self.attempts = 0
        self._fails = fails

    async def accept(self) -> None:
        self.accepted = True

    async def send_text(self, payload: str) -> None:
        # Starlette raises on a socket whose client has gone rather than
        # returning a failure, which is why the hub catches rather than
        # checks.
        self.attempts += 1
        if self._fails:
            raise RuntimeError("client went away")
        self.sent.append(payload)


@pytest.fixture(autouse=True)
def _empty_hub():
    """
    ws_manager is a process-wide singleton and the endpoint tests connect
    real sockets to it.

    Cleared on the way in as well as out: tidying up after yourself says
    nothing about what ran before you, and a socket left registered by an
    earlier test would be written to by every broadcast in this file.
    Cleared in place rather than rebound, so it is the same list every
    other importer of the singleton is holding.
    """
    ws_manager._connections.clear()
    yield
    ws_manager._connections.clear()


# ── The hub itself ────────────────────────────────────────────────────────────

def test_a_connected_socket_is_sent_the_broadcast():
    """
    Closes: connect() dropping the append. The socket is accepted, the tab
    believes it is live, and it is registered nowhere.
    """
    async def driver():
        hub = WebSocketManager()
        tab = FakeSocket()

        await hub.connect(tab)
        await hub.broadcast_json({"event": "job_started", "job_id": 7})

        assert len(tab.sent) == 1, "a connected tab was sent nothing"

    asyncio.run(driver())


def test_the_broadcast_is_json_the_browser_can_parse():
    """
    Closes: json.dumps(data) becoming str(data).

    Asserted by parsing rather than by comparing the string, so this pins
    what useWebSocket.js does with the message — JSON.parse — instead of
    pinning json.dumps's choice of separators.
    """
    async def driver():
        hub = WebSocketManager()
        tab = FakeSocket()
        event = {"event": "job_progress", "job_id": 3, "progress": 41.5,
                 "current_action": None}

        await hub.connect(tab)
        await hub.broadcast_json(event)

        assert json.loads(tab.sent[0]) == event

    asyncio.run(driver())


def test_disconnect_removes_only_the_socket_it_was_given():
    """
    Closes: `c is not ws` becoming `c is ws`, and the filter becoming [].
    Both leave the tab that left registered, or every other tab dropped —
    one tab closing takes the rest of the dashboard down with it.
    """
    async def driver():
        hub = WebSocketManager()
        leaving, staying = FakeSocket(), FakeSocket()
        await hub.connect(leaving)
        await hub.connect(staying)

        hub.disconnect(leaving)
        await hub.broadcast_json({"event": "scan_progress", "scanned": 3})

        assert len(staying.sent) == 1, "the tab that stayed stopped receiving"
        assert leaving.sent == [], "the tab that left is still being sent to"

    asyncio.run(driver())


def test_one_socket_that_has_gone_does_not_stop_the_rest():
    """
    Closes: dropping the try/except around send_text.

    The third socket is the assertion. Without the catch the exception from
    the second one leaves broadcast_json entirely, so every tab after it in
    the list silently stops receiving events part-way through a job, and
    the caller sees a broadcast that raised for a reason that has nothing
    to do with it.
    """
    async def driver():
        hub = WebSocketManager()
        first, gone, last = FakeSocket(), FakeSocket(fails=True), FakeSocket()
        for tab in (first, gone, last):
            await hub.connect(tab)

        await hub.broadcast_json({"event": "job_completed", "job_id": 9})

        assert len(first.sent) == 1
        assert len(last.sent) == 1, "a dead socket stopped the fan-out"

    asyncio.run(driver())


def test_a_socket_that_failed_is_not_written_to_again():
    """
    Closes: dead.append(ws) becoming pass.

    A socket whose client has gone raises on every send, so one that is
    never pruned is retried by every broadcast for the life of the process.
    Counting attempts rather than reading the connection list keeps the
    assertion on what the hub does rather than on what it stores.
    """
    async def driver():
        hub = WebSocketManager()
        gone, tab = FakeSocket(fails=True), FakeSocket()
        await hub.connect(gone)
        await hub.connect(tab)

        await hub.broadcast_json({"event": "scan_started"})
        await hub.broadcast_json({"event": "scan_completed", "queued": 2})

        assert gone.attempts == 1, "a socket known to be dead was written to again"
        assert len(tab.sent) == 2, "pruning took a live socket with it"

    asyncio.run(driver())


# ── The endpoint that wires it up ─────────────────────────────────────────────

def _receive_within(ws, seconds: float = 5.0) -> str:
    """
    ws.receive_text() with a deadline.

    WebSocketTestSession.receive_text() blocks with no timeout, and two of
    the mutants below leave the server with nothing to send at all: both
    were confirmed to hang the run until it was killed rather than to fail
    it. A regression that hangs CI says nothing about itself; one that
    fails names the socket that went quiet.

    The read runs on a worker thread so the deadline can be enforced. It is
    a daemon because on the timeout path there is nothing left to wait for,
    and its exception is carried back rather than raised there, so a socket
    that closes under a test reports that instead of an unhandled exception
    in a thread nobody is watching.
    """
    outcome: list[tuple[str, object]] = []

    def _read() -> None:
        try:
            outcome.append(("ok", ws.receive_text()))
        except BaseException as exc:      # carried to the caller below
            outcome.append(("raised", exc))

    reader = threading.Thread(target=_read, daemon=True)
    reader.start()
    reader.join(seconds)

    assert outcome, f"nothing arrived on the socket within {seconds}s"
    kind, value = outcome[0]
    if kind == "raised":
        raise value
    return value


@pytest.mark.skipif(
    TestClient is None, reason="starlette TestClient requires httpx"
)
class TestTheWebSocketEndpoint:
    """
    The real endpoint over the real singleton, which is the only place the
    two halves are checked together.

    The lifespan is deliberately not entered — TestClient is used without
    its context manager. Nothing on this path depends on startup, and
    entering it starts the worker, the scheduler and the Plex backlog
    drain, whose own broadcasts would arrive on the socket under test.
    """

    @pytest.fixture
    def client(self):
        from app.main import app

        return TestClient(app)

    def test_connecting_registers_the_socket_with_the_hub(self, client):
        """
        Closes: the endpoint accepting the socket itself rather than
        calling ws_manager.connect(). The tab connects either way, since
        the frontend sets `connected` from onopen, and then never receives
        an event again.

        Asserted on the hub's list rather than by waiting for a message so
        that this mutant fails here in milliseconds, rather than as a
        five-second timeout in the test below.
        """
        with client.websocket_connect("/ws"):
            assert len(ws_manager._connections) == 1

    def test_a_connected_client_receives_a_broadcast(self, client):
        """
        The end-to-end shape: what a browser tab actually gets when the
        backend broadcasts. Also the only test that would notice
        await ws.accept() going missing from connect() — the handshake
        never completes and websocket_connect raises before the body runs.

        The broadcast goes through the session's own portal because it has
        to run on the loop the endpoint is running on; awaiting it from
        this thread's loop would be writing to the socket from outside the
        loop that owns it.
        """
        event = {"event": "job_completed", "job_id": 12, "status": "success"}

        with client.websocket_connect("/ws") as tab:
            tab.portal.call(ws_manager.broadcast_json, event)

            assert json.loads(_receive_within(tab)) == event

    def test_the_keepalive_ping_is_answered(self, client):
        """
        Closes: dropping the "pong" reply.

        useWebSocket.js sends "ping" every 25 seconds and ignores the
        reply, so nothing in the UI notices when it stops coming — the
        traffic is the point, and an idle socket is what a reverse proxy
        times out.
        """
        with client.websocket_connect("/ws") as tab:
            tab.send_text("ping")

            assert _receive_within(tab) == "pong"

    def test_a_client_that_goes_away_is_unregistered(self, client):
        """
        Closes: dropping ws_manager.disconnect() from the
        WebSocketDisconnect handler. Closed sockets would accumulate for
        the life of the process, each one retried and pruned by whichever
        broadcast happened to come next.

        No wait is needed before the assertion: the session's teardown
        registers the endpoint task's future before the close callback, so
        it runs last and blocks until the endpoint has unwound.
        """
        with client.websocket_connect("/ws"):
            pass

        assert ws_manager._connections == []
