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

The test at the end is about an answer given after the job rather than
during it: the question had to follow its track through the job first. Its
mutation record is in test_job_finalisation.py, beside the tests that pin
the re-key itself; three of those ten mutants are killed here as well (the
re-key not called, the old track list read only with answers, and audio
questions left out).
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
    Movie.mkv with two untagged audio tracks, scanned: a job is queued to
    convert it to MP4, and each track is a question in Audio Language Review.
    """
    from app.core import scanner
    from app.database.models import AudioLanguageFlag, QueueItem

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
        for key, value in (("fix_undefined_language_audio", "always_ask"),
                           ("undefined_language_mode_audio", "all_undefined")):
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
