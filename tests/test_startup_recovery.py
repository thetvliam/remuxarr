"""
Startup recovery — stranded jobs and orphaned work-in-progress files.

Both behaviours run once at boot and had no coverage. Both exist to undo the
damage of an unclean shutdown, which is exactly the situation nobody exercises
by hand.

JOB RESET
  QueueItem rows stuck at "processing" were already reset at startup;
  Ac3ForgeJob rows were not. A forge row left at "processing" was permanently
  wedged — _has_pending_forge() matches only pending/undo_pending, candidate
  listing and add_to_queue exclude "processing", /api/forge/active shows it
  running forever, DELETE requires "pending", and abort_job() only knows the
  main task registry. The only recovery was editing the database.

  This file previously transcribed that reset into a local helper, because
  start_worker() opened its own SessionLocal and spawned the asyncio loop.
  The tests passed against the copy, so the shipped code was never executed:
  deleting BOTH resets from start_worker outright left the whole suite green.
  The logic now lives in worker.recover_interrupted_jobs(db), which takes a
  caller-supplied session so these tests can drive the real thing, and
  test_start_worker_actually_performs_the_recovery pins the wiring that the
  transcription was hiding.

  Verified by mutation: 13 mutations of the recovery, each killed by at least
  one test here.

ORPHANED FILE SWEEP
  Cleanup globbed TEMP_DIR only. _stage_parts writes "<final>.part" inside the
  media library, and _pick_temp_dir falls back to the media directory when
  TEMP_DIR is short on space — so the two places large orphans actually
  accumulate were both unswept. These tests already called
  _cleanup_orphaned_temp_files directly and were not part of the mutation run
  above, which targeted the recovery function only.
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
    Drive the real startup reset and return how many forge jobs it touched.

    This used to be a transcription of the block inside start_worker(),
    because start_worker() opens its own SessionLocal and spawns the asyncio
    loop. That made these tests inert: deleting the reset from start_worker
    entirely left the whole suite green. The logic now lives in
    worker.recover_interrupted_jobs(db), which takes a caller-supplied
    session precisely so this can call the shipped code.
    """
    import app.core.worker as worker

    _, forge_count = worker.recover_interrupted_jobs(db)
    return forge_count


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


# ── Queue item reset ─────────────────────────────────────────────────────────

def _seed_queue(db, status, idx=0):
    from app.database.models import MediaFile, QueueItem

    name = f"q{idx}.mkv"
    mf = MediaFile(path=f"/m/{name}", filename=name, directory="/m",
                   size=1, mtime=1.0, status="processing")
    db.add(mf)
    db.commit()
    job = QueueItem(file_id=mf.id, status=status, progress=37.0)
    db.add(job)
    db.commit()
    return job.id


def test_stranded_queue_item_becomes_failed(db):
    """
    The older half of the same reset. A QueueItem left at "processing" after a
    crash otherwise shows as running forever, and max_concurrent_jobs counts
    it against the pool, so enough of them stall the queue outright.
    """
    import app.core.worker as worker
    from app.database.models import QueueItem

    jid = _seed_queue(db, "processing")
    queue_count, _ = worker.recover_interrupted_jobs(db)
    assert queue_count == 1

    job = db.get(QueueItem, jid)
    assert job.status == "failed"
    assert job.completed_at is not None
    assert "Interrupted" in job.error_message


def test_stranded_queue_item_errors_its_media_row(db):
    """
    The MediaFile is dragged to "error" alongside the job. Left at
    "processing" it would misreport in the library view, and no scan resets
    it — the scanner only writes status on files whose size/mtime changed.
    """
    import app.core.worker as worker
    from app.database.models import MediaFile, QueueItem

    jid = _seed_queue(db, "processing")
    worker.recover_interrupted_jobs(db)

    file_id = db.get(QueueItem, jid).file_id
    assert db.get(MediaFile, file_id).status == "error"


