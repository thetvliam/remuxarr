"""
history.py — the completed-work view, and the two routes that dismiss from it.

THE SENTINEL INVARIANT, AGAIN
  clear_history and delete_history_item both reset MediaFile.size/mtime to the
  delta sentinels, for the same reason the queue's dismissal routes do: the
  scanner's delta check compares ONLY size/mtime against the on-disk stat and
  has no awareness of .status, so without the reset a dismissed file is never
  re-evaluated by any delta scan.

  These two reset status to "unprocessed", NOT "skipped" like the queue
  routes. That difference is real and intentional — clearing history means
  "forget this ever ran", whereas dismissing from the queue means "I chose not
  to do this". Both tables therefore assert the sentinel pair, which is the
  actual invariant, and their own status separately.

  The tables are per-module by design: consolidating them would make
  test_queue_routes.py and this file import each other's routes. Neither table
  can catch a dismissal route added in some third module, which is a real
  limit of this approach rather than something the tests hide.

FAILED MEANS FAILED-OR-CANCELLED
  list_history, history_summary and clear_history all fold "cancelled" into
  "failed", because the UI has no separate cancelled tab. Any one of them
  drifting leaves rows visible in a tab that claims to have cleared them, or a
  count that disagrees with the list under it. All three are pinned here.

Verified by mutation: 39 mutations of history.py, of which 38 are killed by at
least one test here. The survivor is equivalent, checked rather than assumed:
dropping the TERMINAL_STATUSES filter from history_summary's group-by lets
pending/processing/manual_review rows into the intermediate counts dict, but
the return reads only the five terminal keys, so the response is byte-
identical with rows of every status present.
"""
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


def _file(db, file_id=1, filename=None, status="processed"):
    from app.database.models import MediaFile

    filename = filename or f"f{file_id}.mkv"
    mf = MediaFile(id=file_id, path=f"/media/{filename}", filename=filename,
                   directory="/media", size=REAL_SIZE, mtime=REAL_MTIME,
                   status=status, container="mkv")
    db.add(mf)
    db.commit()
    return mf


def _item(db, item_id=1, file_id=1, status="success", **kw):
    from app.database.models import QueueItem

    qi = QueueItem(id=item_id, file_id=file_id, status=status, **kw)
    db.add(qi)
    db.commit()
    return qi


def _at(minutes):
    from datetime import datetime, timedelta

    return datetime(2026, 1, 1) + timedelta(minutes=minutes)


def _sentinels(db, file_id=1):
    from app.database.models import MediaFile

    m = db.get(MediaFile, file_id)
    return (m.size, m.mtime, m.status)


# ── The sentinel invariant ───────────────────────────────────────────────────

def _dismiss_via_clear_history(db):
    from app.api.routes.history import clear_history
    _file(db)
    _item(db, status="success")
    clear_history("all", db)


def _dismiss_via_clear_history_filtered(db):
    from app.api.routes.history import clear_history
    _file(db)
    _item(db, status="failed")
    clear_history("failed", db)


def _dismiss_via_delete_history_item(db):
    from app.api.routes.history import delete_history_item
    _file(db)
    _item(db, status="success")
    delete_history_item(1, db)


DISMISSAL_ROUTES = {
    "clear_history (all)":     _dismiss_via_clear_history,
    "clear_history (failed)":  _dismiss_via_clear_history_filtered,
    "delete_history_item":     _dismiss_via_delete_history_item,
}


@pytest.mark.parametrize("label", sorted(DISMISSAL_ROUTES))
def test_every_history_dismissal_resets_the_sentinels(db, label):
    """
    The sentinel pair is the invariant. A dismissal that updates status but
    leaves size/mtime matching the on-disk file is invisible to every delta
    scan, so "the next scan re-evaluates it" is false until someone forces a
    full scan.

    If a new history dismissal route is added, add it to DISMISSAL_ROUTES.
    """
    DISMISSAL_ROUTES[label](db)

    size, mtime, _ = _sentinels(db)
    assert size == -1, f"{label}: size not reset — file invisible to delta scans"
    assert mtime == -1.0, f"{label}: mtime not reset"


