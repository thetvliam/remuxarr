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
were applied to _finish_job one at a time, and each of the 17 tests written
then fails against at least one of them. That makes this a net rather than a
coverage number. test_the_font_count_is_refreshed_to_the_processed_file came
later, with its own mutant — the refresh's write deleted — which survived the
full suite until it existed.

The two stored-answer tests at the end came with the re-keying in
test_answers_follow_tracks.py, whose docstring holds their mutation record:
the re-key not called, an extracted subtitle counted as kept, each of the
three columns left out, and a failed probe keeping the answers, all of which
survived the full suite first. Because the re-key sits inside the Track
refresh, two mutants of that block were run again with it in place: the
whole delete and re-add removed, as recorded above, and the delete alone.
test_track_rows_are_replaced_with_what_is_now_on_disk still kills both.

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

The Language Review tests at the end came with scanner.rekey_language_flags.
Ten mutants, each run against the full 2167-test suite before the tests
existed, and all ten survived it. Killed now as follows, the end-to-end test
being test_language_answer_during_job.py's answer after a job:

  • the re-key not called from _finish_job     → all nine new tests
  • the old track list read only when there
    are answers, as it was                     → all nine
  • audio questions left out                   → the five audio ones,
                                                 end-to-end included
  • subtitle questions left out                → the five subtitle ones
  • an extracted question kept at its old
    position, and one deleted instead          → the three extracted ones
                                                 (two mutants)
  • a removed track's question kept, audio
    and subtitle separately                    → removed track (two mutants)
  • the renumber done in one step              → creation order (3, 2) only
  • an extracted question placed at -(old+1)   → second extraction only

The one-step renumber fails in one creation order and not the other, which
is why both are run; the order that fails is the ORM's choice and could
change between SQLAlchemy versions.

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


def test_the_font_count_is_refreshed_to_the_processed_file(worker, Session,
                                                          media_dir,
                                                          monkeypatch):
    """
    The same delta-scan reasoning as the Track rows above, for the count the
    decision engine's font gate reads.

    A file converted to MP4 has no fonts left, since MP4 cannot hold an
    attachment. Left at its pre-job count, the row describes fonts that no
    longer exist, and nothing corrects it: a delta scan skips the file, and
    the worker only counts rows that have never been counted.

    Only probe_file is replaced, so the real extract_format_info does the
    counting. _stub_probe replaces extract_format_info itself, with a dict
    that has no font key, and the other tests here depend on that shape.
    """
    from app.database.models import MediaFile

    src = media_dir / "episode.mp4"
    src.write_bytes(b"x" * 10)
    media_id, job_id = _seed(Session, src)
    with Session() as db:
        db.get(MediaFile, media_id).font_attachments = 3
        db.commit()

    monkeypatch.setattr(worker, "probe_file", lambda *_a, **_kw: {
        "format": {"format_name": "mov,mp4,m4a,3gp,3g2,mj2"},
        "streams": [
            {"index": 0, "codec_type": "video", "codec_name": "h264"},
            {"index": 1, "codec_type": "audio", "codec_name": "aac",
             "tags": {"language": "eng"}},
        ],
    })

    worker._finish_job(job_id, True, str(src), 10, None)

    assert _media(Session, media_id).font_attachments == 0


# ── Stored answers follow the tracks ─────────────────────────────────────────
# The rekeying itself is tested in test_answers_follow_tracks.py. These pin
# that _finish_job does it, for all three columns, with the job's own drops
# and extractions, and that a probe it cannot read forgets the answers.

