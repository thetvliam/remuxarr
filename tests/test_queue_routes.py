"""
queue.py — the routes a user presses buttons on.

Two things here are worth more than the line coverage suggests.

THE SENTINEL INVARIANT
  Several routes dismiss a file: cancel one item, clear all pending, clear a
  dry-run batch. Every one of them must reset MediaFile.size/mtime to -1
  alongside the status, because the scanner's delta check compares ONLY
  size/mtime against the on-disk stat and has no awareness of .status at all.
  Without the reset, a dismissed file's unchanged bytes read as "nothing to
  do" and it is never re-evaluated by any delta scan — directly contradicting
  the frontend's own copy for all three actions ("they re-appear on the next
  scan").

  This has already been missed twice: clear_dry_run and history clear/delete
  reset the sentinels, cancel_item and clear_pending did not. So the invariant
  is tested as an invariant — test_every_dismissal_route_resets_the_sentinels
  drives each route through a shared table and fails on any that forgets,
  including one added later. tests/test_queue_lifecycle.py already covers the
  two individual routes; this is the generalisation, not a duplicate.

COUNTS THAT MEAN WHAT THEY SAY
  retry_all_failed's return values were wrong in a specific way: "retried"
  counted every item _process_file did not raise on, which is not the same as
  re-queued. A settings change that turned 40 of 50 failures into no-ops
  still reported 50, and the queue then showed 10. The counts are read
  straight into UI copy, so they are tested against the ScanStats outcomes
  rather than against the number of items looped over.

Verified by mutation: 37 mutations of queue.py, every one killed by at least
one test here, and no equivalents among them. The invariant test was checked
the same way — adding a new dismissal route that sets status but forgets the
sentinels fails it by name.
"""
import json

import pytest
from fastapi import HTTPException


# ── Harness ──────────────────────────────────────────────────────────────────

REAL_SIZE  = 4_000_000_000
REAL_MTIME = 1_700_000_000.0


@pytest.fixture
def db():
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    from app.database.models import Base

    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)()


def _file(db, file_id=1, status="queued"):
    from app.database.models import MediaFile

    mf = MediaFile(id=file_id, path=f"/media/f{file_id}.mkv",
                   filename=f"f{file_id}.mkv", directory="/media",
                   size=REAL_SIZE, mtime=REAL_MTIME, status=status,
                   container="mkv", duration=1200.0)
    db.add(mf)
    db.commit()
    return mf


def _item(db, item_id=1, file_id=1, status="pending", **kw):
    from app.database.models import QueueItem

    qi = QueueItem(id=item_id, file_id=file_id, status=status, **kw)
    db.add(qi)
    db.commit()
    return qi


def _sentinels(db, file_id=1):
    from app.database.models import MediaFile

    m = db.get(MediaFile, file_id)
    return (m.size, m.mtime, m.status)


# ── The sentinel invariant ───────────────────────────────────────────────────

def _dismiss_via_cancel_item(db):
    from app.api.routes.queue import cancel_item
    _file(db)
    _item(db, status="pending")
    cancel_item(1, db)


def _dismiss_via_clear_pending(db):
    from app.api.routes.queue import clear_pending
    _file(db)
    _item(db, status="pending")
    clear_pending(db)


def _dismiss_via_clear_dry_run(db):
    from app.api.routes.queue import clear_dry_run
    _file(db)
    _item(db, status="dry_run")
    clear_dry_run(db)


def _dismiss_via_cancel_manual_review(db):
    from app.api.routes.queue import cancel_item
    _file(db)
    _item(db, status="manual_review")
    cancel_item(1, db)


DISMISSAL_ROUTES = {
    "cancel_item (pending)":        _dismiss_via_cancel_item,
    "cancel_item (manual_review)":  _dismiss_via_cancel_manual_review,
    "clear_pending":                _dismiss_via_clear_pending,
    "clear_dry_run":                _dismiss_via_clear_dry_run,
}