@pytest.mark.parametrize("label", sorted(DISMISSAL_ROUTES))
def test_history_dismissal_marks_the_file_unprocessed_not_skipped(db, label):
    """
    Deliberately different from the queue's dismissal routes, which use
    "skipped". Clearing history means "forget this ever ran"; dismissing from
    the queue means "I chose not to do this". Pinned separately from the
    sentinel pair so a change to one doesn't silently redefine the other.
    """
    DISMISSAL_ROUTES[label](db)

    assert _sentinels(db)[2] == "unprocessed"


# ── clear_history ────────────────────────────────────────────────────────────

def test_clearing_everything_removes_all_terminal_items(db):
    from app.api.routes.history import clear_history
    from app.database.models import QueueItem

    for i, status in enumerate(
            ["success", "failed", "skipped", "cancelled", "dry_run"], 1):
        _file(db, file_id=i)
        _item(db, item_id=i, file_id=i, status=status)

    assert clear_history("all", db) == {"deleted": 5}
    assert db.query(QueueItem).count() == 0


def test_clearing_never_removes_unfinished_work(db):
    """
    pending and processing are not terminal. Deleting a processing row would
    pull the record out from under a running job.
    """
    from app.api.routes.history import clear_history
    from app.database.models import QueueItem

    for i, status in enumerate(["pending", "processing", "manual_review"], 1):
        _file(db, file_id=i)
        _item(db, item_id=i, file_id=i, status=status)

    assert clear_history("all", db) == {"deleted": 0}
    assert db.query(QueueItem).count() == 3


def test_clearing_the_failed_tab_also_clears_cancelled(db):
    """
    The Failed tab shows both, so clearing it must remove both. Otherwise
    cancelled rows stay visible in the tab this endpoint just claimed to have
    cleared.
    """
    from app.api.routes.history import clear_history
    from app.database.models import QueueItem

    _file(db, file_id=1)
    _item(db, item_id=1, file_id=1, status="failed")
    _file(db, file_id=2)
    _item(db, item_id=2, file_id=2, status="cancelled")
    _file(db, file_id=3)
    _item(db, item_id=3, file_id=3, status="success")

    assert clear_history("failed", db) == {"deleted": 2}
    assert [q.status for q in db.query(QueueItem).all()] == ["success"]


@pytest.mark.parametrize("status", ["success", "skipped", "dry_run"])
def test_clearing_one_status_leaves_the_others(db, status):
    from app.api.routes.history import clear_history
    from app.database.models import QueueItem

    for i, s in enumerate(["success", "failed", "skipped", "dry_run"], 1):
        _file(db, file_id=i)
        _item(db, item_id=i, file_id=i, status=s)

    clear_history(status, db)

    remaining = {q.status for q in db.query(QueueItem).all()}
    assert status not in remaining
    assert len(remaining) == 3


def test_clearing_an_empty_history_is_a_no_op(db):
    from app.api.routes.history import clear_history

    assert clear_history("all", db) == {"deleted": 0}


def test_clearing_commits_its_deletions(tmp_path):
    """
    Checked from a second session. A single session cannot see this: deleted
    rows are already invisible to the session that deleted them, so a missing
    db.commit() looks identical to a successful one until the session closes
    and everything comes back. Same blind spot as the scanner's cleanup path.
    """
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    from app.api.routes.history import clear_history
    from app.database.models import Base, QueueItem

    engine = create_engine(f"sqlite:///{tmp_path / 'h.db'}")
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)

    with Session() as seed:
        _file(seed)
        _item(seed, status="success")

    with Session() as work:
        assert clear_history("all", work) == {"deleted": 1}

    with Session() as check:
        assert check.query(QueueItem).count() == 0, (
            "clear_history did not commit — the deletions were rolled back"
        )


