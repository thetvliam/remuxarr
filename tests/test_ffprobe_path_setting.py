"""
REMUXARR_FFPROBE_PATH is the ffprobe every probe runs, and
REMUXARR_FFMPEG_PATH the ffmpeg that reads a video for packed B-frames.

probe_file took its binary as an argument that defaulted to "ffprobe", so
any call that left it out ran whichever ffprobe was on PATH. Nine calls
passed the setting and seven did not: the check that a file keeps its own
tags, the four probes revert capture makes, and the probe of a file a
revert point is being attached to. With the setting naming an ffprobe and
none on PATH, every job stopped at the tags check, saying the file's tags
could not be checked, revert capture recorded no revert point (or refused
the job when one was required), and attaching a point said the file could
not be read. probe_file now reads the setting when no binary is given, as
is_packed_divx does REMUXARR_FFMPEG_PATH.

Every test here runs with the configured binary under a name of its own
and PATH holding neither, so only the configured path can be run.

Verified by mutation, 9 applied, 9 killed. Each of the seven calls given
"ffprobe" by name, probe_file's default put back to "ffprobe" and
is_packed_divx's to "ffmpeg": eight survived the suite as it stood. The
ninth, the probe of the produced file in revert capture, was already
killed, but only because test_revert_capture's unprobeable-file stub takes
one argument and the mutant passed two; nothing there checks which ffprobe
runs.
"""

import asyncio
import os
import shutil
import subprocess

import pytest

ffmpeg_required = pytest.mark.skipif(
    shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None,
    reason="ffmpeg/ffprobe not available",
)

pytestmark = ffmpeg_required


@pytest.fixture
def binaries(tmp_path, monkeypatch):
    """
    ffprobe configured as "my-ffprobe", ffmpeg by its full path, and a PATH
    with nothing on it, so "ffprobe" or "ffmpeg" by name finds nothing.
    """
    from app.config import settings as app_settings

    real_ffmpeg = shutil.which("ffmpeg")
    real_ffprobe = shutil.which("ffprobe")
    tools = tmp_path / "tools"
    tools.mkdir()
    custom_ffprobe = tools / "my-ffprobe"
    custom_ffprobe.symlink_to(real_ffprobe)
    empty = tmp_path / "empty-path"
    empty.mkdir()

    monkeypatch.setattr(app_settings, "FFPROBE_PATH", str(custom_ffprobe))
    monkeypatch.setattr(app_settings, "FFMPEG_PATH", real_ffmpeg)
    monkeypatch.setenv("PATH", str(empty))
    assert shutil.which("ffprobe") is None and shutil.which("ffmpeg") is None
    return {"ffmpeg": real_ffmpeg, "ffprobe": str(custom_ffprobe), "tools": tools}


def _ffmpeg(binaries, *args):
    subprocess.run([binaries["ffmpeg"], "-v", "error", "-y", *args], check=True)


def _film(binaries, path):
    """A second of video, two audio tracks and a subtitle, in Matroska."""
    srt = path.parent / "eng.srt"
    srt.write_text("1\n00:00:00,000 --> 00:00:01,000\nENG\n\n")
    _ffmpeg(binaries,
            "-f", "lavfi", "-i", "testsrc=size=160x120:rate=10:duration=1",
            "-f", "lavfi", "-i", "sine=frequency=440:duration=1",
            "-f", "lavfi", "-i", "sine=frequency=880:duration=1",
            "-i", str(srt),
            "-map", "0", "-map", "1", "-map", "2", "-map", "3",
            "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", "-c:s", "srt",
            "-metadata:s:a:0", "language=eng", "-metadata:s:a:1", "language=ger",
            "-metadata:s:s:0", "language=eng", str(path))
    return path


# ── probe_file and is_packed_divx ────────────────────────────────────────────

def test_probe_file_with_no_binary_given_runs_the_configured_ffprobe(binaries, tmp_path):
    from app.core.probe import probe_file

    film = _film(binaries, tmp_path / "Film.mkv")

    assert [s["codec_type"] for s in probe_file(str(film))["streams"]] == \
        ["video", "audio", "audio", "subtitle"]


def test_a_missing_configured_ffprobe_is_named_in_the_error(binaries, tmp_path, monkeypatch):
    from app.config import settings as app_settings
    from app.core.probe import ProbeError, probe_file

    film = _film(binaries, tmp_path / "Film.mkv")
    missing = str(binaries["tools"] / "not-there")
    monkeypatch.setattr(app_settings, "FFPROBE_PATH", missing)

    with pytest.raises(ProbeError, match="not-there"):
        probe_file(str(film))


def test_a_binary_passed_to_probe_file_is_the_one_it_runs(binaries, tmp_path):
    """The nine calls that pass the setting themselves still get what they pass."""
    from app.core.probe import ProbeError, probe_file

    film = _film(binaries, tmp_path / "Film.mkv")

    with pytest.raises(ProbeError, match="passed-ffprobe"):
        probe_file(str(film), str(binaries["tools"] / "passed-ffprobe"))


