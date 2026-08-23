"""
Revert API Routes
=================
GET    /api/revert/                  — revert points, attached and detached
GET    /api/revert/status            — whether a revert is currently running
POST   /api/revert/{point_id}/restore/ — put the file back the way it was
POST   /api/revert/{point_id}/attach/  — match a detached point to a file
DELETE /api/revert/{point_id}/       — discard one revert point
DELETE /api/revert/                  — empty the recycle bin
"""
import asyncio
import logging
import threading

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.api.ws_manager import broadcast_threadsafe
from app.core import revert_lock
from app.core.recycle import delete_sidecar, recycle_dir_status
from app.core.revert_match import attach, find_candidates, list_detached
from app.core.revert_restore import restore_revert_point, revert_blocked_reason
from app.database.models import MediaFile, QueueItem, RevertPoint
from app.database.session import get_db

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/revert", tags=["revert"])


# A revert rewrites a media file, so exactly one runs at a time. The state
# lives in app.core.revert_lock rather than here so the queue worker can
# read it too — see that module for why it is in-process state and not a
# database column.

# Queue states that mean the worker is about to write, or is writing, this
# file. Reverting underneath either produces two writers for one path.
_ACTIVE_QUEUE_STATES = ("pending", "processing")


class AttachRequest(BaseModel):
    file_id: int
    # Never defaulted true. This is the user having been shown what could
    # not be verified and saying yes anyway; what it permits is a revert
    # that produces a plausible, wrong file.
    confirm_mismatch: bool = False


def _serialise(point: RevertPoint, media: MediaFile | None) -> dict:
    # Whether this could actually be reverted RIGHT NOW, decided by the
    # same function the revert itself uses. A list that offers Revert on
    # an entry the revert then refuses makes the button look broken
    # rather than the file look changed, and the user has no way to tell
    # which — so the reason travels with the row.
    problem = (revert_blocked_reason(point, media.path) if media else
               "This revert point is not attached to a file.")

    return {
        "id": point.id,
        "file_id": point.file_id,
        "current_path": media.path if media else None,
        "current_filename": media.filename if media else None,
        "original_path": point.original_path,
        "original_container": point.original_container,
        "sidecar_size": point.sidecar_size,
        "created_at": point.created_at,
        "detached_at": point.detached_at,
        "restorable": problem is None,
        "blocked_reason": problem,
    }


@router.get("/")
def list_points(
    include_detached: bool = Query(default=True),
    db: Session = Depends(get_db),
):
    """
    Every revert point, with the detached ones separated out.

    They are returned apart rather than mixed with a flag because they
    need different actions: an attached point can be reverted, a detached
    one can only be matched to a file first. A single list invites a UI
    that offers Revert on something that has no file to revert.
    """
    ready, reason = recycle_dir_status()

    attached = []
    for point in (db.query(RevertPoint)
                    .filter(RevertPoint.file_id.isnot(None))
                    .order_by(RevertPoint.created_at.desc())
                    .all()):
        attached.append(_serialise(point, db.get(MediaFile, point.file_id)))

    return {
        "recycle_bin_ready": ready,
        "recycle_bin_reason": reason,
        "attached": attached,
        "detached": list_detached() if include_detached else [],
    }


@router.get("/status")
def revert_status():
    return {"running": revert_lock.is_running(), **revert_lock.status()}


