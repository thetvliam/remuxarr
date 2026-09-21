"""
Progress reporting for the copy that follows a successful subprocess.

FFmpeg finishing is not the job finishing. Its output is in the temp
directory at that point and still has to be written to the library, which on
a parity-protected array is most of the wall time — in the report this came
from, 60 seconds of a 70-second job, during which the bar sat at 100% and
the label still said "Remuxing tracks". These tests cover the two halves of
the fix: the runner measuring that copy, and the adapters giving it the last
tenth of the bar.

Two things here are less obvious than they look.

The counter has to advance AFTER a flush rather than after a write.
shutil.copyfile, which this replaced, returns once the bytes are in the page
cache, so a copy of a large file "finishes" long before the disk has it and
the fsync afterwards carries the rest of the wall time — measured at between
40 and 60 per cent of it on a 700 MB file. Progress counted from writes
would therefore reach the end of the bar and stall there, which is the
behaviour being fixed rather than a fix for it.

And progress is a fraction of ALL outputs together. A remux with subtitle
sidecars stages several files; measuring each against its own size restarts
the bar per file, and the restart is invisible on the common single-output
job, which is exactly the shape of bug that ships.

Both pipelines divide their bars this way: the remux executors in ffmpeg.py
and AC3 Forge, which builds its own ForgeProgress. The split itself lives in
subprocess_runner, next to the staging it describes.

Mutation, 21 applied and 20 killed on the current suite; the survivor is
recorded as equivalent in test_an_abort_mid_copy_settles_before_cleanup.
All 21 survived the suite as it stood before the tests that kill them.
"""
import asyncio
import os
import shutil
import subprocess
import threading
import time

import pytest

import app.core.subprocess_runner as sr
from app.core.decision import analyze_file
from app.core.ffmpeg import execute_ffmpeg, execute_ffmpeg_combined
from app.core.forge import build_add_ac3_command, run_forge_command
from app.core.subprocess_runner import (
    SUBPROCESS_PROGRESS_SHARE, STAGING_ACTION,
    StagedOutput, run_staged_subprocess,
)
from tests.conftest import make_file_info, make_track


# ── Helpers ──────────────────────────────────────────────────────────────────


def _sizer_cmd(pairs: list[tuple[str, int]]) -> list[str]:
    """A command that produces temp outputs of exactly the given sizes."""
    script = "; ".join(
        f"head -c {size} /dev/zero > '{path}'" for path, size in pairs
    )
    return ["/bin/sh", "-c", script]


@pytest.fixture
def small_chunks(monkeypatch):
    """
    Copy in bytes rather than megabytes, so a test file produces several
    flushes without being several hundred megabytes.

    monkeypatch.setattr restores all three afterwards: they are module-level
    names, and a test that left one small would quietly change how every
    later test in the run copies.
    """
    monkeypatch.setattr(sr, "_STAGE_FLUSH_BYTES", 4096)
    monkeypatch.setattr(sr, "_STAGE_COPY_BUFFER", 1024)
    monkeypatch.setattr(sr, "_STAGE_POLL_SECONDS", 0.005)


@pytest.fixture
def slow_disk(monkeypatch):
    """
    Make each flush take long enough for the poll loop to see it.

    The delay goes in fdatasync rather than in the write because that is
    where a real slow destination spends its time, and because it keeps the
    copy loop itself unmodified — the thing under test.
    """
    real = os.fdatasync

    def _slow(fd):
        time.sleep(0.02)
        return real(fd)

    monkeypatch.setattr(sr.os, "fdatasync", _slow)


def _run(cmd, outputs, **kw):
    return asyncio.run(run_staged_subprocess(cmd, outputs, **kw))


# ── The counter ──────────────────────────────────────────────────────────────


def test_only_bytes_that_reached_the_disk_are_counted(tmp_path, monkeypatch,
                                                      small_chunks):
    """
    White-box, and deliberately so: "the number means durable bytes" has no
    black-box symptom on a fast disk, and the whole point of the number is
    what it does on a slow one.

    Reading the counter at the moment each flush STARTS is what separates
    the two orderings. Correct code has not yet added the chunk being
    flushed, so the readings trail by one chunk and open at zero; counting
    before the flush opens at 4096 and reports bytes that are still only in
    the page cache.
    """
    temp = tmp_path / "a.tmp"
    temp.write_bytes(b"x" * 40960)          # ten flushes of 4096
    final = tmp_path / "a.mkv"

    at_flush: list[int] = []
    real = os.fdatasync
    flushed = [0]

    def _spy(fd):
        at_flush.append(flushed[0])
        return real(fd)

    monkeypatch.setattr(sr.os, "fdatasync", _spy)

    sr._stage_parts(
        [StagedOutput(temp_path=str(temp), final_path=str(final))],
        [], flushed,
    )

    assert at_flush, "nothing was flushed during the copy, so nothing was durable"
    assert at_flush == [n * 4096 for n in range(10)], (
        f"the counter does not trail the flushes: {at_flush}"
    )
    assert flushed[0] == 40960, "the tail after the last flush was never counted"