def test_is_packed_divx_with_no_binary_given_runs_the_configured_ffmpeg(
        binaries, tmp_path, monkeypatch):
    """
    None is what it answers when the video cannot be read, so a False here
    is the configured ffmpeg having read it.
    """
    from app.config import settings as app_settings
    from app.core.probe import is_packed_divx

    avi = tmp_path / "Film.avi"
    _ffmpeg(binaries, "-f", "lavfi", "-i", "testsrc=size=160x120:rate=10:duration=1",
            "-c:v", "mpeg4", str(avi))
    custom_ffmpeg = binaries["tools"] / "my-ffmpeg"
    custom_ffmpeg.symlink_to(binaries["ffmpeg"])
    monkeypatch.setattr(app_settings, "FFMPEG_PATH", str(custom_ffmpeg))

    assert is_packed_divx(str(avi)) is False


# ── The calls that left the binary out ───────────────────────────────────────

def test_the_tags_check_reads_both_files_with_the_configured_ffprobe(binaries, tmp_path):
    """A file compared with itself keeps every tag; nothing should stop it."""
    from app.core.worker import _lost_tags_error

    film = str(_film(binaries, tmp_path / "Film.mkv"))

    assert asyncio.run(_lost_tags_error(film, film)) is None


@pytest.fixture
def lib(tmp_path, monkeypatch, binaries):
    """A recycle volume, a live database and a tracked film."""
    from sqlalchemy.orm import sessionmaker

    from tests.conftest import memory_engine

    from app.config import settings as app_settings
    from app.database.models import Base, MediaFile
    import app.core.worker as worker_mod
    import app.database.session as session_mod

    media_dir = tmp_path / "media"
    media_dir.mkdir()
    recycle = tmp_path / "recycle"
    recycle.mkdir()
    monkeypatch.setattr(app_settings, "RECYCLE_DIR", str(recycle), raising=False)

    engine = memory_engine()
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine)
    monkeypatch.setattr(session_mod, "SessionLocal", factory)
    monkeypatch.setattr(worker_mod, "SessionLocal", factory)

    path = _film(binaries, media_dir / "Film.mkv")
    db = factory()
    media = MediaFile(path=str(path), filename="Film.mkv", directory=str(media_dir),
                      size=0, mtime=0, container="mkv", status="processed")
    db.add(media)
    db.commit()
    return {"db": db, "media": media, "path": path, "tmp": tmp_path,
            "binaries": binaries}


def _run_job(lib, ffmpeg_args, *, job_id, removed):
    """
    What a job does around capture: FFmpeg writes the output, capture runs
    while both files exist, the output is swapped in and the point recorded.
    A revert point is required, so a probe that fails comes back as the
    error rather than as a job that quietly went ahead without one.
    """
    from app.core.revert_capture import capture
    from app.core.worker import _record_revert_point

    produced = lib["tmp"] / f"job{job_id}.remuxarr_tmp"
    _ffmpeg(lib["binaries"], "-i", str(lib["path"]), *ffmpeg_args,
            "-f", "matroska", str(produced))
    captured, error = asyncio.run(capture(
        input_path=str(lib["path"]), produced_path=str(produced),
        file_id=lib["media"].id, job_id=job_id,
        app_cfg={"revert_enabled": True, "revert_require_point": True},
        removed=frozenset(removed),
    ))
    assert error is None, error
    assert captured is not None
    assert os.path.exists(captured.sidecar_path)
    os.replace(produced, lib["path"])
    _record_revert_point(lib["media"].id, captured, str(lib["path"]), [])
    return captured


def test_revert_capture_probes_with_the_configured_ffprobe(lib):
    """
    The first job probes the output, the source and the sidecar it wrote;
    the second, extending the point, probes the file it was handed in
    place of the source.
    """
    from app.database.models import RevertPoint

    _run_job(lib, ["-map", "0:0", "-map", "0:1", "-map", "0:3", "-c", "copy"],
             job_id=1, removed={2})
    _run_job(lib, ["-map", "0:0", "-map", "0:1", "-c", "copy"], job_id=2, removed={2})

    lib["db"].expire_all()
    point = lib["db"].query(RevertPoint).one()
    assert point.file_id == lib["media"].id


def test_attaching_a_revert_point_probes_with_the_configured_ffprobe(lib):
    from app.core.revert_match import EXACT, attach
    from app.database.models import RevertPoint

    _run_job(lib, ["-map", "0:0", "-map", "0:1", "-map", "0:3", "-c", "copy"],
             job_id=1, removed={2})
    lib["db"].expire_all()
    point = lib["db"].query(RevertPoint).one()
    point.file_id = None
    lib["db"].commit()

    outcome = attach(point.id, lib["media"].id)

    assert outcome.success, outcome.error
    assert outcome.tier == EXACT
