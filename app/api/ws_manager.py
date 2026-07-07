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