def test_progress_spans_every_output_rather_than_restarting_per_file(
    tmp_path, small_chunks, slow_disk,
):
    """
    A remux with sidecars stages several files under one bar.

    The two outputs are deliberately different sizes. Equal ones would make
    "fraction of everything" and "fraction of this file" agree at the
    boundary, which is the shape of fixture that hides a per-file bug.
    """
    temps  = [tmp_path / "a.tmp", tmp_path / "b.tmp"]
    finals = [tmp_path / "a.mkv", tmp_path / "b.srt"]
    seen: list[float] = []

    async def on_staging(fraction):
        seen.append(fraction)

    res = _run(
        _sizer_cmd([(str(temps[0]), 4096), (str(temps[1]), 12288)]),
        [StagedOutput(temp_path=str(t), final_path=str(f))
         for t, f in zip(temps, finals)],
        on_staging_progress=on_staging,
    )

    assert res.success is True, res.error
    assert seen == sorted(seen), f"progress went backwards: {seen}"
    assert max(seen) == 1.0 and min(seen) == 0.0
    # The first output is a quarter of the total, so a bar measured against
    # it alone is already full here.
    assert any(0.2 < f < 0.3 for f in seen), (
        f"no reading near a quarter, so the bar is not measuring the whole "
        f"job: {seen}"
    )
    assert any(0.45 < f < 0.55 for f in seen), f"no reading near a half: {seen}"
    assert any(0.7 < f < 0.8 for f in seen), (
        f"no reading near three quarters: {seen}"
    )


def test_the_copy_opens_at_zero_and_closes_at_one_before_the_swap(
    tmp_path, monkeypatch,
):
    """
    The two fixed readings, which is all a fast destination produces.

    Zero has to arrive before the first byte moves, because it is what lets
    a caller change its label as the copy begins rather than a poll later —
    seconds, on the destination this exists for. One has to arrive before
    the swap, or the bar is still short when the file is already in place.
    """
    temp  = tmp_path / "a.tmp"
    final = tmp_path / "a.mkv"
    events: list[str] = []
    real_replace = os.replace

    async def on_staging(fraction):
        events.append(f"progress {fraction}")

    def _replace(a, b):
        events.append("replace")
        return real_replace(a, b)

    monkeypatch.setattr(sr.os, "replace", _replace)

    res = _run(_sizer_cmd([(str(temp), 8192)]),
               [StagedOutput(temp_path=str(temp), final_path=str(final))],
               on_staging_progress=on_staging)

    assert res.success is True, res.error
    assert events[0] == "progress 0.0", f"the copy did not announce itself: {events}"
    assert "replace" in events
    assert events.index("progress 1.0") < events.index("replace"), (
        f"the file was in place before the bar said so: {events}"
    )


def test_a_slow_copy_with_progress_enabled_still_completes(
    tmp_path, small_chunks, slow_disk,
):
    """
    Watching the copy must not interrupt it.

    asyncio.wait_for would: it cancels what it is waiting on when the
    timeout fires, so polling with it abandons the copy on the first tick
    and aborts a job that was doing nothing wrong.
    """
    temp  = tmp_path / "a.tmp"
    final = tmp_path / "a.mkv"
    final.write_bytes(b"ORIGINAL")
    ticks: list[float] = []

    async def on_staging(fraction):
        ticks.append(fraction)

    res = _run(_sizer_cmd([(str(temp), 20480)]),
               [StagedOutput(temp_path=str(temp), final_path=str(final))],
               on_staging_progress=on_staging)

    assert res.success is True, res.error
    assert final.stat().st_size == 20480, "the finished file never arrived"
    assert len([t for t in ticks if 0.0 < t < 1.0]) >= 2, (
        f"no intermediate readings, so this did not exercise the poll: {ticks}"
    )


