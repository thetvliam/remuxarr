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
from datetime import datetime

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
                "ts":      datetime.now().strftime("%H:%M:%S"),
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
