"""
A conversion never overwrites a file already at its new name.

A container change writes to a new name: Movie.mkv becomes Movie.mp4.
determine_output_path swaps the extension and nothing checked the disk, so
a Movie.mp4 already beside the MKV was overwritten by the conversion and
the MKV then deleted. The revert point stores only the converted file's
tracks; the overwritten file could not be brought back. The output-name
check that test_source_changed_mid_job.py covers did not see it: that one
records what is at the name when the job starts and stops only if it
changes, and a file that was there all along does not.

Libraries Sonarr and Radarr manage rarely hold both, since each keeps one
file per episode or film. A library managed by hand easily can.

The job is now refused before anything is captured, run or written, as a
failure that counts towards failure emails: only the user can say which
file to keep. A dry run reports the same failure, so the collision shows
in the preview. One file under two names is not a collision: on a
case-insensitive share Movie.MP4 and Movie.mp4 are the same file, and a
hard link stands in for that here, on a case-sensitive test filesystem.

The second half is the record cleanup after a conversion. A leftover
record at the new name was removed with a bare delete. Its job history
went with it, through the ORM's cascade, but a language review flag made
the delete fail outright: the ORM tried to set the flag's file_id to NULL,
which the column does not allow. The job then ended as "Finalisation
failed", after the conversion had already replaced the file. A revert
point was unlinked without being marked detached. It now goes through
_delete_media_file_and_related, as a scan removes a deleted file's record.
Found by this file's last test, which failed against the old code on the
flag and, with the flag left out, on the revert point.

These run the real worker and real FFmpeg against real files, with the
harness test_source_changed_mid_job.py uses.

Confirmed unprotected before this file was written: six mutations, each
run against the whole 1834-test suite, all six survived. The check
removed from the real run; removed from the dry run; a collision recorded
as a success; the leftover record removed with a bare delete again; the
same-file exemption removed; and the exemption for a job writing to its
own name removed. Five are killed here.

The sixth, the own-name exemption, is equivalent while the source is on
disk: a file is the same file as itself, so os.path.samefile exempts it
too. It stays because samefile has to stat the source, and if the source
vanished at that moment an in-place job would be refused with a message
about a collision that does not exist.
"""
import asyncio
import hashlib
import os
import shutil
import subprocess
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

import app.core.worker as worker
import app.database.session as session_module
from app.config import settings as app_settings
from app.core import scanner
from app.database.models import (AppSetting, AudioLanguageFlag, Base, MediaFile,
                                 NotificationState, QueueItem, RevertPoint)

pytestmark = pytest.mark.skipif(
    shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None,
    reason="needs real ffmpeg and ffprobe",
)


# ── Harness ──────────────────────────────────────────────────────────────────

@pytest.fixture
def lib(tmp_path, monkeypatch):
    engine = create_engine(f"sqlite:///{tmp_path / 'remuxarr.db'}",
                           connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine)
    monkeypatch.setattr(worker, "SessionLocal", factory)
    monkeypatch.setattr(session_module, "SessionLocal", factory)
    monkeypatch.setattr(app_settings, "TEMP_DIR", str(tmp_path / "tmp"))
    monkeypatch.setattr(app_settings, "RECYCLE_DIR", str(tmp_path / "recycle"))
    (tmp_path / "recycle").mkdir()
    (tmp_path / "library").mkdir()

    # FFmpeg is recorded, not replaced: the refusal has to happen before it.
    runs = []
    for name in ("execute_ffmpeg", "execute_ffmpeg_combined"):
        real = getattr(worker, name)

        def record(real=real, name=name):
            async def run(**kwargs):
                runs.append(name)
                return await real(**kwargs)
            return run

        monkeypatch.setattr(worker, name, record())

    rig = SimpleNamespace(Session=factory, root=tmp_path,
                          library=tmp_path / "library", runs=runs)
    _settings(rig, dry_run_mode=False)
    yield rig
    engine.dispose()


def _settings(rig, **values):
    import json
    with rig.Session() as db:
        for key, value in values.items():
            db.merge(AppSetting(key=key, value=json.dumps(value)))
        db.commit()


def _make(path, *, title="OLD", seconds=1):
    """A real file with English audio; under the defaults an MKV is converted."""
    path = str(path)
    subprocess.run(
        ["ffmpeg", "-v", "error", "-y",
         "-f", "lavfi", "-i", f"testsrc=size=160x120:rate=10:duration={seconds}",
         "-f", "lavfi", "-i", f"sine=frequency=440:duration={seconds}",
         "-map", "0:v", "-map", "1:a", "-metadata:s:a:0", "language=eng",
         "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p",
         "-c:a", "aac", "-metadata", f"title={title}", path],
        check=True)
    return path


def _sha(path):
    with open(path, "rb") as f:
        return hashlib.sha256(f.read()).hexdigest()


class _WS:
    async def broadcast_json(self, payload):
        pass


def _queue(rig, path):
    with rig.Session() as db:
        item = scanner.queue_single_file(db, str(path))
        assert item is not None and item.status == "pending", "fixture queued nothing"
        return item.id


def _run_next(rig):
    async def go():
        job_id = worker._claim_next()
        assert job_id is not None
        await worker._run_and_broadcast(job_id, _WS(), asyncio.get_running_loop())
    asyncio.run(go())


def _job(rig, job_id):
    with rig.Session() as db:
        job = db.get(QueueItem, job_id)
        return SimpleNamespace(status=job.status, error=job.error_message or "",
                               file_id=job.file_id)


