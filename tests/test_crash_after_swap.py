"""
A job cut off by a crash at its swap, and what startup recovery makes of it.

A job's output replaces the file in one rename, then the job records what it
did: the revert point, the original removed after a conversion, the new
tracks, size and mtime. A crash between the two used to leave the job at
"processing", and recover_interrupted_jobs failed it. The next scan then read
the new file as a replacement and forgot its answers, a conversion left the
old MKV beside the new MP4, and the original saved for a revert sat in the
recycle bin with no record until the orphan pass deleted it. All measured
with a real job before the fix.

Now each swap is recorded just before it happens (QueueItem.swap_record),
the main file is swapped last, and recovery reads the record: the main
output in place means the job happened and is finished, and anything else
means nothing was replaced and what the job did put in place is taken out.

Each test runs a real FFmpeg job against a real file and stops it with an
exception no handler catches, the nearest thing to the process dying: after
the swap, part-way through it, or before it. Then recovery runs, as at
startup, and in most a scan after it.

Verified by mutation, 17 applied 17 killed, each surviving the whole
2322-test suite first (the last, written after the others, the same suite
with this file left out):

  • the main file swapped first           → part-way, and the order test
  • no record written                     → finished at startup
  • recovery not reading the record       → finished at startup
  • the main path existing taken for the
    swap having happened                  → before the swap
  • the original removed after a
    conversion without checking it        → the changed-original test
  • the revert point not recorded         → finished at startup
  • the job not finished                  → finished at startup
  • the job's subtitles left in place     → part-way
  • any file at a subtitle's name removed → the not-the-job's test
  • a file moved aside not moved back     → the moved-aside test
  • the unused revert copy kept           → part-way
  • the after-job steps not run           → the start_worker test
  • a claim keeping an earlier record     → the claim test
  • the finish keeping the record         → finished at startup
  • a record that cannot be stored
    swapping anyway                       → the cannot-be-stored test
  • the runner ignoring a declined swap   → the runner test
  • the job's staged copies left after a
    crash before the swap                 → before the swap

The column's upgrade step left out is recorded in test_schema_migration.py.
"""
import asyncio
import json
import os
import shutil
import subprocess
from types import SimpleNamespace

import pytest

needs_ffmpeg = pytest.mark.skipif(
    shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None,
    reason="ffmpeg/ffprobe not available",
)


class Crash(BaseException):
    """Stands in for the process dying: nothing catches a BaseException."""


class _WS:
    async def broadcast_json(self, *args, **kwargs):
        pass