def test_an_item_with_no_media_row_does_not_break_the_clear(db):
    """One orphaned row must not abort the batch and strand the rest."""
    from app.api.routes.history import clear_history
    from app.database.models import QueueItem

    _file(db, file_id=1)
    _item(db, item_id=1, file_id=1, status="success")
    _item(db, item_id=2, file_id=1, status="success")
    db.query(QueueItem).filter(QueueItem.id == 2).update({"file_id": 4242})
    db.commit()

    assert clear_history("all", db) == {"deleted": 2}
    assert db.query(QueueItem).count() == 0


# ── delete_history_item ──────────────────────────────────────────────────────

def test_deleting_a_missing_item_is_a_404(db):
    from app.api.routes.history import delete_history_item

    with pytest.raises(HTTPException) as exc:
        delete_history_item(999, db)
    assert exc.value.status_code == 404


@pytest.mark.parametrize("status", ["pending", "processing", "manual_review"])
def test_only_terminal_items_can_be_deleted(db, status):
    """
    "processing" is the one that matters — the worker owns that row and is
    still writing progress to it.
    """
    from app.api.routes.history import delete_history_item
    from app.database.models import QueueItem

    _file(db)
    _item(db, status=status)

    with pytest.raises(HTTPException) as exc:
        delete_history_item(1, db)
    assert exc.value.status_code == 400

    assert db.query(QueueItem).count() == 1
    assert _sentinels(db) == (REAL_SIZE, REAL_MTIME, "processed"), (
        "a rejected delete still reset the file's delta sentinels"
    )


@pytest.mark.parametrize("status", ["success", "failed", "skipped",
                                    "cancelled", "dry_run"])
def test_every_terminal_status_can_be_deleted(db, status):
    from app.api.routes.history import delete_history_item
    from app.database.models import QueueItem

    _file(db)
    _item(db, status=status)

    assert delete_history_item(1, db) == {"success": True}
    assert db.query(QueueItem).count() == 0


# ── history_summary ──────────────────────────────────────────────────────────

def test_the_summary_counts_each_terminal_status(db):
    from app.api.routes.history import history_summary

    for i, status in enumerate(
            ["success", "success", "failed", "skipped", "dry_run"], 1):
        _file(db, file_id=i)
        _item(db, item_id=i, file_id=i, status=status)

    s = history_summary(db)

    assert s["success"] == 2
    assert s["failed"] == 1
    assert s["skipped"] == 1
    assert s["dry_run"] == 1


def test_the_summary_folds_cancelled_into_failed(db):
    """Matches what the Failed tab shows — the count and the list must agree."""
    from app.api.routes.history import history_summary

    _file(db, file_id=1)
    _item(db, item_id=1, file_id=1, status="failed")
    _file(db, file_id=2)
    _item(db, item_id=2, file_id=2, status="cancelled")

    assert history_summary(db)["failed"] == 2


def test_bytes_saved_sums_only_successful_jobs(db):
    """
    A failed job's recorded sizes describe work that was thrown away, so
    counting them would report savings that don't exist on disk.
    """
    from app.api.routes.history import history_summary

    _file(db, file_id=1)
    _item(db, item_id=1, file_id=1, status="success",
          original_size=1000, output_size=600)
    _file(db, file_id=2)
    _item(db, item_id=2, file_id=2, status="success",
          original_size=500, output_size=400)
    _file(db, file_id=3)
    _item(db, item_id=3, file_id=3, status="failed",
          original_size=9999, output_size=1)

    assert history_summary(db)["bytes_saved"] == 500


def test_jobs_with_no_recorded_sizes_are_excluded_from_savings(db):
    """
    NULL sizes would make the whole SUM null. A job that never got far enough
    to record them must not blank out everything else's savings.
    """
    from app.api.routes.history import history_summary

    _file(db, file_id=1)
    _item(db, item_id=1, file_id=1, status="success",
          original_size=1000, output_size=600)
    _file(db, file_id=2)
    _item(db, item_id=2, file_id=2, status="success")

    assert history_summary(db)["bytes_saved"] == 400


def test_an_empty_history_summarises_as_zeroes(db):
    """bytes_saved in particular must be 0, not None — the UI formats it."""
    from app.api.routes.history import history_summary

    assert history_summary(db) == {
        "success": 0, "failed": 0, "skipped": 0, "dry_run": 0, "bytes_saved": 0,
    }