def _answered_job(Session, src):
    """
    A job that dropped the first of two identical audio tracks and the first
    of two identical PGS tracks, and extracted the English subtitle, on a
    file holding answers in all three columns.

    Before: 0 video, 1 and 2 undefined AC3 (answered fre and jpn; 1 dropped),
    3 SubRip eng (extracted), 4 and 5 undefined PGS (answered remove and
    keep, 5 also spa; 4 dropped). After: 0 video, 1 AC3, 2 PGS.

    Dropping the first of each pair is what renumbers the second, so every
    column holds an answer whose key changes, and a column left alone keeps
    the dropped track's answer on the survivor. The extracted subtitle sits
    among the PGS tracks: counted as kept, it would make the subtitle count
    disagree with the output.
    """
    import json

    from app.core.scanner import descriptors_by_stream
    from app.database.models import MediaFile, PlannedAction, Track

    before = [
        _track(0, "video", "h264", "und", channels=None),
        _track(1, "audio", "ac3", "und"),
        _track(2, "audio", "ac3", "und"),
        _track(3, "subtitle", "subrip", "eng", channels=None),
        _track(4, "subtitle", "hdmv_pgs_subtitle", "und", channels=None),
        _track(5, "subtitle", "hdmv_pgs_subtitle", "und", channels=None),
    ]
    keys = descriptors_by_stream(before)
    media_id, job_id = _seed(Session, src)
    with Session() as db:
        media = db.get(MediaFile, media_id)
        media.audio_language_overrides = json.dumps({keys[1]: "fre", keys[2]: "jpn"})
        media.subtitle_overrides = json.dumps({keys[4]: "remove", keys[5]: "keep"})
        media.subtitle_language_overrides = json.dumps({keys[5]: "spa"})
        for td in before:
            db.add(Track(file_id=media_id, **td))
        for order, (action, si) in enumerate([
                ("drop_track", 1), ("copy_track", 2), ("extract_subtitle", 3),
                ("drop_track", 4), ("copy_track", 5)]):
            db.add(PlannedAction(queue_item_id=job_id, order=order, action_type=action,
                                 description=action, stream_index=si))
        db.commit()
    after = [
        _track(0, "video", "h264", "und", channels=None),
        _track(1, "audio", "ac3", "und"),
        _track(2, "subtitle", "hdmv_pgs_subtitle", "und", channels=None),
    ]
    return media_id, job_id, after


def _stored(Session, media_id, column, tracks):
    import json

    from app.core.scanner import resolve_track_answers
    return resolve_track_answers(
        json.loads(getattr(_media(Session, media_id), column) or "{}"), tracks)


def test_answers_move_with_the_tracks_the_job_kept(worker, Session, media_dir,
                                                   monkeypatch):
    src = media_dir / "movie.mkv"
    src.write_bytes(b"x" * 10)
    media_id, job_id, after = _answered_job(Session, src)
    _stub_probe(monkeypatch, worker, container="mkv", tracks=after)

    worker._finish_job(job_id, True, str(src), 10, None)

    assert _stored(Session, media_id, "audio_language_overrides", after) == {1: "jpn"}, (
        "the survivor took the dropped track's language answer")
    assert _stored(Session, media_id, "subtitle_overrides", after) == {2: "keep"}
    assert _stored(Session, media_id, "subtitle_language_overrides", after) == {2: "spa"}


def test_a_probe_failure_after_a_job_forgets_the_answers(worker, Session,
                                                         media_dir, monkeypatch):
    """
    The file was rewritten and its new layout could not be read, so the
    answers cannot be carried. Kept, they would be resolved against whatever
    the next full scan finds; forgotten, the questions are asked again.
    """
    from app.core.probe import ProbeError

    src = media_dir / "movie.mkv"
    src.write_bytes(b"x" * 10)
    media_id, job_id, _after = _answered_job(Session, src)
    _stub_probe(monkeypatch, worker, raises=ProbeError("ffprobe returned 1"))

    worker._finish_job(job_id, True, str(src), 10, None)

    media = _media(Session, media_id)
    assert (media.audio_language_overrides, media.subtitle_overrides,
            media.subtitle_language_overrides) == (None, None, None)


# ── Language Review questions follow the tracks ──────────────────────────────
# Each row asks about the track at one stream_index, and Set language stores
# its answer against whatever track sits there when it is clicked. A job
# renumbers the tracks after any it drops or extracts, and records the new
# size and mtime so no delta scan looks again; these pin that _finish_job
# moves the rows itself (scanner.rekey_language_flags). Mutation record in
# the module docstring.