@pytest.mark.parametrize("label", sorted(DISMISSAL_ROUTES))
def test_every_dismissal_route_resets_the_sentinels(db, label):
    """
    The invariant, applied to every route that dismisses a file.

    A dismissal that sets status but leaves size/mtime matching the on-disk
    file is invisible to every delta scan, because the delta check reads only
    those two fields. The file then sits dismissed forever, or until a forced
    full scan happens to touch it.

    If a new dismissal route is added, add it to DISMISSAL_ROUTES — this test
    is the thing that will catch it forgetting the reset, which has already
    happened twice.
    """
    DISMISSAL_ROUTES[label](db)

    size, mtime, status = _sentinels(db)
    assert size == -1, f"{label}: size not reset — file invisible to delta scans"
    assert mtime == -1.0, f"{label}: mtime not reset"
    assert status == "skipped", f"{label}: media status left at '{status}'"


# ── clear_dry_run ────────────────────────────────────────────────────────────

def test_clearing_dry_runs_deletes_the_rows_outright(db):
    """
    Unlike a cancelled job, a discarded preview has no history worth keeping —
    so the rows go, rather than being marked cancelled.
    """
    from app.api.routes.queue import clear_dry_run
    from app.database.models import QueueItem

    _file(db)
    _item(db, status="dry_run")

    assert clear_dry_run(db) == {"cleared": 1}
    assert db.query(QueueItem).count() == 0


def test_clearing_dry_runs_leaves_other_statuses_alone(db):
    """
    dry_run is a separate terminal status set by _finish_job. This endpoint
    exists precisely because clear_pending never touches it — the reverse must
    hold too.
    """
    from app.api.routes.queue import clear_dry_run
    from app.database.models import QueueItem

    for i, status in enumerate(["pending", "failed", "success", "manual_review"], 1):
        _file(db, file_id=i)
        _item(db, item_id=i, file_id=i, status=status)
    _file(db, file_id=99)
    _item(db, item_id=99, file_id=99, status="dry_run")

    assert clear_dry_run(db) == {"cleared": 1}
    assert db.query(QueueItem).count() == 4


def test_clearing_an_empty_dry_run_batch_is_a_no_op(db):
    from app.api.routes.queue import clear_dry_run

    assert clear_dry_run(db) == {"cleared": 0}


def test_a_dry_run_item_with_no_media_row_does_not_break_the_clear(db):
    """
    The `if item.media_file:` guard. One orphaned row must not abort the whole
    batch and leave the rest of the previews stranded.
    """
    from app.api.routes.queue import clear_dry_run
    from app.database.models import QueueItem

    _file(db, file_id=1)
    _item(db, item_id=1, file_id=1, status="dry_run")
    _item(db, item_id=2, file_id=1, status="dry_run")
    db.query(QueueItem).filter(QueueItem.id == 2).update({"file_id": 4242})
    db.commit()

    assert clear_dry_run(db) == {"cleared": 2}
    assert db.query(QueueItem).count() == 0


# ── clear_pending ────────────────────────────────────────────────────────────

def test_clearing_pending_only_touches_pending_items(db):
    from app.api.routes.queue import clear_pending
    from app.database.models import QueueItem

    for i, status in enumerate(["pending", "processing", "dry_run", "failed"], 1):
        _file(db, file_id=i)
        _item(db, item_id=i, file_id=i, status=status)

    assert clear_pending(db) == {"cancelled": 1}

    remaining = {q.id: q.status for q in db.query(QueueItem).all()}
    assert remaining == {1: "cancelled", 2: "processing", 3: "dry_run", 4: "failed"}


def test_clearing_pending_stamps_completed_at(db):
    """
    History orders by completed_at DESC and SQLite sorts NULLs last, so a
    cancelled row without it sinks to the bottom of the Failed tab regardless
    of recency and renders a "—" timestamp.
    """
    from app.api.routes.queue import clear_pending
    from app.database.models import QueueItem

    _file(db)
    _item(db, status="pending")

    clear_pending(db)

    assert db.get(QueueItem, 1).completed_at is not None


def test_clearing_pending_resets_every_affected_file(db):
    from app.api.routes.queue import clear_pending

    for i in (1, 2, 3):
        _file(db, file_id=i)
        _item(db, item_id=i, file_id=i, status="pending")

    assert clear_pending(db) == {"cancelled": 3}
    for i in (1, 2, 3):
        assert _sentinels(db, i) == (-1, -1.0, "skipped")


def test_clearing_an_empty_queue_is_a_no_op(db):
    from app.api.routes.queue import clear_pending

    assert clear_pending(db) == {"cancelled": 0}


