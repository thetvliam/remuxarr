"""
A language answered in Review while the file's job is running.

apply_language saved the override and deleted the question, then saw the
job running and left it alone, saying the choice would apply after the job
"(next scan)". It did not. The job had decided before the answer existed,
and when it finished it recorded the file's new size and mtime, so every
delta scan, scheduled ones included, saw the file as unchanged. The question
was gone from Review and only a Full Scan applied the answer. Reproduced
with real FFmpeg before this file existed. A failed or aborted job leaves
the file as it was, so the stored size and mtime still match and the answer
is lost the same way.

The file is now marked (MediaFile.reevaluate_after_job). After a successful
job the worker looks at it again straight away, so the answer goes into a
follow-up job. After any other ending the next scan does, as it would for a
changed file. The mark is cleared only when the file is looked at with no
job running for it: a scan during the job would otherwise use it up and
stop at the in-progress check, which queues nothing.

Verified by mutation, 7 applied, 7 killed, each surviving the suite as it
stood (1990 tests) first. Six are killed here, the seventh, the column's
upgrade step missing, by test_schema_migration:

  • the mark never set                       → all four
  • no look after a successful job           → the successful job
  • a look after any ending, not only success → both endings
  • a delta scan ignoring the mark           → both endings
  • the mark used up while a job runs        → the scan during the job
  • the mark never cleared                   → the successful job, both
                                               endings

test_an_answer_after_a_job_lands_on_the_track_it_was_asked_about is about
an answer given after the job rather than during it: the question had to
follow its track through the job first. Its mutation record is in
test_job_finalisation.py, beside the tests that pin the re-key itself; three
of those ten mutants are killed here as well (the re-key not called, the old
track list read only with answers, and audio questions left out).

test_a_claim_during_an_answer_takes_the_job_the_answer_queues is about the
worker claiming the file's job while an answer replaces it. Its mutation
record is in test_queue_routes.py, beside the claim tests that kill the same
mutants with no file to probe; two of those three are killed here as well (a
miss giving up, and the row count ignored).

test_a_claim_while_an_answer_replaces_the_job_waits_for_it is about the
answer itself: its running-job check, its read of the waiting job and its
delete, which it now does under revert_lock.CLAIM_LOCK. Verified by
mutation, 4 applied, 4 killed, each surviving first the 55 test files that
reach the answer or the lock, as they stood, and each killed by the claim
taking the job the answer was about to replace:

  • no lock                                  → both moments
  • the lock around the check only           → before the delete
  • the lock released before the delete      → before the delete
  • the lock taken after the check           → after the check

With the lock around the check only, the claim started after the check
waits for the lock and then races the answer's read, so that case may pass
or fail under the mutant; it is not counted.
"""
import asyncio
import json
import shutil
import subprocess
from types import SimpleNamespace

import pytest


needs_ffmpeg = pytest.mark.skipif(
    shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None,
    reason="ffmpeg/ffprobe not available",
)
pytestmark = needs_ffmpeg


class _WS:
    async def broadcast_json(self, *args, **kwargs):
        pass


def _audio_languages(path):
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "a",
         "-show_entries", "stream_tags=language", "-of", "json", str(path)],
        capture_output=True, text=True, check=True)
    return [(s.get("tags") or {}).get("language") for s in json.loads(out.stdout)["streams"]]


@pytest.fixture
def database(tmp_path, monkeypatch):
    """
    A file-backed database the worker and the routes both use, with Dry Run
    off and an empty library folder.
    """
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    import app.core.worker as worker
    import app.database.session as session_module
    from app.config import settings as app_settings
    from app.database.models import AppSetting, Base

    engine = create_engine(f"sqlite:///{tmp_path / 'remuxarr.db'}",
                           connect_args={"check_same_thread": False, "timeout": 30})
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine)
    monkeypatch.setattr(worker, "SessionLocal", factory)
    monkeypatch.setattr(session_module, "SessionLocal", factory)
    monkeypatch.setattr(app_settings, "TEMP_DIR", str(tmp_path / "tmp"))
    monkeypatch.setattr(app_settings, "RECYCLE_DIR", str(tmp_path / "recycle"))
    (tmp_path / "recycle").mkdir()

    library = tmp_path / "library"
    library.mkdir()
    with factory() as db:
        db.merge(AppSetting(key="dry_run_mode", value=json.dumps(False)))
        db.commit()

    yield SimpleNamespace(Session=factory, worker=worker, library=library)
    engine.dispose()


