"""
Generic subprocess execution + progress streaming + staged temp-file output.

This module has no knowledge of FFmpeg command-building, ProcessingDecision,
or forge jobs — it is pure infrastructure for "run a command, optionally
stream -progress pipe:1 key=value lines to a callback, then move one or more
temp output files to their final destinations on success."

Used by both the main remux/extract pipeline (app/core/ffmpeg.py) and the
AC3 forge feature (app/core/forge.py), which previously each maintained
their own separate, near-identical copy of this logic.
"""

import asyncio
import os
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from app.config import settings as app_settings


# ── Data classes ─────────────────────────────────────────────────────────────


@dataclass
class StagedOutput:
    """One temp→final file pair to move after a successful subprocess run."""
    temp_path: str
    final_path: str


@dataclass
class SubprocessRunResult:
    success: bool
    error: str | None
    returncode: int | None = None


# ── Progress parsing ─────────────────────────────────────────────────────────


def parse_out_time_seconds(progress_kv: dict[str, str]) -> float:
    """
    Convert the 'out_time_us' field from an FFmpeg `-progress pipe:1` line
    into seconds.  Guards against FFmpeg emitting "N/A" before the first frame.
    """
    try:
        time_us = int(progress_kv.get("out_time_us", "0") or "0")
    except (ValueError, TypeError):
        time_us = 0
    return time_us / 1_000_000


# ── File helpers ──────────────────────────────────────────────────────────────


def cleanup_temp_file(path: str) -> None:
    """Remove a temp file if it exists. Never raises — best-effort cleanup."""
    try:
        if os.path.exists(path):
            os.remove(path)
    except OSError:
        pass


async def probe_duration(path: str) -> float | None:
    """
    Quick ffprobe call to get duration in seconds.
    Returns None on any failure — callers must handle None gracefully.
    """
    try:
        proc = await asyncio.create_subprocess_exec(
            app_settings.FFPROBE_PATH,
            "-v", "quiet",
            "-show_entries", "format=duration",
            "-of", "csv=p=0",
            path,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
        )
        stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=30)
        return float(stdout.decode().strip())
    except Exception:
        return None


# ── The generic executor ─────────────────────────────────────────────────────


def staged_part_path(output: StagedOutput) -> str:
    """
    Where a finished copy waits for its atomic swap into place.

    In the destination directory, because os.replace is only atomic on one
    filesystem and the swap is what guarantees a half-written file never
    appears under the final name. Named after the temp file rather than the
    final one, because appending to the final name makes the temporary
    longer than the thing it stands in for — and a media filename can
    already be at the 255-byte limit its own extension left it at.

    That failed in the wild on a sidecar: the final name fitted at 252
    bytes, the .part did not at 257, and the job failed after FFmpeg had
    already done the work. FFmpeg's own output learned this earlier, which
    is why it writes job_35106.remuxarr_tmp instead of anything derived
    from the file. This is the same fix, one step further down.

    The ".part" suffix is load-bearing beyond this module: the startup
    sweep clears "*.part" under the scan paths, and without it an
    interrupted run leaves a copy the scanner never surfaces, since .part
    is not in MEDIA_EXTENSIONS.

    Uniqueness rests on temp paths being distinct, which they are by
    construction: two outputs of one FFmpeg run cannot share an output
    file, and the names carry the job id, so neither two outputs of one job
    nor two jobs staging into the same directory can collide.
    """
    return os.path.join(
        os.path.dirname(output.final_path),
        os.path.basename(output.temp_path) + ".part",
    )


# How much is written between flushes, and how often the event loop looks at
# the counter those flushes advance.
#
# Sized against the slow case rather than the fast one: staging to a
# parity-protected array runs at tens of MB/s, so 64 MiB is a couple of
# seconds between updates there and a fraction of one on an SSD. Smaller
# would report more often at the cost of interrupting the write more often,
# and the write is the thing the user is waiting for.
_STAGE_FLUSH_BYTES  = 64 * 1024 * 1024
_STAGE_COPY_BUFFER  = 1024 * 1024
_STAGE_POLL_SECONDS = 0.5


