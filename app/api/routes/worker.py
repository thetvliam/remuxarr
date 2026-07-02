"""
Worker control routes
=====================
GET  /api/worker/status       — current pause state + active job count
POST /api/worker/pause        — stop picking up new jobs
POST /api/worker/resume       — resume picking up jobs
POST /api/worker/abort/{id}   — cancel a currently-processing job + pause auto-start
"""
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.core.worker import (
    abort_job,
    get_active_job_count,
    is_worker_paused,
    pause_worker,
    resume_worker,
)
from app.database.session import get_db, update_app_setting

router = APIRouter(prefix="/api/worker", tags=["worker"])


@router.get("/status")
def worker_status():
    return {
        "paused":      is_worker_paused(),
        "active_jobs": get_active_job_count(),
    }


@router.post("/pause")
def pause():
    pause_worker()
    return {"paused": True}


@router.post("/resume")
def resume():
    resume_worker()
    return {"paused": False}


@router.post("/abort/{job_id}")
def abort(job_id: int, db: Session = Depends(get_db)):
    """
    Cancel a currently-processing job immediately AND disable auto-start.

    The combined action exists specifically for the scenario a new user is
    most likely to hit: a scan runs without dry-run enabled, auto-start is
    on, and the very first file being processed turns out to not be what
    they wanted (wrong settings, wrong language, etc.). Aborting the file
    alone wouldn't help if the next queued file starts immediately after —
    auto-start is switched off in the same action so the queue pauses,
    giving the user a chance to review settings before resuming manually.
    """
    aborted = abort_job(job_id)
    if not aborted:
        raise HTTPException(404, "Job is not currently processing")

    update_app_setting(db, "auto_start_jobs", False)
    return {"aborted": True, "auto_start_disabled": True}
