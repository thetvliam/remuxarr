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

The forge's own record of the file went stale the same way. An add ran on
the stream index and audio count stored when it was queued, and a job that
removed an audio track in between shifted the streams under it. With the
French stereo track dropped, the stored index named the Commentary track:
the forge appended a second copy of it, built no AC3, and reported
success. An add now re-reads the file when it starts, works from the
first AAC 5.1 track it finds, the rule queue_forge_job applies, and stores
what it found on the job. With no AAC 5.1 track left it fails and leaves
the file alone.

Verified by mutation, 7 applied, 7 killed. The baseline was the suite with
commit A's tests plus two changes the re-read forced: the faststart test in
test_forge_and_staging loads an add from a file that is not real media and
now stubs the probe, and the forge helper in test_revert_multiple_jobs
forged from the English AAC 5.1 where a Japanese one came first, which
queue_forge_job never does, so it takes the first and two tests expect a
Japanese AC3. Picking the last AAC 5.1 instead of the first is killed by
those two. The other six survived the baseline and are killed here:

  • no re-read at all                        → the shifted-tracks run, the
                                               no-AAC 5.1 run
  • the stored stream index kept             → the shifted-tracks run
  • the stored audio count kept              → the shifted-tracks run
  • the new values not committed to the job  → the shifted-tracks run
  • no refusal with no AAC 5.1 left          → the no-AAC 5.1 run
  • a probe failure not caught               → the unprobeable-file run

The forge also read an empty stored container as "mkv", and wrote
Matroska into the file whatever it was. The scanner stores an empty
container only when ffprobe reports no format name, so this guards a
value that should not arrive rather than one any file was seen to produce.
With the guess gone the command builders refuse it, as they refuse any
container they do not know, and the job fails with the file untouched.

Verified by mutation, 1 applied, 1 killed. Putting `or "mkv"` back is the
code as it stood, which the suite then (2044 tests) passed. The
no-container run kills it: on that code the forge reported success and
left Matroska in the .mp4.
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


def _audio(path):
    """(codec, channels, language, title) of each audio stream, in order."""
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "a", "-show_entries",
         "stream=codec_name,channels:stream_tags=language,title",
         "-of", "json", str(path)],
        capture_output=True, text=True, check=True)
    return [(s["codec_name"], s["channels"], (s.get("tags") or {}).get("language"),
             (s.get("tags") or {}).get("title"))
            for s in json.loads(out.stdout)["streams"]]


ENGLISH_51 = ("eng", 6, None)


def _video(path, audio=(ENGLISH_51,)):
    """Video, an AAC track for each (language, channels, title) in `audio`,
    and a French SubRip track."""
    srt = path.parent / "input.srt"
    srt.write_text("1\n00:00:00,000 --> 00:00:00,900\nbonjour\n")
    cmd = ["ffmpeg", "-v", "error", "-y",
           "-f", "lavfi", "-i", "testsrc=size=64x36:rate=5:duration=2"]
    for n, _ in enumerate(audio):
        cmd += ["-f", "lavfi", "-i", f"sine=frequency={440 + 110 * n}:duration=2"]
    cmd += ["-i", str(srt), "-map", "0:v"]
    for n, (language, channels, title) in enumerate(audio):
        cmd += ["-map", f"{n + 1}:a", f"-ac:a:{n}", str(channels),
                f"-metadata:s:a:{n}", f"language={language}"]
        if title:
            cmd += [f"-metadata:s:a:{n}", f"title={title}"]
    cmd += ["-map", f"{len(audio) + 1}:s", "-metadata:s:s:0", "language=fre",
            "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p",
            "-c:a", "aac", "-b:a", "64k", "-c:s", "srt",
            "-f", "matroska", str(path)]
    subprocess.run(cmd, check=True)
    srt.unlink()
    return path