def _flagged_job(Session, src, before, actions, flags):
    """
    A job about to finish on a file with Language Review questions and no
    stored answers, so nothing but the questions can call for the re-key.

    flags: (kind, stream_index, extracted_path or None), kind "audio" or
    "subtitle", created in the order given, so a test controls their ids.
    Returns (media_id, job_id, {stream_index: flag id}).
    """
    from app.database.models import (AudioLanguageFlag, PlannedAction,
                                      SubtitleLanguageFlag, Track)

    media_id, job_id = _seed(Session, src)
    ids = {}
    with Session() as db:
        for td in before:
            db.add(Track(file_id=media_id, **td))
        for order, (action, si) in enumerate(actions):
            db.add(PlannedAction(queue_item_id=job_id, order=order, action_type=action,
                                 description=action, stream_index=si))
        for kind, si, sidecar in flags:
            if kind == "audio":
                flag = AudioLanguageFlag(file_id=media_id, stream_index=si,
                                         detected_language="und", origin="mismatch")
            else:
                flag = SubtitleLanguageFlag(file_id=media_id, stream_index=si,
                                            detected_language="und",
                                            extracted_path=sidecar)
            db.add(flag)
            db.flush()
            ids[si] = flag.id
        db.commit()
    return media_id, job_id, ids


def _flags(Session, model):
    """{flag id: (stream_index, extracted_path)} for every row of one table."""
    with Session() as db:
        return {f.id: (f.stream_index, getattr(f, "extracted_path", None))
                for f in db.query(model)}


def _v():
    return _track(0, "video", "h264", "und", channels=None)


def _sub(si, codec="subrip", language="und"):
    return _track(si, "subtitle", codec, language, channels=None)


def test_an_audio_question_follows_its_track(worker, Session, media_dir,
                                             monkeypatch):
    """
    The reported case: French, untagged and English audio, French dropped.
    The question raised for the untagged track at stream 2 was left at 2,
    where the English track now sits, and answering it retagged English.

    No answers are stored, which is what reaches the question-only path: the
    old track list used to be read only when there were answers to carry.
    """
    from app.database.models import AudioLanguageFlag

    src = media_dir / "movie.mkv"
    src.write_bytes(b"x" * 10)
    before = [_v(), _track(1, "audio", "aac", "fre"), _track(2, "audio", "aac", "und"),
              _track(3, "audio", "aac", "eng")]
    _media_id, job_id, ids = _flagged_job(
        Session, src, before,
        [("drop_track", 1), ("copy_track", 2), ("copy_track", 3)],
        [("audio", 2, None)])
    after = [_v(), _track(1, "audio", "aac", "und"), _track(2, "audio", "aac", "eng")]
    _stub_probe(monkeypatch, worker, container="mkv", tracks=after)

    worker._finish_job(job_id, True, str(src), 10, None)

    assert _flags(Session, AudioLanguageFlag) == {ids[2]: (1, None)}, (
        "the question stayed on stream 2, which is the English track now")


def test_a_subtitle_question_follows_its_track(worker, Session, media_dir,
                                               monkeypatch):
    from app.database.models import SubtitleLanguageFlag

    src = media_dir / "movie.mkv"
    src.write_bytes(b"x" * 10)
    before = [_v(), _track(1, "audio", "aac", "eng"), _sub(2, language="fre"), _sub(3)]
    _media_id, job_id, ids = _flagged_job(
        Session, src, before,
        [("copy_track", 1), ("drop_track", 2), ("copy_track", 3)],
        [("subtitle", 3, None)])
    after = [_v(), _track(1, "audio", "aac", "eng"), _sub(2)]
    _stub_probe(monkeypatch, worker, container="mkv", tracks=after)

    worker._finish_job(job_id, True, str(src), 10, None)

    assert _flags(Session, SubtitleLanguageFlag) == {ids[3]: (2, None)}


