"""
QuickTime movies stay QuickTime movies.

ffprobe names a QuickTime file and an MP4 alike ("mov,mp4,m4a,3gp,3g2,mj2"),
and both normalise to the "mp4" container, so every .mov was written with
FFmpeg's MP4 muxer under its .mov name. Reproduced with real FFmpeg before
this file existed, on a camera-style .mov with PCM audio: a job and a revert
of it both gave back a file whose major brand was "isom" rather than "qt  ",
whose PCM audio carried the ISO "ipcm" sample entry rather than QuickTime's
"sowt", which many players cannot play, and whose video had gained an "und"
language. The packets were the same. AC3 Forge wrote its add and undo the
same way.

A file whose major brand is "qt  " (probe.is_quicktime) was then written
with FFmpeg's QuickTime muxer (ffmpeg.output_muxer) by a job, a revert and
Forge alike. A QuickTime movie has since become one of the files left as
they are: no job or Forge add starts on one (probe.recording_signs,
test_recording_signs.py), and the job's and the add's QuickTime handling
has gone. A revert point or a forged file made before then is still
restored, or undone, as QuickTime: a revert from the brand the revert
point recorded for the original, a Forge undo from its own probe.

The revert and the undo here run on what a released version left, which
the helpers below make (_left_by_a_job, _left_by_a_forge_add): the file
written with the QuickTime muxer, as the last release to process one
wrote it, or with the MP4 muxer, as the releases before that did, and the
revert point or the Forge job the run recorded. Then the real revert, or
the real worker's undo, runs on it.

Verified by mutation, 12 applied, 12 killed. Each was first run against
the whole suite as it stood (2459 tests, with test_job_preflight.py's
plain-data test expecting the new quicktime key), and all 12 survived it.
Four were of the job's and the add's QuickTime handling, which has since
gone, as no job or add starts on a QuickTime movie: the worker never
reporting QuickTime, execute_ffmpeg not passing it, the worker not
passing it to execute_ffmpeg, and the add not passing it. The other 8,
run again against the whole suite when the revert and the undo moved to
the helpers and again when that handling went (2818 and 2810 tests), are
killed by:

  • output_muxer ignoring quicktime          → the rule, and the revert,
                                               both kinds
  • output_muxer ignoring the container      → the rule
  • is_quicktime always False                → the brand, and forge undo
  • the restore ignoring the recorded brand  → the revert, both kinds
  • the restore's fast start for MP4 only    → the revert, both kinds
  • Forge's muxer ignoring quicktime         → forge undo
  • Forge's job data never QuickTime         → forge undo
  • the undo not passing it                  → forge undo

The revert takes the brand from its point, not from is_quicktime, so
is_quicktime always False leaves it as it is.

Forge's titles were then found lost on a QuickTime file: _title_args wrote
them back for the "mp4" muxer only. The forge test now gives its tracks
titles. Its mutant, titles for "mp4" only again, survived the full suite
before that and is killed by forge undo alone (forge add and undo before
the helpers); titles for "mov" only is killed by test_mp4_track_titles.py.
"""
import asyncio
import json
import os
import shutil
import subprocess
from types import SimpleNamespace

import pytest

from app.core.ffmpeg import output_muxer
from app.core.probe import is_quicktime, probe_file

needs_ffmpeg = pytest.mark.skipif(
    shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None,
    reason="ffmpeg/ffprobe not available",
)


# ── The rule ─────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("probe, expected", [
    ({"format": {"tags": {"major_brand": "qt  "}}}, True),
    ({"format": {"tags": {"major_brand": "isom"}}}, False),
    ({"format": {"tags": {"major_brand": "M4V "}}}, False),
    ({"format": {"tags": {}}}, False),
    ({"format": {}}, False),
    (None, False),
])
def test_a_quicktime_movie_is_told_apart_by_its_brand(probe, expected):
    assert is_quicktime(probe) is expected


@pytest.mark.parametrize("container, quicktime, muxer", [
    ("mp4", True, "mov"),
    ("mp4", False, "mp4"),
    ("mkv", True, "matroska"),
    ("mkv", False, "matroska"),
    ("nope", True, None),
])
def test_only_an_mp4_container_is_written_as_quicktime(container, quicktime, muxer):
    assert output_muxer(container, quicktime) == muxer


# ── Real files ───────────────────────────────────────────────────────────────

class _WS:
    async def broadcast_json(self, payload):
        pass


def _mov(path, audio):
    """
    A QuickTime movie written by FFmpeg's QuickTime muxer: brand "qt  ",
    its index at the front. audio holds (language, codec, channels) per
    track, or (language, codec, channels, title); pcm_s16le gets
    QuickTime's "sowt" sample entry.
    """
    cmd = ["ffmpeg", "-v", "error", "-y",
           "-f", "lavfi", "-i", "testsrc=size=64x36:rate=10:duration=1"]
    for n, _ in enumerate(audio):
        cmd += ["-f", "lavfi", "-i", f"sine=frequency={440 + 110 * n}:duration=1"]
    cmd += ["-map", "0:v"]
    for n, (language, codec, channels, *title) in enumerate(audio):
        cmd += ["-map", f"{n + 1}:a", f"-c:a:{n}", codec, f"-ac:a:{n}", str(channels),
                f"-metadata:s:a:{n}", f"language={language}"]
        if title:
            cmd += [f"-metadata:s:a:{n}", f"title={title[0]}"]
    cmd += ["-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p",
            "-movflags", "+faststart", "-f", "mov", str(path)]
    subprocess.run(cmd, check=True)
    return path