@pytest.fixture
def lib(tmp_path, monkeypatch):
    """
    A file-backed database every session in both pipelines points at, and
    Movie.mkv in a library folder. French audio and subtitles are kept to
    begin with, so nothing is queued until a test drops them.
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
        for key in ("keep_audio_languages", "keep_subtitle_languages"):
            db.merge(AppSetting(key=key, value=json.dumps(["eng", "fre"])))
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


def _drop_french(lib, key="keep_subtitle_languages"):
    from app.database.models import AppSetting

    with lib.Session() as db:
        db.merge(AppSetting(key=key, value=json.dumps(["eng"])))
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


# ── An add works from the file as it is when it starts ──────────────────────

def _forge_job(lib, job_id):
    from app.database.models import Ac3ForgeJob

    with lib.Session() as db:
        job = db.get(Ac3ForgeJob, job_id)
        return SimpleNamespace(status=job.status, error=job.error_message,
                               aac_stream_index=job.aac_stream_index,
                               audio_track_count=job.audio_track_count)


def _queue_forge(lib):
    from app.database.models import MediaFile

    with lib.Session() as db:
        return lib.forge.queue_forge_job(db, db.query(MediaFile).one().id).id


def _run_forge(lib):
    assert asyncio.run(lib.worker._process_next_forge(_WS())) is True


@needs_ffmpeg
def test_an_add_queued_before_a_job_shifted_the_tracks_builds_the_ac3(lib):
    _video(lib.video, audio=[("fre", 2, None), ENGLISH_51, ("eng", 2, "Commentary")])
    _queue(lib)
    forge_job = _queue_forge(lib)
    assert _forge_job(lib, forge_job).aac_stream_index == 2

    _drop_french(lib, "keep_audio_languages")
    assert _queue(lib) is not None, "dropping French audio queued nothing"
    assert _run_queued(lib) == ("success", None)

    _run_forge(lib)
    job = _forge_job(lib, forge_job)
    assert job.status == "success", job.error
    assert _audio(lib.video) == [("aac", 6, "eng", None),
                                 ("aac", 2, "eng", "Commentary"),
                                 ("ac3", 6, "eng", None)]
    assert (job.aac_stream_index, job.audio_track_count) == (1, 2), \
        "the job does not record what the run used"


@needs_ffmpeg
def test_an_add_with_no_aac_51_left_fails_and_changes_nothing(lib):
    _video(lib.video, audio=[("eng", 2, None), ("fre", 6, None)])
    _queue(lib)
    forge_job = _queue_forge(lib)

    _drop_french(lib, "keep_audio_languages")
    assert _queue(lib) is not None, "dropping French audio queued nothing"
    assert _run_queued(lib) == ("success", None)
    before = lib.video.read_bytes()

    _run_forge(lib)
    job = _forge_job(lib, forge_job)
    assert job.status == "failed"
    assert "no longer has an AAC 5.1 track" in job.error
    assert lib.video.read_bytes() == before


@needs_ffmpeg
def test_an_add_that_cannot_probe_the_file_fails_and_changes_nothing(lib, monkeypatch):
    from app.core.probe import ProbeError

    _queue(lib)
    forge_job = _queue_forge(lib)
    before = lib.video.read_bytes()

    def unreadable(path, ffprobe_path):
        raise ProbeError("moov atom not found")
    monkeypatch.setattr(lib.forge, "probe_file", unreadable)

    _run_forge(lib)
    job = _forge_job(lib, forge_job)
    assert job.status == "failed"
    assert "moov atom not found" in job.error
    assert lib.video.read_bytes() == before


def _mp4(path):
    """A real MP4: video and one English AAC 5.1 track."""
    subprocess.run(
        ["ffmpeg", "-v", "error", "-y",
         "-f", "lavfi", "-i", "testsrc=size=64x36:rate=5:duration=2",
         "-f", "lavfi", "-i", "sine=frequency=440:duration=2",
         "-map", "0:v", "-map", "1:a", "-ac", "6",
         "-metadata:s:a:0", "language=eng",
         "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p",
         "-c:a", "aac", "-b:a", "64k", "-f", "mp4", str(path)],
        check=True)
    return path


@needs_ffmpeg
def test_an_add_for_a_file_with_no_stored_container_fails_and_changes_nothing(lib):
    """
    An empty container is what the scanner stores when ffprobe reports no
    format name. The forge read it as "mkv" and wrote Matroska into the
    file whatever it was, here an .mp4.
    """
    from app.core import scanner
    from app.database.models import MediaFile

    mp4 = _mp4(lib.video.parent / "Clip.mp4")
    with lib.Session() as db:
        scanner.queue_single_file(db, str(mp4))
        media = db.query(MediaFile).filter(MediaFile.path == str(mp4)).one()
        media.container = ""
        db.commit()
        forge_job = lib.forge.queue_forge_job(db, media.id).id
    before = mp4.read_bytes()

    _run_forge(lib)
    job = _forge_job(lib, forge_job)
    assert job.status == "failed"
    assert "Unsupported container ''" in job.error
    assert mp4.read_bytes() == before