@pytest.fixture
def lib(database):
    """
    Movie.mkv with two untagged audio tracks, scanned under Ask for audio:
    a job is queued to convert it to MP4, and each track is a question in
    Audio Language Review. The default, Leave, asks nothing.
    """
    from app.core import scanner
    from app.database.models import AppSetting, AudioLanguageFlag, QueueItem

    factory, library = database.Session, database.library
    video = library / "Movie.mkv"
    subprocess.run([
        "ffmpeg", "-v", "error", "-y",
        "-f", "lavfi", "-i", "testsrc=size=64x36:rate=5:duration=1",
        "-f", "lavfi", "-i", "sine=frequency=440:duration=1",
        "-f", "lavfi", "-i", "sine=frequency=880:duration=1",
        "-map", "0:v", "-map", "1:a", "-map", "2:a",
        "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-f", "matroska", str(video)], check=True)

    with factory() as db:
        db.merge(AppSetting(key="fix_undefined_language_audio",
                            value=json.dumps("always_ask")))
        db.commit()
        scanner.scan_library(db, [str(library)], force_probe=True)
        assert [q.status for q in db.query(QueueItem)] == ["pending"], "no job queued"
        assert db.query(AudioLanguageFlag).count() == 2, "no questions to answer"

    return database


def _answer(lib, language="jpn"):
    """Answer every audio question, as the Review page does."""
    from app.api.routes._language_review import ApplyRequest
    from app.api.routes.audio_language import apply_language
    from app.database.models import AudioLanguageFlag

    with lib.Session() as db:
        ids = [f.id for f in db.query(AudioLanguageFlag)]
        return apply_language(ApplyRequest(flag_ids=ids, target_language=language), db)


def _run_next(lib):
    """Claim the next job and run it through the worker's own wrapper."""
    async def go():
        job_id = lib.worker._claim_next()
        assert job_id is not None, "nothing to claim"
        await lib.worker._run_and_broadcast(job_id, _WS(), asyncio.get_running_loop())
        return job_id
    return asyncio.run(go())


def _state(lib):
    from app.database.models import MediaFile, QueueItem

    with lib.Session() as db:
        media = db.query(MediaFile).one()
        return SimpleNamespace(
            path=media.path, marked=media.reevaluate_after_job,
            jobs=[(q.status, q.reason) for q in db.query(QueueItem).order_by(QueueItem.id)])


def _scan(lib):
    from app.core import scanner

    with lib.Session() as db:
        return scanner.scan_library(db, [str(lib.library)], force_probe=False)


def test_an_answer_given_while_the_job_runs_is_applied_after_it(lib, monkeypatch):
    real_load = lib.worker._load_job_data
    replies = []

    def load_then_answer(job_id):
        data = real_load(job_id)        # the job's decision is made here
        replies.append(_answer(lib))
        return data
    monkeypatch.setattr(lib.worker, "_load_job_data", load_then_answer)

    _run_next(lib)
    monkeypatch.setattr(lib.worker, "_load_job_data", real_load)

    assert replies[0]["applied"] == 0, "the file was not running when answered"
    after = _state(lib)
    assert after.jobs[0][0] == "success"
    assert after.jobs[1][0] == "pending", f"nothing queued after the job: {after.jobs}"
    assert "language" in after.jobs[1][1]
    assert after.marked is False

    _run_next(lib)
    assert _audio_languages(after.path) == ["jpn", "jpn"]


@pytest.mark.parametrize("ending", ["failed", "cancelled"])
def test_after_a_job_that_did_not_succeed_the_next_scan_applies_it(lib, monkeypatch, ending):
    """
    Nothing is queued when the job ends: that would be a retry nobody asked
    for. The next delta scan looks at the file although it is unchanged on
    disk, and only that once.
    """
    from app.database.models import QueueItem

    async def answered_then_ended(job_id, ws_manager, loop):
        assert _answer(lib)["applied"] == 0
        with lib.Session() as db:
            db.get(QueueItem, job_id).status = ending
            db.commit()
    monkeypatch.setattr(lib.worker, "_run_job", answered_then_ended)

    _run_next(lib)
    ended = _state(lib)
    assert ended.jobs == [(ending, ended.jobs[0][1])], "a job was queued as this one ended"
    assert ended.marked is True

    stats = _scan(lib)
    assert stats.queued == 1
    after = _state(lib)
    assert "language" in after.jobs[-1][1]
    assert after.marked is False

    assert _scan(lib).unchanged == 1, "the file is looked at on every scan"


def test_a_scan_while_the_job_runs_leaves_the_mark(lib):
    """The job is still running, so its file cannot be queued yet; using the
    mark up here would lose the answer when the job ends."""
    job_id = lib.worker._claim_next()
    assert _answer(lib)["applied"] == 0

    stats = _scan(lib)

    assert stats.unchanged == 1
    assert _state(lib).marked is True
    lib.worker._finish_job(job_id, False, None, None, "stopped for the test")