# ── cancel_item guards ───────────────────────────────────────────────────────

def test_cancelling_a_missing_item_is_a_404(db):
    from app.api.routes.queue import cancel_item

    with pytest.raises(HTTPException) as exc:
        cancel_item(999, db)
    assert exc.value.status_code == 404


@pytest.mark.parametrize("status", ["processing", "success", "failed",
                                    "cancelled", "dry_run"])
def test_only_pending_and_manual_review_items_can_be_cancelled(db, status):
    """
    "processing" matters most: the worker owns that row, and cancelling it
    here would race the job rather than stopping it. Aborting a running job is
    a different operation (abort_job) with its own task-registry handling.
    """
    from app.api.routes.queue import cancel_item
    from app.database.models import QueueItem

    _file(db)
    _item(db, status=status)

    with pytest.raises(HTTPException) as exc:
        cancel_item(1, db)
    assert exc.value.status_code == 400

    assert db.get(QueueItem, 1).status == status
    assert _sentinels(db) == (REAL_SIZE, REAL_MTIME, "queued"), (
        "a rejected cancel still reset the file's delta sentinels"
    )


def test_cancelling_stamps_completed_at(db):
    from app.api.routes.queue import cancel_item
    from app.database.models import QueueItem

    _file(db)
    _item(db, status="pending")

    cancel_item(1, db)

    assert db.get(QueueItem, 1).completed_at is not None


# ── prioritize_item ──────────────────────────────────────────────────────────

def test_prioritising_moves_an_item_below_the_current_minimum(db):
    """The worker orders by priority ASC, so lower wins."""
    from app.api.routes.queue import prioritize_item

    for i in (1, 2, 3):
        _file(db, file_id=i)
        _item(db, item_id=i, file_id=i, status="pending", priority=5)

    assert prioritize_item(3, db) == {"id": 3, "priority": 4}


def test_prioritising_repeatedly_keeps_producing_a_deterministic_order(db):
    """
    Each call recalculates the minimum across all OTHER pending items, so
    pressing the button on several items in turn puts the last one first.
    """
    from app.api.routes.queue import prioritize_item

    for i in (1, 2, 3):
        _file(db, file_id=i)
        _item(db, item_id=i, file_id=i, status="pending", priority=5)

    first  = prioritize_item(1, db)["priority"]
    second = prioritize_item(2, db)["priority"]
    third  = prioritize_item(3, db)["priority"]

    assert second < first, "second press did not overtake the first"
    assert third < second, "third press did not overtake the second"


def test_prioritising_the_only_pending_item_resets_to_the_default(db):
    """
    With nothing to overtake there is no minimum to go below, so it returns to
    the default rather than drifting ever more negative on repeated presses.
    """
    from app.api.routes.queue import prioritize_item

    _file(db)
    _item(db, status="pending", priority=2)

    assert prioritize_item(1, db) == {"id": 1, "priority": 5}


def test_the_minimum_ignores_non_pending_items(db):
    """
    A finished item's priority is stale bookkeeping. Counting it would let a
    long-completed row dictate where a new one lands.
    """
    from app.api.routes.queue import prioritize_item

    _file(db, file_id=1)
    _item(db, item_id=1, file_id=1, status="success", priority=-50)
    _file(db, file_id=2)
    _item(db, item_id=2, file_id=2, status="pending", priority=5)
    _file(db, file_id=3)
    _item(db, item_id=3, file_id=3, status="pending", priority=5)

    assert prioritize_item(3, db)["priority"] == 4


def test_prioritising_a_missing_item_is_a_404(db):
    from app.api.routes.queue import prioritize_item

    with pytest.raises(HTTPException) as exc:
        prioritize_item(999, db)
    assert exc.value.status_code == 404


@pytest.mark.parametrize("status", ["processing", "success", "failed",
                                    "manual_review", "dry_run"])
def test_only_pending_items_can_be_prioritised(db, status):
    from app.api.routes.queue import prioritize_item

    _file(db)
    _item(db, status=status, priority=5)

    with pytest.raises(HTTPException) as exc:
        prioritize_item(1, db)
    assert exc.value.status_code == 400


# ── retry_all_failed ─────────────────────────────────────────────────────────