def _extracted_job(Session, media_dir):
    """
    An untagged SubRip at stream 2 extracted to its sidecar, with a kept PGS
    behind it: after the job the PGS is stream 2.
    """
    src = media_dir / "movie.mkv"
    src.write_bytes(b"x" * 10)
    sidecar = media_dir / "movie.und.srt"
    sidecar.write_text("extracted")
    before = [_v(), _track(1, "audio", "aac", "eng"), _sub(2),
              _sub(3, "hdmv_pgs_subtitle", "eng")]
    media_id, job_id, ids = _flagged_job(
        Session, src, before,
        [("copy_track", 1), ("extract_subtitle", 2), ("copy_track", 3)],
        [("subtitle", 2, str(sidecar))])
    after = [_v(), _track(1, "audio", "aac", "eng"), _sub(2, "hdmv_pgs_subtitle", "eng")]
    return src, sidecar, media_id, job_id, ids, after


def test_an_extracted_subtitles_question_gives_up_its_position(worker, Session,
                                                               media_dir,
                                                               monkeypatch):
    """
    The question stays, because the sidecar's name is the only thing left
    that can carry the language, but no position in the file is its now.
    Kept at 2 it claimed the PGS that moved there; deleted, nothing would
    ever offer to correct the sidecar.
    """
    from app.database.models import SubtitleLanguageFlag

    src, sidecar, _media_id, job_id, ids, after = _extracted_job(Session, media_dir)
    _stub_probe(monkeypatch, worker, container="mkv", tracks=after)

    worker._finish_job(job_id, True, str(src), 10, None)

    rows = _flags(Session, SubtitleLanguageFlag)
    assert list(rows) == [ids[2]], "the question for the sidecar was lost"
    position, path = rows[ids[2]]
    assert position < 0, f"still claims stream {position}, which is the PGS now"
    assert path == str(sidecar)


def test_answering_an_extracted_subtitle_after_its_job_renames_the_sidecar(
        worker, Session, media_dir, monkeypatch):
    """
    What the position is for. Left at 2, Set language found the PGS there,
    took the question for an embedded one and stored the answer against the
    PGS, so the follow-up job retagged it.
    """
    import app.api.routes._language_review as lr
    from app.api.routes._language_review import ApplyRequest
    from app.api.routes.subtitle_language import apply_language

    src, sidecar, media_id, job_id, ids, after = _extracted_job(Session, media_dir)
    _stub_probe(monkeypatch, worker, container="mkv", tracks=after)
    worker._finish_job(job_id, True, str(src), 10, None)
    monkeypatch.setattr(lr, "get_app_settings", lambda _db: {"dry_run_mode": False})
    monkeypatch.setattr(lr, "_process_file", lambda *a, **k: None)

    with Session() as db:
        apply_language(ApplyRequest(flag_ids=[ids[2]], target_language="fre"), db)

    assert _media(Session, media_id).subtitle_language_overrides in (None, "{}"), (
        "the answer for the sidecar was stored against the PGS track")
    assert not sidecar.exists()
    assert (media_dir / "movie.fr.srt").exists()


def test_a_removed_tracks_question_goes_with_it(worker, Session, media_dir,
                                                monkeypatch):
    """
    A dropped track has nothing left to answer for, audio or subtitle, and a
    subtitle with no sidecar has no filename to correct either. Kept, each
    sat at a position another track now holds.
    """
    from app.database.models import AudioLanguageFlag, SubtitleLanguageFlag

    src = media_dir / "movie.mkv"
    src.write_bytes(b"x" * 10)
    before = [_v(), _track(1, "audio", "aac", "fre"), _track(2, "audio", "aac", "eng"),
              _sub(3), _sub(4, language="eng")]
    _media_id, job_id, _ids = _flagged_job(
        Session, src, before,
        [("drop_track", 1), ("copy_track", 2), ("drop_track", 3), ("copy_track", 4)],
        [("audio", 1, None), ("subtitle", 3, None)])
    after = [_v(), _track(1, "audio", "aac", "eng"), _sub(2, language="eng")]
    _stub_probe(monkeypatch, worker, container="mkv", tracks=after)

    worker._finish_job(job_id, True, str(src), 10, None)

    assert _flags(Session, AudioLanguageFlag) == {}
    assert _flags(Session, SubtitleLanguageFlag) == {}