def test_an_abort_mid_copy_settles_before_cleanup(tmp_path, monkeypatch,
                                                  small_chunks, slow_disk):
    """
    Cleanup must never run while the copy thread is still writing.

    A thread-pool thread cannot be interrupted, so cleanup racing a live
    copy deletes .part files the thread then recreates — the orphans
    cleanup exists to prevent.

    The suite already had a test named for the shield, but it built its own
    future and awaited its own shield inside the test body, so the real
    function was never called: removing the real shield left the whole
    suite green. This one aborts a real run.

    EQUIVALENT MUTANT, recorded rather than killed: removing
    asyncio.shield() from the poll loop still passes this, and that is not
    a gap here. asyncio.wait() does not cancel what it waits on even when
    the waiting task is itself cancelled — confirmed by running it — so the
    copy survives an abort either way. The shield stays because it makes
    that a property of this code rather than of a detail of asyncio.wait,
    and because the bare await it rejects WOULD cancel the copy.
    """
    temp  = tmp_path / "a.tmp"
    final = tmp_path / "a.mkv"
    finished = threading.Event()
    cleanup_saw_live_thread: list[bool] = []

    real_stage   = sr._stage_parts
    real_cleanup = sr.cleanup_temp_file

    def _stage(outputs, part_paths, flushed=None):
        try:
            real_stage(outputs, part_paths, flushed)
        finally:
            finished.set()

    def _cleanup(path):
        cleanup_saw_live_thread.append(not finished.is_set())
        return real_cleanup(path)

    monkeypatch.setattr(sr, "_stage_parts", _stage)
    monkeypatch.setattr(sr, "cleanup_temp_file", _cleanup)

    async def driver():
        task: asyncio.Task | None = None

        async def on_staging(fraction):
            # Mid-copy, not at the opening zero: the point is an abort that
            # lands while the thread is inside the copy.
            if fraction > 0.0:
                task.cancel()

        task = asyncio.create_task(run_staged_subprocess(
            _sizer_cmd([(str(temp), 40960)]),
            [StagedOutput(temp_path=str(temp), final_path=str(final))],
            on_staging_progress=on_staging,
        ))
        with pytest.raises(asyncio.CancelledError):
            await task

    asyncio.run(driver())

    assert finished.is_set(), "the copy thread was still running at the end"
    assert cleanup_saw_live_thread, "no cleanup ran, so this proved nothing"
    assert not any(cleanup_saw_live_thread), (
        "cleanup ran while the copy thread was still writing"
    )
    assert not list(tmp_path.glob("*.part")), "an aborted copy left a .part behind"


@pytest.mark.parametrize("raised, expect_failure_result", [
    (RuntimeError("callback exploded"), False),
    (OSError("callback exploded"), True),
])
def test_a_progress_callback_that_raises_does_not_race_the_copy(
    tmp_path, monkeypatch, small_chunks, slow_disk, raised,
    expect_failure_result,
):
    """
    The callback is awaited while the copy thread runs, which is a route
    into the cleanup handlers that did not exist before progress was
    reported: every other route reaches them after the thread has stopped.

    Both handlers are covered because they are separate paths chosen by
    exception type, and an OSError from a callback is indistinguishable at
    the handler from an OSError from the copy — which is exactly why that
    branch has to settle too.
    """
    temp  = tmp_path / "a.tmp"
    final = tmp_path / "a.mkv"
    final.write_bytes(b"ORIGINAL")
    finished = threading.Event()
    cleanup_saw_live_thread: list[bool] = []

    real_stage   = sr._stage_parts
    real_cleanup = sr.cleanup_temp_file

    def _stage(outputs, part_paths, flushed=None):
        try:
            real_stage(outputs, part_paths, flushed)
        finally:
            finished.set()

    def _cleanup(path):
        cleanup_saw_live_thread.append(not finished.is_set())
        return real_cleanup(path)

    monkeypatch.setattr(sr, "_stage_parts", _stage)
    monkeypatch.setattr(sr, "cleanup_temp_file", _cleanup)

    async def on_staging(fraction):
        if 0.0 < fraction < 1.0:
            raise raised

    args = (_sizer_cmd([(str(temp), 40960)]),
            [StagedOutput(temp_path=str(temp), final_path=str(final))])

    if expect_failure_result:
        res = _run(*args, on_staging_progress=on_staging)
        assert res.success is False
        assert "originals untouched" in res.error
    else:
        with pytest.raises(RuntimeError):
            _run(*args, on_staging_progress=on_staging)

    assert finished.is_set()
    assert cleanup_saw_live_thread, "no cleanup ran, so this proved nothing"
    assert not any(cleanup_saw_live_thread), (
        "cleanup ran while the copy thread was still writing"
    )
    assert final.read_bytes() == b"ORIGINAL", "the original was not left alone"


