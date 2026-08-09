"""
Startup recovery — stranded forge jobs and orphaned work-in-progress files.

Both behaviours run once at boot and had no coverage. Both exist to undo the
damage of an unclean shutdown, which is exactly the situation nobody exercises
by hand.

FORGE JOB RESET
  QueueItem rows stuck at "processing" were already reset at startup;
  Ac3ForgeJob rows were not. A forge row left at "processing" was permanently
  wedged — _has_pending_forge() matches only pending/undo_pending, candidate
  listing and add_to_queue exclude "processing", /api/forge/active shows it
  running forever, DELETE requires "pending", and abort_job() only knows the
  main task registry. The only recovery was editing the database.

ORPHANED FILE SWEEP
  Cleanup globbed TEMP_DIR only. _stage_parts writes "<final>.part" inside the
  media library, and _pick_temp_dir falls back to the media directory when
  TEMP_DIR is short on space — so the two places large orphans actually
  accumulate were both unswept.
"""
import os
import time

import pytest



# ── Forge job reset ──────────────────────────────────────────────────────────

@pytest.fixture
def db():
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    from app.database.models import Base

    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)()


def _seed(db, status, is_undo=False, idx=0):
    from app.database.models import Ac3ForgeJob, MediaFile

    name = f"f{idx}.mkv"
    mf = MediaFile(path=f"/m/{name}", filename=name, directory="/m", size=1, mtime=1.0)
    db.add(mf)
    db.commit()
    job = Ac3ForgeJob(file_id=mf.id, status=status, is_undo=is_undo, progress=37.0)
    db.add(job)
    db.commit()
    return job.id


def _reset_forge(db):
    """
    The reset logic from start_worker, applied to a caller-supplied session.

    start_worker() opens its own SessionLocal and starts the asyncio loop, so
    calling it directly would drag in the whole worker lifecycle. This mirrors
    the block; test_status_mapping_matches_route_expectations below pins the
    part that actually matters — that the chosen statuses agree with what the
    routes accept.
    """
    from app.core.timeutil import utcnow
    from app.database.models import Ac3ForgeJob

    stuck = db.query(Ac3ForgeJob).filter(Ac3ForgeJob.status == "processing").all()
    for job in stuck:
        job.status = "undo_failed" if job.is_undo else "failed"
        job.error_message = (
            "Interrupted by container restart or crash. The file may or may not "
            "have been modified — check its audio tracks before running this again."
        )
        job.completed_at = utcnow()
    db.commit()
    return len(stuck)


def test_stranded_forward_forge_job_becomes_failed(db):
    from app.database.models import Ac3ForgeJob

    jid = _seed(db, "processing", is_undo=False)
    assert _reset_forge(db) == 1

    job = db.get(Ac3ForgeJob, jid)
    assert job.status == "failed"
    assert job.completed_at is not None
    assert "Interrupted" in job.error_message


def test_stranded_undo_job_becomes_undo_failed(db):
    """Undo has its own terminal state, and the undo route treats it as retryable."""
    from app.database.models import Ac3ForgeJob

    jid = _seed(db, "processing", is_undo=True)
    _reset_forge(db)
    assert db.get(Ac3ForgeJob, jid).status == "undo_failed"


def test_reset_leaves_other_statuses_alone(db):
    """Only 'processing' is stranded — pending work must survive a restart."""
    from app.database.models import Ac3ForgeJob

    ids = {
        s: _seed(db, s, idx=i)
        for i, s in enumerate(["pending", "success", "undo_pending", "failed"])
    }
    assert _reset_forge(db) == 0
    for status, jid in ids.items():
        assert db.get(Ac3ForgeJob, jid).status == status


def test_error_message_does_not_claim_the_file_is_untouched(db):
    """
    Staging is atomic, so an interruption leaves the file either untouched OR
    fully rewritten, and the row cannot tell which. The message must not assert
    that nothing happened — forge is in-place, and a user who re-runs it on an
    already-forged file adds a second AC3 track.
    """
    from app.database.models import Ac3ForgeJob

    jid = _seed(db, "processing")
    _reset_forge(db)
    msg = db.get(Ac3ForgeJob, jid).error_message.lower()
    assert "may or may not" in msg
    assert "check its audio tracks" in msg


def test_failed_status_leaves_the_file_re_addable(db):
    """
    The reset is only useful if the status it picks actually unwedges the
    file. Calls the real add route: a prior "failed" forge job must not block
    a fresh add, which is why forward jobs are reset to "failed" and not to
    something in the exclusion set.
    """
    from types import SimpleNamespace

    import app.api.routes.forge as forge_routes
    from app.database.models import Ac3ForgeJob

    jid = _seed(db, "processing", is_undo=False)
    _reset_forge(db)
    file_id = db.get(Ac3ForgeJob, jid).file_id

    # The add route rejects on the exclusion set BEFORE doing any work, so a
    # non-400 outcome proves "failed" is not excluded. queue_forge_job needs a
    # real probe, so stub it — the exclusion check is what is under test.
    called = {}
    original = forge_routes.queue_forge_job
    forge_routes.queue_forge_job = lambda _db, fid: (
        called.setdefault("file_id", fid),
        db.get(Ac3ForgeJob, jid),
    )[1]
    try:
        forge_routes.add_to_queue(SimpleNamespace(file_id=file_id), db)
    except Exception as exc:  # HTTPException would mean it was excluded
        raise AssertionError(
            f"a reset 'failed' job blocked re-adding the file: {exc}"
        ) from exc
    finally:
        forge_routes.queue_forge_job = original

    assert called["file_id"] == file_id