def test_queue_reset_leaves_other_statuses_alone(db):
    """Pending work must survive a restart — only "processing" is stranded."""
    import app.core.worker as worker
    from app.database.models import QueueItem

    ids = {
        s: _seed_queue(db, s, idx=i)
        for i, s in enumerate(["pending", "success", "failed", "manual_review"])
    }
    queue_count, _ = worker.recover_interrupted_jobs(db)
    assert queue_count == 0
    for status, jid in ids.items():
        assert db.get(QueueItem, jid).status == status


def test_a_queue_item_with_a_dangling_media_row_does_not_break_the_reset(db):
    """
    The `if job.media_file:` guard. file_id is NOT NULL, so the orphan this
    protects against is a dangling foreign key, not a missing one — and
    SQLite does not enforce ondelete="CASCADE" unless PRAGMA foreign_keys=ON,
    which this project never sets. That is why scanner.py's
    _delete_media_file_and_related deletes related tables by hand, and its
    docstring records two tables having been silently orphaned by exactly
    that mistake.

    Such a job must not take the whole startup reset down with it — that
    would strand every OTHER interrupted job too, on the one code path whose
    entire purpose is to unstrand them.
    """
    from sqlalchemy import text

    import app.core.worker as worker
    from app.database.models import QueueItem

    orphan = _seed_queue(db, "processing", idx=0)
    normal = _seed_queue(db, "processing", idx=1)

    # Delete the MediaFile the way a bulk path that forgot a table would.
    orphan_file_id = db.get(QueueItem, orphan).file_id
    db.execute(text("DELETE FROM media_files WHERE id = :i"),
               {"i": orphan_file_id})
    db.commit()
    db.expire_all()

    queue_count, _ = worker.recover_interrupted_jobs(db)

    assert queue_count == 2
    assert db.get(QueueItem, orphan).status == "failed"
    assert db.get(QueueItem, normal).status == "failed", (
        "an orphaned job aborted the reset before the healthy ones were done"
    )


def test_both_halves_run_in_one_pass(db):
    """
    Queue items and forge jobs are reset by the same call. Recovering only one
    kind would leave the other wedged, and start_worker calls this exactly
    once at boot.
    """
    import app.core.worker as worker
    from app.database.models import Ac3ForgeJob, QueueItem

    q = _seed_queue(db, "processing")
    f = _seed(db, "processing")

    assert worker.recover_interrupted_jobs(db) == (1, 1)
    assert db.get(QueueItem, q).status == "failed"
    assert db.get(Ac3ForgeJob, f).status == "failed"


def test_start_worker_actually_performs_the_recovery(db, monkeypatch):
    """
    The point of the extraction. Everything above drives
    recover_interrupted_jobs directly, which proves the logic but not that
    anything calls it — the previous version of this file transcribed the
    logic instead, so removing the block from start_worker left all 334 tests
    green.

    Drives the real start_worker with its session factory and loop stubbed,
    so what is under test is the wiring: boot reaches the recovery.
    """
    import asyncio
    from contextlib import contextmanager

    import app.core.worker as worker
    from app.database.models import Ac3ForgeJob, QueueItem

    q = _seed_queue(db, "processing")
    f = _seed(db, "processing")

    @contextmanager
    def _session():
        yield db          # never closes the test's session

    async def _noop():
        pass

    monkeypatch.setattr(worker, "SessionLocal", _session)
    monkeypatch.setattr(worker, "get_app_settings", lambda _db: {})
    monkeypatch.setattr(worker, "_loop", _noop)

    asyncio.run(worker.start_worker())
    if worker._worker_task:
        worker._worker_task.cancel()

    assert db.get(QueueItem, q).status == "failed", (
        "start_worker booted without resetting an interrupted queue item"
    )
    assert db.get(Ac3ForgeJob, f).status == "failed", (
        "start_worker booted without resetting an interrupted forge job"
    )


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
