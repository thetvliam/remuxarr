"""
The AC3 forge keeps the stored track list in step with the file.

A forge add or undo rewrites the file in place. The stored Track rows used
to stay as they were until the next scan, and a queued job decides from
those rows when it starts. Reproduced with real FFmpeg before this file
existed:

  • a job waiting for the file when the forge added an AC3 was decided from
    a track list with no AC3 in it, and the remux left the new AC3 out;
  • a job waiting for the file when the forge removed its AC3 mapped the
    removed stream and failed ("Failed to set value '0:3' for option
    'map'").

finish_forge_job now replaces the rows from a probe of the rewritten file,
in the same commit as the status change. A queued job is held back only
while the forge job is "processing", so rows written later would leave a
window for a job to start on the old ones. Only a race shows that
ordering, so it is argued at the write rather than tested here.

Verified by mutation, 6 applied, 6 killed. The baseline was the suite as it
stood (1979 tests) plus one mechanical change: the forge fixture in
test_forge_orchestration stubs finish_forge_job, and gained the keyword and
a stub for the new probe. All six survived that baseline and are killed
here:

  • no refresh at all                        → both real runs
  • refresh after an add but not an undo     → the undo run
  • new rows added beside the old ones       → both real runs
  • the worker passing no tracks             → both real runs
  • the worker probing the temp file         → both real runs
  • a probe failure not caught               → the unreadable-file test
"""
import asyncio
import json
import logging
import shutil
import subprocess
from types import SimpleNamespace

import pytest


needs_ffmpeg = pytest.mark.skipif(
    shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None,
    reason="ffmpeg/ffprobe not available",
)


class _WS:
    async def broadcast_json(self, *args, **kwargs):
        pass


def _streams(path):
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries",
         "stream=index,codec_type,codec_name,channels:stream_tags=language",
         "-of", "json", str(path)],
        capture_output=True, text=True, check=True)
    return [(s["codec_type"], s["codec_name"],
             (s.get("tags") or {}).get("language"))
            for s in json.loads(out.stdout)["streams"]]


def _video(path):
    """Video, English AAC 5.1 (what the forge works from), French SubRip."""
    srt = path.parent / "input.srt"
    srt.write_text("1\n00:00:00,000 --> 00:00:00,900\nbonjour\n")
    subprocess.run([
        "ffmpeg", "-v", "error", "-y",
        "-f", "lavfi", "-i", "testsrc=size=64x36:rate=5:duration=2",
        "-f", "lavfi", "-i", "sine=frequency=440:duration=2",
        "-i", str(srt),
        "-map", "0:v", "-map", "1:a", "-map", "2:s",
        "-metadata:s:a:0", "language=eng", "-metadata:s:s:0", "language=fre",
        "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-ac", "6", "-b:a", "64k", "-c:s", "srt",
        "-f", "matroska", str(path)], check=True)
    srt.unlink()
    return path


@pytest.fixture
def lib(tmp_path, monkeypatch):
    """
    A file-backed database every session in both pipelines points at, and
    Movie.mkv in a library folder. French subtitles are kept to begin with,
    so nothing is queued until a test drops them from the keep list.
    """
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    import app.core.forge as forge
    import app.core.worker as worker
    import app.database.session as session_module
    from app.config import settings as app_settings
    from app.database.models import AppSetting, Base

    # A file, not memory: the forge writes progress from executor threads
    # while its bookkeeping writes from another (see forge_lib in
    # test_revert_multiple_jobs for what a shared connection did).
    engine = create_engine(f"sqlite:///{tmp_path / 'remuxarr.db'}",
                           connect_args={"check_same_thread": False,
                                         "timeout": 30})
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine)
    for module in (worker, forge, session_module):
        monkeypatch.setattr(module, "SessionLocal", factory)
    monkeypatch.setattr(app_settings, "TEMP_DIR", str(tmp_path / "tmp"))
    monkeypatch.setattr(app_settings, "RECYCLE_DIR", str(tmp_path / "recycle"))
    (tmp_path / "recycle").mkdir()
    monkeypatch.setattr(worker, "_pick_temp_dir", lambda _p: str(tmp_path))
    monkeypatch.setattr(worker, "_load_forge_plex_notify_data", lambda _id: None)

    library = tmp_path / "library"
    library.mkdir()
    with factory() as db:
        db.merge(AppSetting(key="dry_run_mode", value=json.dumps(False)))
        # Stays Movie.mkv, so every step works on the same path.
        db.merge(AppSetting(key="prefer_mp4_container", value=json.dumps(False)))
        db.merge(AppSetting(key="keep_subtitle_languages",
                            value=json.dumps(["eng", "fre"])))
        db.commit()

    yield SimpleNamespace(Session=factory, worker=worker, forge=forge,
                          video=_video(library / "Movie.mkv"))
    engine.dispose()