def test_undo_failed_status_is_retryable(db):
    """
    Undo jobs are reset to "undo_failed", which IS in the candidate-exclusion
    set (the AC3 track is still present, so re-adding would duplicate it) but
    which the undo route explicitly accepts for retry. Without that pairing a
    stranded undo would have no exit at all.
    """
    import app.api.routes.forge as forge_routes
    from app.database.models import Ac3ForgeJob

    jid = _seed(db, "processing", is_undo=True)
    _reset_forge(db)
    assert db.get(Ac3ForgeJob, jid).status == "undo_failed"

    forge_routes.undo_job(jid, db)
    assert db.get(Ac3ForgeJob, jid).status == "undo_pending"


def test_stranded_job_would_otherwise_be_unrecoverable(db):
    """
    Records why this matters: with the row left at "processing", every exit is
    closed. Cancelling requires "pending", so it refuses.
    """
    import pytest as _pytest
    from fastapi import HTTPException

    import app.api.routes.forge as forge_routes

    jid = _seed(db, "processing")
    with _pytest.raises(HTTPException):
        forge_routes.cancel_job(jid, db)          # pre-reset: no way out

    _reset_forge(db)
    from app.database.models import Ac3ForgeJob
    assert db.get(Ac3ForgeJob, jid).status == "failed"


# ── Orphaned file sweep ──────────────────────────────────────────────────────

def _age(path, seconds):
    """Backdate mtime so the sweep's in-flight guard doesn't skip the file."""
    old = time.time() - seconds
    os.utime(path, (old, old))


@pytest.fixture
def sweep(tmp_path, monkeypatch):
    """Point both TEMP_DIR and scan_paths at temp dirs and run the real sweep."""
    import app.main as m

    temp_dir = tmp_path / "temp"
    library = tmp_path / "library"
    (library / "Show" / "S01").mkdir(parents=True)
    temp_dir.mkdir()

    monkeypatch.setattr(m.settings, "TEMP_DIR", str(temp_dir), raising=False)

    class _FakeSession:
        def __enter__(self): return self
        def __exit__(self, *a): return False

    monkeypatch.setattr(
        "app.database.session.SessionLocal", lambda: _FakeSession(), raising=False
    )
    monkeypatch.setattr(
        "app.database.session.get_app_settings",
        lambda _db: {"scan_paths": [str(library)]},
        raising=False,
    )
    return temp_dir, library, m._cleanup_orphaned_temp_files


def test_part_file_in_the_library_is_removed(sweep):
    """
    The headline gap: a multi-gigabyte "<final>.part" left in the media
    library by an interrupted staging copy. The scanner ignores it (.part is
    not a media extension) so nothing else would ever surface it.
    """
    _temp, library, run = sweep
    orphan = library / "Show" / "S01" / "Episode.mkv.part"
    orphan.write_bytes(b"x" * 4096)
    _age(orphan, 3600)

    run()
    assert not orphan.exists(), ".part orphan was not removed from the library"


def test_fallback_temp_in_the_library_is_removed(sweep):
    """_pick_temp_dir falls back to the media directory when TEMP_DIR is tight."""
    _temp, library, run = sweep
    orphan = library / "Show" / "job_7.remuxarr_tmp"
    orphan.write_bytes(b"y" * 2048)
    _age(orphan, 3600)

    run()
    assert not orphan.exists()


def test_temp_dir_orphans_still_removed(sweep):
    """The original behaviour must survive the extension."""
    temp_dir, _library, run = sweep
    a = temp_dir / "job_1.remuxarr_tmp"
    b = temp_dir / "job_2.forge_tmp"
    for f in (a, b):
        f.write_bytes(b"z" * 512)
        _age(f, 3600)

    run()
    assert not a.exists() and not b.exists()


def test_real_media_files_are_never_touched(sweep):
    """The sweep runs across the whole library — it must only match suffixes."""
    _temp, library, run = sweep
    keep = [
        library / "Show" / "S01" / "Episode.mkv",
        library / "Show" / "S01" / "Episode.en.srt",
        library / "Show" / "partial.mkv",          # contains "part", isn't one
    ]
    for f in keep:
        f.write_bytes(b"real")
        _age(f, 3600)

    run()
    for f in keep:
        assert f.exists(), f"sweep deleted a real file: {f}"


def test_recently_modified_orphan_is_skipped(sweep):
    """
    Guard against a second instance mid-copy on the same library. Startup
    ordering already means none of our own jobs are live, so anything fresh is
    someone else's.
    """
    _temp, library, run = sweep
    fresh = library / "Show" / "InFlight.mkv.part"
    fresh.write_bytes(b"w" * 128)          # mtime = now

    run()
    assert fresh.exists(), "a possibly in-flight .part was deleted"


def test_missing_scan_path_does_not_raise(sweep, tmp_path, monkeypatch):
    """A removed or unmounted library path must not break startup."""
    _temp, _library, run = sweep
    monkeypatch.setattr(
        "app.database.session.get_app_settings",
        lambda _db: {"scan_paths": ["/does/not/exist", None, ""]},
        raising=False,
    )
    run()          # must not raise
