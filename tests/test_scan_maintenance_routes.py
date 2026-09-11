"""
The scan module's maintenance endpoints: cleanup and orphaned rows.

test_scan_routes.py covers the scan lifecycle — the running flag, the
cancel plumbing, the thread body. This covers the four routes that are not
part of a scan at all: removing rows for files that have gone, listing
rows that fall outside the configured library, removing those, and the
executor helpers all three of them run their database work through.

POST /file is deliberately not re-covered. Its missing-file guard, its
broadcast and its not-queued response are already pinned by
test_scan_and_cancellation.py, confirmed by mutation rather than assumed:
three of the four mutants aimed at it were already dead before this file
existed. Only its session handling was not, and that is here with its two
siblings.

THE RULE WITH THE INCIDENT BEHIND IT
------------------------------------
_scan_file_sync, _cleanup_sync and _remove_orphaned_sync each open their
own SessionLocal on the executor thread instead of taking the
request-scoped Depends(get_db) session across a thread boundary. That
session belongs to the request's lifecycle, and passing it into
run_in_executor only ever worked because of check_same_thread=False.

The module's own docstrings record how this went: _cleanup_sync was fixed
and its docstring declared it "the one place in the codebase doing this",
while _remove_orphaned_sync was doing the identical thing four routes down
and was missed. Three copies of one rule, so three separate mutants and
three separate tests — a fix applied to one of them says nothing about the
other two, which is exactly what happened.

Nine mutants, all confirmed surviving the full suite before this file
existed:

  /cleanup    the empty scan_paths guard removed              killed
              the completion broadcast dropped                killed
              _cleanup_sync leaving its session open          killed
  /file       _scan_file_sync leaving its session open        killed
  /orphaned   on_disk inverted                                killed
              the configured paths not passed                 killed
  /remove     the empty file_ids guard removed                killed
              _remove_orphaned_sync leaving its session open  killed
              the requested ids not passed through            killed

Run from the project root:
    pytest tests/test_scan_maintenance_routes.py -v
"""
import asyncio

import pytest
from fastapi import HTTPException

from app.api.routes import scan as scan_routes


class _FakeSession:
    """Records whether it was closed, which is the whole point here."""

    def __init__(self, ledger):
        self._ledger = ledger
        ledger["opened"] += 1

    def close(self):
        self._ledger["closed"] += 1


@pytest.fixture
def sessions(monkeypatch):
    """Count sessions opened and closed by the executor helpers."""
    ledger = {"opened": 0, "closed": 0}
    monkeypatch.setattr(scan_routes, "SessionLocal",
                        lambda: _FakeSession(ledger))
    return ledger


@pytest.fixture
def broadcasts(monkeypatch):
    sent = []

    async def _capture(data):
        sent.append(data)

    monkeypatch.setattr(scan_routes.ws_manager, "broadcast_json", _capture)
    return sent


def _settings(monkeypatch, **cfg):
    monkeypatch.setattr(scan_routes, "get_app_settings", lambda _db: cfg)


class _Row:
    def __init__(self, id, path, size=100):
        self.id = id
        self.path = path
        self.filename = path.rsplit("/", 1)[-1]
        self.size = size


# ── POST /cleanup ─────────────────────────────────────────────────────────────

def test_cleanup_is_refused_when_no_scan_paths_are_configured(monkeypatch,
                                                              sessions):
    """
    Closes: the empty scan_paths guard removed.

    cleanup_deleted_files is scoped by the prefixes it is handed, so an
    empty list is a question with no meaning rather than a request to
    clean nothing. Refusing says so; running anyway reports "removed: 0"
    and reads as a library that is already tidy.
    """
    called = []
    monkeypatch.setattr(scan_routes, "cleanup_deleted_files",
                        lambda db, paths: called.append(paths) or 0)
    _settings(monkeypatch)

    with pytest.raises(HTTPException) as raised:
        asyncio.run(scan_routes.run_cleanup(db=object()))

    assert raised.value.status_code == 400
    assert called == [], "the cleanup ran with no paths to scope it"


