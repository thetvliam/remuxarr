from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import case as sa_case, desc, func
from sqlalchemy.orm import Session

from app.api.routes.queue import _retry_with_reprobe, _serialize
from app.database.models import MediaFile, QueueItem
from app.database.session import get_db

router = APIRouter(prefix="/api/history", tags=["history"])

TERMINAL_STATUSES = ["success", "failed", "skipped", "cancelled", "dry_run"]


@router.get("/")
def list_history(
    status: str = Query(
        default="all",
        description="Filter: all | success | failed | skipped | dry_run. "
                    "Passing 'failed' includes cancelled items too.",
    ),
    limit:  int = Query(default=50, ge=1, le=10000),
    offset: int = Query(default=0, ge=0),
    search: str = Query(
        default="",
        description="Filter by filename (case-insensitive substring match).",
    ),
    db: Session = Depends(get_db),
):
    """Paginated processing history with optional filename search."""
    query = db.query(QueueItem).filter(
        QueueItem.status.in_(TERMINAL_STATUSES)
    )

    # Status filter — "failed" includes cancelled so both appear in the
    # Failed tab without needing a separate cancelled tab.
    if status == "failed":
        query = query.filter(QueueItem.status.in_(["failed", "cancelled"]))
    elif status != "all":
        query = query.filter(QueueItem.status == status)

    # Server-side filename search — join MediaFile only when needed.
    if search.strip():
        s = search.strip()
        query = (
            query
            .join(QueueItem.media_file)
            .filter(MediaFile.filename.ilike(f"%{s}%"))
        )

    total = query.count()

    # Ordering — when searching, rank by relevance so that filenames
    # starting with the search term appear first, ahead of entries that
    # merely contain the term somewhere in the middle.
    #
    # Rank 0: filename starts with the search term          "Wanderers - S01…"
    # Rank 1: a word in the filename starts with the term   "The Long Wanderers"
    # Rank 2: match anywhere else                            "…Wander…"
    #
    # Within each rank group, most-recently completed items appear first.
    if search.strip():
        s = search.strip()
        relevance = sa_case(
            (MediaFile.filename.ilike(f"{s}%"),   0),
            (MediaFile.filename.ilike(f"% {s}%"), 1),
            else_=2,
        )
        order_clause = [relevance, desc(QueueItem.completed_at)]
    else:
        order_clause = [desc(QueueItem.completed_at)]

    items = (
        query
        .order_by(*order_clause)
        .offset(offset)
        .limit(limit)
        .all()
    )

    return {
        "total":  total,
        "offset": offset,
        "limit":  limit,
        "items":  [_history_serialize(i) for i in items],
    }


@router.get("/summary")
def history_summary(db: Session = Depends(get_db)):
    """
    Aggregate stats: total processed, bytes saved, success rate.
    Useful for a dashboard card.
    """
    rows = (
        db.query(QueueItem.status, func.count(QueueItem.id))
        .filter(QueueItem.status.in_(TERMINAL_STATUSES))
        .group_by(QueueItem.status)
        .all()
    )
    counts = {s: c for s, c in rows}

    # Sum size savings across successful jobs
    saved_row = (
        db.query(
            func.sum(QueueItem.original_size - QueueItem.output_size)
        )
        .filter(
            QueueItem.status == "success",
            QueueItem.original_size != None,
            QueueItem.output_size   != None,
        )
        .scalar()
    )

    return {
        "success":     counts.get("success",   0),
        # failed includes cancelled — matches what the Failed tab shows
        "failed":      counts.get("failed",    0) + counts.get("cancelled", 0),
        "skipped":     counts.get("skipped",   0),
        "dry_run":     counts.get("dry_run",   0),
        "bytes_saved": int(saved_row or 0),
    }


# ── Must be declared before /{item_id} so FastAPI doesn't treat the path
#    segment as an item ID. ─────────────────────────────────────────────────