def _copy_and_flush(src: str, dst: str, flushed: list[int] | None) -> None:
    """
    Copy one file, flushing as it goes, and count only what reached the disk.

    shutil.copyfile did this job and did it faster, and was still the wrong
    tool once anything wanted to watch the copy. It returns when the bytes
    are in the page cache, not when they are on the disk, so the caller's
    fsync afterwards carried a large and invisible share of the wall time —
    on a 700 MB file here, between 40 and 60 per cent of it. Progress
    counted from a copyfile would therefore reach the end and then stop
    dead for the length of that fsync, which is the stall it was added to
    report.

    Flushing every _STAGE_FLUSH_BYTES and advancing the counter AFTER the
    flush makes the number mean "this much survives a power cut", so it
    moves at the speed of the destination rather than the speed of RAM.

    The source is opened before the destination so a missing temp raises
    before any .part exists — copyfile's order, which the caller's cleanup
    contract relies on.
    """
    with open(src, "rb") as fi, open(dst, "wb") as fo:
        since = 0
        while True:
            chunk = fi.read(_STAGE_COPY_BUFFER)
            if not chunk:
                break
            fo.write(chunk)
            since += len(chunk)
            if since >= _STAGE_FLUSH_BYTES:
                fo.flush()
                os.fdatasync(fo.fileno())
                if flushed is not None:
                    flushed[0] += since
                since = 0
        fo.flush()
        # fsync, not fdatasync: this is the durability guarantee the swap
        # depends on, and the test that pins it counts fsync calls.
        os.fsync(fo.fileno())
        if flushed is not None:
            flushed[0] += since


def _stage_parts(
    outputs: list[StagedOutput],
    part_paths: list[str],
    flushed: list[int] | None = None,
) -> None:
    """
    Copy every temp output to its staged .part and fsync it. Synchronous.

    staged_part_path names each one; it is not "<final>.part".

    MUST be called through run_in_executor, never directly from a coroutine.
    These are potentially multi-gigabyte cross-filesystem copies (tmpfs → array):
    running one on the event loop stalls WebSocket broadcasts, API responses and
    the worker's job-claiming loop for its entire duration, which is what this
    helper exists to prevent.

    `part_paths` is caller-owned and appended to as each copy completes, so a
    partially-finished run is still visible to the caller's OSError handler
    after the exception propagates out of the executor. Do not turn this into a
    return value — on the failure path there is no return.

    `flushed`, when given, is a single-element list this function adds to as
    bytes reach the disk, across ALL outputs rather than per file. It is read
    from the event loop while this runs on a worker thread: a list holding an
    int is written here and only read there, which needs no lock, and an int
    is the whole of the shared state on purpose. Nothing else may be handed
    back this way — anything requiring the two threads to agree on more than
    one number belongs behind a queue.

    Raises OSError (including ENOSPC), handled by the caller.
    """
    for o in outputs:
        part = staged_part_path(o)
        _copy_and_flush(o.temp_path, part, flushed)
        part_paths.append(part)