def _shape(path):
    """The brand, and what each stream says it is: codec, sample entry,
    language and title (read back from QuickTime as the tag "name"). All
    but the title changed under the MP4 muxer, and Forge dropped the title
    under the QuickTime one."""
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-print_format", "json",
         "-show_format", "-show_streams", str(path)],
        capture_output=True, text=True, check=True).stdout
    probe = json.loads(out)
    return ((probe["format"].get("tags") or {}).get("major_brand"),
            [(s["codec_type"], s["codec_name"], s.get("codec_tag_string"),
              (s.get("tags") or {}).get("language"), (s.get("tags") or {}).get("name"))
             for s in probe["streams"]])


def _packets(path):
    """Each stream's packets, hashed."""
    return subprocess.run(
        ["ffmpeg", "-v", "error", "-i", str(path), "-map", "0", "-c", "copy",
         "-f", "streamhash", "-hash", "sha256", "-"],
        capture_output=True, text=True, check=True).stdout.split()


@pytest.fixture
def rig(tmp_path, monkeypatch):
    """A file-backed database every session points at, scratch temp and
    recycle folders, and an empty library. English audio only is kept, and
    revert points are on."""
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    import app.core.forge as forge
    import app.core.worker as worker
    import app.database.session as session_module
    from app.config import settings as app_settings
    from app.database.models import AppSetting, Base

    engine = create_engine(f"sqlite:///{tmp_path / 'remuxarr.db'}",
                           connect_args={"check_same_thread": False, "timeout": 30})
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine)
    for module in (worker, forge, session_module):
        monkeypatch.setattr(module, "SessionLocal", factory)
    monkeypatch.setattr(app_settings, "TEMP_DIR", str(tmp_path / "tmp"))
    monkeypatch.setattr(app_settings, "RECYCLE_DIR", str(tmp_path / "recycle"))
    (tmp_path / "recycle").mkdir()
    monkeypatch.setattr(worker, "_load_forge_plex_notify_data", lambda _id: None)
    monkeypatch.setattr(worker, "_forge_task", None)
    monkeypatch.setattr(worker, "_active_task_registry", {})
    library = tmp_path / "library"
    library.mkdir()
    with factory() as db:
        for key, value in [("dry_run_mode", False), ("revert_enabled", True),
                           ("prefer_mp4_container", False),
                           ("keep_audio_languages", ["eng"]),
                           ("keep_default_audio", False)]:
            db.merge(AppSetting(key=key, value=json.dumps(value)))
        db.commit()
    yield SimpleNamespace(Session=factory, worker=worker, forge=forge, library=library)
    engine.dispose()


def _run_job(rig, path):
    from app.core import scanner

    with rig.Session() as db:
        item = scanner.queue_single_file(db, str(path))
        assert item is not None and item.status == "pending", "nothing was queued"
        planned = [a.action_type for a in item.planned_actions]

    async def go():
        job_id = rig.worker._claim_next()
        await rig.worker._run_and_broadcast(job_id, _WS(), asyncio.get_running_loop())
    asyncio.run(go())
    return planned


# ── What a released version left ─────────────────────────────────────────────
#
# No job or AC3 Forge add starts on a QuickTime movie any more: every one is
# a recording, left as it is (probe.recording_signs). A revert point or a
# forged file made before then is still restored, or undone, as QuickTime.
# These make what was left: the file written with FFmpeg's QuickTime muxer,
# as the last release to process QuickTime movies wrote it, or with its MP4
# muxer, as the releases before that did, and what the run recorded.

def _left_by_a_job(rig, path, keep, drop, *, muxer="mov"):
    """
    What a job left: the file rewritten with the streams in keep, in that
    order, and a revert point for those in drop. A stream in neither is one
    the muxer writes itself, as a job leaves a chapter track. The point goes
    through the capture and the record a job uses (revert_capture.capture,
    worker._record_revert_point).
    """
    from app.core import revert_capture, scanner
    from app.database.models import MediaFile
    from app.database.session import get_app_settings

    with rig.Session() as db:
        scanner.scan_library(db, [str(rig.library)], force_probe=True)
        file_id = db.query(MediaFile).filter(MediaFile.path == str(path)).one().id
        app_cfg = get_app_settings(db)
    processed = path.with_name("processed" + path.suffix)
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", str(path),
                    *[arg for i in keep for arg in ("-map", f"0:{i}")],
                    "-c", "copy", "-movflags", "+faststart", "-f", muxer, str(processed)],
                   check=True)
    captured, error = asyncio.run(revert_capture.capture(
        input_path=str(path), produced_path=str(processed), file_id=file_id, job_id=1,
        app_cfg=app_cfg, removed=frozenset(drop)))
    assert captured is not None, error
    os.replace(processed, path)
    rig.worker._record_revert_point(file_id, captured, str(path))