def test_cleanup_reports_and_announces_what_it_removed(monkeypatch, sessions,
                                                       broadcasts):
    """
    Closes: the completion broadcast dropped.

    The dashboard updates its row count from this event. Without it the
    request succeeds, the rows are gone, and the page keeps showing the
    old total until someone reloads — which reads as a cleanup that did
    nothing.
    """
    monkeypatch.setattr(scan_routes, "cleanup_deleted_files",
                        lambda db, paths: 7)
    _settings(monkeypatch, scan_paths=["/media/tv"])

    result = asyncio.run(scan_routes.run_cleanup(db=object()))

    assert result == {"removed": 7}
    assert broadcasts == [{"event": "cleanup_completed", "removed": 7}]


# ── The three executor helpers ────────────────────────────────────────────────

def test_the_cleanup_helper_closes_the_session_it_opened(monkeypatch, sessions):
    """
    Closes: _cleanup_sync leaving its session open.

    One of three copies of the same rule, mutated and tested separately
    because the recorded incident is precisely that the fix was applied
    here and missed in _remove_orphaned_sync. A leaked session holds its
    SQLite connection for the life of the process, and this route is
    reachable from a button anyone can hold down.
    """
    monkeypatch.setattr(scan_routes, "cleanup_deleted_files",
                        lambda db, paths: 0)

    scan_routes._cleanup_sync(["/media"])

    assert sessions == {"opened": 1, "closed": 1}


def test_the_file_scan_helper_closes_the_session_it_opened(monkeypatch,
                                                           sessions):
    """Closes: _scan_file_sync leaving its session open. Copy two of three."""
    monkeypatch.setattr(scan_routes, "queue_single_file",
                        lambda db, path: None)

    scan_routes._scan_file_sync("/media/Show/ep.mkv")

    assert sessions == {"opened": 1, "closed": 1}


def test_the_orphan_removal_helper_closes_the_session_it_opened(monkeypatch,
                                                                sessions):
    """
    Closes: _remove_orphaned_sync leaving its session open. Copy three,
    and the one that was missed when the other two were fixed.
    """
    monkeypatch.setattr(scan_routes, "remove_orphaned_media_files",
                        lambda db, ids: len(ids))

    scan_routes._remove_orphaned_sync([1, 2])

    assert sessions == {"opened": 1, "closed": 1}


def test_a_helper_closes_its_session_even_when_the_work_raises(monkeypatch,
                                                               sessions):
    """
    The close is in a finally, and this is what that buys: a probe that
    times out or a delete that hits a constraint must not leak the
    connection. Without it the leak only shows under the failures that are
    already the worst time to lose a connection.
    """
    def _boom(db, path):
        raise RuntimeError("ffprobe exploded")

    monkeypatch.setattr(scan_routes, "queue_single_file", _boom)

    with pytest.raises(RuntimeError):
        scan_routes._scan_file_sync("/media/Show/ep.mkv")

    assert sessions == {"opened": 1, "closed": 1}


# ── GET /orphaned ─────────────────────────────────────────────────────────────

def test_orphaned_rows_are_found_against_the_configured_paths(monkeypatch):
    """
    Closes: the configured paths not passed to the finder.

    find_orphaned_media_files decides membership from the prefixes it is
    given, so handing it an empty list makes every row in the library
    orphaned — and the remove endpoint below will act on exactly what this
    one reports.
    """
    seen = {}

    def _find(db, scan_paths):
        seen["paths"] = scan_paths
        return []

    monkeypatch.setattr(scan_routes, "find_orphaned_media_files", _find)
    _settings(monkeypatch, scan_paths=["/media/tv", "/media/movies"])

    scan_routes.list_orphaned(db=object())

    assert seen["paths"] == ["/media/tv", "/media/movies"]


def test_an_orphaned_row_reports_whether_its_file_is_still_there(monkeypatch,
                                                                 tmp_path):
    """
    Closes: on_disk inverted.

    Informational only — it does not decide whether a row is orphaned —
    but it is what the UI shows next to a Remove button, and inverted it
    invites someone to delete the rows for files that are still there
    while leaving the ones that are gone.

    Real files rather than a patched os.path.exists, since the thing being
    checked is a filesystem answer.
    """
    present = tmp_path / "here.mkv"
    present.write_text("x")
    missing = tmp_path / "gone.mkv"

    monkeypatch.setattr(
        scan_routes, "find_orphaned_media_files",
        lambda db, paths: [_Row(1, str(present)), _Row(2, str(missing))],
    )
    _settings(monkeypatch, scan_paths=["/media"])

    result = scan_routes.list_orphaned(db=object())

    assert result["total"] == 2
    assert [(r["id"], r["on_disk"]) for r in result["items"]] == [
        (1, True), (2, False),
    ]


