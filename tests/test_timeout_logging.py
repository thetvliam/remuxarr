"""
What the container log says when an FFmpeg run hits the job time limit.

run_staged_subprocess returns no exit code for that run, and only for that
one. Each of the four places that log a failed run handled it badly:

  • the remux formatted the missing code with %d, so the logging call raised
    and the log got "--- Logging error ---" and a traceback instead of the
    reason;
  • the combined remux, the subtitle extraction and forge skipped it, so the
    reason was not logged at all.

Each now logs "... failed: Job timed out after N minute(s) ...". The run
here is a real one, cut off by the real runner: the fake keeps every
argument the executor passed, the time limit included, and swaps only the
command for one that outlasts it.

Verified by mutation, 4 applied, 4 killed, each surviving the suite as it
stood (2031 tests) first. Each puts one place back as it was, and is killed
by that place's test here.
"""
import asyncio
import logging

import pytest

from app.core import ffmpeg, forge
from app.core import subprocess_runner
from app.core.decision import analyze_file
from conftest import make_file_info, make_track


LIMIT = 0.2     # seconds; "sleep 30" outlasts it by a long way


@pytest.fixture
def cut_off(monkeypatch, tmp_path):
    """Every FFmpeg run is replaced by one the time limit stops."""
    from app.config import settings as app_settings

    monkeypatch.setattr(app_settings, "TEMP_DIR", str(tmp_path / "tmp"))
    real = subprocess_runner.run_staged_subprocess

    async def run(cmd, outputs, **kwargs):
        assert kwargs["timeout_seconds"] == LIMIT, "the limit was not passed down"
        return await real(["sleep", "30"], outputs, **kwargs)

    async def no_duration(path):
        return None

    for module in (ffmpeg, forge):
        monkeypatch.setattr(module, "run_staged_subprocess", run)
        monkeypatch.setattr(module, "probe_duration", no_duration)


def _decision(source, settings):
    settings["prefer_mp4_container"] = False
    tracks = [
        make_track(stream_index=0, track_type="video", codec="h264"),
        make_track(stream_index=1, track_type="audio", codec="aac",
                   language="eng", is_default=True),
        make_track(stream_index=2, track_type="audio", codec="aac",
                   language="fre"),
    ]
    decision = analyze_file(
        make_file_info(path=str(source), container="mkv", video_codec="h264"),
        tracks, settings,
    )
    assert decision.should_process, "fixture no longer produces work"
    return decision, tracks


def _logged(caplog, start):
    """The one ERROR record that starts with `start`, formatted. For the
    remux, the old %d fails the test before this: pytest's log capture
    re-raises a record that cannot be formatted, where the container's
    handler printed "--- Logging error ---" and carried on."""
    found = [r for r in caplog.records
             if r.levelno == logging.ERROR and r.getMessage().startswith(start)]
    assert len(found) == 1, (
        f"no single error starting {start!r}: "
        f"{[(r.levelname, r.msg) for r in caplog.records]}")
    return found[0].getMessage()


def _source(tmp_path):
    source = tmp_path / "Movie.mkv"
    source.write_bytes(b"ORIGINAL")
    return source


def test_the_remux_logs_the_reason(cut_off, tmp_path, settings, caplog):
    source = _source(tmp_path)
    decision, tracks = _decision(source, settings)

    with caplog.at_level(logging.ERROR, logger=ffmpeg.logger.name):
        result = asyncio.run(ffmpeg.execute_ffmpeg(
            str(source), str(source), decision, tracks, job_id=1,
            timeout_seconds=LIMIT))

    assert result.success is False
    assert "timed out" in result.error
    assert _logged(caplog, "FFmpeg failed") == f"FFmpeg failed: {result.error}"
    assert source.read_bytes() == b"ORIGINAL"


def test_the_combined_remux_logs_the_reason(cut_off, tmp_path, settings, caplog):
    source = _source(tmp_path)
    decision, tracks = _decision(source, settings)
    srt = tmp_path / "Movie.eng.srt"

    with caplog.at_level(logging.ERROR, logger=ffmpeg.logger.name):
        result, srt_results = asyncio.run(ffmpeg.execute_ffmpeg_combined(
            str(source), str(source), decision, tracks, [(3, str(srt))], job_id=1,
            timeout_seconds=LIMIT))

    assert result.success is False
    assert "timed out" in result.error
    assert [r.success for r in srt_results] == [False]
    assert _logged(caplog, "FFmpeg (combined) failed") == (
        f"FFmpeg (combined) failed: {result.error}")
    assert not srt.exists()


def test_a_subtitle_extraction_logs_the_reason(cut_off, tmp_path, caplog):
    source = _source(tmp_path)
    srt = tmp_path / "Movie.eng.srt"

    with caplog.at_level(logging.ERROR, logger=ffmpeg.logger.name):
        result = asyncio.run(ffmpeg.execute_subtitle_extraction(
            str(source), 3, str(srt), job_id=1, timeout_seconds=LIMIT))

    assert result.success is False
    assert "timed out" in result.error
    assert _logged(caplog, "Subtitle extraction failed") == (
        f"Subtitle extraction failed (stream 3): {result.error}")
    assert not srt.exists()


def test_forge_logs_the_reason(cut_off, tmp_path, caplog):
    source = _source(tmp_path)
    temp = tmp_path / "forge.tmp"
    cmd = forge.build_add_ac3_command(
        input_path=str(source), temp_path=str(temp),
        aac_stream_index=1, audio_track_count=1, container="mkv")

    with caplog.at_level(logging.ERROR, logger=forge.logger.name):
        result = asyncio.run(forge.run_forge_command(
            cmd, str(source), str(source), str(temp), "Adding AC3",
            timeout_seconds=LIMIT))

    assert result.success is False
    assert "timed out" in result.error
    assert _logged(caplog, "Forge FFmpeg failed") == (
        f"Forge FFmpeg failed: {result.error}")
    assert source.read_bytes() == b"ORIGINAL"
