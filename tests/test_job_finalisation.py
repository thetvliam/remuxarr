"""
Job finalisation — worker._finish_job().

This is the step that runs after FFmpeg has already succeeded or failed, and
it decides what the rest of the system believes about the file from then on:
the terminal status History shows, whether the MediaFile row still points at
a path that exists, and whether a delta scan will ever look at the file again.

It had no direct coverage. That was established by mutation rather than by
reading the coverage report — four behaviours in this function were disabled
one at a time and the pre-existing 334-test suite stayed green for every one:

  • the dry-run terminal status collapsed into a plain "success"
  • the stale-MediaFile deletion turned off (`if stale:` → `if False:`)
  • the size/mtime refresh removed entirely
  • the Track-row refresh removed entirely

So every test below was then checked the same way, not assumed: 18 mutations
were applied to _finish_job one at a time, and each of the 17 tests here
fails against at least one of them. That makes this a net rather than a
coverage number.

Two of those mutations are worth recording because they are the ones a
plausible "tidy-up" would actually produce, and neither is caught by
anything else:

  • `job.status = "dry_run"` unconditionally when is_dry_run — simpler to
    read, and it silently reports a dry run that FAILED as a completed one.
    Only test_dry_run_failure_is_still_a_failure catches it.
  • scoping the stale-row sweep to the output's directory instead of its
    exact path — which deletes other files' MediaFile rows, and their Track
    rows with them via cascade. Only
    test_stale_row_removal_does_not_touch_unrelated_rows catches it.

WHY THE FIXTURES LOOK LIKE THIS
_finish_job opens its own session and closes it in a finally block. The
established `monkeypatch.setattr(worker, "SessionLocal", lambda: db)` shortcut
used elsewhere in this suite hands it the test's own session, so that close()
detaches every ORM object the test is holding and assertions afterwards raise
InvalidRequestError. So SessionLocal is bound to a sessionmaker instead, and
each test opens short-lived sessions of its own — which is also what
production does.

The database is a file rather than sqlite://. In-memory would put both
sessions on one shared connection, which hides exactly the kind of
cross-session commit visibility this function depends on.

probe_file is stubbed per test. What is under test is what _finish_job DOES
with a probe result; the probe itself is covered by the ffprobe-backed tests
in test_scan_stats_and_subtitle_classifier.py.
"""
import os

import pytest


@pytest.fixture
def Session(tmp_path):
    """A sessionmaker against a fresh file-backed database per test."""
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    from app.database.models import Base

    db_dir = tmp_path / "db"
    db_dir.mkdir()
    engine = create_engine(f"sqlite:///{db_dir / 'test.db'}")
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)


@pytest.fixture
def worker(Session, monkeypatch):
    """worker module with its session factory pointed at the test database."""
    import app.core.worker as worker_mod

    monkeypatch.setattr(worker_mod, "SessionLocal", Session)
    return worker_mod


@pytest.fixture
def media_dir(tmp_path):
    """Kept separate from the database file so neither can be mistaken for
    the other by a path-scoped assertion."""
    d = tmp_path / "library"
    d.mkdir()
    return d


def _seed(Session, path, size=1000, mtime=1.0, container="mkv",
          is_dry_run=False, progress=42.0):
    """
    One MediaFile plus the QueueItem being finalised, both mid-flight.

    Returns plain ids, not ORM objects: the session is closed before the
    function under test runs, so nothing is left holding a read transaction
    against the same file.
    """
    from app.database.models import MediaFile, QueueItem

    with Session() as db:
        media = MediaFile(
            path=str(path),
            filename=os.path.basename(str(path)),
            directory=os.path.dirname(str(path)),
            size=size,
            mtime=mtime,
            container=container,
            status="processing",
        )
        db.add(media)
        db.commit()

        job = QueueItem(
            file_id=media.id,
            status="processing",
            is_dry_run=is_dry_run,
            progress=progress,
        )
        db.add(job)
        db.commit()
        return media.id, job.id