# ── POST /orphaned/remove ─────────────────────────────────────────────────────

def test_removing_with_no_ids_is_refused(monkeypatch, sessions):
    """
    Closes: the empty file_ids guard removed. An empty request removes
    nothing and returns removed: 0, which is indistinguishable from ids
    that matched no rows — so a UI bug sending an empty selection reads
    as rows that were already gone.
    """
    _settings(monkeypatch, scan_paths=["/media/tv"])
    body = scan_routes.RemoveOrphanedRequest(file_ids=[])

    with pytest.raises(HTTPException) as raised:
        asyncio.run(scan_routes.remove_orphaned(body, db=object()))

    assert raised.value.status_code == 400


def test_the_requested_ids_are_the_ones_removed(monkeypatch, sessions):
    """
    Closes: the ids not passed through to the executor.

    This route deletes MediaFile rows and everything referencing them, so
    what reaches the helper is the whole safety question — and it is
    handed across a thread boundary, where an argument is easy to lose.
    """
    seen = {}

    def _remove(db, file_ids):
        seen["ids"] = file_ids
        return len(file_ids)

    monkeypatch.setattr(scan_routes, "remove_orphaned_media_files", _remove)
    _settings(monkeypatch, scan_paths=["/media/tv"])
    body = scan_routes.RemoveOrphanedRequest(file_ids=[4, 9])

    result = asyncio.run(scan_routes.remove_orphaned(body, db=object()))

    assert seen["ids"] == [4, 9]
    assert result == {"removed": 2}


# ── The unconfigured-library guard ────────────────────────────────────────────

def test_listing_orphans_is_refused_with_no_scan_paths(monkeypatch):
    """
    Membership is decided by the prefixes handed to the finder, so with
    none every row in the library is outside it. Without this guard the
    endpoint reports a user's whole collection under a heading saying
    these rows are orphaned, beside a button that removes them.

    Asserted on the finder never being called, not only on the status:
    a 400 raised after the query still means the answer was computed, and
    the thing being prevented is the answer existing at all.
    """
    called = []
    monkeypatch.setattr(scan_routes, "find_orphaned_media_files",
                        lambda db, paths: called.append(paths) or [])
    _settings(monkeypatch)

    with pytest.raises(HTTPException) as raised:
        scan_routes.list_orphaned(db=object())

    assert raised.value.status_code == 400
    assert called == []


def test_removing_orphans_is_refused_with_no_scan_paths(monkeypatch, sessions):
    """
    The listing refuses, so ids can only reach this from a page loaded
    before the paths were removed — or from something that is not the UI.

    This endpoint deletes MediaFile rows and every row referencing them,
    and unlike Clear Database it detaches RevertPoint rows rather than
    deleting them, leaving their sidecars on the recycle volume. Reaching
    a whole-library wipe by this route gets a worse outcome than the
    action built for it.
    """
    called = []
    monkeypatch.setattr(scan_routes, "remove_orphaned_media_files",
                        lambda db, ids: called.append(ids) or 0)
    _settings(monkeypatch)
    body = scan_routes.RemoveOrphanedRequest(file_ids=[4, 9])

    with pytest.raises(HTTPException) as raised:
        asyncio.run(scan_routes.remove_orphaned(body, db=object()))

    assert raised.value.status_code == 400
    assert called == [], "rows were deleted before the refusal"


def test_the_refusal_points_at_the_action_that_does_this_properly(monkeypatch):
    """
    Someone hitting this generally does want everything gone. The message
    has to say where that lives, or the obvious next move is to add a
    throwaway scan path and come straight back to this button.

    Pinned because it is the whole user-facing value of the guard: a bare
    400 stops the deletion and leaves the person with no idea what to do
    instead.
    """
    _settings(monkeypatch)

    with pytest.raises(HTTPException) as raised:
        scan_routes.list_orphaned(db=object())

    assert "Clear Database" in raised.value.detail