def _left_by_a_forge_add(rig, path, *, leave_out=()):
    """
    What an AC3 Forge add left: the file with an AC3 5.1 made from its AAC
    5.1 appended, marked and flagged as an add marks and flags it
    (forge.build_add_ac3_command), each title written back and the index at
    the front, and the job recorded as a success. leave_out holds streams
    the muxer writes itself, which the add leaves out: a chapter track.
    Returns the job's id.
    """
    from app.core import scanner
    from app.core.forge import FORGE_AC3_HANDLER_NAME
    from app.database.models import Ac3ForgeJob, MediaFile

    streams = probe_file(str(path))["streams"]
    audio = [s for s in streams if s["codec_type"] == "audio"]
    aac = next(s for s in audio if s["codec_name"] == "aac" and s.get("channels") == 6)
    new = len(audio)
    titles = []
    for out, stream in enumerate(s for s in streams if s["index"] not in leave_out):
        if (stream.get("tags") or {}).get("name"):
            titles += [f"-metadata:s:{out}", f"title={stream['tags']['name']}"]
    forged = path.with_name("forged" + path.suffix)
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", str(path), "-map", "0",
                    *[arg for i in leave_out for arg in ("-map", f"-0:{i}")],
                    "-c", "copy", "-map", f"0:{aac['index']}",
                    f"-c:a:{new}", "ac3", f"-b:a:{new}", "640k", f"-ac:a:{new}", "6",
                    f"-metadata:s:a:{new}", f"handler_name={FORGE_AC3_HANDLER_NAME}",
                    f"-disposition:a:{new}", "-default", *titles,
                    "-movflags", "+faststart", "-f", "mov", str(forged)], check=True)
    os.replace(forged, path)
    with rig.Session() as db:
        scanner.scan_library(db, [str(rig.library)], force_probe=True)
        media = db.query(MediaFile).filter(MediaFile.path == str(path)).one()
        job = Ac3ForgeJob(file_id=media.id, status="success", aac_stream_index=aac["index"],
                          audio_track_count=new, ac3_marked=True, original_size=media.size)
        db.add(job)
        db.commit()
        return job.id


def _undo(rig, job_id):
    """The worker's undo of a successful add."""
    from app.database.models import Ac3ForgeJob

    with rig.Session() as db:
        job = db.get(Ac3ForgeJob, job_id)
        job.status, job.is_undo, job.progress = "undo_pending", True, 0.0
        db.commit()
    assert asyncio.run(rig.worker._process_next_forge(_WS())) is True
    with rig.Session() as db:
        job = db.get(Ac3ForgeJob, job_id)
        assert job.status == "undone", job.error_message


@needs_ffmpeg
@pytest.mark.parametrize("muxer, brand", [("mov", "qt  "), ("mp4", "isom")],
                         ids=["written as QuickTime", "written as MP4"])
def test_a_revert_gives_back_a_quicktime_movie(rig, muxer, brand):
    """
    A job dropped the German track, writing the file with the QuickTime
    muxer as the last release to process one did, or with the MP4 muxer as
    the releases before it did. The revert puts the track back as QuickTime
    either way, with the original's shape, packets and fast-start layout.
    """
    from app.core.probe import is_faststart_mp4
    from app.core.revert_restore import restore_revert_point
    from app.database.models import RevertPoint

    movie = _mov(rig.library / "Camera.mov",
                 [("eng", "pcm_s16le", 2), ("ger", "pcm_s16le", 2)])
    before, packets = _shape(movie), _packets(movie)

    _left_by_a_job(rig, movie, keep=[0, 1], drop=[2], muxer=muxer)
    assert _shape(movie)[0] == brand, "fixture is not what the job wrote"

    with rig.Session() as db:
        point_id = db.query(RevertPoint).one().id
    outcome = asyncio.run(restore_revert_point(point_id))
    assert outcome.success, outcome.error

    assert _shape(movie) == before
    assert _packets(movie) == packets
    assert is_faststart_mp4(str(movie)) is True


@needs_ffmpeg
def test_forge_undo_gives_back_a_quicktime_movie(rig):
    """The add appended an AC3, written as QuickTime; the undo gives the
    original's shape and packets back. Track titles survive: Forge wrote
    them back for "mp4" only, so a QuickTime file lost them."""
    movie = _mov(rig.library / "Camera.mov",
                 [("eng", "aac", 6, "Surround Mix"), ("eng", "pcm_s16le", 2, "Stereo")])
    before, packets = _shape(movie), _packets(movie)
    assert [s[4] for s in before[1]] == [None, "Surround Mix", "Stereo"], "fixture has no titles"

    job_id = _left_by_a_forge_add(rig, movie)
    brand, streams = _shape(movie)
    assert brand == "qt  " and streams[:3] == before[1] and streams[3][1] == "ac3", (
        "fixture is not what the add wrote")

    _undo(rig, job_id)

    assert _shape(movie) == before
    assert _packets(movie) == packets