# ── The adapters ─────────────────────────────────────────────────────────────
#
# Real FFmpeg, following test_staging_hook.py: the bug this guards against is
# an adapter not passing the callback down, and a mocked runner cannot see it.

ffmpeg_required = pytest.mark.skipif(
    shutil.which("ffmpeg") is None, reason="ffmpeg not available",
)


def _tiny_job(tmp_path, settings):
    """A real one-second file with a droppable foreign audio track."""
    source = tmp_path / "source.mkv"
    subprocess.run(
        ["ffmpeg", "-v", "error", "-y",
         "-f", "lavfi", "-i", "testsrc=size=160x120:rate=10:duration=1",
         "-f", "lavfi", "-i", "sine=frequency=440:duration=1",
         "-f", "lavfi", "-i", "sine=frequency=880:duration=1",
         "-map", "0:v", "-map", "1:a", "-map", "2:a",
         "-metadata:s:a:0", "language=eng", "-metadata:s:a:1", "language=fre",
         "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac",
         "-f", "matroska", str(source)],
        check=True,
    )
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
    return source, decision, tracks


def _split(events):
    remux   = [e for e in events if e.current_action != STAGING_ACTION]
    writing = [e for e in events if e.current_action == STAGING_ACTION]
    return remux, writing


@ffmpeg_required
def test_ffmpeg_owns_the_first_share_of_the_bar(tmp_path, settings):
    """
    FFmpeg's own 100% is the end of its work, not the end of the job.
    Reporting it as 100 is what left the bar full while the file was still
    being written.
    """
    source, decision, tracks = _tiny_job(tmp_path, settings)
    events = []

    async def on_progress(prog):
        events.append(prog)

    result = asyncio.run(execute_ffmpeg(
        str(source), str(source), decision, tracks, job_id=1,
        progress_callback=on_progress,
    ))

    assert result.success is True, result.error
    remux, _ = _split(events)
    assert remux, "FFmpeg reported no progress at all"
    top = max(e.percent for e in remux)
    # Not equality: FFmpeg's last out_time lands fractionally short of the
    # probed duration, so its own percentage finishes near 100 rather than
    # at it. The property is that it never crosses into the write's share.
    assert top <= SUBPROCESS_PROGRESS_SHARE, (
        f"FFmpeg reported {top}, past its share of the bar — unscaled, its "
        f"own 100% reads as a finished job while the file is still in /tmp"
    )
    assert top > SUBPROCESS_PROGRESS_SHARE * 0.9, (
        f"FFmpeg only reached {top} on a job it completed"
    )


@ffmpeg_required
def test_the_write_phase_covers_the_rest(tmp_path, settings):
    source, decision, tracks = _tiny_job(tmp_path, settings)
    events = []

    async def on_progress(prog):
        events.append(prog)

    result = asyncio.run(execute_ffmpeg(
        str(source), str(source), decision, tracks, job_id=1,
        progress_callback=on_progress,
    ))

    assert result.success is True, result.error
    _, writing = _split(events)
    assert writing, (
        "nothing reported the write, so the bar stops where FFmpeg does"
    )
    assert writing[0].percent == SUBPROCESS_PROGRESS_SHARE
    assert writing[-1].percent == 100.0
    assert events[-1] is writing[-1], "the write was not the last thing reported"


@ffmpeg_required
def test_the_combined_executor_reports_both_phases(tmp_path, settings):
    """
    Its own test, not a parametrisation of the one above: the two adapters
    build their progress separately, so a fix applied to one says nothing
    about the other. This is the path a remux with subtitle sidecars takes.
    """
    source, decision, tracks = _tiny_job(tmp_path, settings)
    srt = tmp_path / "source.eng.srt"
    events = []

    async def on_progress(prog):
        events.append(prog)

    result, _ = asyncio.run(execute_ffmpeg_combined(
        str(source), str(source), decision, tracks, [], job_id=1,
        progress_callback=on_progress,
    ))

    assert result.success is True, result.error
    assert not srt.exists(), "fixture changed: this job has no sidecars"
    remux, writing = _split(events)
    assert remux, "FFmpeg reported no progress at all"
    top = max(e.percent for e in remux)
    assert top <= SUBPROCESS_PROGRESS_SHARE, f"FFmpeg reported {top}"
    assert top > SUBPROCESS_PROGRESS_SHARE * 0.9, f"FFmpeg only reached {top}"
    assert writing, "the combined executor does not report its write"
    assert writing[0].percent == SUBPROCESS_PROGRESS_SHARE
    assert writing[-1].percent == 100.0


