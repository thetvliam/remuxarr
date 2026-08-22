"""
Revert API routes.

Two things carry real risk here and the rest is plumbing.

Concurrency. A revert rewrites a media file through a staged swap, and so
does the worker. If both run on one file the loser's output silently
replaces the winner's, and the result is whichever finished second — with
a revert point and a queue item that both now describe a file that never
existed. Nothing downstream can detect that, so it has to be refused up
front: one revert at a time, and never on a file the queue is holding.

Consent. Attaching a detached point to a file that cannot be confirmed is
the one place a user can overrule a safety check. The route must not be
able to do it by accident, and a refusal has to carry the reasons — the
user is choosing from a list, and a bare "no" just sends them to try the
next point at random.

The restore endpoint returns as soon as the work is RUNNING, so the tests
assert what it refuses and what it starts, not what it produces. What it
produces is tested against real files in test_revert_execution.py.

Verified by mutation, 11 applied, 11 killed:

  • Single-flight check removed                      → killed
  • Flag not rolled back when the thread fails to
    start                                             → killed
  • Flag not cleared when the revert finishes         → killed
  • Active-queue-item check removed                   → killed
  • Active check narrowed to "processing" only        → killed
  • Active check widened to every queue state         → killed
  • Detached point accepted for restore               → killed
  • confirm_mismatch defaulting to True               → killed
  • Attach refusal returning no reasons               → killed
  • Discard leaving the sidecar on the volume         → killed
  • Empty-bin ignoring detached_only                  → killed

No equivalent mutants.
"""
import json

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def client(tmp_path, monkeypatch):
    """The app wired to an isolated database and a mounted recycle volume."""
    from sqlalchemy import create_engine
    from sqlalchemy.pool import StaticPool
    from sqlalchemy.orm import sessionmaker

    from app.config import settings as app_settings
    from app.database.models import Base
    import app.database.session as session_mod
    import app.api.routes.revert as revert_routes

    recycle = tmp_path / "recycle"
    recycle.mkdir()
    monkeypatch.setattr(app_settings, "RECYCLE_DIR", str(recycle), raising=False)

    # StaticPool, and it is not optional: every new connection to
    # "sqlite://" gets its OWN empty database, and the TestClient opens a
    # session per request. Without it create_all runs against one
    # throwaway connection and every request then reports "no such table".
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine)
    monkeypatch.setattr(session_mod, "SessionLocal", factory)

    # Reset the module-level flag: it outlives any single test, and a test
    # that left it set would 409 every test after it.
    monkeypatch.setattr(revert_routes, "_revert_running", False, raising=False)

    from app.main import app

    app.dependency_overrides[session_mod.get_db] = lambda: factory()
    try:
        yield TestClient(app), factory(), recycle
    finally:
        app.dependency_overrides.clear()


_seq = iter(range(1, 1000))


def _seed(db, recycle, *, detached=False, with_sidecar=True):
    """
    A media file and its revert point. Paths are unique per call because
    media_files.path is UNIQUE — tests that seed twice are seeding two
    different files, not the same one twice.
    """
    from app.database.models import MediaFile, RevertPoint

    n = next(_seq)
    path = f"/m/Show{n}.mkv"
    media = MediaFile(path=path, filename=f"Show{n}.mkv", directory="/m",
                      size=100, mtime=1.0, container="mkv")
    db.add(media)
    db.commit()

    sidecar = recycle / f"{media.id}_1.remuxarr_revert"
    if with_sidecar:
        sidecar.write_bytes(b"dropped tracks")

    point = RevertPoint(
        file_id=None if detached else media.id,
        sidecar_path=str(sidecar), sidecar_size=14,
        manifest=json.dumps({"version": 2, "streams": [], "duration": 60.0}),
        original_path=path, original_container="mkv",
        processed_size=100, processed_mtime=1.0,
    )
    db.add(point)
    db.commit()
    return media, point, sidecar


def _queue(db, media, status):
    from app.database.models import QueueItem

    db.add(QueueItem(file_id=media.id, status=status))
    db.commit()


# ── Listing ──────────────────────────────────────────────────────────────────

