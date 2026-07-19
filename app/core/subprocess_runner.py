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
import shutil
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


async def run_staged_subprocess(
    cmd: list[str],
    outputs: list[StagedOutput],
    *,
    on_progress_line: Callable[[dict[str, str]], Awaitable[None]] | None = None,
    stderr_tail_lines: int = 30,
    timeout_seconds: float | None = None,
) -> SubprocessRunResult:
    """
    Run `cmd` as a subprocess, stream progress, then stage output files.

    timeout_seconds: if set and > 0, the entire subprocess (drain + wait) is
    wrapped in asyncio.wait_for() with this limit.  On timeout the process is
    killed and a clean failure result is returned.  Set to None or 0 to disable.

    All outputs succeed together or all fail together — any failure cleans up
    all temp paths.  On exception temp paths are cleaned and the exception is
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
        # Caught by independent review.
        #
        # Phase 1 copies every temp to "<final>.part" on the DESTINATION
        # filesystem — originals untouched, any failure (incl. ENOSPC)
        # cleans up the .part files and fails the job with every original
        # exactly as it was. The worker's disk-space preflight already
        # requires file-size free in the output dir while the original
        # still exists, which is precisely this phase's peak requirement.
        # Each .part is fsync'd: os.replace guarantees which NAME you
        # see, not that the new bytes survived a power cut — without the
        # fsync, a crash shortly after the swap could leave the new name
        # pointing at data still in the page cache. (Directory-entry
        # fsync after the rename is deliberately omitted as
        # disproportionate here — worst realistic post-crash outcome is
        # the OLD file still fully in place, i.e. a retry, not a loss.)
        #
        # Phase 2 swaps each .part into place with os.replace — atomic on
        # POSIX, and guaranteed same-filesystem since the .part sits in
        # the final's own directory. The exposure drops from
        # "gigabytes of copying with no original" to per-file metadata
        # renames.
        part_paths: list[str] = []
        try:
            for o in outputs:
                part = o.final_path + ".part"
                shutil.copyfile(o.temp_path, part)
                with open(part, "rb") as f:
                    os.fsync(f.fileno())
                part_paths.append(part)
        except OSError as exc:
            for p in part_paths + [o.final_path + ".part" for o in outputs]:
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

        for o in outputs:
            os.replace(o.final_path + ".part", o.final_path)

        # Temps are no longer consumed by a move — remove them explicitly.
        for o in outputs:
            cleanup_temp_file(o.temp_path)

        return SubprocessRunResult(success=True, error=None, returncode=proc.returncode)

    except asyncio.CancelledError:
        for o in outputs:
            cleanup_temp_file(o.temp_path)
            cleanup_temp_file(o.final_path + ".part")
        raise
    except Exception:
        for o in outputs:
            cleanup_temp_file(o.temp_path)
            cleanup_temp_file(o.final_path + ".part")
        raise