@pytest.mark.parametrize("created", [(2, 3), (3, 2)])
def test_questions_move_whatever_order_they_were_made_in(worker, Session, media_dir,
                                                         monkeypatch, created):
    """
    Two questions both moving down one, 2 to 1 and 3 to 2. The table is
    unique on (file_id, stream_index), so a single pass can move 3 onto 2
    while the other row still holds it, depending on the order the rows are
    written in; both creation orders are run because which one fails is the
    ORM's business, not this test's.
    """
    from app.database.models import AudioLanguageFlag, QueueItem

    src = media_dir / "movie.mkv"
    src.write_bytes(b"x" * 10)
    before = [_v(), _track(1, "audio", "aac", "fre"), _track(2, "audio", "aac", "und"),
              _track(3, "audio", "aac", "und")]
    _media_id, job_id, ids = _flagged_job(
        Session, src, before,
        [("drop_track", 1), ("copy_track", 2), ("copy_track", 3)],
        [("audio", si, None) for si in created])
    after = [_v(), _track(1, "audio", "aac", "und"), _track(2, "audio", "aac", "und")]
    _stub_probe(monkeypatch, worker, container="mkv", tracks=after)

    worker._finish_job(job_id, True, str(src), 10, None)

    assert _job(Session, job_id).status == "success"
    assert _flags(Session, AudioLanguageFlag) == {ids[2]: (1, None), ids[3]: (2, None)}
    with Session() as db:
        assert db.get(QueueItem, job_id).error_message is None


def test_a_second_extraction_does_not_clash_with_the_first(worker, Session,
                                                           media_dir, monkeypatch):
    """
    Two untagged subtitles at 2 and 3. The first job extracts 2, so 3 moves
    to 2; a later job extracts that one too, from the same old position.
    Each extracted question needs its own negative position, so the second
    is placed below the first rather than derived from the old position,
    which is 2 both times.
    """
    from app.database.models import (PlannedAction, QueueItem,
                                      SubtitleLanguageFlag, Track)

    src = media_dir / "movie.mkv"
    src.write_bytes(b"x" * 10)
    first, second = media_dir / "movie.und.srt", media_dir / "movie.und.2.srt"
    first.write_text("one")
    second.write_text("two")
    before = [_v(), _track(1, "audio", "aac", "eng"), _sub(2), _sub(3)]
    media_id, job_id, ids = _flagged_job(
        Session, src, before,
        [("copy_track", 1), ("extract_subtitle", 2), ("copy_track", 3)],
        [("subtitle", 2, str(first)), ("subtitle", 3, str(second))])
    after_first = [_v(), _track(1, "audio", "aac", "eng"), _sub(2)]
    _stub_probe(monkeypatch, worker, container="mkv", tracks=after_first)
    worker._finish_job(job_id, True, str(src), 10, None)

    with Session() as db:
        job2 = QueueItem(file_id=media_id, status="processing")
        db.add(job2)
        db.flush()
        db.add(PlannedAction(queue_item_id=job2.id, order=0, action_type="copy_track",
                             description="copy", stream_index=1))
        db.add(PlannedAction(queue_item_id=job2.id, order=1,
                             action_type="extract_subtitle", description="extract",
                             stream_index=2))
        db.commit()
        job2_id = job2.id
    _stub_probe(monkeypatch, worker, container="mkv",
                tracks=[_v(), _track(1, "audio", "aac", "eng")])
    worker._finish_job(job2_id, True, str(src), 10, None)

    assert _job(Session, job2_id).status == "success"
    rows = _flags(Session, SubtitleLanguageFlag)
    assert {fid: path for fid, (_si, path) in rows.items()} == {
        ids[2]: str(first), ids[3]: str(second)}
    assert all(si < 0 for si, _path in rows.values())
    with Session() as db:
        assert db.query(Track).filter(Track.file_id == media_id).count() == 2