def test_attached_and_detached_are_returned_separately(client):
    """
    Mixed into one list with a flag, a UI ends up offering Revert on a
    point that has no file to revert. They support different actions, so
    they arrive as different lists.
    """
    api, db, recycle = client
    _seed(db, recycle)
    _seed(db, recycle, detached=True)

    body = api.get("/api/revert/").json()

    assert len(body["attached"]) == 1
    assert len(body["detached"]) == 1
    assert body["attached"][0]["current_path"].startswith("/m/Show")


def test_listing_reports_whether_the_recycle_volume_is_mounted(client,
                                                               monkeypatch,
                                                               tmp_path):
    """
    An empty list means two very different things — nothing kept yet, or
    the volume was never mounted. The UI cannot tell them apart without
    this and would show "no revert points" to someone whose bin is simply
    not plugged in.
    """
    api, _db, _recycle = client
    from app.config import settings as app_settings

    monkeypatch.setattr(app_settings, "RECYCLE_DIR", str(tmp_path / "gone"),
                        raising=False)

    body = api.get("/api/revert/").json()

    assert body["recycle_bin_ready"] is False
    assert "mounted" in body["recycle_bin_reason"]


# ── Restorability in the listing ─────────────────────────────────────────────

def test_a_usable_point_is_listed_as_restorable(client, tmp_path):
    api, db, recycle = client
    from app.database.models import MediaFile, RevertPoint

    media_file = tmp_path / "Live.mkv"
    media_file.write_bytes(b"processed output")
    stat = media_file.stat()

    media = MediaFile(path=str(media_file), filename="Live.mkv",
                      directory=str(tmp_path), size=stat.st_size,
                      mtime=stat.st_mtime, container="mkv")
    db.add(media)
    db.commit()
    sidecar = recycle / "live.remuxarr_revert"
    sidecar.write_bytes(b"stored tracks")
    db.add(RevertPoint(file_id=media.id, sidecar_path=str(sidecar),
                       sidecar_size=1, manifest="{}", original_path=str(media_file),
                       processed_size=stat.st_size, processed_mtime=stat.st_mtime))
    db.commit()

    entry = api.get("/api/revert/").json()["attached"][0]

    assert entry["restorable"] is True
    assert entry["blocked_reason"] is None


def test_a_changed_file_is_listed_as_not_restorable(client, tmp_path):
    """
    Sonarr upgrading the episode is the everyday case. The entry stays —
    the stored tracks are still there and still take up space, so it has
    to be visible to be discarded — but offering Revert on it produces a
    refusal the user cannot explain. The reason travels with the row.
    """
    api, db, recycle = client
    from app.database.models import MediaFile, RevertPoint

    media_file = tmp_path / "Upgraded.mkv"
    media_file.write_bytes(b"a different release entirely")
    stat = media_file.stat()

    media = MediaFile(path=str(media_file), filename="Upgraded.mkv",
                      directory=str(tmp_path), size=stat.st_size,
                      mtime=stat.st_mtime, container="mkv")
    db.add(media)
    db.commit()
    sidecar = recycle / "upgraded.remuxarr_revert"
    sidecar.write_bytes(b"stored tracks")
    db.add(RevertPoint(file_id=media.id, sidecar_path=str(sidecar),
                       sidecar_size=1, manifest="{}", original_path=str(media_file),
                       processed_size=stat.st_size + 4096,
                       processed_mtime=stat.st_mtime))
    db.commit()

    entry = api.get("/api/revert/").json()["attached"][0]

    assert entry["restorable"] is False
    assert "changed size" in entry["blocked_reason"]


def test_the_listing_and_the_revert_agree(client, tmp_path):
    """
    Both call the same function, and this is what that buys. Written
    twice they drift, and the drift is invisible in the direction that
    matters: the list keeps offering Revert on entries the revert then
    refuses, so the button looks broken rather than the file looking
    changed.
    """
    api, db, recycle = client
    from app.database.models import MediaFile, RevertPoint

    media_file = tmp_path / "Upgraded.mkv"
    media_file.write_bytes(b"a different release entirely")
    stat = media_file.stat()

    media = MediaFile(path=str(media_file), filename="Upgraded.mkv",
                      directory=str(tmp_path), size=stat.st_size,
                      mtime=stat.st_mtime, container="mkv")
    db.add(media)
    db.commit()
    sidecar = recycle / "agree.remuxarr_revert"
    sidecar.write_bytes(b"stored tracks")
    point = RevertPoint(file_id=media.id, sidecar_path=str(sidecar),
                        sidecar_size=1, manifest="{}",
                        original_path=str(media_file),
                        processed_size=stat.st_size + 4096,
                        processed_mtime=stat.st_mtime)
    db.add(point)
    db.commit()

    entry = api.get("/api/revert/").json()["attached"][0]

    from app.core.revert_restore import restore_revert_point
    import asyncio

    outcome = asyncio.run(restore_revert_point(point.id))

    assert entry["restorable"] is False
    assert outcome.success is False
    assert entry["blocked_reason"] == outcome.error