@router.post("/{point_id}/restore/")
async def restore(point_id: int, db: Session = Depends(get_db)):
    """
    Start a revert. Returns as soon as it is running, not when it finishes.

    Validation that can be done cheaply happens HERE, synchronously, so
    the user gets a real error instead of a "started" they have to go
    looking for the failure of. The expensive checks — the sentinel, the
    sidecar — belong to restore_revert_point and report over the socket.
    """
    if revert_lock.is_running():
        raise HTTPException(409, "A revert is already running")

    point = db.get(RevertPoint, point_id)
    if point is None:
        raise HTTPException(404, "No such revert point")
    if point.file_id is None:
        raise HTTPException(
            409,
            "This revert point is not attached to a file. Match it to one "
            "first.",
        )

    # Refusing beats racing. The worker writes through a staged swap and so
    # does revert, so the loser's output silently replaces the winner's and
    # the result is whichever finished second — with a revert point and a
    # queue item that both now describe a file that never existed.
    active = (db.query(QueueItem)
                .filter(QueueItem.file_id == point.file_id,
                        QueueItem.status.in_(_ACTIVE_QUEUE_STATES))
                .first())
    if active:
        raise HTTPException(
            409,
            f"This file is {active.status} in the queue. Wait for it to "
            f"finish, or remove it from the queue, before reverting.",
        )

    media = db.get(MediaFile, point.file_id)

    # Acquired before the thread starts, and carrying the file id: from
    # here until release() the worker will not claim a job for this file,
    # which closes the half of the exclusion that did not exist. The check
    # above only covers jobs queued BEFORE this point — a scan running
    # during the revert would otherwise queue this same file and the
    # worker would pick it straight up.
    revert_lock.acquire(point.file_id, point_id,
                        media.path if media else None)
    started = False
    try:
        loop = asyncio.get_running_loop()
        # A dedicated daemon thread rather than BackgroundTasks, for the
        # reason trigger_scan documents: BackgroundTasks shares the default
        # thread pool with every sync route handler, and a revert blocking
        # on FFmpeg and ffprobe would starve HTTP requests.
        threading.Thread(
            target=_run_revert, args=(point_id, loop),
            name="remuxarr-revert", daemon=True,
        ).start()
        started = True
    finally:
        # Same lifecycle contract as _scan_running: whoever sets the flag
        # either hands it to a thread that will clear it, or clears it on
        # every other exit. Without this an exception between the two lines
        # above wedges every future revert behind a 409 until restart.
        if not started:
            revert_lock.release()

    return {"status": "started", "point_id": point_id}


def _run_revert(point_id: int, loop) -> None:
    try:
        outcome = asyncio.run(restore_revert_point(point_id))
        payload = {
            # "event", not "type" — that is the key the frontend switches
            # on, and every other broadcast in this codebase uses it. A
            # payload keyed "type" is delivered, matches nothing, and is
            # dropped in silence.
            "event": "revert_complete",
            "point_id": point_id,
            "success": outcome.success,
            "error": outcome.error,
            "restored_path": outcome.restored_path,
        }
    except Exception as exc:
        logger.exception("Revert of point %d raised", point_id)
        payload = {"event": "revert_complete", "point_id": point_id,
                   "success": False, "error": str(exc)}
    finally:
        revert_lock.release()

    broadcast_threadsafe(payload, loop)


@router.get("/{point_id}/candidates/")
def candidates(point_id: int, db: Session = Depends(get_db)):
    """
    Files a detached revert point might belong to.

    "exact" is not a suggestion — a rename does not touch a byte, so a
    file still carrying the fingerprint the job recorded IS the file. The
    UI can offer those as one-click. "nearby" is a guess and still goes
    through the full check on attach.
    """
    point = db.get(RevertPoint, point_id)
    if point is None:
        raise HTTPException(404, "No such revert point")
    if point.file_id is not None:
        raise HTTPException(409, "That revert point is already attached to a file")

    return find_candidates(point_id)


@router.post("/{point_id}/attach/")
def attach_point(point_id: int, body: AttachRequest):
    """
    Match a detached revert point to a file.

    A refusal returns 409 with the specific reasons rather than a bare
    error: the user is choosing from a list, and "no" without a reason
    just sends them to try the next one at random.
    """
    outcome = attach(point_id, body.file_id,
                     confirm_mismatch=body.confirm_mismatch)

    if not outcome.success:
        raise HTTPException(
            409,
            detail={"error": outcome.error, "tier": outcome.tier,
                    "reasons": outcome.reasons},
        )

    return {"status": "attached", "tier": outcome.tier,
            "reasons": outcome.reasons}


@router.delete("/{point_id}/")
def discard_point(point_id: int, db: Session = Depends(get_db)):
    """Throw one revert point away, sidecar included."""
    point = db.get(RevertPoint, point_id)
    if point is None:
        raise HTTPException(404, "No such revert point")

    delete_sidecar(point.sidecar_path)
    db.delete(point)
    db.commit()
    return {"status": "deleted", "id": point_id}


@router.delete("/")
def empty_bin(
    detached_only: bool = Query(
        default=False,
        description="Discard only points no longer attached to a file",
    ),
    db: Session = Depends(get_db),
):
    """
    Empty the recycle bin.

    detached_only exists because the two are very different acts. Clearing
    unmatched leftovers after a library rename is housekeeping; clearing
    everything throws away the ability to undo every job that has run
    inside the retention window.
    """
    query = db.query(RevertPoint)
    if detached_only:
        query = query.filter(RevertPoint.file_id.is_(None))

    points = query.all()
    for point in points:
        delete_sidecar(point.sidecar_path)
        db.delete(point)
    db.commit()

    logger.info("Recycle bin emptied: %d revert point(s) discarded", len(points))
    return {"status": "emptied", "discarded": len(points)}