@router.delete("/clear")
def clear_history(
    status: str = Query(default="all", description="all | failed | success | skipped | cancelled"),
    db: Session = Depends(get_db),
):
    """
    Bulk-delete all terminal history items, optionally filtered by status.

    Resets each dismissed file's size/mtime to sentinel values (-1 / -1.0)
    alongside status="unprocessed" — matching clear_dry_run's precedent in
    queue.py for the identical problem. Without this, the scanner's delta
    check in _process_file compares ONLY size/mtime against the current
    on-disk stat() and has no awareness of .status at all — a plain
    re-scan (force_probe=False) would see the file's actual bytes are
    unchanged from what was last stamped and return immediately, never
    calling analyze_file() again. So a dismissed file would only actually
    get re-evaluated on a forced FULL scan, not "the next scan" as this
    endpoint's own behavior otherwise implies.

    status="failed" also matches "cancelled" — mirrors list_history's own
    folding of cancelled into the Failed tab exactly. Without this,
    clearing the Failed tab left cancelled rows behind, still visible in
    the tab this endpoint just claimed to have cleared.
    """
    query = db.query(QueueItem).filter(QueueItem.status.in_(TERMINAL_STATUSES))
    if status == "failed":
        query = query.filter(QueueItem.status.in_(["failed", "cancelled"]))
    elif status != "all":
        query = query.filter(QueueItem.status == status)

    items = query.all()
    count = 0
    for item in items:
        if item.media_file:
            item.media_file.status = "unprocessed"
            item.media_file.size   = -1
            item.media_file.mtime  = -1.0
        db.delete(item)
        count += 1

    db.commit()
    return {"deleted": count}


# ── Per-item endpoints ────────────────────────────────────────────────────────

@router.get("/{item_id}")
def get_history_item(item_id: int, db: Session = Depends(get_db)):
    item = db.get(QueueItem, item_id)
    if not item:
        raise HTTPException(404, "History item not found")
    return _history_serialize(item, include_actions=True)


@router.delete("/{item_id}")
def delete_history_item(item_id: int, db: Session = Depends(get_db)):
    """
    Remove a single completed item from history.

    Resets the MediaFile status to 'unprocessed', with a size/mtime
    sentinel reset — see clear_history for the full rationale on why
    the sentinel is necessary for "the next scan re-evaluates" to
    actually be true for a plain delta scan, not just a forced full one.
    """
    item = db.get(QueueItem, item_id)
    if not item:
        raise HTTPException(404, "History item not found")
    if item.status not in TERMINAL_STATUSES:
        raise HTTPException(
            400,
            f"Can only delete terminal items — current status is '{item.status}'"
        )

    if item.media_file:
        item.media_file.status = "unprocessed"
        item.media_file.size   = -1
        item.media_file.mtime  = -1.0

    db.delete(item)
    db.commit()
    return {"success": True}


@router.post("/{item_id}/retry")
def retry_history_item(item_id: int, db: Session = Depends(get_db)):
    """
    Re-evaluate and re-queue a failed/cancelled item from the history view,
    or — for a dry-run preview — queue it for REAL processing. The file is
    re-probed first (force_probe=True); see _retry_with_reprobe for why.
    """
    item = db.get(QueueItem, item_id)
    if not item:
        raise HTTPException(404, "History item not found")
    if item.status not in ("failed", "cancelled", "dry_run", "success", "skipped"):
        raise HTTPException(400, "Only failed, cancelled, dry-run, success, or skipped items can be retried")

    return _retry_with_reprobe(db, item)


# ── Serialiser ─────────────────────────────────────────────────────────────────

def _history_serialize(item: QueueItem, include_actions: bool = False) -> dict:
    base = _serialize(item, include_actions=include_actions)

    # Extra fields only relevant in history
    base["output_path"]   = item.output_path
    base["original_size"] = item.original_size
    base["output_size"]   = item.output_size

    if item.original_size and item.output_size and item.original_size > 0:
        saved     = item.original_size - item.output_size
        saved_pct = (saved / item.original_size) * 100
        base["bytes_saved"]       = saved
        base["bytes_saved_pct"]   = round(saved_pct, 1)
    else:
        base["bytes_saved"]     = None
        base["bytes_saved_pct"] = None

    return base