# ── Restore: what it refuses ─────────────────────────────────────────────────

def test_restoring_a_detached_point_is_refused(client):
    api, db, recycle = client
    _media, point, _sidecar = _seed(db, recycle, detached=True)

    r = api.post(f"/api/revert/{point.id}/restore/")

    assert r.status_code == 409
    assert "not attached" in r.json()["detail"]


def test_restoring_an_unknown_point_is_a_404(client):
    api, _db, _recycle = client

    assert api.post("/api/revert/999/restore/").status_code == 404


@pytest.mark.parametrize("status", ["pending", "processing"])
def test_restoring_a_file_the_queue_is_holding_is_refused(client, status):
    """
    The dangerous one. Both the worker and revert write through a staged
    swap, so two writers on one path means the second silently replaces
    the first — and nothing downstream can detect it afterwards.

    "pending" counts as well as "processing": the worker may pick the job
    up at any moment, and a revert that starts first simply loses the race
    a few seconds later.
    """
    api, db, recycle = client
    media, point, _sidecar = _seed(db, recycle)
    _queue(db, media, status)

    r = api.post(f"/api/revert/{point.id}/restore/")

    assert r.status_code == 409
    assert status in r.json()["detail"]


def test_a_finished_queue_item_does_not_block_a_revert(client):
    """
    Every processed file has a completed queue item. Treating those as
    active would make revert impossible for exactly the files that have
    something to revert.
    """
    api, db, recycle = client
    media, point, _sidecar = _seed(db, recycle)
    _queue(db, media, "completed")

    started = api.post(f"/api/revert/{point.id}/restore/")

    assert started.status_code == 200


def test_a_second_revert_is_refused_while_one_is_running(client, monkeypatch):
    api, db, recycle = client
    import app.api.routes.revert as revert_routes

    _media, point, _sidecar = _seed(db, recycle)
    monkeypatch.setattr(revert_routes, "_revert_running", True)

    r = api.post(f"/api/revert/{point.id}/restore/")

    assert r.status_code == 409
    assert "already running" in r.json()["detail"]


def test_the_running_flag_is_released_when_the_thread_cannot_start(client,
                                                                   monkeypatch):
    """
    The lifecycle contract scan.py documents: whoever sets the flag either
    hands it to a thread that clears it, or clears it themselves on every
    other exit. Without the rollback one failure wedges every future
    revert behind a 409 until the container restarts.
    """
    api, db, recycle = client
    import app.api.routes.revert as revert_routes

    _media, point, _sidecar = _seed(db, recycle)

    def boom(*_a, **_k):
        raise RuntimeError("cannot spawn")

    monkeypatch.setattr(revert_routes.threading, "Thread", boom)

    with pytest.raises(RuntimeError):
        api.post(f"/api/revert/{point.id}/restore/")

    assert revert_routes._revert_running is False


def test_a_completed_revert_releases_the_flag(client, monkeypatch):
    api, db, recycle = client
    import app.api.routes.revert as revert_routes

    _media, point, _sidecar = _seed(db, recycle)
    monkeypatch.setattr(revert_routes, "restore_revert_point", None)

    # Drive the worker body directly: the thread is what clears the flag,
    # and a test that only calls the route races it.
    revert_routes._revert_running = True
    revert_routes._run_revert(point.id, loop=_DummyLoop())

    assert revert_routes._revert_running is False


class _DummyLoop:
    """Absorbs the completion broadcast without a running event loop."""

    def call_soon_threadsafe(self, *_a, **_k):
        return None