def _queue(lib):
    """Re-decide the file the way a webhook does; returns the pending job, if any."""
    from app.core import scanner

    with lib.Session() as db:
        item = scanner.queue_single_file(db, str(lib.video))
        return item.id if item is not None and item.status == "pending" else None


def _drop_french(lib):
    from app.database.models import AppSetting

    with lib.Session() as db:
        db.merge(AppSetting(key="keep_subtitle_languages", value=json.dumps(["eng"])))
        db.commit()


def _forge(lib, *, undo_of=None):
    """Queue a forge add (or turn job `undo_of` into an undo, as the undo
    route does) and run it through the real worker step."""
    from app.database.models import Ac3ForgeJob, MediaFile

    with lib.Session() as db:
        if undo_of is None:
            media = db.query(MediaFile).one()
            job_id = lib.forge.queue_forge_job(db, media.id).id
        else:
            job = db.get(Ac3ForgeJob, undo_of)
            job.status, job.is_undo, job.progress = "undo_pending", True, 0.0
            db.commit()
            job_id = job.id
    assert asyncio.run(lib.worker._process_next_forge(_WS())) is True
    with lib.Session() as db:
        return job_id, db.get(Ac3ForgeJob, job_id).status


def _run_queued(lib):
    from app.database.models import QueueItem

    async def go():
        job_id = lib.worker._claim_next()
        assert job_id is not None, "no queued job to run"
        await lib.worker._run_and_broadcast(job_id, _WS(), asyncio.get_running_loop())
        return job_id
    job_id = asyncio.run(go())
    with lib.Session() as db:
        item = db.get(QueueItem, job_id)
        return item.status, item.error_message


def _stored(lib):
    from app.database.models import Track

    with lib.Session() as db:
        return [(t.stream_index, t.track_type, t.codec, t.language)
                for t in db.query(Track).order_by(Track.stream_index)]


def _probed(lib):
    from app.config import settings as app_settings
    from app.core.probe import extract_tracks, probe_file

    return [(t["stream_index"], t["track_type"], t["codec"], t["language"])
            for t in extract_tracks(probe_file(str(lib.video), app_settings.FFPROBE_PATH))]


@needs_ffmpeg
def test_a_job_waiting_when_the_forge_adds_its_ac3_keeps_it(lib):
    _queue(lib)                     # registers the file; nothing to do yet
    _drop_french(lib)
    assert _queue(lib) is not None, "dropping French queued nothing"

    _, status = _forge(lib)
    assert status == "success"
    assert _stored(lib) == _probed(lib), "the stored tracks are not the forged file's"

    assert _run_queued(lib) == ("success", None)
    assert _streams(lib.video) == [("video", "h264", None),
                                   ("audio", "aac", "eng"),
                                   ("audio", "ac3", "eng")]


@needs_ffmpeg
def test_a_job_waiting_when_the_forge_removes_its_ac3_runs(lib):
    _queue(lib)
    forge_job, status = _forge(lib)
    assert status == "success"
    _drop_french(lib)
    assert _queue(lib) is not None, "dropping French queued nothing"

    _, status = _forge(lib, undo_of=forge_job)
    assert status == "undone"
    assert _stored(lib) == _probed(lib), "the stored tracks are not the restored file's"

    assert _run_queued(lib) == ("success", None)
    assert _streams(lib.video) == [("video", "h264", None), ("audio", "aac", "eng")]


def test_a_file_that_cannot_be_probed_after_the_forge_gives_no_tracks(monkeypatch, caplog):
    """
    The rewrite succeeded, so the job must not fail over this: no tracks
    means finish_forge_job leaves the rows as they were, and the next scan
    re-probes the file because its size and mtime changed.
    """
    import app.core.forge as forge
    from app.core.probe import ProbeError

    def unreadable(path, ffprobe_path):
        raise ProbeError("moov atom not found")
    monkeypatch.setattr(forge, "probe_file", unreadable)

    with caplog.at_level(logging.WARNING, logger="app.core.forge"):
        assert forge.probe_tracks_after_forge("/media/Movie.mkv") is None
    assert "moov atom not found" in caplog.text
