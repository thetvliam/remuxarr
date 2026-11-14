"""
A job keeps the date a file was made.

Reproduced with real FFmpeg before this file existed, on real recordings
(an Android phone's MP4, a Sony camera's, an editor's QuickTime export):
every job that rewrote one, a fast-start rewrite included, gave back a file
with no creation date, on the file or on any track. For a personal video
that is the day it was shot, and photo libraries sort by it. A Matroska
lost its date the same way.

Nothing Remuxarr asked for did it. FFmpeg copies the file's own tags to the
output and then removes creation_time from them (copy_meta in
fftools/ffmpeg_mux_init.c, FFmpeg 8.1.3). The job now reads the date from
the probe it already makes for cover art and data streams
(ffmpeg._prepare_extra_streams) and names it in the command
(ffmpeg.build_ffmpeg_command), which FFmpeg honours.

What is not kept, each measured: a track's own date where it differs from
the file's, as the MP4 muxer stamps every track with the file's; and a date
FFmpeg cannot read back as one, which it passes over.

The real-file tests run the real scan, worker and revert.

Verified by mutation, 11 applied, 11 killed. Each was first run against
the whole suite as it stood (2569 tests) with the change in place, and all
11 survived it. One of them is not about the change: a revert already wrote
the date back, from the tags its revert point records, and nothing pinned
that either.

  • the argument never written              → the command, and every real
                                              file that has a date
  • written with no date to write           → the command
  • the Matroska branch not reading it      → the Matroska job
  • the QuickTime branch not reading it     → the QuickTime job
  • the MP4 branch not reading it           → the MP4 job, the conversion,
                                              the combined pass, the revert
  • the current time written, not the
    source's                                → the command, every real file
                                              that has a date, and the one
                                              whose date cannot be read
  • a revert leaving the date out           → the revert
  • the rule always None                    → the rule, every real file
                                              that has a date
  • the rule keeping an empty date          → the rule
  • execute_ffmpeg not passing it on        → the three jobs, the
                                              conversion, the revert
  • execute_ffmpeg_combined not passing it  → the combined pass
"""
import asyncio
import json
import shutil
import subprocess
from types import SimpleNamespace

import pytest

from app.core.decision import ProcessingDecision
from app.core.ffmpeg import build_ffmpeg_command
from app.core.probe import creation_time

needs_ffmpeg = pytest.mark.skipif(
    shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None,
    reason="ffmpeg/ffprobe not available",
)

# Far from today, so that a date FFmpeg made up cannot pass for it.
MADE = "2014-03-02T10:20:30.000000Z"


# ── The rule ─────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("probe, expected", [
    ({"format": {"tags": {"creation_time": MADE}}}, MADE),
    ({"format": {"tags": {"creation_time": MADE, "major_brand": "isom"}}}, MADE),
    ({"format": {"tags": {"creation_time": ""}}}, None),
    ({"format": {"tags": {"major_brand": "isom"}}}, None),
    ({"format": {"tags": {}}}, None),
    ({"format": {}}, None),
    ({}, None),
    (None, None),
])
def test_the_date_is_the_files_own_creation_time_tag(probe, expected):
    assert creation_time(probe) == expected


# ── The command ──────────────────────────────────────────────────────────────

_VIDEO = {"stream_index": 0, "track_type": "video", "codec": "h264", "language": "und"}


@pytest.mark.parametrize("target, quicktime", [("mp4", False), ("mp4", True), ("mkv", False)])
def test_the_job_command_names_the_date_for_the_output(target, quicktime):
    """Before the output's format, so it belongs to the file being written
    and not to anything named after it."""
    decision = ProcessingDecision(should_process=True, reason="", target_container=target)

    cmd = build_ffmpeg_command("in", "out.tmp", decision, [_VIDEO],
                               quicktime=quicktime, creation_time=MADE)

    at = cmd.index(f"creation_time={MADE}")
    assert cmd[at - 1] == "-metadata"
    assert at < cmd.index("-f")
    assert sum("creation_time" in arg for arg in cmd) == 1


