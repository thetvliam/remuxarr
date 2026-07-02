from fastapi import APIRouter, Query

from app.core.log_handler import get_handler

router = APIRouter(prefix="/api/logs", tags=["logs"])


@router.get("/")
def list_logs(limit: int = Query(default=200, ge=1, le=500)):
    """
    Return the most recent log records captured by the in-memory handler.
    Records are ordered oldest-first so the frontend can append new ones
    without reordering.  Level filtering is done client-side so toggling
    the filter in the UI is instant without a new network request.
    """
    records = get_handler().get_records(limit=limit)
    return {"records": records, "total": len(records)}


@router.delete("/")
def clear_logs():
    """Flush the in-memory log buffer."""
    get_handler().clear()
    return {"cleared": True}