# ── The worker claiming the file's job while an answer replaces it ───────────
#
# apply_language deletes the file's waiting job, then probes the file and
# queues a replacement, and commits only after the probe. A test of that
# race needs two things a real queue has and the fixture does not: the
# database in WAL mode, as the app's engine runs it, and a row newer than
# the file's job. SQLite hands a deleted row's id to the next insert when
# the deleted row was the newest, so without that row the replacement takes
# the deleted job's id, the racing UPDATE lands on it, and nothing fails.

def _like_a_real_queue(lib):
    """Switch the fixture's database to WAL and add another file's finished
    job after Movie.mkv's. Returns Movie.mkv's waiting job's id."""
    from app.database.models import MediaFile, QueueItem

    with lib.Session.kw["bind"].connect() as conn:
        assert conn.exec_driver_sql("PRAGMA journal_mode=WAL").scalar() == "wal"
    with lib.Session() as db:
        waiting = db.query(QueueItem).filter(QueueItem.status == "pending").one().id
        other = MediaFile(path=str(lib.library / "Other.mkv"), filename="Other.mkv",
                          directory=str(lib.library), size=1, mtime=1.0, status="success")
        db.add(other)
        db.flush()
        db.add(QueueItem(file_id=other.id, status="success"))
        db.commit()
    return waiting


def test_a_claim_during_an_answer_takes_the_job_the_answer_queues(lib, monkeypatch, caplog):
    """
    The worker selected Movie.mkv's job while the answer was probing the
    file, after the answer had deleted the job and before it committed. The
    claim's UPDATE waited for that commit and then matched no row: "Failed
    to claim next job" with a StaleDataError, as in a user's log after
    answering Review questions, and a claim that returned None, which the
    worker loop reads as an empty queue. The claim now takes the job the
    answer queued in its place.
    """
    import logging
    import threading

    from sqlalchemy import event

    import app.api.routes._language_review as review
    from app.database.models import MediaFile, QueueItem

    waiting = _like_a_real_queue(lib)

    # The answer stops before its probe, its delete flushed but not
    # committed, until the claim has selected the job and reached its UPDATE.
    real_process_file = review._process_file
    probing, go_on = threading.Event(), threading.Event()

    def process_file_after_the_claim(*args, **kwargs):
        probing.set()
        go_on.wait(10)
        return real_process_file(*args, **kwargs)
    monkeypatch.setattr(review, "_process_file", process_file_after_the_claim)

    replies = []
    answering = threading.Thread(target=lambda: replies.append(_answer(lib)))
    answering.start()
    assert probing.wait(10), "the answer never reached its probe"

    claiming = threading.get_ident()
    reached = []

    def release_the_answer(conn, cursor, statement, *rest):
        if (threading.get_ident() == claiming
                and statement.lstrip().upper().startswith("UPDATE QUEUE_ITEMS")):
            reached.append(statement)
            go_on.set()
    engine = lib.Session.kw["bind"]
    event.listen(engine, "before_cursor_execute", release_the_answer)
    caplog.set_level(logging.INFO)
    try:
        claimed = lib.worker._claim_next()
    finally:
        go_on.set()
        answering.join(30)
        event.remove(engine, "before_cursor_execute", release_the_answer)

    assert reached, "the claim never reached its UPDATE"
    assert replies == [{"applied": 1, "errors": [], "deferred": [], "dry_run": False}]
    with lib.Session() as db:
        movie = db.query(MediaFile).filter(MediaFile.filename == "Movie.mkv").one()
        assert db.get(QueueItem, waiting) is None, "the answer did not replace the job"
        assert claimed is not None, "the claim gave up with a job waiting"
        job = db.get(QueueItem, claimed)
        assert job is not None, f"claimed job {claimed}, which the answer had deleted"
        assert (job.file_id, job.status) == (movie.id, "processing")
    assert not [r for r in caplog.records if r.levelno >= logging.ERROR], (
        "a job that stopped waiting was logged as a failed claim")


class _WatchedLock:
    """
    A stand-in for revert_lock.CLAIM_LOCK that records which threads found
    it held and had to wait, and sets an event when one does: how a test
    knows a claim is blocked on the lock without sleeping to find out.
    """

    def __init__(self, on_wait):
        import threading

        self._lock = threading.Lock()
        self._on_wait = on_wait
        self.waited = []

    def acquire(self, blocking=True, timeout=-1):
        import threading

        if self._lock.acquire(blocking=False):
            return True
        if not blocking:
            return False
        self.waited.append(threading.get_ident())
        self._on_wait.set()
        return self._lock.acquire(True, timeout)

    def release(self):
        self._lock.release()

    def locked(self):
        return self._lock.locked()

    def __enter__(self):
        return self.acquire()

    def __exit__(self, *exc):
        self.release()