@pytest.mark.parametrize("given", [None, ""])
def test_the_job_command_names_no_date_when_the_source_has_none(given):
    decision = ProcessingDecision(should_process=True, reason="", target_container="mp4")

    cmd = build_ffmpeg_command("in", "out.tmp", decision, [_VIDEO], creation_time=given)

    assert not [arg for arg in cmd if "creation_time" in arg]


# ── Real files ───────────────────────────────────────────────────────────────

class _WS:
    async def broadcast_json(self, payload):
        pass


def _file(path, muxer, *, made=MADE, subtitle=False):
    """
    A file with English and German audio, so there is a track to drop, its
    index at the end, and the creation date `made` (None for no date at
    all). `subtitle` adds an English text subtitle.
    """
    cmd = ["ffmpeg", "-v", "error", "-y",
           "-f", "lavfi", "-i", "testsrc=size=64x36:rate=10:duration=1",
           "-f", "lavfi", "-i", "sine=frequency=440:duration=1",
           "-f", "lavfi", "-i", "sine=frequency=550:duration=1"]
    if subtitle:
        srt = path.parent / "in.srt"
        srt.write_text("1\n00:00:00,100 --> 00:00:00,900\nHello\n", encoding="utf-8")
        cmd += ["-i", str(srt)]
    cmd += ["-map", "0:v", "-map", "1:a", "-map", "2:a",
            "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p",
            "-c:a", "aac",
            "-metadata:s:a:0", "language=eng", "-metadata:s:a:1", "language=ger"]
    if subtitle:
        cmd += ["-map", "3:s", "-c:s", "mov_text" if muxer == "mp4" else "subrip",
                "-metadata:s:s:0", "language=eng"]
    if made:
        cmd += ["-metadata", f"creation_time={made}"]
    cmd += ["-f", muxer, str(path)]
    subprocess.run(cmd, check=True)
    return path


def _dates(path):
    """The file's creation date and each track's, as ffprobe shows them."""
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-print_format", "json",
         "-show_format", "-show_streams", str(path)],
        capture_output=True, text=True, check=True).stdout
    probe = json.loads(out)
    return ((probe["format"].get("tags") or {}).get("creation_time"),
            [(s.get("tags") or {}).get("creation_time") for s in probe["streams"]])


def _audio_languages(path):
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "a", "-show_entries",
         "stream_tags=language", "-of", "csv=p=0", str(path)],
        capture_output=True, text=True, check=True).stdout
    return out.split()


@pytest.fixture
def rig(tmp_path, monkeypatch):
    """A file-backed database every session points at, scratch temp and
    recycle folders, and an empty library. English audio only is kept, a
    file keeps its container, and revert points are on. `set` changes a
    setting."""
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
    for module in (worker, session_module):
        monkeypatch.setattr(module, "SessionLocal", factory)
    monkeypatch.setattr(app_settings, "TEMP_DIR", str(tmp_path / "tmp"))
    monkeypatch.setattr(app_settings, "RECYCLE_DIR", str(tmp_path / "recycle"))
    (tmp_path / "recycle").mkdir()
    monkeypatch.setattr(worker, "_active_task_registry", {})
    library = tmp_path / "library"
    library.mkdir()

    def set_(key, value):
        with factory() as db:
            db.merge(AppSetting(key=key, value=json.dumps(value)))
            db.commit()

    for key, value in [("dry_run_mode", False), ("revert_enabled", True),
                       ("prefer_mp4_container", False),
                       ("add_faststart_to_mp4", False),
                       ("extract_text_subtitles_to_srt", False),
                       ("keep_audio_languages", ["eng"]),
                       ("keep_default_audio", False)]:
        set_(key, value)
    yield SimpleNamespace(Session=factory, worker=worker, library=library, set=set_)
    engine.dispose()


def _run_job(rig, path):
    """Queue the file as a scan would and run the job. Returns what the
    job planned, and the file as it is afterwards."""
    from app.core import scanner
    from app.database.models import MediaFile

    with rig.Session() as db:
        item = scanner.queue_single_file(db, str(path))
        assert item is not None and item.status == "pending", "nothing was queued"
        planned = [a.action_type for a in item.planned_actions]
        file_id = item.file_id

    async def go():
        job_id = rig.worker._claim_next()
        await rig.worker._run_and_broadcast(job_id, _WS(), asyncio.get_running_loop())
    asyncio.run(go())

    with rig.Session() as db:
        after = db.get(MediaFile, file_id).path
    return planned, after


