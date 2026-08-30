"""
Which file a revert is currently rewriting, if any.

WHY THIS IS A MODULE AND NOT A COLUMN
-------------------------------------
Two subsystems write media files in place: the queue worker and revert.
Both stage to a temporary file and swap, so if they run on the same path
the loser's output silently replaces the winner's and the survivor is
whichever finished second — leaving a revert point and a queue item that
both describe a file that never existed. Nothing about the result looks
wrong; it plays.

The obvious alternative is a RevertPoint.restoring_at column, and it is
the wrong tool here specifically because the lock must NOT outlive the
process:

  * The container runs uvicorn with --workers 1, pinned in the Dockerfile,
    and the worker is started in the same process by the app lifespan. So
    there is no second process for a database to coordinate with. The two
    writers are threads sharing this interpreter.

  * A revert runs in a daemon thread. If the process dies mid-revert, the
    revert dies with it — so a lock that survives the restart is stale by
    definition, and describes work that is no longer happening. A DB
    column would therefore need startup code to clear it, and if that
    code were ever wrong the failure mode is a permanently wedged queue
    with no way to clear it from the UI. In-process state has that
    property for free: the flag and the thread it describes are lost
    together, which is correct.

That reasoning depends on --workers 1. If this ever runs multiple
processes, or the worker moves out of the API process, this module is not
enough and the lock has to move into the database — with the stale-lock
recovery that implies.

WHY NOT JUST LIVE IN routes/revert.py
-------------------------------------
It did. The worker cannot import from a route module without inverting
the layering (routes depend on core, not the reverse), so the flag was
readable only by the code that set it, and the exclusion ran one way:
revert refused to start while the worker held the file, and the worker
happily started while revert held it. This module has no imports of its
own precisely so anything can depend on it.

DISCIPLINE
----------
Set and read from the event loop, or from the revert thread on the way
out. The same check-and-set discipline scan.py documents: asyncio only
switches coroutines at await points, so a check and the set that follows
it are atomic as long as nothing awaits between them.
"""

# The file_id being rewritten, or None. file_id rather than point_id
# because the worker's question is "may I write this file", and a revert
# point's identity is not what makes two writers collide — the path is.
_reverting_file_id: int | None = None

# Kept for the /status endpoint, which reports what the user is waiting on.
_status: dict = {"point_id": None, "path": None}


def acquire(file_id: int | None, point_id: int, path: str | None) -> None:
    """Record that a revert is now rewriting file_id."""
    global _reverting_file_id
    _reverting_file_id = file_id
    _status.update(point_id=point_id, path=path)


def release() -> None:
    """Record that no revert is running. Safe to call when none was."""
    global _reverting_file_id
    _reverting_file_id = None
    _status.update(point_id=None, path=None)


def is_running() -> bool:
    return _status["point_id"] is not None


def reverting_file_id() -> int | None:
    """
    The file a revert is rewriting, for the worker to steer around.

    None means either that no revert is running, or that one is running on
    a point with no attached file — which cannot happen, because restore()
    refuses an unattached point before acquiring. A caller must therefore
    treat None as "nothing to avoid" rather than "avoid everything", or an
    unattached point would stall the entire queue.
    """
    return _reverting_file_id


def status() -> dict:
    return dict(_status)