def _failures_counted(rig):
    with rig.Session() as db:
        state = db.get(NotificationState, 1)
        return state.consecutive_failures if state else 0


def _anything_left_behind(rig):
    found = []
    for folder in (rig.root / "tmp", rig.root / "recycle", rig.library):
        for dirpath, _dirs, files in os.walk(folder):
            found += [os.path.join(dirpath, f) for f in files
                      if f.endswith((".part", ".remuxarr_tmp", ".remuxarr_revert"))]
    return found


REFUSAL = ("Movie (2020).mp4 already exists beside Movie (2020).mkv, and "
           "converting would overwrite it. Move or rename one of them, then retry.")


# ── A file at the new name ───────────────────────────────────────────────────

def test_a_conversion_onto_an_existing_file_is_refused_and_changes_nothing(lib):
    source = _make(lib.library / "Movie (2020).mkv")
    other = _make(lib.library / "Movie (2020).mp4", title="OTHER", seconds=2)
    shas = (_sha(source), _sha(other))
    job_id = _queue(lib, source)

    _run_next(lib)

    job = _job(lib, job_id)
    assert (job.status, job.error) == ("failed", REFUSAL)
    assert (_sha(source), _sha(other)) == shas
    assert lib.runs == [], "FFmpeg ran"
    assert _anything_left_behind(lib) == []
    with lib.Session() as db:
        assert db.query(RevertPoint).count() == 0
    assert _failures_counted(lib) == 1


def test_a_dry_run_reports_the_same_refusal(lib):
    _settings(lib, dry_run_mode=True)
    source = _make(lib.library / "Movie (2020).mkv")
    _make(lib.library / "Movie (2020).mp4", title="OTHER")
    job_id = _queue(lib, source)

    _run_next(lib)

    assert (_job(lib, job_id).status, _job(lib, job_id).error) == ("failed", REFUSAL)


def test_a_dry_run_with_the_name_free_is_an_ordinary_preview(lib):
    _settings(lib, dry_run_mode=True)
    source = _make(lib.library / "Movie (2020).mkv")
    job_id = _queue(lib, source)

    _run_next(lib)

    assert _job(lib, job_id).status == "dry_run"


def test_moving_the_other_file_away_and_retrying_converts(lib):
    source = _make(lib.library / "Movie (2020).mkv")
    other = _make(lib.library / "Movie (2020).mp4", title="OTHER")
    _queue(lib, source)
    _run_next(lib)
    os.rename(other, lib.library / "Movie (2020) - other.mp4")

    job_id = _queue(lib, source)
    _run_next(lib)

    assert _job(lib, job_id).status == "success", _job(lib, job_id).error
    assert not os.path.exists(source)
    assert os.path.exists(lib.library / "Movie (2020).mp4")


# ── Not a collision ──────────────────────────────────────────────────────────

def test_a_job_writing_to_its_own_name_runs_as_before(lib):
    """An MP4 rewritten in place for fast start: output name and input name agree."""
    source = _make(lib.library / "Movie (2020).mp4")
    job_id = _queue(lib, source)

    _run_next(lib)

    assert _job(lib, job_id).status == "success", _job(lib, job_id).error
    assert lib.runs


def test_the_same_file_under_the_new_name_is_not_a_collision(lib):
    """
    On a case-insensitive share, Movie.MP4 beside Movie.mkv can be the file
    itself. A hard link is the same thing on this filesystem.
    """
    source = _make(lib.library / "Movie (2020).mkv")
    os.link(source, lib.library / "Movie (2020).mp4")
    job_id = _queue(lib, source)

    _run_next(lib)

    assert _job(lib, job_id).status == "success", _job(lib, job_id).error


# ── The leftover record at the new name ──────────────────────────────────────

def test_a_leftover_record_at_the_new_name_goes_with_everything_on_it(lib):
    """
    A record at Movie.mp4 whose file is not on disk: left from an earlier
    conversion whose output was deleted since. It had a job, a review flag
    and a revert point. The job's own record takes the name; the leftover's
    rows go with it, and its revert point is detached, not deleted.
    """
    target = str(lib.library / "Movie (2020).mp4")
    with lib.Session() as db:
        old = MediaFile(path=target, filename=os.path.basename(target),
                        directory=str(lib.library), size=1, mtime=1.0,
                        status="processed")
        db.add(old)
        db.flush()
        db.add(QueueItem(file_id=old.id, status="success"))
        db.add(AudioLanguageFlag(file_id=old.id, stream_index=1, detected_language="und"))
        db.add(RevertPoint(file_id=old.id, sidecar_path="/recycle/old.remuxarr_revert",
                           original_path=target, manifest="{}"))
        db.commit()
        old_id = old.id

    source = _make(lib.library / "Movie (2020).mkv")
    job_id = _queue(lib, source)
    _run_next(lib)

    job = _job(lib, job_id)
    assert job.status == "success", job.error
    with lib.Session() as db:
        assert db.get(MediaFile, old_id) is None
        assert db.query(QueueItem).filter(QueueItem.file_id == old_id).count() == 0
        assert db.query(AudioLanguageFlag).filter(
            AudioLanguageFlag.file_id == old_id).count() == 0
        point = db.query(RevertPoint).filter(
            RevertPoint.sidecar_path == "/recycle/old.remuxarr_revert").one()
        assert (point.file_id, point.detached_at is not None) == (None, True)
        assert db.get(MediaFile, job.file_id).path == target
