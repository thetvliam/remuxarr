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

A file whose major brand is "qt  " (probe.is_quicktime) is now written with
FFmpeg's QuickTime muxer (ffmpeg.output_muxer): by a job, from the probe the
worker makes before it runs; by a revert, from the brand the revert point
recorded for the original; by Forge, from its own probe.

These run the real scan, worker, revert and forge against real files.

Verified by mutation, 12 applied, 12 killed. Each was first run against
the whole suite as it stood (2459 tests, with test_job_preflight.py's
plain-data test expecting the new quicktime key), and all 12 survived it:

  • output_muxer ignoring quicktime          → the rule, the job command,
                                               the fast-start rewrite and
                                               the job and revert
  • output_muxer ignoring the container      → the rule, the job command
  • the worker never reporting QuickTime     → the fast-start rewrite, the
                                               job and revert
  • is_quicktime always False                → the brand, and every real
                                               file test
  • the restore ignoring the recorded brand  → the job and revert
  • the restore's fast start for MP4 only    → the job and revert
  • Forge's muxer ignoring quicktime         → forge add and undo
  • Forge's job data never QuickTime         → forge add and undo
  • the undo, then the add, not passing it   → forge add and undo, one
                                               mutant each
  • execute_ffmpeg not passing it            → the fast-start rewrite, the
                                               job and revert
  • the worker not passing it to
    execute_ffmpeg                           → the same two

Forge's titles were then found lost on a QuickTime file: _title_args wrote
them back for the "mp4" muxer only. The forge test now gives its tracks
titles. Its mutant, titles for "mp4" only again, survived the full suite
before that and is killed by forge add and undo alone; titles for "mov"
only is killed by test_mp4_track_titles.py.
"""
import asyncio
import json
import shutil
import subprocess
from types import SimpleNamespace

import pytest

from app.core.decision import ProcessingDecision
from app.core.ffmpeg import build_ffmpeg_command, output_muxer
from app.core.probe import is_quicktime

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


def _format_of(cmd: list[str]) -> str:
    return cmd[len(cmd) - 1 - cmd[::-1].index("-f") + 1]


@pytest.mark.parametrize("target, muxer", [("mp4", "mov"), ("mkv", "matroska")])
def test_the_job_command_writes_a_quicktime_source_with_its_muxer(target, muxer):
    video = {"stream_index": 0, "track_type": "video", "codec": "h264",
             "language": "und"}
    decision = ProcessingDecision(should_process=True, reason="", target_container=target)

    cmd = build_ffmpeg_command("in.mov", "out.tmp", decision, [video], quicktime=True)

    assert _format_of(cmd) == muxer


# ── Real files ───────────────────────────────────────────────────────────────

class _WS:
    async def broadcast_json(self, payload):
        pass


def _mov(path, audio, *, faststart):
    """
    A QuickTime movie written by FFmpeg's QuickTime muxer: brand "qt  ".
    audio holds (language, codec, channels) per track, or (language, codec,
    channels, title); pcm_s16le gets QuickTime's "sowt" sample entry.
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
    cmd += ["-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p"]
    if faststart:
        cmd += ["-movflags", "+faststart"]
    cmd += ["-f", "mov", str(path)]
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


@needs_ffmpeg
def test_a_fast_start_rewrite_keeps_a_quicktime_movie_one(rig):
    """
    A camera .mov with its index at the end and nothing else to change:
    the job only moves the index to the front, and changes nothing else.
    """
    from app.core.probe import is_faststart_mp4

    movie = _mov(rig.library / "Camera.mov", [("eng", "pcm_s16le", 2)], faststart=False)
    before, packets = _shape(movie), _packets(movie)
    assert before[0] == "qt  " and before[1][1][2] == "sowt", "fixture is not QuickTime"
    assert is_faststart_mp4(str(movie)) is False

    assert [a for a in _run_job(rig, movie) if a != "copy_track"] == ["add_faststart"]

    assert _shape(movie) == before
    assert _packets(movie) == packets
    assert is_faststart_mp4(str(movie)) is True


@needs_ffmpeg
def test_a_job_and_its_revert_keep_a_quicktime_movie_one(rig):
    """
    The job drops the German track. It and the revert that puts the track
    back both write QuickTime, and the revert gives back the original's
    shape, packets and fast-start layout.
    """
    from app.core.probe import is_faststart_mp4
    from app.core.revert_restore import restore_revert_point
    from app.database.models import RevertPoint

    movie = _mov(rig.library / "Camera.mov",
                 [("eng", "pcm_s16le", 2), ("ger", "pcm_s16le", 2)], faststart=True)
    before, packets = _shape(movie), _packets(movie)

    assert "drop_track" in _run_job(rig, movie)
    brand, streams = _shape(movie)
    assert (brand, streams) == (before[0], before[1][:2]), "the job changed the file's shape"

    with rig.Session() as db:
        point_id = db.query(RevertPoint).one().id
    outcome = asyncio.run(restore_revert_point(point_id))
    assert outcome.success, outcome.error

    assert _shape(movie) == before
    assert _packets(movie) == packets
    assert is_faststart_mp4(str(movie)) is True


@needs_ffmpeg
def test_forge_add_and_undo_keep_a_quicktime_movie_one(rig):
    """The add writes QuickTime with its AC3 appended, and the undo gives
    the original's shape and packets back. Track titles survive both: Forge
    wrote them back for "mp4" only, so a QuickTime file lost them."""
    from app.core import scanner
    from app.database.models import Ac3ForgeJob, MediaFile

    movie = _mov(rig.library / "Camera.mov",
                 [("eng", "aac", 6, "Surround Mix"), ("eng", "pcm_s16le", 2, "Stereo")],
                 faststart=True)
    before, packets = _shape(movie), _packets(movie)
    assert [s[4] for s in before[1]] == [None, "Surround Mix", "Stereo"], "fixture has no titles"
    with rig.Session() as db:
        scanner.scan_library(db, [str(rig.library)], force_probe=True)
        job_id = rig.forge.queue_forge_job(db, db.query(MediaFile).one().id).id

    assert asyncio.run(rig.worker._process_next_forge(_WS())) is True
    brand, streams = _shape(movie)
    assert brand == "qt  "
    assert streams[:3] == before[1] and streams[3][1] == "ac3", "the add changed the file's shape"

    with rig.Session() as db:
        job = db.get(Ac3ForgeJob, job_id)
        assert job.status == "success", job.error_message
        job.status, job.is_undo, job.progress = "undo_pending", True, 0.0
        db.commit()
    assert asyncio.run(rig.worker._process_next_forge(_WS())) is True

    assert _shape(movie) == before
    assert _packets(movie) == packets
