"""
In-memory log handler for the UI log viewer.

Captures the last MAX_RECORDS log records from the Python logging system into
a thread-safe deque.  The GET /api/logs endpoint drains this buffer so the
frontend can display recent application logs without needing filesystem access
or SSH.

Design notes:
  - Thread-safe: a lock protects all deque access since the logging system
    may emit from multiple threads simultaneously (worker, asyncio, uvicorn).
  - uvicorn.access is filtered out so every call to GET /api/logs doesn't
    add another entry, which would create an ever-growing noise loop.
  - The module-level singleton is created once and shared; all callers that
    import get_handler() get the same instance.
"""

import logging
import threading
from collections import deque
from app.core.timeutil import utcnow

MAX_RECORDS = 500


class MemoryLogHandler(logging.Handler):
    """Logging handler that stores formatted records in a fixed-size deque."""

    def __init__(self, maxlen: int = MAX_RECORDS) -> None:
        super().__init__()
        self._records: deque[dict] = deque(maxlen=maxlen)
        self._lock = threading.Lock()

    def emit(self, record: logging.LogRecord) -> None:
        try:
            msg = self.format(record)
            entry = {
                # Full ISO-8601 UTC, not a pre-formatted "%H:%M:%S" string.
                #
                # This field is the one place in the app that bypassed the
                # store-UTC / display-local convention: it was formatted
                # server-side and rendered raw by LogViewer, so the displayed
                # clock was whatever the server's local time happened to be.
                # It looked right only because the container has no TZ set and
                # therefore runs UTC — matching a UTC-offset user by accident,
                # and being silently wrong for everyone else.
                #
                # Switching the format to UTC while still rendering it raw made
                # that visible: log lines showed UTC while the queue and history
                # panels showed local, so on BST they disagreed by an hour.
                #
                # Sending ISO lets the frontend apply the same toUtcDate() +
                # toLocaleTimeString() path everything else uses, so the log
                # viewer agrees with the rest of the UI in every timezone.
                "ts":      utcnow().isoformat(),
                "level":   record.levelname,
                "module":  record.name,
                "message": msg,
            }
            with self._lock:
                self._records.append(entry)
        except Exception:
            self.handleError(record)

    def get_records(self, limit: int = 200) -> list[dict]:
        """Return the most recent `limit` records (oldest first)."""
        with self._lock:
            records = list(self._records)
        return records[-limit:]

    def clear(self) -> None:
        with self._lock:
            self._records.clear()


# Module-level singleton — registered into the root logger by main.py.
_handler: MemoryLogHandler | None = None


def get_handler() -> MemoryLogHandler:
    global _handler
    if _handler is None:
        _handler = MemoryLogHandler()
    return _handler