@needs_ffmpeg
@pytest.mark.parametrize("name, muxer, tracks_dated", [
    ("Phone.mp4", "mp4", True),
    ("Camera.mov", "mov", True),
    ("Film.mkv", "matroska", False),
])
def test_a_job_keeps_the_date_the_file_was_made(rig, name, muxer, tracks_dated):
    """
    The job drops the German track and changes nothing else. An MP4 and a
    QuickTime file carry the date on each track as well; a Matroska has it
    on the file alone.
    """
    source = _file(rig.library / name, muxer)
    before = _dates(source)
    assert before[0] == MADE, "fixture has no date"
    assert all(d == (MADE if tracks_dated else None) for d in before[1])

    planned, after = _run_job(rig, source)

    assert "drop_track" in planned
    assert after == str(source)
    assert _audio_languages(source) == ["eng"], "the job did not run"
    assert _dates(source) == (MADE, before[1][:2])


@needs_ffmpeg
def test_a_matroska_converted_to_mp4_keeps_its_date(rig):
    """The date goes with the file into the new container, where each
    track takes it too."""
    rig.set("prefer_mp4_container", True)
    source = _file(rig.library / "Film.mkv", "matroska")

    planned, after = _run_job(rig, source)

    assert "change_container" in planned
    assert after.endswith(".mp4")
    assert _dates(after) == (MADE, [MADE, MADE])


@needs_ffmpeg
def test_a_job_that_also_extracts_a_subtitle_keeps_the_date(rig):
    """That job runs execute_ffmpeg_combined, which builds its own call."""
    rig.set("extract_text_subtitles_to_srt", True)
    source = _file(rig.library / "Phone.mp4", "mp4", subtitle=True)
    assert _dates(source)[0] == MADE

    planned, _ = _run_job(rig, source)

    assert "extract_subtitle" in planned
    assert list(rig.library.glob("*.srt")), "no subtitle was extracted"
    assert _dates(source) == (MADE, [MADE, MADE])


@needs_ffmpeg
@pytest.mark.parametrize("name, muxer", [("Phone.mp4", "mp4"), ("Film.mkv", "matroska")])
def test_a_file_with_no_date_is_not_given_one(rig, name, muxer):
    source = _file(rig.library / name, muxer, made=None)
    assert _dates(source) == (None, [None, None, None])

    _run_job(rig, source)

    assert _audio_languages(source) == ["eng"], "the job did not run"
    assert _dates(source) == (None, [None, None])


@needs_ffmpeg
def test_a_revert_gives_back_the_date(rig):
    """The job keeps the date and so does the revert that puts the German
    track back."""
    from app.core.revert_restore import restore_revert_point
    from app.database.models import RevertPoint

    source = _file(rig.library / "Phone.mp4", "mp4")
    before = _dates(source)

    _run_job(rig, source)
    assert _dates(source) == (MADE, [MADE, MADE])

    with rig.Session() as db:
        point_id = db.query(RevertPoint).one().id
    outcome = asyncio.run(restore_revert_point(point_id))
    assert outcome.success, outcome.error

    assert _audio_languages(source) == ["eng", "ger"]
    assert _dates(source) == before


@needs_ffmpeg
def test_a_date_ffmpeg_cannot_read_does_not_fail_the_job(rig, monkeypatch):
    """
    A real phone video gave its date as "40096-09-19T03:52:44.000000Z".
    FFmpeg cannot write a file with such a date, so the probe the job makes
    is given it here. The job succeeds, and the file comes out with no date
    rather than a wrong one.
    """
    import app.core.ffmpeg as ffmpeg_module

    source = _file(rig.library / "Phone.mp4", "mp4")
    real_probe = ffmpeg_module.probe_file

    def probe(path, *args, **kwargs):
        data = real_probe(path, *args, **kwargs)
        data["format"]["tags"]["creation_time"] = "40096-09-19T03:52:44.000000Z"
        return data
    monkeypatch.setattr(ffmpeg_module, "probe_file", probe)

    _run_job(rig, source)

    assert _audio_languages(source) == ["eng"], "the job did not run"
    assert _dates(source) == (None, [None, None])