@pytest.fixture(autouse=True)
def _no_broadcast(monkeypatch):
    """
    Swallow the completion broadcast, but close the coroutine while doing it.

    The real run_coroutine_threadsafe() consumes the coroutine it is handed —
    it schedules it on the loop and it eventually runs. A stub that merely
    drops the argument does not, so the coroutine is collected un-awaited and
    Python emits "coroutine 'broadcast_json' was never awaited" from whatever
    unrelated test happens to trigger the GC. pytest.ini sets
    filterwarnings=default precisely so a genuinely new warning is visible;
    a stub manufacturing a permanent one spends that signal for nothing.
    The two per-test stubs further down this file already close theirs — this
    autouse one was the outlier.
    """
    import app.api.routes.revert as revert_routes

    def _swallow(coro, *_a, **_k):
        coro.close()
        return None

    monkeypatch.setattr(revert_routes.asyncio, "run_coroutine_threadsafe",
                        _swallow)


# ── Attach ───────────────────────────────────────────────────────────────────

def test_attaching_forwards_the_refusal_reasons(client, monkeypatch):
    """
    A refusal the user cannot act on is a refusal that sends them to try
    the next point at random.
    """
    api, db, recycle = client
    import app.api.routes.revert as revert_routes
    from app.core.revert_match import AttachOutcome, INCOMPATIBLE

    media, point, _sidecar = _seed(db, recycle, detached=True)
    monkeypatch.setattr(
        revert_routes, "attach",
        lambda *_a, **_k: AttachOutcome(False, tier=INCOMPATIBLE,
                                        error="does not belong",
                                        reasons=["runtime differs by 47s"]),
    )

    r = api.post(f"/api/revert/{point.id}/attach/", json={"file_id": media.id})

    assert r.status_code == 409
    detail = r.json()["detail"]
    assert detail["tier"] == INCOMPATIBLE
    assert detail["reasons"] == ["runtime differs by 47s"]


def test_confirm_mismatch_defaults_to_false(client, monkeypatch):
    """
    The one place a user can overrule the safety check, so the route must
    not be able to do it by omission.
    """
    api, db, recycle = client
    import app.api.routes.revert as revert_routes
    from app.core.revert_match import AttachOutcome

    media, point, _sidecar = _seed(db, recycle, detached=True)
    seen = {}

    def fake(_pid, _fid, *, confirm_mismatch):
        seen["confirm"] = confirm_mismatch
        return AttachOutcome(True, tier="exact")

    monkeypatch.setattr(revert_routes, "attach", fake)

    api.post(f"/api/revert/{point.id}/attach/", json={"file_id": media.id})

    assert seen["confirm"] is False


def test_confirm_mismatch_is_forwarded_when_given(client, monkeypatch):
    api, db, recycle = client
    import app.api.routes.revert as revert_routes
    from app.core.revert_match import AttachOutcome

    media, point, _sidecar = _seed(db, recycle, detached=True)
    seen = {}

    def fake(_pid, _fid, *, confirm_mismatch):
        seen["confirm"] = confirm_mismatch
        return AttachOutcome(True, tier="compatible")

    monkeypatch.setattr(revert_routes, "attach", fake)

    api.post(f"/api/revert/{point.id}/attach/",
             json={"file_id": media.id, "confirm_mismatch": True})

    assert seen["confirm"] is True


# ── The completion broadcast ─────────────────────────────────────────────────

def test_the_completion_broadcast_uses_the_key_the_frontend_reads(client,
                                                                  monkeypatch):
    """
    The frontend switches on msg.event. A payload keyed "type" is
    delivered, matches nothing, and is dropped in silence — which is
    exactly what shipped: the panel never learned a revert had finished,
    so entries stayed on screen looking as though the button did nothing.

    Asserted here rather than left to the UI, because nothing on either
    side fails when the key is wrong. The message simply goes nowhere.
    """
    import app.api.routes.revert as revert_routes
    from app.core.revert_restore import RestoreOutcome

    api, db, recycle = client
    _media, point, _sidecar = _seed(db, recycle)

    sent = []
    monkeypatch.setattr(revert_routes.asyncio, "run_coroutine_threadsafe",
                        lambda coro, _loop: (coro.close(), sent.append("sent")))
    monkeypatch.setattr(revert_routes.ws_manager, "broadcast_json",
                        lambda payload: _capture(sent, payload))
    # _run_revert wraps this in asyncio.run(), so the stub has to be a
    # coroutine function — returning the outcome directly raises, and the
    # test then passes or fails on the exception handler instead.
    async def fake(_pid, **_k):
        return RestoreOutcome(success=True, restored_path="/m/Show.mkv")

    monkeypatch.setattr(revert_routes, "restore_revert_point", fake)

    revert_routes._run_revert(point.id, loop=None)

    payload = next(p for p in sent if isinstance(p, dict))
    assert payload["event"] == "revert_complete", (
        "the frontend switches on 'event'; a payload keyed otherwise is "
        "silently dropped"
    )
    assert payload["success"] is True
    assert payload["restored_path"] == "/m/Show.mkv"