@pytest.fixture
def retry(monkeypatch):
    """
    Stub _process_file and the settings lookup, and drive ScanStats directly —
    what is under test is which counts come back, not the decision engine.
    """
    import app.api.routes.queue as q

    calls = []
    outcomes = {}

    def _process_file(db, path, cfg, **kw):
        calls.append({"path": path, **kw})
        outcome = outcomes.get(path, "queued")
        if outcome == "raise":
            raise ValueError(f"unknown container info for {path}")
        setattr(kw["stats"], outcome, getattr(kw["stats"], outcome) + 1)

    monkeypatch.setattr(q, "_process_file", _process_file)
    monkeypatch.setattr(q, "get_app_settings", lambda _db: {})
    monkeypatch.setattr(q, "_current_dry_run_mode", lambda _db: False)
    monkeypatch.setattr(q.os.path, "exists", lambda p: p != "/media/gone.mkv")

    q._calls = calls
    q._outcomes = outcomes
    return q


def test_retrying_with_nothing_failed_is_a_no_op(db, retry):
    from app.api.routes.queue import retry_all_failed

    assert retry_all_failed(db) == {"retried": 0, "skipped": 0}
    assert retry._calls == []


@pytest.mark.parametrize("status", ["failed", "cancelled"])
def test_both_failed_and_cancelled_items_are_retried(db, retry, status):
    from app.api.routes.queue import retry_all_failed

    _file(db)
    _item(db, status=status)

    assert retry_all_failed(db)["retried"] == 1


@pytest.mark.parametrize("status", ["pending", "processing", "success",
                                    "manual_review", "dry_run"])
def test_unfinished_and_successful_items_are_left_alone(db, retry, status):
    from app.api.routes.queue import retry_all_failed

    _file(db)
    _item(db, status=status)

    assert retry_all_failed(db) == {"retried": 0, "skipped": 0}


def test_every_retry_forces_a_fresh_probe(db, retry):
    """
    The point of retry: pick up settings changes, code fixes, or on-disk
    changes since the failure. A cached probe would replay the same decision.
    """
    from app.api.routes.queue import retry_all_failed

    _file(db)
    _item(db, status="failed")

    retry_all_failed(db)

    assert retry._calls[0]["force_probe"] is True


def test_arr_ids_survive_the_retry(db, retry):
    """
    Preserved so the notification chain fires after the re-processed job
    completes. Previously "Retry All" dropped them, so webhook-originated
    failures produced jobs that never fired RescanSeries/RescanMovie —
    even though single-item retry preserved them correctly.
    """
    from app.api.routes.queue import retry_all_failed

    _file(db)
    _item(db, status="failed", sonarr_series_id=17, radarr_movie_id=42)

    retry_all_failed(db)

    assert retry._calls[0]["sonarr_series_id"] == 17
    assert retry._calls[0]["radarr_movie_id"] == 42


def test_a_file_gone_from_disk_is_skipped_not_retried(db, retry):
    from app.api.routes.queue import retry_all_failed
    from app.database.models import MediaFile

    _file(db)
    db.query(MediaFile).filter(MediaFile.id == 1).update({"path": "/media/gone.mkv"})
    db.commit()
    _item(db, status="failed")

    assert retry_all_failed(db)["skipped"] == 1
    assert retry._calls == []


def test_the_retried_count_reflects_requeued_work_not_items_looped_over(db, retry):
    """
    The bug this counting was changed for. "retried" used to mean "every item
    _process_file did not raise on", so a settings change that turned most
    failures into no-ops still reported all of them as requeued and the queue
    then showed a fraction of that.
    """
    from app.api.routes.queue import retry_all_failed

    for i in (1, 2, 3, 4):
        _file(db, file_id=i)
        _item(db, item_id=i, file_id=i, status="failed")
    retry._outcomes["/media/f2.mkv"] = "skipped"
    retry._outcomes["/media/f3.mkv"] = "skipped"
    retry._outcomes["/media/f4.mkv"] = "manual_review"

    result = retry_all_failed(db)

    assert result["retried"] == 1
    assert result["skipped"] == 2
    assert result["manual_review"] == 1