def _stub_probe(monkeypatch, worker, tracks=(), container=None, duration=None,
                raises=None):
    """
    Replace the three probe helpers _finish_job imports into its own module
    namespace. raises= simulates a probe that fails after the FFmpeg work has
    already succeeded.
    """
    def fake_probe_file(path, ffprobe_path):
        if raises is not None:
            raise raises
        return {"_stub": True}

    monkeypatch.setattr(worker, "probe_file", fake_probe_file)
    monkeypatch.setattr(
        worker, "extract_format_info",
        lambda _data: {"container": container, "duration": duration},
    )
    monkeypatch.setattr(worker, "extract_tracks", lambda _data: list(tracks))


def _track(stream_index=0, track_type="audio", codec="ac3", language="eng",
           channels=6):
    return {
        "stream_index": stream_index,
        "track_type": track_type,
        "codec": codec,
        "language": language,
        "channels": channels,
        "channel_layout": "5.1" if channels == 6 else "stereo",
        "is_default": False,
        "is_forced": False,
        "is_hearing_impaired": False,
        "is_dub": False,
        "title": None,
    }


def _job(Session, job_id):
    from app.database.models import QueueItem
    with Session() as db:
        return db.get(QueueItem, job_id)


def _media(Session, media_id):
    from app.database.models import MediaFile
    with Session() as db:
        return db.get(MediaFile, media_id)


# ── Dry runs ─────────────────────────────────────────────────────────────────

def test_dry_run_success_is_not_recorded_as_a_real_success(worker, Session,
                                                           media_dir,
                                                           monkeypatch):
    """
    A dry run touches no file, so it must never reach a terminal state the
    History panel renders identically to a real success — the user would
    believe work happened that did not.

    Mutation this catches: collapsing the is_dry_run branch so every success
    writes status="success".
    """
    src = media_dir / "movie.mkv"
    src.write_bytes(b"x" * 10)
    _, job_id = _seed(Session, src, is_dry_run=True)
    _stub_probe(monkeypatch, worker, container="mkv")

    worker._finish_job(job_id, True, str(src), 10, None)

    status = _job(Session, job_id).status
    assert status == "dry_run", (
        f"dry run finished as {status!r} — indistinguishable from a real "
        "success in History, despite no file having been touched"
    )


def test_dry_run_leaves_the_file_queued_for_a_real_pass(worker, Session,
                                                        media_dir,
                                                        monkeypatch):
    """
    The counterpart on the MediaFile side. Marking it "processed" would make
    the next scan skip a file nothing has been done to, so a dry run leaves
    it queued.
    """
    src = media_dir / "movie.mkv"
    src.write_bytes(b"x" * 10)
    media_id, job_id = _seed(Session, src, is_dry_run=True)
    _stub_probe(monkeypatch, worker, container="mkv")

    worker._finish_job(job_id, True, str(src), 10, None)

    media = _media(Session, media_id)
    assert media.status == "queued"
    assert media.last_processed is None, (
        "a dry run stamped last_processed — nothing was processed"
    )


def test_dry_run_failure_is_still_a_failure(worker, Session, media_dir,
                                            monkeypatch):
    """The dry-run branch renames only the SUCCESS status. A failure must not
    be swallowed into one that reads as benign."""
    src = media_dir / "movie.mkv"
    src.write_bytes(b"x")
    _, job_id = _seed(Session, src, is_dry_run=True)
    _stub_probe(monkeypatch, worker)

    worker._finish_job(job_id, False, None, None, "ffmpeg exploded")

    assert _job(Session, job_id).status == "failed"


# ── Stale row at the target path ─────────────────────────────────────────────