# ── list_history ─────────────────────────────────────────────────────────────

def test_listing_returns_only_terminal_items(db):
    from app.api.routes.history import list_history

    _file(db, file_id=1)
    _item(db, item_id=1, file_id=1, status="success", completed_at=_at(0))
    _file(db, file_id=2)
    _item(db, item_id=2, file_id=2, status="processing")

    out = list_history("all", 50, 0, "", db)

    assert out["total"] == 1
    assert [i["id"] for i in out["items"]] == [1]


def test_listing_the_failed_tab_includes_cancelled(db):
    from app.api.routes.history import list_history

    _file(db, file_id=1)
    _item(db, item_id=1, file_id=1, status="failed", completed_at=_at(1))
    _file(db, file_id=2)
    _item(db, item_id=2, file_id=2, status="cancelled", completed_at=_at(0))
    _file(db, file_id=3)
    _item(db, item_id=3, file_id=3, status="success", completed_at=_at(2))

    out = list_history("failed", 50, 0, "", db)

    assert out["total"] == 2
    assert {i["status"] for i in out["items"]} == {"failed", "cancelled"}


def test_the_newest_items_come_first(db):
    from app.api.routes.history import list_history

    for i, minute in enumerate([0, 20, 10], 1):
        _file(db, file_id=i)
        _item(db, item_id=i, file_id=i, status="success",
              completed_at=_at(minute))

    out = list_history("all", 50, 0, "", db)

    assert [i["id"] for i in out["items"]] == [2, 3, 1]


def test_paging_reports_the_full_total_not_the_page_size(db):
    """
    The UI builds its pager from `total`. Returning the page length instead
    would collapse it to a single page.
    """
    from app.api.routes.history import list_history

    for i in range(1, 6):
        _file(db, file_id=i)
        _item(db, item_id=i, file_id=i, status="success", completed_at=_at(i))

    out = list_history("all", 2, 0, "", db)

    assert out["total"] == 5
    assert len(out["items"]) == 2
    assert out["limit"] == 2 and out["offset"] == 0


def test_paging_moves_through_the_results(db):
    from app.api.routes.history import list_history

    for i in range(1, 6):
        _file(db, file_id=i)
        _item(db, item_id=i, file_id=i, status="success", completed_at=_at(i))

    first  = list_history("all", 2, 0, "", db)["items"]
    second = list_history("all", 2, 2, "", db)["items"]

    assert {i["id"] for i in first} & {i["id"] for i in second} == set()


def test_search_matches_a_filename_substring_case_insensitively(db):
    from app.api.routes.history import list_history

    _file(db, file_id=1, filename="Wanderers S01E01.mkv")
    _item(db, item_id=1, file_id=1, status="success", completed_at=_at(0))
    _file(db, file_id=2, filename="Something Else.mkv")
    _item(db, item_id=2, file_id=2, status="success", completed_at=_at(1))

    out = list_history("all", 50, 0, "wander", db)

    assert out["total"] == 1
    assert out["items"][0]["file"]["filename"] == "Wanderers S01E01.mkv"


def test_search_ranks_prefix_matches_above_word_and_substring_matches(db):
    """
    Rank 0 filename starts with the term, rank 1 a word starts with it, rank 2
    matches anywhere. Without the ranking, an incidental mid-word match sorts
    purely by recency and can bury the file the user was actually looking for.
    """
    from app.api.routes.history import list_history

    _file(db, file_id=1, filename="Salamanders Rising.mkv")     # rank 2
    _item(db, item_id=1, file_id=1, status="success", completed_at=_at(30))
    _file(db, file_id=2, filename="The Long Anders.mkv")        # rank 1
    _item(db, item_id=2, file_id=2, status="success", completed_at=_at(20))
    _file(db, file_id=3, filename="Anders Quest.mkv")           # rank 0
    _item(db, item_id=3, file_id=3, status="success", completed_at=_at(10))

    out = list_history("all", 50, 0, "anders", db)

    assert [i["id"] for i in out["items"]] == [3, 2, 1], (
        "relevance ranking lost — results fell back to recency order"
    )