def _capture(sink, payload):
    """Stand-in for broadcast_json that records rather than sends."""
    sink.append(payload)

    async def _noop():
        return None

    return _noop()


def test_a_failed_revert_broadcasts_the_reason(client, monkeypatch):
    import app.api.routes.revert as revert_routes
    from app.core.revert_restore import RestoreOutcome

    api, db, recycle = client
    _media, point, _sidecar = _seed(db, recycle)

    sent = []
    monkeypatch.setattr(revert_routes.asyncio, "run_coroutine_threadsafe",
                        lambda coro, _loop: (coro.close(), None))
    monkeypatch.setattr(revert_routes.ws_manager, "broadcast_json",
                        lambda payload: _capture(sent, payload))
    async def fake(_pid, **_k):
        return RestoreOutcome(success=False, error="Show.mkv has changed size")

    monkeypatch.setattr(revert_routes, "restore_revert_point", fake)

    revert_routes._run_revert(point.id, loop=None)

    payload = next(p for p in sent if isinstance(p, dict))
    assert payload["event"] == "revert_complete"
    assert payload["success"] is False
    assert "changed size" in payload["error"]


# ── Candidates ───────────────────────────────────────────────────────────────

def test_candidates_are_offered_for_a_detached_point(client):
    api, db, recycle = client
    from app.database.models import MediaFile, RevertPoint

    _media, point, _sidecar = _seed(db, recycle, detached=True)
    # A renamed copy: same size and mtime, new path.
    stored = db.get(RevertPoint, point.id)
    renamed = MediaFile(path="/m/Renamed.mkv", filename="Renamed.mkv",
                        directory="/m", size=stored.processed_size,
                        mtime=stored.processed_mtime, container="mkv")
    db.add(renamed)
    db.commit()

    body = api.get(f"/api/revert/{point.id}/candidates/").json()

    assert renamed.id in [c["id"] for c in body["exact"]]


def test_candidates_are_refused_for_an_attached_point(client):
    """
    An attached point already knows its file. Offering candidates invites
    a UI that quietly reassigns a live revert point to a different file.
    """
    api, db, recycle = client

    _media, point, _sidecar = _seed(db, recycle)

    assert api.get(f"/api/revert/{point.id}/candidates/").status_code == 409


def test_candidates_for_an_unknown_point_are_a_404(client):
    api, _db, _recycle = client

    assert api.get("/api/revert/999/candidates/").status_code == 404


# ── Discarding ───────────────────────────────────────────────────────────────

def test_discarding_removes_the_sidecar_too(client):
    """
    Row without file is an invisible leak: nothing scans the volume and
    no row records the path.
    """
    api, db, recycle = client
    from app.database.models import RevertPoint

    _media, point, sidecar = _seed(db, recycle)

    assert api.delete(f"/api/revert/{point.id}/").status_code == 200

    db.expire_all()
    assert db.query(RevertPoint).count() == 0
    assert not sidecar.exists()


def test_emptying_the_bin_discards_everything(client):
    api, db, recycle = client
    from app.database.models import RevertPoint

    _seed(db, recycle)
    _seed(db, recycle, detached=True)

    body = api.delete("/api/revert/").json()

    assert body["discarded"] == 2
    db.expire_all()
    assert db.query(RevertPoint).count() == 0


def test_emptying_only_the_detached_leaves_live_points_alone(client):
    """
    Two very different acts behind one button otherwise. Clearing
    leftovers after a library rename is housekeeping; clearing everything
    throws away every undo inside the retention window.
    """
    api, db, recycle = client
    from app.database.models import RevertPoint

    _media, live, live_sidecar = _seed(db, recycle)
    _seed(db, recycle, detached=True)

    body = api.delete("/api/revert/?detached_only=true").json()

    assert body["discarded"] == 1
    db.expire_all()
    remaining = db.query(RevertPoint).all()
    assert [p.id for p in remaining] == [live.id]
    assert live_sidecar.exists()