def test_stale_row_owning_the_new_path_is_removed_first(worker, Session,
                                                        media_dir,
                                                        monkeypatch):
    """
    A container conversion repoints MediaFile.path at the output. That column
    is UNIQUE, so a leftover row from an earlier dismiss → re-copy → re-scan
    cycle already holding the target path makes the UPDATE fail — and it
    surfaces as a job diverted into the emergency-fail path rather than as a
    completed conversion.

    Mutation this catches: `if stale:` → `if False:`.
    """
    from app.database.models import MediaFile

    src = media_dir / "movie.mkv"
    out = media_dir / "movie.mp4"
    src.write_bytes(b"x" * 10)
    out.write_bytes(b"y" * 20)

    media_id, job_id = _seed(Session, src)

    # The ghost: a row for the mp4 path left by a previous cycle. The file it
    # described is gone, but the row still owns the unique path.
    with Session() as db:
        db.add(MediaFile(
            path=str(out), filename="movie.mp4", directory=str(media_dir),
            size=999, mtime=9.0, container="mp4", status="unprocessed",
        ))
        db.commit()

    _stub_probe(monkeypatch, worker, container="mp4")

    worker._finish_job(job_id, True, str(out), 20, None)

    job = _job(Session, job_id)
    assert job.status == "success", (
        f"conversion finalised as {job.status!r} (error: "
        f"{job.error_message!r}) — the stale row at the target path was not "
        "cleared before the UPDATE"
    )
    assert _media(Session, media_id).path == str(out)
    with Session() as db:
        assert db.query(MediaFile).filter(
            MediaFile.path == str(out)
        ).count() == 1


def test_stale_row_removal_does_not_touch_unrelated_rows(worker, Session,
                                                         media_dir,
                                                         monkeypatch):
    """
    The delete is scoped to the exact target path. A blunter version — match
    on directory, or on filename stem — would drop other files' rows and
    their Track rows with them via cascade.
    """
    from app.database.models import MediaFile

    src = media_dir / "movie.mkv"
    out = media_dir / "movie.mp4"
    src.write_bytes(b"x" * 10)
    out.write_bytes(b"y" * 20)

    _, job_id = _seed(Session, src)
    other = media_dir / "other.mkv"
    with Session() as db:
        db.add(MediaFile(
            path=str(other), filename="other.mkv", directory=str(media_dir),
            size=5, mtime=5.0, container="mkv", status="unprocessed",
        ))
        db.commit()

    _stub_probe(monkeypatch, worker, container="mp4")
    worker._finish_job(job_id, True, str(out), 20, None)

    with Session() as db:
        assert db.query(MediaFile).filter(
            MediaFile.path == str(other)
        ).count() == 1, "an unrelated MediaFile row was deleted by the sweep"


# ── size / mtime refresh ─────────────────────────────────────────────────────

def test_size_and_mtime_are_refreshed_to_the_processed_file(worker, Session,
                                                            media_dir,
                                                            monkeypatch):
    """
    Without this the row keeps the ORIGINAL file's fingerprint. The failure
    mode is specific: the user dismisses the job, deletes the processed file
    and restores the original with its timestamps intact — size and mtime
    then match the stale row exactly, the delta scan sees no change, and the
    file is skipped indefinitely despite needing reprocessing.

    Mutation this catches: removing the os.stat() block.
    """
    src = media_dir / "movie.mkv"
    src.write_bytes(b"x" * 10)
    media_id, job_id = _seed(Session, src, size=10, mtime=1.0)

    # Processing rewrote the file in place: different size, different mtime.
    src.write_bytes(b"y" * 4096)
    os.utime(src, (5000.0, 5000.0))

    _stub_probe(monkeypatch, worker, container="mkv")
    worker._finish_job(job_id, True, str(src), 4096, None)

    media = _media(Session, media_id)
    assert media.size == 4096, (
        f"size still {media.size} — the row holds the pre-processing "
        "fingerprint, so a restored original would look unchanged"
    )
    assert media.mtime == pytest.approx(5000.0)