@pytest.mark.parametrize("status", ["success", "skipped", "dry_run"])
def test_listing_a_single_status_excludes_the_others(db, status):
    """
    The tabs other than Failed filter on one status exactly. Only the "failed"
    branch does any folding.
    """
    from app.api.routes.history import list_history

    for i, s in enumerate(["success", "failed", "skipped", "dry_run"], 1):
        _file(db, file_id=i)
        _item(db, item_id=i, file_id=i, status=s, completed_at=_at(i))

    out = list_history(status, 50, 0, "", db)

    assert out["total"] == 1
    assert out["items"][0]["status"] == status


def test_a_blank_search_is_not_treated_as_a_filter(db):
    """Whitespace-only input must not join and filter on an empty pattern."""
    from app.api.routes.history import list_history

    _file(db, file_id=1)
    _item(db, item_id=1, file_id=1, status="success", completed_at=_at(0))

    assert list_history("all", 50, 0, "   ", db)["total"] == 1


@pytest.mark.parametrize("term, wanted, decoy", [
    ("The_Movie", "The_Movie.mkv", "TheXMovie.mkv"),
    ("100%",      "Show 100% Real.mkv", "Show 100Z Real.mkv"),
])
def test_search_treats_underscore_and_percent_as_literal_characters(
    db, term, wanted, decoy,
):
    """
    The search box takes a filename, not a LIKE pattern.

    Underscores are everywhere in release names, so an unescaped `_`
    silently matching any single character is reachable by simply typing
    what you see. `%` is rarer in a filename but matches EVERYTHING once
    unescaped, which turns a search into a no-op that still looks like a
    result.

    The same escaping is required in the relevance ranking below and in
    the sibling search endpoints (forge candidates, language review) —
    escaping only the filter would leave the ranking reading a term the
    filter had already treated as literal.
    """
    from app.api.routes.history import list_history

    _file(db, file_id=1, filename=wanted)
    _item(db, item_id=1, file_id=1, status="success", completed_at=_at(0))
    _file(db, file_id=2, filename=decoy)
    _item(db, item_id=2, file_id=2, status="success", completed_at=_at(1))

    out = list_history("all", 50, 0, term, db)

    assert [i["file"]["filename"] for i in out["items"]] == [wanted]
    assert out["total"] == 1


def test_relevance_ranking_also_treats_wildcards_literally(db):
    """
    The ranking builds two more patterns from the same term, and both
    need the same escaping the filter got.

    Escaping the filter alone is not enough, and asserting on the result
    SET cannot show it: once the filter is literal, every surviving row
    genuinely contains the term, so nothing appears that should not.
    What changes is the ORDER. A row can contain the literal term and
    also match an unescaped pattern earlier in the name, which lifts it
    into a better rank than it has earned.

    All four names below contain a literal "The_Movie" and so survive any
    filter. What differs is the rank each is given:

      id  name                          escaped  unescaped
      1   The_Movie Quest.mkv           0        0
      2   TheXMovie The_Movie.mkv       1        0   ← "_" matches "X"
      3   Zzz TheXMovie-The_Movie.mkv   2        1   ← "_" matches "X"
      4   Aaa-The_Movie.mkv             2        2

    Completion times are set so that a wrong rank always changes the
    order rather than being hidden by a tie.
    """
    from app.api.routes.history import list_history

    for fid, name, minutes in (
        (1, "The_Movie Quest.mkv",         10),
        (2, "TheXMovie The_Movie.mkv",     40),
        (3, "Zzz TheXMovie-The_Movie.mkv", 20),
        (4, "Aaa-The_Movie.mkv",           30),
    ):
        _file(db, file_id=fid, filename=name)
        _item(db, item_id=fid, file_id=fid, status="success",
              completed_at=_at(minutes))

    out = list_history("all", 50, 0, "The_Movie", db)

    assert [i["id"] for i in out["items"]] == [1, 2, 4, 3]


# ── retry_history_item ───────────────────────────────────────────────────────

