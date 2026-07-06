"""
Forge API Routes
================
GET  /api/forge/candidates/          — AAC 5.1 files not yet forge-processed
GET  /api/forge/active               — currently-processing forge job
GET  /api/forge/processed/           — completed / failed forge jobs
POST /api/forge/queue/               — queue a file for AC3 addition
DELETE /api/forge/{job_id}/          — cancel a pending job
POST /api/forge/{job_id}/undo/       — queue undo (remove the AC3 track)
"""
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import desc
from sqlalchemy.orm import Session

from app.core.forge import get_candidates, queue_forge_job
from app.database.models import Ac3ForgeJob
from app.database.session import get_db

router = APIRouter(prefix="/api/forge", tags=["forge"])


# ── Request models ─────────────────────────────────────────────────────────────

class QueueForgeRequest(BaseModel):
    file_id: int


# ── Endpoints ──────────────────────────────────────────────────────────────────

@router.get("/candidates/")
def list_candidates(
    search: str = Query(default="", description="Filter by filename"),
    limit:  int = Query(default=50, ge=1, le=10000),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),
):
    """
    Paginated AAC 5.1 candidates.  Returns {"total": N, "items": [...]}.
    When a search term is provided, results are ranked by relevance
    (filename starts with term first) rather than pure alphabetical order.
    """
    return get_candidates(db, search=search, limit=limit, offset=offset)


@router.get("/active")
def get_active(db: Session = Depends(get_db)):
    """
    The currently-processing OR queued-but-not-yet-started forge job, or
    null if idle.

    Includes "pending" alongside "processing" — previously this only
    matched "processing", meaning a job sitting queued (added via
    add_to_queue, not yet picked up by the worker's forge-priority check)
    was invisible here AND in list_processed below (which already covers
    undo_pending but never plain pending). A user who'd just clicked
    "add to queue" would see nothing happen in the UI until the worker
    loop actually got to it — which, under a busy main-queue backlog,
    could be a long, silent wait with zero visible confirmation anything
    was queued at all.
    """
    job = (
        db.query(Ac3ForgeJob)
        .filter(Ac3ForgeJob.status.in_(["processing", "pending"]))
        .order_by(Ac3ForgeJob.created_at.asc())
        .first()
    )
    return _serialize(job, include_file=True) if job else None


@router.get("/processed/")
def list_processed(db: Session = Depends(get_db)):
    """
    Forge jobs that have completed (success or undo-related states).
    Excludes undone/cancelled jobs — those files are back in the candidates list.
    """
    jobs = (
        db.query(Ac3ForgeJob)
        .filter(Ac3ForgeJob.status.in_(["success", "failed", "undo_failed", "undo_pending"]))
        .order_by(desc(Ac3ForgeJob.completed_at))
        .all()
    )
    return [_serialize(j, include_file=True) for j in jobs]


@router.post("/queue/")
def add_to_queue(body: QueueForgeRequest, db: Session = Depends(get_db)):
    """Queue a media file for AC3 5.1 addition."""
    # Reject if already queued, processed, or has an AC3 track still present
    # from a failed undo attempt (undo_failed means the AC3 track was never
    # actually removed — adding another would create a duplicate/overwrite).
    # "failed" is deliberately NOT included: a failed ADD attempt never
    # produced an AC3 track, so the file is a legitimate candidate to retry.
    existing = db.query(Ac3ForgeJob).filter(
        Ac3ForgeJob.file_id == body.file_id,
        Ac3ForgeJob.status.in_(["pending", "processing", "success", "undo_pending", "undo_failed"]),
    ).first()
    if existing:
        raise HTTPException(400, "File is already in the forge queue or has been processed")

    try:
        job = queue_forge_job(db, body.file_id)
        return _serialize(job, include_file=True)
    except ValueError as exc:
        raise HTTPException(400, str(exc))


@router.delete("/{job_id}/")
def cancel_job(job_id: int, db: Session = Depends(get_db)):
    """Cancel a pending forge job before it starts processing."""
    job = db.get(Ac3ForgeJob, job_id)
    if not job:
        raise HTTPException(404, "Forge job not found")
    if job.status != "pending":
        raise HTTPException(400, f"Only pending jobs can be cancelled (status: {job.status!r})")
    job.status = "cancelled"
    db.commit()
    return {"success": True}


@router.post("/{job_id}/undo/")
def undo_job(job_id: int, db: Session = Depends(get_db)):
    """
    Queue removal of the AC3 track that was added by this forge job.
    Only allowed when status is 'success' or 'undo_failed' (retry undo).
    """
    job = db.get(Ac3ForgeJob, job_id)
    if not job:
        raise HTTPException(404, "Forge job not found")
    if job.status not in ("success", "undo_failed"):
        raise HTTPException(
            400,
            f"Can only undo successful jobs or retry a failed undo "
            f"(current status: {job.status!r})"
        )

    job.status         = "undo_pending"
    job.is_undo        = True
    job.progress       = 0.0
    job.started_at     = None
    job.completed_at   = None
    job.error_message  = None
    job.current_action = None
    db.commit()
    return {"success": True, "job_id": job.id}


# ── Serialiser ─────────────────────────────────────────────────────────────────

def _iso(dt: datetime | None) -> str | None:
    return dt.isoformat() if dt else None


def _serialize(job: Ac3ForgeJob, include_file: bool = False) -> dict:
    result: dict = {
        "id":             job.id,
        "status":         job.status,
        "is_undo":        job.is_undo,
        "progress":       job.progress,
        "current_action": job.current_action,
        "error_message":  job.error_message,
        "original_size":  job.original_size,
        "output_size":    job.output_size,
        "created_at":     _iso(job.created_at),
        "started_at":     _iso(job.started_at),
        "completed_at":   _iso(job.completed_at),
    }

    if include_file and job.media_file:
        f = job.media_file
        result["file"] = {
            "id":        f.id,
            "filename":  f.filename,
            "path":      f.path,
            "size":      f.size,
            "duration":  f.duration,
            "container": f.container,
        }
    else:
        result["file"] = None

    return result