def test_a_missing_output_file_does_not_fail_the_job(worker, Session,
                                                     media_dir, monkeypatch):
    """
    The os.stat is best-effort by design: the FFmpeg work already succeeded,
    and bookkeeping that cannot read the file back should not retroactively
    turn that into a failure. Pins the OSError branch.
    """
    src = media_dir / "movie.mkv"
    src.write_bytes(b"x" * 10)
    _, job_id = _seed(Session, src)
    _stub_probe(monkeypatch, worker, container="mkv")

    worker._finish_job(job_id, True, str(media_dir / "vanished.mkv"), 10, None)

    assert _job(Session, job_id).status == "success"


# ── Track refresh ────────────────────────────────────────────────────────────

def test_track_rows_are_replaced_with_what_is_now_on_disk(worker, Session,
                                                          media_dir,
                                                          monkeypatch):
    """
    Syncing size/mtime above is exactly what makes every future DELTA scan
    (the default, and what the scheduler always uses) treat this file as
    unchanged and skip re-probing it. So Track rows not refreshed here keep
    describing the pre-processing file until someone forces a full rescan.

    The concrete consumer is AC3 Forge's candidate query, which selects on
    the Track table's codec and channels. Stale rows point it wrong in both
    directions — offering a file whose audio is no longer AAC, or hiding one
    that now is.

    Mutation this catches: removing the Track delete/re-add block.
    """
    from app.database.models import Track

    src = media_dir / "movie.mkv"
    src.write_bytes(b"x" * 10)
    media_id, job_id = _seed(Session, src)

    # Pre-processing state: AAC 5.1, which is what makes a forge candidate.
    with Session() as db:
        db.add(Track(file_id=media_id, stream_index=1, track_type="audio",
                     codec="aac", language="eng", channels=6,
                     channel_layout="5.1"))
        db.commit()

    # Post-processing reality: the audio is now AC3.
    _stub_probe(monkeypatch, worker, container="mkv", tracks=[
        _track(stream_index=0, track_type="video", codec="h264",
               channels=None),
        _track(stream_index=1, track_type="audio", codec="ac3", channels=6),
    ])

    worker._finish_job(job_id, True, str(src), 10, None)

    with Session() as db:
        codecs = {
            t.codec for t in db.query(Track).filter(Track.file_id == media_id)
            if t.track_type == "audio"
        }
    assert codecs == {"ac3"}, (
        f"audio Track rows still report {codecs} — they describe the file as "
        "it was before processing, and a delta scan will never correct them"
    )


def test_track_refresh_replaces_rather_than_appends(worker, Session, media_dir,
                                                    monkeypatch):
    """
    The old rows are deleted before the new ones are added. Appending instead
    would leave the file described by two contradictory sets of tracks at
    once, which reads to the forge candidate query as a file that has both
    AAC and AC3 audio.
    """
    from app.database.models import Track

    src = media_dir / "movie.mkv"
    src.write_bytes(b"x" * 10)
    media_id, job_id = _seed(Session, src)

    with Session() as db:
        db.add(Track(file_id=media_id, stream_index=1, track_type="audio",
                     codec="aac", language="eng", channels=6))
        db.commit()

    _stub_probe(monkeypatch, worker, container="mkv", tracks=[
        _track(stream_index=1, track_type="audio", codec="ac3", channels=6),
    ])
    worker._finish_job(job_id, True, str(src), 10, None)

    with Session() as db:
        total = db.query(Track).filter(Track.file_id == media_id).count()
    assert total == 1, f"{total} Track rows after a 1-track probe — appended"