def _the_running_job_check(statement, parameters):
    return (statement.lstrip().upper().startswith("SELECT")
            and "FROM queue_items" in statement and "processing" in tuple(parameters))


@pytest.mark.parametrize("moment", ["after the running-job check", "before the delete"])
def test_a_claim_while_an_answer_replaces_the_job_waits_for_it(lib, monkeypatch, caplog, moment):
    """
    The answer checked that no job was running for the file, then read the
    waiting job and deleted it. A claim between the check and the delete
    took the job, and the answer deleted it while it ran: its result was
    never recorded, and the job the answer queued in its place could start
    beside it on the same file. A claim between the check and the read took
    it as well: nothing was then deleted and the file was marked, so the
    answer still landed, but only after a job decided without it, and the
    reply counted the answer applied rather than deferred.

    The claim is started at each moment, on its own thread, and must wait
    for the answer: the job the answer deletes is still waiting, and the one
    claimed is the replacement.
    """
    import logging
    import threading

    from sqlalchemy import event

    from app.core import revert_lock
    from app.database.models import MediaFile, QueueItem

    waiting = _like_a_real_queue(lib)
    settled = threading.Event()
    lock = _WatchedLock(on_wait=settled)
    monkeypatch.setattr(revert_lock, "CLAIM_LOCK", lock)

    answering, checked, claims, claimers = [], [], [], []

    def claim():
        try:
            claims.append(lib.worker._claim_next())
        finally:
            settled.set()

    def start_a_claim():
        claimer = threading.Thread(target=claim)
        claimers.append(claimer)
        claimer.start()
        # Until the claim is blocked on the lock, or has finished because
        # nothing was holding it.
        assert settled.wait(10), "the claim neither waited nor finished"

    def after_a_statement(conn, cursor, statement, parameters, context, executemany):
        if (threading.get_ident() in answering and not checked
                and _the_running_job_check(statement, parameters)):
            checked.append(statement)
            if moment == "after the running-job check":
                start_a_claim()

    # The answer's first write after its check. That deletes the waiting
    # job's planned actions, not the job itself, and from that statement on
    # the answer holds SQLite's write lock: a claim started any later would
    # wait for SQLite rather than for CLAIM_LOCK.
    def before_a_statement(conn, cursor, statement, parameters, context, executemany):
        if (moment == "before the delete" and threading.get_ident() in answering
                and checked and not claimers
                and statement.lstrip().upper().startswith("DELETE")):
            start_a_claim()

    engine = lib.Session.kw["bind"]
    event.listen(engine, "after_cursor_execute", after_a_statement)
    event.listen(engine, "before_cursor_execute", before_a_statement)
    replies = []

    def answer():
        answering.append(threading.get_ident())
        replies.append(_answer(lib))
    caplog.set_level(logging.INFO)
    try:
        answerer = threading.Thread(target=answer)
        answerer.start()
        answerer.join(30)
        for claimer in claimers:
            claimer.join(30)
    finally:
        event.remove(engine, "after_cursor_execute", after_a_statement)
        event.remove(engine, "before_cursor_execute", before_a_statement)

    assert claimers, "the answer never reached the moment"
    assert replies == [{"applied": 1, "errors": [], "deferred": [], "dry_run": False}]
    with lib.Session() as db:
        movie = db.query(MediaFile).filter(MediaFile.filename == "Movie.mkv").one()
        assert claims != [waiting], "the claim took the job the answer was about to replace"
        assert db.get(QueueItem, waiting) is None, "the answer did not replace the job"
        running = db.query(QueueItem).filter(QueueItem.file_id == movie.id,
                                             QueueItem.status == "processing").all()
        assert [j.id for j in running] == claims, (
            f"claimed {claims}, running for the file {[j.id for j in running]}")
    assert not [r for r in caplog.records if r.levelno >= logging.ERROR]
    assert lock.waited == [claimers[0].ident], (
        "the claim did not wait for the answer's check and delete")