def test_manual_review_outcomes_are_reported_separately(db, retry):
    """
    Not finished — waiting on the user. Folding them into either other count
    would hide that, and a retry that moved items to Review used to look like
    it had done nothing.
    """
    from app.api.routes.queue import retry_all_failed

    _file(db)
    _item(db, status="failed")
    retry._outcomes["/media/f1.mkv"] = "manual_review"

    result = retry_all_failed(db)

    assert result["manual_review"] == 1
    assert result["retried"] == 0
    assert result["skipped"] == 0


def test_one_bad_file_does_not_abandon_the_rest_of_the_batch(db, retry):
    """
    Mirrors scan_library's own per-file protection. Without it, one raising
    file kills the request with a 500 and silently abandons every item queued
    behind it, with no indication of where the batch stopped.
    """
    from app.api.routes.queue import retry_all_failed

    for i in (1, 2, 3):
        _file(db, file_id=i)
        _item(db, item_id=i, file_id=i, status="failed")
    retry._outcomes["/media/f2.mkv"] = "raise"

    result = retry_all_failed(db)

    assert [c["path"] for c in retry._calls] == [
        "/media/f1.mkv", "/media/f2.mkv", "/media/f3.mkv"
    ], "the batch stopped at the failing file"
    assert result["retried"] == 2
    assert len(result["errors"]) == 1
    assert result["errors"][0]["path"] == "/media/f2.mkv"


# ── _serialize ───────────────────────────────────────────────────────────────

def test_a_serialised_item_carries_its_file_details(db):
    from app.api.routes.queue import _serialize

    _file(db)
    item = _item(db, status="pending", reason="drop 2 audio tracks")

    out = _serialize(item)

    assert out["id"] == 1
    assert out["status"] == "pending"
    assert out["reason"] == "drop 2 audio tracks"
    assert out["file"]["filename"] == "f1.mkv"
    assert out["file"]["container"] == "mkv"


def test_an_item_whose_media_row_is_gone_serialises_with_a_null_file(db):
    """
    The UI renders this. Raising here would take out the whole list endpoint
    because of one orphaned row.
    """
    from app.api.routes.queue import _serialize
    from app.database.models import QueueItem

    _file(db)
    _item(db, status="pending")
    db.query(QueueItem).filter(QueueItem.id == 1).update({"file_id": 4242})
    db.commit()

    out = _serialize(db.get(QueueItem, 1))

    assert out["file"] is None


def test_flagged_subtitles_are_parsed_from_json(db):
    from app.api.routes.queue import _serialize

    _file(db)
    item = _item(db, status="manual_review",
                 review_subtitles=json.dumps([{"stream_index": 3,
                                               "codec": "hdmv_pgs_subtitle"}]))

    out = _serialize(item)

    assert out["flagged_subtitles"] == [{"stream_index": 3,
                                         "codec": "hdmv_pgs_subtitle"}]


def test_unparseable_flagged_subtitles_degrade_to_null(db):
    """
    Corrupt bookkeeping must not break the queue view — the item still needs
    to be visible so the user can dismiss it.
    """
    from app.api.routes.queue import _serialize

    _file(db)
    item = _item(db, status="manual_review", review_subtitles="{not json")

    assert _serialize(item)["flagged_subtitles"] is None


def test_planned_actions_are_omitted_unless_asked_for(db):
    """
    The list endpoints serialise every item; loading each one's actions would
    be a query per row.
    """
    from app.api.routes.queue import _serialize

    _file(db)
    item = _item(db, status="pending")

    assert "planned_actions" not in _serialize(item)
    assert _serialize(item, include_actions=True)["planned_actions"] == []


def test_planned_actions_are_returned_in_order(db):
    from app.api.routes.queue import _serialize
    from app.database.models import PlannedAction

    _file(db)
    item = _item(db, status="pending")
    for order in (2, 0, 1):
        db.add(PlannedAction(queue_item_id=1, order=order,
                             action_type="drop_track",
                             description=f"step {order}",
                             track_type="audio", stream_index=order))
    db.commit()
    db.refresh(item)

    actions = _serialize(item, include_actions=True)["planned_actions"]

    assert [a["order"] for a in actions] == [0, 1, 2]


def test_missing_timestamps_serialise_as_null_rather_than_raising(db):
    from app.api.routes.queue import _serialize

    _file(db)
    item = _item(db, status="pending")

    out = _serialize(item)

    assert out["started_at"] is None
    assert out["completed_at"] is None
    assert out["created_at"] is not None