def test_a_probe_failure_leaves_the_job_successful(worker, Session, media_dir,
                                                   monkeypatch):
    """
    Same reasoning as the os.stat branch: this refresh is best-effort
    bookkeeping on top of work that already succeeded. A ProbeError must be
    logged and swallowed, not promoted into a failed job telling the user
    their media was not processed when it was.
    """
    from app.core.probe import ProbeError
    from app.database.models import Track

    src = media_dir / "movie.mkv"
    src.write_bytes(b"x" * 10)
    media_id, job_id = _seed(Session, src)
    with Session() as db:
        db.add(Track(file_id=media_id, stream_index=1, track_type="audio",
                     codec="aac", language="eng", channels=6))
        db.commit()

    _stub_probe(monkeypatch, worker, raises=ProbeError("ffprobe returned 1"))

    worker._finish_job(job_id, True, str(src), 10, None)

    job = _job(Session, job_id)
    assert job.status == "success", (
        f"a best-effort track refresh turned a real success into {job.status!r}"
    )
    assert _media(Session, media_id).status == "processed"
    # The old rows survive rather than being wiped — a stale description is
    # more useful than none, and the next full rescan corrects it.
    with Session() as db:
        assert db.query(Track).filter(Track.file_id == media_id).count() == 1


def test_a_fresh_probe_beats_the_extension_guess_for_container(worker, Session,
                                                               media_dir,
                                                               monkeypatch):
    """
    Two things write media.container on a path change: a lookup table keyed
    on the new extension, then the probe. The probe wins — an extension is a
    naming convention, the probe is the actual file.
    """
    src = media_dir / "movie.mkv"
    out = media_dir / "movie.mp4"
    src.write_bytes(b"x" * 10)
    out.write_bytes(b"y" * 20)
    media_id, job_id = _seed(Session, src)

    # Extension says mp4; the probe disagrees and is right.
    _stub_probe(monkeypatch, worker, container="matroska")
    worker._finish_job(job_id, True, str(out), 20, None)

    assert _media(Session, media_id).container == "matroska"


def test_extension_guess_still_applies_when_the_probe_fails(worker, Session,
                                                            media_dir,
                                                            monkeypatch):
    """
    The counterpart: with no probe result to override it, the extension-based
    guess is what stops History showing the old container after a conversion.
    """
    from app.core.probe import ProbeError

    src = media_dir / "movie.mkv"
    out = media_dir / "movie.mp4"
    src.write_bytes(b"x" * 10)
    out.write_bytes(b"y" * 20)
    media_id, job_id = _seed(Session, src, container="mkv")

    _stub_probe(monkeypatch, worker, raises=ProbeError("nope"))
    worker._finish_job(job_id, True, str(out), 20, None)

    media = _media(Session, media_id)
    assert media.container == "mp4"
    assert media.filename == "movie.mp4"
    assert media.directory == str(media_dir)


# ── Failure path ─────────────────────────────────────────────────────────────

def test_failure_marks_the_file_as_errored_and_keeps_progress(worker, Session,
                                                              media_dir,
                                                              monkeypatch):
    """
    Progress is deliberately left untouched on failure so History can show
    how far the job got before it died. Forcing it to 100 would make a job
    that failed at 42% look complete.
    """
    src = media_dir / "movie.mkv"
    src.write_bytes(b"x" * 10)
    media_id, job_id = _seed(Session, src, progress=42.0)
    _stub_probe(monkeypatch, worker)

    worker._finish_job(job_id, False, None, None, "Conversion failed: code 1")

    job = _job(Session, job_id)
    media = _media(Session, media_id)
    assert job.status == "failed"
    assert job.error_message == "Conversion failed: code 1"
    assert job.progress == pytest.approx(42.0)
    assert media.status == "error"
    assert media.last_processed is None


def test_failure_does_not_repoint_the_media_row(worker, Session, media_dir,
                                                monkeypatch):
    """
    A failed conversion may still have left a partial output on disk. The row
    must keep pointing at the original file, which is the one that exists and
    plays.
    """
    src = media_dir / "movie.mkv"
    src.write_bytes(b"x" * 10)
    media_id, job_id = _seed(Session, src)
    _stub_probe(monkeypatch, worker)

    worker._finish_job(job_id, False, str(media_dir / "movie.mp4"), None,
                       "boom")

    media = _media(Session, media_id)
    assert media.path == str(src)
    assert media.container == "mkv"