def test_an_answer_after_a_job_lands_on_the_track_it_was_asked_about(database):
    """
    French, untagged and English audio, with undefined audio left to ask
    about. The job drops French, so the untagged track moves from stream 2
    to 1 and English takes stream 2. The question stayed at stream 2: the
    answer was stored against English and the follow-up job retagged it,
    leaving [und, jpn] where [jpn, eng] was asked for. Reproduced with real
    FFmpeg before scanner.rekey_language_flags existed.
    """
    from app.api.routes._language_review import ApplyRequest
    from app.api.routes.audio_language import apply_language
    from app.core import scanner
    from app.database.models import AppSetting, AudioLanguageFlag, MediaFile, QueueItem

    lib, factory, library = database, database.Session, database.library
    video = library / "Movie.mkv"
    subprocess.run([
        "ffmpeg", "-v", "error", "-y",
        "-f", "lavfi", "-i", "testsrc=size=64x36:rate=5:duration=1",
        "-f", "lavfi", "-i", "sine=frequency=300:duration=1",
        "-f", "lavfi", "-i", "sine=frequency=500:duration=1",
        "-f", "lavfi", "-i", "sine=frequency=700:duration=1",
        "-map", "0:v", "-map", "1:a", "-map", "2:a", "-map", "3:a",
        "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p", "-c:a", "aac",
        "-metadata:s:a:0", "language=fre", "-metadata:s:a:2", "language=eng",
        "-f", "matroska", str(video)], check=True)

    with factory() as db:
        for key, value in (("fix_undefined_language_audio", "always_ask"),):
            db.merge(AppSetting(key=key, value=json.dumps(value)))
        db.commit()
        scanner.scan_library(db, [str(library)], force_probe=True)
        assert [f.stream_index for f in db.query(AudioLanguageFlag)] == [2]
        assert [q.status for q in db.query(QueueItem)] == ["pending"]
    _run_next(lib)
    # MP4 writes "und" for an untagged track, so that is how it reads back.
    assert _audio_languages(_state(lib).path) == ["und", "eng"], "French was not dropped"

    with factory() as db:
        ids = [f.id for f in db.query(AudioLanguageFlag)]
        apply_language(ApplyRequest(flag_ids=ids, target_language="jpn"), db)
    _run_next(lib)

    with factory() as db:
        path = db.query(MediaFile).one().path
    assert _audio_languages(path) == ["jpn", "eng"]


def test_answering_one_of_two_untagged_tracks_leaves_the_other_asked_about(database):
    """
    Always Fix on audio, and two untagged audio tracks: neither is the
    file's only one, so both are asked about. One is answered Japanese, a
    job writes it, and a Re-check then decides the file again from the
    tracks the job left: one untagged track beside one tagged by an answer.
    The answer is counted with its track, so the file still has two to ask
    about, and the untagged one is not taken as Primary Language.

    Counted from the file's tags alone, the re-check saw a lone untagged
    track and queued a job tagging it English. Mutation-verified; see the
    Fix-rule section of test_decision.

    Prefer MP4 is off so the only job is the one the answer asks for.
    """
    from app.api.routes._language_review import ApplyRequest
    from app.api.routes.audio_language import apply_language
    from app.core import scanner
    from app.database.models import AppSetting, AudioLanguageFlag, QueueItem

    factory, library = database.Session, database.library
    video = library / "Movie.mkv"
    subprocess.run([
        "ffmpeg", "-v", "error", "-y",
        "-f", "lavfi", "-i", "testsrc=size=64x36:rate=5:duration=1",
        "-f", "lavfi", "-i", "sine=frequency=440:duration=1",
        "-f", "lavfi", "-i", "sine=frequency=880:duration=1",
        "-map", "0:v", "-map", "1:a", "-map", "2:a",
        "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-f", "matroska", str(video)], check=True)

    def flagged():
        with factory() as db:
            return sorted(f.stream_index for f in db.query(AudioLanguageFlag))

    def pending():
        with factory() as db:
            return db.query(QueueItem).filter(QueueItem.status == "pending").count()

    with factory() as db:
        for key, value in (("fix_undefined_language_audio", "always_fix"),
                           ("prefer_mp4_container", False)):
            db.merge(AppSetting(key=key, value=json.dumps(value)))
        db.commit()
        scanner.scan_library(db, [str(library)], force_probe=True)
    assert flagged() == [1, 2]
    assert pending() == 0, "Always Fix tagged one of two untagged tracks"

    with factory() as db:
        first = db.query(AudioLanguageFlag).filter(AudioLanguageFlag.stream_index == 1).one().id
        apply_language(ApplyRequest(flag_ids=[first], target_language="jpn"), db)
    assert pending() == 1, "the answer queued no job"
    _run_next(database)
    assert _audio_languages(video) == ["jpn", None]

    with factory() as db:
        scanner.scan_library(db, [str(library)], recheck=True)

    assert pending() == 0, "the re-check queued a job for the untagged track"
    assert flagged() == [2]
    assert _audio_languages(video) == ["jpn", None]