async def run_staged_subprocess(
    cmd: list[str],
    outputs: list[StagedOutput],
    *,
    on_progress_line: Callable[[dict[str, str]], Awaitable[None]] | None = None,
    stderr_tail_lines: int = 30,
    timeout_seconds: float | None = None,
    before_staging: Callable[[], Awaitable[str | None]] | None = None,
    on_staging_progress: Callable[[float], Awaitable[None]] | None = None,
) -> SubprocessRunResult:
    """
    Run `cmd` as a subprocess, stream progress, then stage output files.

    timeout_seconds: if set and > 0, the entire subprocess (drain + wait) is
    wrapped in asyncio.wait_for() with this limit.  On timeout the process is
    killed and a clean failure result is returned.  Set to None or 0 to disable.

    before_staging: called once the subprocess has succeeded and every temp
    is verified present, but before anything on the destination is touched —
    the only point at which the originals and the finished outputs both
    exist.  Return None to continue, or an error string to abort the run with
    every original untouched.  See the call site for the full contract.

    on_staging_progress: called with 0.0 when the copy to the destination
    starts, with the fraction of total bytes flushed to disk as it runs, and
    with 1.0 once every output is staged.  It exists because the copy is the
    long silent tail of a run: the subprocess reports its own progress and
    then finishes, while the bytes still have to travel to the destination.
    Fractions are of the total across all outputs, so one bar covers a main
    file and its sidecars rather than restarting per file.

    Staging is two-pass, so a failure while copying leaves every original
    untouched and every output unswapped.  The swap pass itself is a loop of
    per-file os.replace calls: each one is atomic, but the loop is not, so a
    failure part-way through leaves the earlier finals swapped and the rest
    not.  That window is the residual the two-pass design deliberately trades
    down to — same-filesystem metadata renames rather than gigabytes of
    copying with no original — not one it removes.  Any failure cleans up all
    temp paths.  On exception temp paths are cleaned and the exception is
    re-raised so the caller's job-failure logic runs normally.
    """
    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )

        stderr_lines: list[str] = []
        progress_kv: dict[str, str] = {}

        async def drain_stderr() -> None:
            assert proc.stderr
            async for raw in proc.stderr:
                line = raw.decode(errors="replace").strip()
                if line:
                    stderr_lines.append(line)

        async def drain_progress() -> None:
            assert proc.stdout
            async for raw in proc.stdout:
                line = raw.decode(errors="replace").strip()
                if "=" not in line:
                    continue
                key, _, val = line.partition("=")
                progress_kv[key.strip()] = val.strip()
                if key.strip() == "progress" and on_progress_line:
                    await on_progress_line(dict(progress_kv))

        async def _run() -> None:
            await asyncio.gather(drain_stderr(), drain_progress())
            await proc.wait()

        # ── Timeout guard ─────────────────────────────────────────────────
        effective_timeout = float(timeout_seconds) if timeout_seconds else None
        try:
            if effective_timeout:
                await asyncio.wait_for(_run(), timeout=effective_timeout)
            else:
                await _run()
        except asyncio.TimeoutError:
            try:
                proc.kill()
                await proc.wait()
            except Exception:
                pass
            for o in outputs:
                cleanup_temp_file(o.temp_path)
            minutes = int(effective_timeout // 60)
            return SubprocessRunResult(
                success=False,
                error=f"Job timed out after {minutes} minute(s) — process killed",
                returncode=None,
            )
        except asyncio.CancelledError:
            # The OUTER task (the one wrapping this whole job) was cancelled
            # — e.g. the user pressed Abort. asyncio.wait_for propagates a
            # cancelled parent task's CancelledError rather than converting
            # it to a TimeoutError, so this branch is what actually fires
            # for a manual abort. Without this handler, the coroutine
            # unwinds past proc.kill() entirely, leaving an orphaned FFmpeg
            # process still writing to the temp file.
            try:
                proc.kill()
                await proc.wait()
            except Exception:
                pass
            for o in outputs:
                cleanup_temp_file(o.temp_path)
            raise   # re-raise so the cancellation still propagates to the caller

        if proc.returncode != 0:
            for o in outputs:
                cleanup_temp_file(o.temp_path)
            error = (
                "\n".join(stderr_lines[-stderr_tail_lines:])
                or "Unknown error (no stderr output)"
            )
            return SubprocessRunResult(
                success=False, error=error, returncode=proc.returncode
            )

        missing = [o.temp_path for o in outputs if not os.path.exists(o.temp_path)]
        if missing:
            for o in outputs:
                cleanup_temp_file(o.temp_path)
            return SubprocessRunResult(
                success=False,
                error=f"Temp file(s) missing after command completed: {', '.join(missing)}",
                returncode=proc.returncode,
            )

        # ── Last look at both versions of the file ─────────────────────────
        # This is the only moment in the run where the ORIGINAL and the
        # finished OUTPUT both exist: the subprocess has succeeded, every
        # temp is verified present, and nothing on the destination has
        # been touched yet. One line further down the originals start
        # being overwritten and the pre-job state is gone.
        #
        # That window is what the revert feature's capture needs — it
        # compares the two to work out what the job actually destroyed,
        # rather than what it planned to. Exposed as a hook rather than
        # done here because this function has no business knowing about
        # revert points; it knows about the window.
        #
        # A hook that returns an error string aborts the run cleanly, with
        # every original exactly as it was, matching what a staging
        # failure does. That is deliberately the same contract as the rest
        # of this function: nothing is half-applied. It is also what makes
        # "refuse to process when a revert point cannot be recorded" a
        # real option rather than a best-effort one — the refusal lands
        # while the source file is still untouched.
        #
        # A hook that RAISES is not caught here. It falls to the outer
        # handler, which cleans up temps and .part files and re-raises to
        # the caller's job-failure logic — same outcome for the file,
        # noisier for the operator, which is the right treatment for an
        # unexpected error as opposed to a considered refusal.
        if before_staging is not None:
            hook_error = await before_staging()
            if hook_error:
                for o in outputs:
                    cleanup_temp_file(o.temp_path)
                return SubprocessRunResult(
                    success=False,
                    error=hook_error,
                    returncode=proc.returncode,
                )

        # ── Stage outputs into place — two phases, originals protected ────
        # The previous implementation deleted each original FIRST, then
        # shutil.move()d the temp into place. With temps on tmpfs and
        # finals on the array, that move is a NON-atomic cross-filesystem
        # copy — so for the entire duration of a potentially multi-GB
        # copy, the original was already gone and the final was partial.
        # A crash, power loss, or mid-copy error (ENOSPC) in that window
        # lost the original outright: FFmpeg had succeeded, the original
        # was deleted, and the only complete copy of the output was a
        # temp on a RAM-backed tmpfs. Worse with multiple outputs (main
        # file + SRT sidecars): a failure mid-loop left earlier originals
        # deleted-and-replaced and later ones deleted with nothing staged,
        # and the outer exception handler then deleted the temps too.
        #
        # The first pass copies every temp to its staged .part on the DESTINATION
        # filesystem — originals untouched, any failure (incl. ENOSPC)
        # cleans up the .part files and fails the job with every original
        # exactly as it was. The worker's disk-space preflight already
        # requires file-size free in the output dir while the original
        # still exists, which is precisely this pass's peak requirement.
        # Each .part is fsync'd: os.replace guarantees which NAME you
        # see, not that the new bytes survived a power cut — without the
        # fsync, a crash shortly after the swap could leave the new name
        # pointing at data still in the page cache. (Directory-entry
        # fsync after the rename is deliberately omitted as
        # disproportionate here — worst realistic post-crash outcome is
        # the OLD file still fully in place, i.e. a retry, not a loss.)
        #
        # The second pass swaps each .part into place with os.replace — atomic on
        # POSIX, and guaranteed same-filesystem since the .part sits in
        # the final's own directory. The exposure drops from
        # "gigabytes of copying with no original" to per-file metadata
        # renames.
        #
        # The first pass runs in an executor rather than inline: it is the one
        # genuinely long blocking operation left in this coroutine, and on the
        # event loop it froze the UI (no progress updates, no API responses) for
        # the whole copy. _stage_parts appends to part_paths as it goes, so the
        # handler below still sees exactly which .part files a failed run had
        # created.
        part_paths: list[str] = []
        # Total across every output, measured before the copy starts: the
        # temps all exist by here (checked above) and none of them changes
        # size while being copied.
        total_bytes = sum(os.path.getsize(o.temp_path) for o in outputs)
        flushed = [0]
        staging = asyncio.ensure_future(
            asyncio.get_running_loop().run_in_executor(
                None, _stage_parts, outputs, part_paths, flushed
            )
        )
        try:
            # Reported before the first byte moves, so a caller showing a
            # label can change it as the copy begins rather than one poll
            # later. On a slow destination that gap is seconds.
            if on_staging_progress:
                await on_staging_progress(0.0)
            # shield(), not a bare await: staging is now interruptible where the
            # inline loop it replaced was not, and an abort landing mid-copy
            # would otherwise send us straight to the outer CancelledError
            # handler while this thread is still running. A default
            # ThreadPoolExecutor thread cannot be interrupted, so that handler's
            # cleanup would race a live copy — deleting .part files the thread
            # then recreates for later outputs, leaving exactly the orphans the
            # cleanup exists to prevent. Shielding lets the copy finish, so
            # cleanup always runs against a settled filesystem, and the abort is
            # re-raised immediately afterwards. This preserves the previous
            # behaviour: staging was effectively uninterruptible before.
            #
            # asyncio.wait() rather than wait_for(): wait_for CANCELS what it
            # is waiting on when the timeout fires, which here would abandon
            # the copy every poll. wait() only tells us whether it finished
            # yet, and leaves it alone — including when this coroutine is
            # itself cancelled, which is how the shield above survives a
            # poll landing at the same moment as an abort.
            shielded = asyncio.shield(staging)
            while True:
                done, _ = await asyncio.wait(
                    {shielded}, timeout=_STAGE_POLL_SECONDS
                )
                if done:
                    break
                if on_staging_progress and total_bytes:
                    await on_staging_progress(min(1.0, flushed[0] / total_bytes))
            await shielded          # re-raises whatever the copy raised
            if on_staging_progress:
                await on_staging_progress(1.0)
        except asyncio.CancelledError:
            try:
                await staging
            except Exception:
                pass          # already aborting; a staging failure changes nothing
            raise
        except OSError as exc:
            # Usually the copy's own error, in which case it has already
            # stopped. Not always: on_staging_progress is awaited while the
            # thread runs, so one raising OSError arrives here mid-copy, and
            # the cleanup below would then race it — the same race the shield
            # exists to prevent. Settling first costs nothing in the common
            # case, where this returns immediately.
            try:
                await staging
            except Exception:
                pass
            for p in part_paths + [staged_part_path(o) for o in outputs]:
                cleanup_temp_file(p)
            for o in outputs:
                cleanup_temp_file(o.temp_path)
            return SubprocessRunResult(
                success=False,
                error=(
                    f"Failed staging output to destination "
                    f"(originals untouched): {exc}"
                ),
                returncode=proc.returncode,
            )
        except Exception:
            # Anything else raised while the copy thread is live — in
            # practice a progress callback failing. Same reasoning as the
            # OSError branch: settle first, then let the outer handler clean
            # up against a filesystem nothing is still writing to.
            try:
                await staging
            except Exception:
                pass
            raise

        for o in outputs:
            os.replace(staged_part_path(o), o.final_path)

        # Temps are no longer consumed by a move — remove them explicitly.
        for o in outputs:
            cleanup_temp_file(o.temp_path)

        return SubprocessRunResult(success=True, error=None, returncode=proc.returncode)

    except asyncio.CancelledError:
        for o in outputs:
            cleanup_temp_file(o.temp_path)
            cleanup_temp_file(staged_part_path(o))
        raise
    except Exception:
        for o in outputs:
            cleanup_temp_file(o.temp_path)
            cleanup_temp_file(staged_part_path(o))
        raise