# ── Robustness ───────────────────────────────────────────────────────────────

def test_a_vanished_job_row_is_a_quiet_no_op(worker, Session, caplog):
    """
    A job can be deleted from the UI while FFmpeg is still running, so
    finalisation has to tolerate the row being gone.

    Asserting only that this does not raise proves nothing: without the
    `if job is None: return` guard the attribute access still gets caught by
    the function's own broad `except Exception`, which then calls
    _emergency_fail_job on an id that does not exist, which also finds
    nothing and returns. Every version is silent to the caller.

    What differs is the log. The guarded version says nothing; the unguarded
    one writes a full AttributeError traceback at ERROR level for what is a
    routine, expected race. That is the thing worth pinning — an exception
    traceback in the log should mean something is wrong.
    """
    import logging

    with caplog.at_level(logging.ERROR, logger="app.core.worker"):
        worker._finish_job(9999, True, "/nowhere.mkv", 1, None)

    errors = [r for r in caplog.records if r.levelno >= logging.ERROR]
    assert not errors, (
        "finalising an already-deleted job logged "
        f"{errors[0].getMessage()!r} — a routine race is being reported as a "
        "failure"
    )


def test_an_orphaned_job_still_reaches_a_terminal_status(worker, Session,
                                                         media_dir,
                                                         monkeypatch):
    """
    _finish_job guards its MediaFile lookup with `if media:`. Reaching that
    branch takes some doing, and it is worth recording why it is not dead
    code.

    Deleting a MediaFile through the ORM cannot produce this state:
    MediaFile.queue_items carries cascade="all, delete-orphan", so the job
    goes with it. What CAN produce it is a deletion path that issues a bulk
    query().delete() and forgets a table. Those paths exist here — the
    ondelete="CASCADE" foreign keys are declared but not enforced, because
    SQLite only honours them under PRAGMA foreign_keys=ON and this project
    does not set it, which is why scanner.py's _delete_media_file_and_related
    deletes four related tables by hand. That helper's own docstring records
    two tables having been silently orphaned by exactly this mistake.

    So the row is orphaned here the same way such a path would do it: a raw
    DELETE that touches media_files only. Without the guard the attribute
    access raises, finalisation diverts into the emergency path, and a job
    that genuinely succeeded is reported as failed.
    """
    from sqlalchemy import text

    src = media_dir / "movie.mkv"
    src.write_bytes(b"x" * 10)
    media_id, job_id = _seed(Session, src)

    with Session() as db:
        db.execute(text("DELETE FROM media_files WHERE id = :i"),
                   {"i": media_id})
        db.commit()
    _stub_probe(monkeypatch, worker)

    worker._finish_job(job_id, True, str(src), 10, None)

    job = _job(Session, job_id)
    assert job.status == "success", (
        f"orphaned job finalised as {job.status!r} (error: "
        f"{job.error_message!r}) — the missing-MediaFile guard did not hold"
    )


def test_a_crash_during_finalisation_does_not_strand_the_job(worker, Session,
                                                             media_dir,
                                                             monkeypatch):
    """
    If the transaction fails, the emergency fallback opens a FRESH connection
    — the failed one is poisoned and cannot commit — and does a minimal
    status write. Without it the job stays "processing" indefinitely in the
    UI with no exit but editing the database.
    """
    src = media_dir / "movie.mkv"
    src.write_bytes(b"x" * 10)
    _, job_id = _seed(Session, src)

    def explode(_data):
        raise RuntimeError("simulated finalisation failure")

    _stub_probe(monkeypatch, worker, container="mkv")
    monkeypatch.setattr(worker, "extract_tracks", explode)

    worker._finish_job(job_id, True, str(src), 10, None)

    job = _job(Session, job_id)
    assert job.status == "failed", (
        f"job left at {job.status!r} after finalisation crashed — it would "
        "show as running forever"
    )
    assert "simulated finalisation failure" in (job.error_message or "")
