"""
WebSocket Connection Manager
============================
A simple broadcast hub. Every connected client (browser tab) receives every
event. The UI uses event.type to decide what to update.

Event types emitted by the worker
----------------------------------
job_started     { job_id }
job_progress    { job_id, progress, current_action, speed }
job_completed   { job_id, status, filename, error }
file_queued     { file_path, queue_item_id, reason }
scan_started    {}
scan_completed  { queued, manual_review, errors }
"""
import asyncio
import json
import logging

from fastapi import WebSocket

logger = logging.getLogger(__name__)


class WebSocketManager:
    def __init__(self) -> None:
        self._connections: list[WebSocket] = []

    async def connect(self, ws: WebSocket) -> None:
        await ws.accept()
        self._connections.append(ws)
        logger.debug("WS connected  — total: %d", len(self._connections))

    def disconnect(self, ws: WebSocket) -> None:
        self._connections = [c for c in self._connections if c is not ws]
        logger.debug("WS disconnected — total: %d", len(self._connections))

    async def broadcast_json(self, data: dict) -> None:
        """Send a JSON message to every connected client, pruning dead sockets."""
        if not self._connections:
            return

        payload   = json.dumps(data)
        dead: list[WebSocket] = []

        for ws in list(self._connections):
            try:
                await ws.send_text(payload)
            except Exception:
                dead.append(ws)

        for ws in dead:
            self.disconnect(ws)

# Global singleton — imported by worker and routes
ws_manager = WebSocketManager()


def broadcast_threadsafe(data: dict, loop) -> None:
    """
    Fire-and-forget broadcast from a worker thread onto the app's loop.

    Background threads cannot await, and the loop belongs to the request
    that spawned them, so the coroutine has to be handed across via
    run_coroutine_threadsafe(). Two things make that worth centralising
    rather than inlining at each call site:

    A broadcast must never take down the work it is reporting on. If the
    loop is gone — shutdown, or a test client whose loop closed while a
    daemon thread was still finishing — run_coroutine_threadsafe() raises,
    and in a thread that exception has nowhere to go but threading's
    excepthook. A revert that completed successfully would be reported as
    a crashed thread.

    And the coroutine has to be closed when scheduling fails. It is built
    when the argument is evaluated, before the call it is being passed to
    ever runs, so a raise leaves a live coroutine that was never awaited.
    Python reports that at garbage-collection time, which means it is
    attributed to whatever happens to be running when the GC fires rather
    than to the thread that leaked it — arriving as an unrelated,
    intermittent warning somewhere else entirely. Catching the error
    without closing the coroutine silences the traceback and keeps the
    leak, which is the shape this had before: a warning that appeared only
    under timing, on a different test each run.
    """
    coro = ws_manager.broadcast_json(data)
    try:
        asyncio.run_coroutine_threadsafe(coro, loop)
    except Exception:
        coro.close()
        logger.debug("Dropped a WebSocket broadcast: no live loop to send it on")
