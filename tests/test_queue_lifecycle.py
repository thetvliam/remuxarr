"""
Regression tests for queue-lifecycle behavior.

Cancelled files never re-appeared on delta scans:
    cancel_item and clear_pending set MediaFile.status but left
    size/mtime matching the on-disk file, and the scanner's delta check
    compares ONLY size/mtime — so a cancelled file read as "unchanged"
    and was never re-evaluated by any delta scan, contradicting the
    frontend's own copy for both actions ("re-appear on the next
    scan"). The sibling endpoints that faced the identical problem
    (clear_dry_run, history clear/delete) all reset the sentinels;
    these two were missed. The tests call the route functions directly
    (they're plain functions once `db` is passed explicitly) against an
    in-memory database.

A file could hold pending + manual_review items simultaneously:
    the scanner's manual-review branch never touched an existing
    pending item, so the worker would later claim it, recompute, and
    convert it to manual_review too — a wasted claim ending in a
    duplicate review row. _supersede_stale_pending_items deletes the
    stale pending item AND its PlannedActions children explicitly
    (bulk deletes bypass ORM cascades and the SQLite foreign_keys
    PRAGMA is not enabled, so schema-level CASCADE is inert — the
    children test would orphan-fail without the explicit delete), while
    never touching items the worker owns ("processing").

Run from the project root:
    pytest tests/ -v
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.api.routes.queue import cancel_item, clear_pending
from app.core.scanner import _supersede_stale_pending_items
from app.database.models import Base, MediaFile, PlannedAction, QueueItem


def _make_db():
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)()


def _add_file(db, file_id=1, size=4_000_000_000, mtime=1_700_000_000.0,
              status="queued"):
    db.add(MediaFile(id=file_id, path=f"/media/f{file_id}.mkv",
                     filename=f"f{file_id}.mkv", directory="/media",
                     size=size, mtime=mtime, status=status))
    db.commit()


def _add_item(db, item_id, file_id=1, status="pending", n_actions=0):
    db.add(QueueItem(id=item_id, file_id=file_id, status=status))
    db.flush()
    for i in range(n_actions):
        db.add(PlannedAction(queue_item_id=item_id, order=i,
                             action_type="drop_track",
                             description=f"drop {i}", track_type="audio",
                             stream_index=i + 1))
    db.commit()


# ═══════════════════════════════════════════════════════════════════════════
# Sentinel resets
# ═══════════════════════════════════════════════════════════════════════════

def test_cancel_item_resets_delta_sentinels():
    """
    After cancelling a pending item, the file's size/mtime must be the
    sentinel values — real files never have negative size or mtime, so
    the scanner's stat() comparison can never read the file as
    "unchanged" again. Leaving the real values here is the bug: the
    file's bytes still match the DB and no delta scan ever re-evaluates
    it, despite the UI promising exactly that.
    """
    db = _make_db()
    _add_file(db)
    _add_item(db, 10, status="pending")

    cancel_item(10, db)

    media = db.get(MediaFile, 1)
    item  = db.get(QueueItem, 10)
    assert item.status == "cancelled"
    assert media.status == "skipped"
    assert media.size == -1 and media.mtime == -1.0, (
        f"Sentinels not reset (size={media.size}, mtime={media.mtime}) — "
        "the cancelled file is invisible to delta scans."
    )


def test_cancel_manual_review_item_resets_sentinels_too():
    """
    The Review page's Skip action hits the same endpoint with a
    manual_review item — the review flag must equally resurface on the
    next delta scan (full scans already re-flag it; the fix makes the
    two scan types consistent, it doesn't change what Skip means).
    """
    db = _make_db()
    _add_file(db, status="manual_review")
    _add_item(db, 11, status="manual_review")

    cancel_item(11, db)

    media = db.get(MediaFile, 1)
    assert media.size == -1 and media.mtime == -1.0


def test_clear_pending_resets_sentinels_for_every_affected_file():
    """Bulk counterpart: every cancelled file gets the reset, and files
    with no pending item are left completely alone."""
    db = _make_db()
    _add_file(db, file_id=1)
    _add_file(db, file_id=2)
    _add_file(db, file_id=3, status="skipped")   # no pending item
    _add_item(db, 20, file_id=1, status="pending")
    _add_item(db, 21, file_id=2, status="pending")

    result = clear_pending(db)
    assert result == {"cancelled": 2}

    for fid in (1, 2):
        media = db.get(MediaFile, fid)
        assert media.status == "skipped"
        assert media.size == -1 and media.mtime == -1.0
    untouched = db.get(MediaFile, 3)
    assert untouched.size > 0 and untouched.mtime > 0, (
        "clear_pending reset sentinels on a file that had no pending item."
    )


# ═══════════════════════════════════════════════════════════════════════════
# Pending items superseded by a manual-review decision
# ═══════════════════════════════════════════════════════════════════════════

def test_supersede_deletes_pending_item_and_its_planned_actions():
    """
    The failing state: a pending item (with its scan-time PlannedActions)
    exists when a later scan decides manual-review. The pending item
    must be deleted AND its children must go with it — bulk deletes
    bypass ORM cascades and SQLite FK enforcement is off in this
    codebase, so without the explicit child delete the planned_actions
    rows would be orphaned forever.
    """
    db = _make_db()
    _add_file(db)
    _add_item(db, 30, status="pending", n_actions=3)

    _supersede_stale_pending_items(db, file_id=1)
    db.commit()

    assert db.get(QueueItem, 30) is None
    orphans = db.query(PlannedAction).filter(
        PlannedAction.queue_item_id == 30
    ).count()
    assert orphans == 0, f"{orphans} PlannedAction rows orphaned"


def test_supersede_never_touches_processing_items():
    """
    A "processing" item is owned by the worker — deleting its rows
    mid-run would yank the job out from under _finish_job. The helper
    must only ever match status 'pending' (this same guard is what
    protects against the fetch→claim race)."""
    db = _make_db()
    _add_file(db)
    _add_item(db, 31, status="processing", n_actions=2)

    _supersede_stale_pending_items(db, file_id=1)
    db.commit()

    assert db.get(QueueItem, 31) is not None
    assert db.query(PlannedAction).filter(
        PlannedAction.queue_item_id == 31
    ).count() == 2


def test_supersede_is_scoped_to_the_given_file():
    """Another file's pending item must survive untouched."""
    db = _make_db()
    _add_file(db, file_id=1)
    _add_file(db, file_id=2)
    _add_item(db, 32, file_id=1, status="pending", n_actions=1)
    _add_item(db, 33, file_id=2, status="pending", n_actions=1)

    _supersede_stale_pending_items(db, file_id=1)
    db.commit()

    assert db.get(QueueItem, 32) is None
    assert db.get(QueueItem, 33) is not None
    assert db.query(PlannedAction).filter(
        PlannedAction.queue_item_id == 33
    ).count() == 1