@pytest.fixture
def lib(tmp_path, monkeypatch):
    """
    Movie.mkv: English, untagged and German audio and an English SRT, with
    the untagged track answered English. Revert points are on, so the job
    saves the original first. The job planned drops the German audio, tags
    the untagged track, and extracts the SRT beside the file.
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
        for key, value in {"dry_run_mode": False, "revert_enabled": True,
                           "fix_undefined_language_audio": "always_ask"}.items():
            db.merge(AppSetting(key=key, value=json.dumps(value)))
        db.commit()

    yield SimpleNamespace(Session=factory, worker=worker, library=library,
                          recycle=tmp_path / "recycle")
    engine.dispose()


def _queue(lib, *, mp4=True, srt_mode=None, existing_srt=None):
    """Make the file, scan it, answer it; returns the media path."""
    from app.api.routes._language_review import ApplyRequest
    from app.api.routes.audio_language import apply_language
    from app.core import scanner
    from app.database.models import AppSetting, AudioLanguageFlag, QueueItem

    source = lib.library.parent / "source.srt"
    source.write_text("1\n00:00:00,000 --> 00:00:00,900\nHello\n")
    media = lib.library / "Movie.mkv"
    subprocess.run([
        "ffmpeg", "-v", "error", "-y",
        "-f", "lavfi", "-i", "testsrc=size=64x36:rate=5:duration=1",
        "-f", "lavfi", "-i", "sine=frequency=440:duration=1",
        "-f", "lavfi", "-i", "sine=frequency=660:duration=1",
        "-f", "lavfi", "-i", "sine=frequency=880:duration=1",
        "-i", str(source),
        "-map", "0:v", "-map", "1:a", "-map", "2:a", "-map", "3:a", "-map", "4:s",
        "-metadata:s:a:0", "language=eng", "-metadata:s:a:2", "language=ger",
        "-metadata:s:s:0", "language=eng",
        "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-c:s", "srt", "-f", "matroska", str(media)], check=True)
    if existing_srt is not None:
        (lib.library / "Movie.en.srt").write_text(existing_srt)

    with lib.Session() as db:
        settings = {"prefer_mp4_container": mp4}
        if srt_mode:
            settings["existing_srt_handling"] = srt_mode
        for key, value in settings.items():
            db.merge(AppSetting(key=key, value=json.dumps(value)))
        db.commit()
        scanner.scan_library(db, [str(lib.library)], force_probe=True)
        ids = [f.id for f in db.query(AudioLanguageFlag)]
        apply_language(ApplyRequest(flag_ids=ids, target_language="eng"), db)
        (job,) = db.query(QueueItem).filter_by(status="pending").all()
        assert "Extract 1 subtitle" in job.reason
    return str(media)


def _crash_after_the_swap(monkeypatch):
    """
    The process dies the moment the swap has returned. Patched in its own
    context, so taking it away leaves the fixture's patches in place.
    """
    import app.core.ffmpeg as ffmpeg
    real = ffmpeg.run_staged_subprocess

    async def run(*args, **kwargs):
        result = await real(*args, **kwargs)
        if result.success:
            raise Crash()
        return result
    monkeypatch.setattr(ffmpeg, "run_staged_subprocess", run)


def _crash_at_rename(monkeypatch, after: int):
    """
    The process dies at the swap: after `after` of its renames have
    happened, so 0 is before the first. Its own patch context, as above.
    """
    import app.core.subprocess_runner as runner
    done = []

    class _Os:
        def __getattr__(self, name):
            return getattr(os, name)

        @staticmethod
        def replace(src, dst):
            if len(done) == after:
                raise Crash()
            os.replace(src, dst)
            done.append(dst)
    monkeypatch.setattr(runner, "os", _Os())
    return done


def _run(lib):
    async def go():
        job_id = lib.worker._claim_next()
        assert job_id is not None
        try:
            await lib.worker._run_job(job_id, _WS(), asyncio.get_running_loop())
        except Crash:
            return job_id
        raise AssertionError("the job was not cut off")
    return asyncio.run(go())


def _recover(lib):
    finished = []
    with lib.Session() as db:
        lib.worker.recover_interrupted_jobs(db, finished=finished)
    return finished


def _scan(lib):
    from app.core import scanner
    with lib.Session() as db:
        scanner.scan_library(db, [str(lib.library)])


def _job(lib, job_id):
    from app.database.models import QueueItem
    with lib.Session() as db:
        job = db.get(QueueItem, job_id)
        return SimpleNamespace(status=job.status, error=job.error_message,
                               record=job.swap_record)


def _media(lib):
    from app.database.models import MediaFile
    with lib.Session() as db:
        return [(m.filename, m.status, m.audio_language_overrides)
                for m in db.query(MediaFile).order_by(MediaFile.id)]


def _revert_points(lib):
    from app.database.models import RevertPoint
    with lib.Session() as db:
        return [r.sidecar_path for r in db.query(RevertPoint)]


def _fingerprint(path):
    stat = os.stat(path)
    return stat.st_size, stat.st_mtime


def _subtitle_streams(path):
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "s",
         "-show_entries", "stream=index", "-of", "json", path],
        capture_output=True, text=True, check=True)
    return len(json.loads(out.stdout)["streams"])


# ── After the swap: finished at startup ──────────────────────────────────────

@needs_ffmpeg
@pytest.mark.parametrize("mp4", [True, False], ids=["converted", "stayed_mkv"])
def test_a_crash_after_the_swap_is_finished_at_startup(lib, monkeypatch, mp4):
    _queue(lib, mp4=mp4)
    with monkeypatch.context() as crash:
        _crash_after_the_swap(crash)
        job_id = _run(lib)
    assert _job(lib, job_id).status == "processing"

    assert _recover(lib) == [job_id]

    job = _job(lib, job_id)
    assert (job.status, job.error, job.record) == ("success", None, None)
    name = "Movie.mp4" if mp4 else "Movie.mkv"
    assert sorted(os.listdir(lib.library)) == ["Movie.en.srt", name], (
        "the original was left beside the converted file" if mp4 else "")
    (point,) = _revert_points(lib)
    assert os.path.exists(point), "the revert point names a copy that is gone"

    _scan(lib)

    ((filename, status, answers),) = _media(lib)
    assert (filename, status) == (name, "processed")
    assert answers, "the next scan forgot the file's answers"


@needs_ffmpeg
def test_an_original_that_changed_while_stopped_is_not_removed(lib, monkeypatch):
    """
    After a conversion the job removes the original, but only while it is
    still the file the job read. Something can replace it while Remuxarr is
    down, and that file is not the job's to delete.
    """
    media = _queue(lib, mp4=True)
    with monkeypatch.context() as crash:
        _crash_after_the_swap(crash)
        job_id = _run(lib)
    with open(media, "ab") as f:
        f.write(b"an upgrade arrived while Remuxarr was stopped")

    assert _recover(lib) == [job_id]

    assert os.path.exists(media)
    assert _job(lib, job_id).status == "success"


# ── At the swap: nothing replaced ────────────────────────────────────────────

@needs_ffmpeg
def test_a_crash_part_way_through_the_swap_keeps_every_subtitle(lib, monkeypatch):
    """
    One rename done: the extracted subtitle, which goes before the main
    file. The video still holds the subtitle, so recovery takes the sidecar
    away again, as a job that ends short of success does, and the saved
    original no revert point will name goes too.

    The main file used to go first. Cut off here, the video had lost the
    subtitle and the sidecar was still a .part, which the startup clean-up
    deletes.
    """
    media = _queue(lib, mp4=True)
    before = _fingerprint(media)
    with monkeypatch.context() as crash:
        done = _crash_at_rename(crash, after=1)
        job_id = _run(lib)
    assert [os.path.basename(p) for p in done] == ["Movie.en.srt"]

    assert _recover(lib) == []

    job = _job(lib, job_id)
    assert job.status == "failed"
    assert "before its output replaced anything" in job.error
    assert not os.path.exists(lib.library / "Movie.en.srt")
    assert not os.path.exists(lib.library / "Movie.mp4")
    assert _fingerprint(media) == before
    assert _subtitle_streams(media) == 1
    assert os.listdir(lib.recycle) == [], "the unused revert copy was kept"
    assert _revert_points(lib) == []


@needs_ffmpeg
def test_a_crash_before_the_swap_replaces_nothing(lib, monkeypatch):
    """
    Staying MKV, the main output's final path is the original's, so the
    file being there says nothing: only its matching the staged copy does.
    """
    media = _queue(lib, mp4=False)
    before = _fingerprint(media)
    with monkeypatch.context() as crash:
        _crash_at_rename(crash, after=0)
        job_id = _run(lib)

    assert _recover(lib) == []

    assert _job(lib, job_id).status == "failed"
    assert _fingerprint(media) == before
    assert sorted(os.listdir(lib.library)) == ["Movie.mkv"]


@needs_ffmpeg
def test_a_subtitle_file_that_is_not_the_jobs_is_left(lib, monkeypatch):
    """
    Under Replace a subtitle Bazarr wrote is overwritten at the swap. Cut
    off before it, the file at that name is still Bazarr's, and recovery
    removes only what matches the copy the job staged.
    """
    _queue(lib, mp4=False, srt_mode="replace", existing_srt="FROM BAZARR")
    with monkeypatch.context() as crash:
        _crash_at_rename(crash, after=0)
        _run(lib)

    _recover(lib)

    assert (lib.library / "Movie.en.srt").read_text() == "FROM BAZARR"


@needs_ffmpeg
def test_a_subtitle_moved_aside_goes_back_to_its_name(lib, monkeypatch):
    """
    Under Embedded First the file holding the name is moved to the next
    number just before the swap. Cut off then, it goes back.
    """
    _queue(lib, mp4=False, srt_mode="embedded_first", existing_srt="FROM BAZARR")
    with monkeypatch.context() as crash:
        _crash_at_rename(crash, after=0)
        _run(lib)
    assert (lib.library / "Movie.en.2.srt").read_text() == "FROM BAZARR"

    _recover(lib)

    assert (lib.library / "Movie.en.srt").read_text() == "FROM BAZARR"
    assert not (lib.library / "Movie.en.2.srt").exists()


# ── The wiring ───────────────────────────────────────────────────────────────

@needs_ffmpeg
def test_start_worker_runs_the_after_job_steps_for_a_finished_job(lib, monkeypatch):
    """
    Sonarr, Radarr and Plex are told after a success, and a converted file
    is a new path to them. A job finished at startup gets the same.
    """
    _queue(lib, mp4=True)
    with monkeypatch.context() as crash:
        _crash_after_the_swap(crash)
        job_id = _run(lib)
    import app.core.worker as worker

    after = []

    async def record(job, ws_manager, loop):
        after.append(job)

    async def idle():
        pass

    monkeypatch.setattr(worker, "_after_job", record)
    monkeypatch.setattr(worker, "_loop", idle)

    async def boot():
        await worker.start_worker()
        await asyncio.sleep(0)
        await asyncio.gather(*list(worker._startup_tasks))
        if worker._worker_task:
            worker._worker_task.cancel()
    asyncio.run(boot())

    assert after == [job_id]


def test_a_claim_clears_a_record_an_earlier_attempt_left(lib):
    from app.database.models import MediaFile, QueueItem
    with lib.Session() as db:
        media = MediaFile(path="/m/Old.mkv", filename="Old.mkv", directory="/m",
                          size=1, mtime=1.0, status="queued")
        db.add(media)
        db.flush()
        db.add(QueueItem(file_id=media.id, status="pending",
                         swap_record=json.dumps({"main": "/m/Old.mkv"})))
        db.commit()

    job_id = lib.worker._claim_next()

    assert _job(lib, job_id).record is None


def test_a_record_that_cannot_be_stored_stops_the_swap(monkeypatch):
    """
    A swap without its record would leave a crash straight after it as
    unrecoverable as before the record existed, so the swap is declined.
    """
    import app.core.worker as worker

    class _Session:
        def get(self, *_):
            return SimpleNamespace(swap_record=None)

        def commit(self):
            raise RuntimeError("database is locked")

        def rollback(self):
            pass

        def close(self):
            pass

    monkeypatch.setattr(worker, "SessionLocal", _Session)

    error = worker._write_swap_record(1, "{}")

    assert error and "Nothing was replaced" in error


def test_the_runner_swaps_the_main_file_last_and_honours_a_decline(tmp_path):
    """
    on_swap_ready is handed the outputs in the order they are swapped, the
    first output (the main file) last, and an error from it leaves every
    destination as it was.
    """
    from app.core.subprocess_runner import StagedOutput, run_staged_subprocess

    temps = [tmp_path / "main.tmp", tmp_path / "side.tmp"]
    finals = [tmp_path / "Movie.mkv", tmp_path / "Movie.en.srt"]
    finals[0].write_text("ORIGINAL")
    command = ["sh", "-c", f"printf new > '{temps[0]}'; printf sub > '{temps[1]}'"]
    outputs = [StagedOutput(temp_path=str(t), final_path=str(f))
               for t, f in zip(temps, finals)]
    seen = []

    async def decline(staged):
        seen.extend(final for _part, final in staged)
        return "declined"

    result = asyncio.run(run_staged_subprocess(command, outputs, on_swap_ready=decline))

    assert seen == [str(finals[1]), str(finals[0])]
    assert result.success is False and result.error == "declined"
    assert finals[0].read_text() == "ORIGINAL"
    assert not finals[1].exists()
    assert sorted(p.name for p in tmp_path.iterdir()) == ["Movie.mkv"]