# ── AC3 Forge ────────────────────────────────────────────────────────────────
#
# Its own section, and its own tests, because forge builds its progress
# separately: ForgeProgress is a different dataclass whose label field is
# .action, so nothing above touches this path. A forge job also re-encodes
# audio rather than copying it, which makes its FFmpeg phase genuinely long —
# and leaves the copy afterwards exactly as long as it was, which is the case
# for giving it the same fixed share.


def _forge_job(tmp_path):
    """A real file with a 5.1 AAC track, and the command to add AC3 to it."""
    source = tmp_path / "source.mkv"
    subprocess.run(
        ["ffmpeg", "-v", "error", "-y",
         "-f", "lavfi", "-i", "testsrc=size=160x120:rate=10:duration=1",
         "-f", "lavfi", "-i", "sine=frequency=440:duration=1",
         "-map", "0:v", "-map", "1:a",
         "-c:v", "libx264", "-pix_fmt", "yuv420p",
         "-c:a", "aac", "-ac", "6",
         "-f", "matroska", str(source)],
        check=True,
    )
    temp   = tmp_path / "forge.tmp"
    output = tmp_path / "output.mkv"
    cmd = build_add_ac3_command(
        input_path=str(source), temp_path=str(temp),
        aac_stream_index=1, audio_track_count=1, container="mkv",
    )
    return cmd, source, temp, output


def _forge_split(events):
    work    = [e for e in events if e.action != STAGING_ACTION]
    writing = [e for e in events if e.action == STAGING_ACTION]
    return work, writing


@ffmpeg_required
def test_forge_transcode_owns_the_first_share_of_the_bar(tmp_path):
    cmd, source, temp, output = _forge_job(tmp_path)
    events = []

    async def on_progress(prog):
        events.append(prog)

    result = asyncio.run(run_forge_command(
        cmd, str(source), str(output), str(temp),
        action_label="Adding AC3 5.1 track",
        progress_callback=on_progress,
    ))

    assert result.success is True, result.error
    work, _ = _forge_split(events)
    assert work, "the transcode reported no progress at all"
    top = max(e.percent for e in work)
    assert top <= SUBPROCESS_PROGRESS_SHARE, (
        f"the transcode reported {top}, past its share of the bar"
    )
    assert top > SUBPROCESS_PROGRESS_SHARE * 0.9, (
        f"the transcode only reached {top} on a job it completed"
    )


@ffmpeg_required
def test_forge_reports_its_write_to_the_library(tmp_path):
    """
    The half a forge job used to spend at 100% with "Adding AC3 5.1 track"
    still on screen.
    """
    cmd, source, temp, output = _forge_job(tmp_path)
    events = []

    async def on_progress(prog):
        events.append(prog)

    result = asyncio.run(run_forge_command(
        cmd, str(source), str(output), str(temp),
        action_label="Adding AC3 5.1 track",
        progress_callback=on_progress,
    ))

    assert result.success is True, result.error
    _, writing = _forge_split(events)
    assert writing, (
        "nothing reported the write, so the forge bar stops where FFmpeg does"
    )
    assert writing[0].percent == SUBPROCESS_PROGRESS_SHARE
    assert writing[-1].percent == 100.0
    assert events[-1] is writing[-1], "the write was not the last thing reported"


@ffmpeg_required
def test_forge_without_a_progress_callback_still_runs(tmp_path):
    """
    Every forge caller passes one today, so a staging callback that assumed
    the same would break nothing here and everything on the first caller
    that did not — the shape of bug the adapter tests in test_staging_hook.py
    exist for.
    """
    cmd, source, temp, output = _forge_job(tmp_path)

    result = asyncio.run(run_forge_command(
        cmd, str(source), str(output), str(temp),
        action_label="Adding AC3 5.1 track",
    ))

    assert result.success is True, result.error
    assert output.exists(), "the forged file never arrived"