def test_retrying_a_missing_history_item_is_a_404(db):
    from app.api.routes.history import retry_history_item

    with pytest.raises(HTTPException) as exc:
        retry_history_item(999, db)
    assert exc.value.status_code == 404


@pytest.mark.parametrize("status", ["pending", "processing", "manual_review"])
def test_unfinished_items_cannot_be_retried_from_history(db, monkeypatch, status):
    """
    _retry_with_reprobe is stubbed deliberately. Without the stub this test
    passes even when the route's own status guard is removed, because the real
    _retry_with_reprobe rejects these statuses too — so it would be pinning
    the collaborator's validation rather than the route's.
    """
    import app.api.routes.history as history

    called = []
    monkeypatch.setattr(history, "_retry_with_reprobe",
                        lambda _db, item: called.append(item.id) or {"ok": True})

    _file(db)
    _item(db, status=status)

    with pytest.raises(HTTPException) as exc:
        history.retry_history_item(1, db)
    assert exc.value.status_code == 400
    assert called == [], "the route handed an unfinished item to the retry path"


@pytest.mark.parametrize("status", ["failed", "cancelled", "dry_run",
                                    "success", "skipped"])
def test_every_terminal_status_can_be_retried(db, monkeypatch, status):
    """
    success and skipped included deliberately: re-running a file after a
    settings change is a legitimate action, not only a recovery from failure.
    """
    import app.api.routes.history as history

    seen = []
    monkeypatch.setattr(history, "_retry_with_reprobe",
                        lambda _db, item: seen.append(item.id) or {"ok": True})

    _file(db)
    _item(db, status=status)

    assert history.retry_history_item(1, db) == {"ok": True}
    assert seen == [1]


# ── _history_serialize ───────────────────────────────────────────────────────

def test_history_items_carry_their_size_figures(db):
    from app.api.routes.history import _history_serialize

    _file(db)
    item = _item(db, status="success", original_size=1000, output_size=600,
                 output_path="/media/f1.mkv")

    out = _history_serialize(item)

    assert out["original_size"] == 1000
    assert out["output_size"] == 600
    assert out["output_path"] == "/media/f1.mkv"
    assert out["bytes_saved"] == 400
    assert out["bytes_saved_pct"] == 40.0


def test_a_file_that_grew_reports_a_negative_saving(db):
    """
    Remuxing can legitimately produce a larger file. Clamping it to zero would
    hide the one case a user most wants to see.
    """
    from app.api.routes.history import _history_serialize

    _file(db)
    item = _item(db, status="success", original_size=1000, output_size=1200)

    out = _history_serialize(item)

    assert out["bytes_saved"] == -200
    assert out["bytes_saved_pct"] == -20.0


@pytest.mark.parametrize("original,output", [
    (None, None), (1000, None), (None, 600), (0, 600),
])
def test_missing_or_zero_sizes_report_no_saving_rather_than_raising(
        db, original, output):
    """
    A zero original_size would be a division by zero. None on either side
    means the job never recorded it — reporting 0 saved would be a claim the
    data does not support.
    """
    from app.api.routes.history import _history_serialize

    _file(db)
    item = _item(db, status="failed", original_size=original, output_size=output)

    out = _history_serialize(item)

    assert out["bytes_saved"] is None
    assert out["bytes_saved_pct"] is None


def test_history_serialisation_keeps_the_queue_fields(db):
    """It wraps the queue serialiser rather than replacing it."""
    from app.api.routes.history import _history_serialize

    _file(db)
    item = _item(db, status="success", reason="dropped 2 audio tracks")

    out = _history_serialize(item)

    assert out["status"] == "success"
    assert out["reason"] == "dropped 2 audio tracks"
    assert out["file"]["filename"] == "f1.mkv"


def test_planned_actions_are_omitted_from_list_rows(db):
    """
    The list endpoint serialises every row; loading actions for each would be
    a query per row.
    """
    from app.api.routes.history import _history_serialize

    _file(db)
    item = _item(db, status="success")

    assert "planned_actions" not in _history_serialize(item)
    assert _history_serialize(item, include_actions=True)["planned_actions"] == []
