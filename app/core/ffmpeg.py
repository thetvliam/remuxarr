"""
FFmpeg command builder and async executor.

build_ffmpeg_command()       — pure function, returns argv for the main remux
execute_ffmpeg()              — runs the main remux, streams progress via async callback
build_extract_subtitle_command() — pure function, argv for one SRT extraction
execute_subtitle_extraction()    — runs a single subtitle extraction to a sidecar .srt
determine_output_path()       — decides where to write the output file
"""

import logging
import os
import shutil
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path

from app.config import settings as app_settings
from app.core.decision import ProcessingDecision
from app.core.subprocess_runner import (
    StagedOutput,
    cleanup_temp_file,
    parse_out_time_seconds,
    probe_duration,
    run_staged_subprocess,
)

logger = logging.getLogger(__name__)


# ── Data classes ───────────────────────────────────────────────────────────────


@dataclass
class FFmpegProgress:
    percent: float  # 0–100
    current_time: float  # seconds processed so far
    speed: str  # "2.50x"
    current_action: str  # human label shown in the UI


@dataclass
class FFmpegResult:
    success: bool
    output_path: str | None
    error: str | None
    output_size: int | None


@dataclass
class ExtractionResult:
    success: bool
    output_path: str | None
    error: str | None


# ── Command builder — main remux ────────────────────────────────────────────────


def build_ffmpeg_command(
    input_path: str,
    output_path: str,
    decision: ProcessingDecision,
    all_tracks: list[dict],
) -> list[str]:
    """
    Return the full FFmpeg argv for this decision.

    Strategy
    --------
    • All video streams  → always mapped, always copy
    • Kept audio streams → copy, OR transcode (AAC 5.1 → AC3)
    • Kept sub streams   → copy (subtitles being extracted to external SRT,
      or dropped entirely, are excluded from the map list)
    • -progress pipe:1   → structured key=value progress on stdout
    • -nostats -v error  → suppress noisy stderr; errors still appear
    """
    dropped = {
        a.stream_index for a in decision.actions if a.action_type == "drop_track"
    }
    # Subtitles being extracted to an external .srt are removed from the
    # muxed output entirely — treat them the same as "dropped" for mapping.
    extracted = {
        a.stream_index for a in decision.actions if a.action_type == "extract_subtitle"
    }
    transcode_map = {
        a.stream_index: a
        for a in decision.actions
        if a.action_type == "transcode_track"
    }
    # Tracks whose language metadata should be overwritten — set by
    # decision.py's language-fix pass when fix_undefined_language is enabled.
    language_map = {
        a.stream_index: a.target_language
        for a in decision.actions
        if getattr(a, "target_language", None)
    }

    cmd = [
        app_settings.FFMPEG_PATH,
        "-i", input_path,
        "-y",
        "-v", "error",  # suppress info/warnings but keep error messages
        "-nostats",
        "-progress", "pipe:1",  # structured progress → stdout
    ]

    video_tracks = [t for t in all_tracks if t["track_type"] == "video"]
    audio_tracks = [t for t in all_tracks if t["track_type"] == "audio"]
    sub_tracks   = [t for t in all_tracks if t["track_type"] == "subtitle"]

    # ── Video: always copy ─────────────────────────────────────────────────
    for t in video_tracks:
        cmd += ["-map", f"0:{t['stream_index']}"]
    if video_tracks:
        cmd += ["-c:v", "copy"]

    # ── Audio ──────────────────────────────────────────────────────────────
    kept_audio = [t for t in audio_tracks if t["stream_index"] not in dropped]
    for out_idx, t in enumerate(kept_audio):
        si = t["stream_index"]
        cmd += ["-map", f"0:{si}"]
        action = transcode_map.get(si)
        if action and action.track_type == "audio":
            cmd += [f"-c:a:{out_idx}", action.output_codec]
            for opt_k, opt_v in action.output_codec_options.items():
                # e.g. -b:a:0 640k  or  -ac:0 6
                cmd += [f"-{opt_k}:{out_idx}", str(opt_v)]
        else:
            cmd += [f"-c:a:{out_idx}", "copy"]
        if si in language_map:
            cmd += [f"-metadata:s:a:{out_idx}", f"language={language_map[si]}"]

    # ── Subtitles ──────────────────────────────────────────────────────────
    # Anything dropped or extracted to external SRT is excluded from the
    # muxed output. Everything else (only possible when SRT extraction is
    # disabled) is copied as-is — no transcoding.
    kept_subs = [
        t for t in sub_tracks
        if t["stream_index"] not in dropped and t["stream_index"] not in extracted
    ]
    for out_idx, t in enumerate(kept_subs):
        cmd += ["-map", f"0:{t['stream_index']}"]
        if t["stream_index"] in language_map:
            cmd += [f"-metadata:s:s:{out_idx}", f"language={language_map[t['stream_index']]}"]
    if kept_subs:
        cmd += ["-c:s", "copy"]

    # ── Output format & flags ──────────────────────────────────────────────
    # Always pass -f explicitly: the temp file ends in .remuxarr_tmp which
    # FFmpeg doesn't recognise, so it would otherwise refuse to mux.
    _CONTAINER_FORMAT = {
        "mkv": "matroska",
        "mp4": "mp4",
        "avi": "avi",
        "ts": "mpegts",
        "m2ts": "mpegts",
        "wmv": "asf",
        "webm": "webm",
        "mov": "mov",
    }
    out_fmt = _CONTAINER_FORMAT.get(decision.target_container or "mkv", "matroska")
    logger.info(
        "build_ffmpeg_command: decision.target_container=%r -> out_fmt=%r "
        "(output_path=%s)",
        decision.target_container, out_fmt, output_path,
    )
    cmd += ["-f", out_fmt]

    # Apply +faststart when the output is MP4 — this covers two cases:
    #   1. Container conversion (MKV → MP4): always web-optimise the new
    #      file, regardless of add_faststart_to_mp4 — a genuinely new MP4
    #      should always be web-optimised.
    #   2. add_faststart action: rewriting an EXISTING MP4 that was
    #      missing it. Gated on decision actually having generated that
    #      action, which decision.py only does when add_faststart_to_mp4
    #      is enabled AND the existing file genuinely needs it.
    #
    # Previously this only checked target_container == "mp4", which is
    # true for every MP4 output regardless of the setting's value or
    # whether an add_faststart action was ever generated — meaning the
    # setting had no effect at all and every MP4 got +faststart
    # unconditionally, including plain in-place edits (e.g. a pure
    # language-tag fix) on files that already had it correctly disabled.
    has_container_conversion = any(a.action_type == "change_container" for a in decision.actions)
    has_faststart_action     = any(a.action_type == "add_faststart"    for a in decision.actions)
    if decision.target_container == "mp4" and (has_container_conversion or has_faststart_action):
        cmd += ["-movflags", "+faststart"]

    cmd.append(output_path)
    return cmd


# ── Command builder — subtitle extraction ────────────────────────────────────────


def build_extract_subtitle_command(
    input_path: str,
    stream_index: int,
    output_srt_path: str,
) -> list[str]:
    """
    Return the FFmpeg argv to extract a single subtitle stream to an
    external SubRip (.srt) file.

    Works for any text-based subtitle codec FFmpeg can decode (SubRip,
    mov_text, ASS/SSA) — the "srt" subtitle encoder handles the conversion.
    """
    return [
        app_settings.FFMPEG_PATH,
        "-i", input_path,
        "-y",
        "-v", "error",
        "-map", f"0:{stream_index}",
        "-c:s", "srt",
        "-f", "srt",
        output_srt_path,
    ]


# ── Executor — main remux ───────────────────────────────────────────────────────


async def execute_ffmpeg(
    input_path: str,
    output_path: str,
    decision: ProcessingDecision,
    all_tracks: list[dict],
    job_id: int,
    progress_callback: Callable[[FFmpegProgress], Awaitable[None]] | None = None,
    timeout_seconds: float | None = None,
) -> FFmpegResult:
    """
    Run FFmpeg asynchronously.

    • Writes to a temp file in TEMP_DIR (typically a RAM-backed or fast
      cache location), then moves to output_path on success.  This keeps
      FFmpeg I/O off the main array while it is running, avoiding contention
      with other array activity.  The final move is a sequential write to
      the array and does not compete with encode I/O.
    • Parses -progress pipe:1 output to emit FFmpegProgress objects.
    • Always cleans up the temp file on failure.

    Thin adapter over the shared run_staged_subprocess() executor in
    subprocess_runner.py — this function only handles what's specific to
    the main remux: building the temp path, building the FFmpeg command,
    describing the current action, and translating raw progress snapshots
    into FFmpegProgress objects. The subprocess spawn/drain/stage/cleanup
    machinery itself lives in the shared module (also used by forge.py).
    """
    # Stage in TEMP_DIR (e.g. /tmp/remuxarr) when space allows; fall back
    # to the output file's own directory when TEMP_DIR is too full.
    #
    # The temp filename is derived from job_id, NOT the source/output
    # filename — deliberately. Appending ".remuxarr_tmp" (13 bytes) to an
    # already-long filename can push it past the 255-byte NAME_MAX most
    # Linux filesystems enforce per path component, even when the FINAL
    # filename (without the suffix) is comfortably under that limit. A
    # multi-episode file with several episode titles joined together by
    # Sonarr's naming format is exactly the kind of filename this hits —
    # confirmed in production: a 247-byte original filename failed with
    # "File name too long" purely because of the 260-byte temp version.
    # job_id is always short and always unique, so this eliminates the
    # whole class of failure rather than just raising the threshold.
    tmp_dir     = _pick_temp_dir(input_path)
    temp_output = os.path.join(tmp_dir, f"job_{job_id}.remuxarr_tmp")
    os.makedirs(tmp_dir, exist_ok=True)
    cmd = build_ffmpeg_command(input_path, temp_output, decision, all_tracks)

    logger.info("FFmpeg command:\n  %s", " ".join(cmd))

    # Get duration once for percentage calculation
    duration = await probe_duration(input_path)
    current_action = _describe_action(decision)

    async def on_progress_line(progress_kv: dict[str, str]) -> None:
        if not progress_callback or not duration:
            return
        secs  = parse_out_time_seconds(progress_kv)
        pct   = min(100.0, (secs / duration * 100)) if duration > 0 else 0.0
        speed = progress_kv.get("speed", "?x")
        await progress_callback(
            FFmpegProgress(
                percent=pct,
                current_time=secs,
                speed=speed,
                current_action=current_action,
            )
        )

    result = await run_staged_subprocess(
        cmd,
        [StagedOutput(temp_path=temp_output, final_path=output_path)],
        on_progress_line=on_progress_line,
        stderr_tail_lines=30,
        timeout_seconds=timeout_seconds,
    )

    if not result.success:
        # Original only logged the genuine-FFmpeg-failure case (non-zero
        # exit), not the "temp file missing after a clean exit" edge case
        # — preserve that distinction. A clean exit (rc=0) combined with
        # success=False uniquely identifies the missing-temp-file case.
        if result.returncode != 0:
            logger.error("FFmpeg failed (rc=%d):\n%s", result.returncode, result.error)
        return FFmpegResult(
            success=False, output_path=None, error=result.error, output_size=None
        )

    output_size = os.path.getsize(output_path)
    logger.info(
        "FFmpeg success → %s (%.1f MB)", output_path, output_size / 1024 / 1024
    )
    return FFmpegResult(
        success=True, output_path=output_path, error=None, output_size=output_size
    )


# ── Executor — subtitle extraction ──────────────────────────────────────────────


async def execute_subtitle_extraction(
    input_path: str,
    stream_index: int,
    output_srt_path: str,
    job_id: int,
) -> ExtractionResult:
    """
    Extract a single subtitle stream to an external .srt file.

    Stages through TEMP_DIR so the intermediate file never lands on the
    array during extraction — consistent with the main remux executor.
    On success, moves the completed .srt to output_srt_path.

    Thin adapter over run_staged_subprocess() — this function only handles
    what's specific to subtitle extraction: temp path, command building, and
    translating the result into an ExtractionResult. The subprocess machinery
    lives in subprocess_runner.py.

    NOTE: unlike execute_ffmpeg(), exceptions are caught and returned as
    ExtractionResult(success=False) rather than re-raised. worker.py's
    two-pass fallback path calls this in a loop and checks the result
    object — it expects a result, not a raised exception.
    """
    # See execute_ffmpeg's docstring for why the temp name is derived from
    # job_id rather than the destination filename. stream_index (already
    # unique per subtitle track) distinguishes multiple SRT extractions
    # within the same job from each other.
    tmp_dir     = _pick_temp_dir(input_path)
    temp_output = os.path.join(tmp_dir, f"job_{job_id}_srt_{stream_index}.remuxarr_tmp")
    os.makedirs(tmp_dir, exist_ok=True)
    cmd = build_extract_subtitle_command(input_path, stream_index, temp_output)

    logger.info("Subtitle extraction command:\n  %s", " ".join(cmd))

    try:
        result = await run_staged_subprocess(
            cmd,
            [StagedOutput(temp_path=temp_output, final_path=output_srt_path)],
            # No on_progress_line — build_extract_subtitle_command does not
            # include -progress pipe:1 so stdout is empty. run_staged_subprocess
            # always drains stdout, but it just reaches EOF immediately here.
            on_progress_line=None,
            stderr_tail_lines=30,
        )

        if not result.success:
            # Only log for genuine FFmpeg failures (rc != 0), not for the
            # "temp file missing after a clean exit" edge case — matches the
            # original asymmetric logging behaviour.
            if result.returncode is not None and result.returncode != 0:
                logger.error(
                    "Subtitle extraction failed (stream %d, rc=%d): %s",
                    stream_index, result.returncode, result.error,
                )
            return ExtractionResult(success=False, output_path=None, error=result.error)

        logger.info("Subtitle extracted → %s", output_srt_path)
        return ExtractionResult(success=True, output_path=output_srt_path, error=None)

    except Exception as exc:
        # run_staged_subprocess already cleaned up temp_output before re-raising.
        cleanup_temp_file(temp_output)
        return ExtractionResult(success=False, output_path=None, error=str(exc))


# ── Path helpers ───────────────────────────────────────────────────────────────


def determine_output_path(input_path: str, decision: ProcessingDecision) -> str:
    """
    Return the target output path.

    • Container conversion (e.g. MKV → MP4): same directory, new extension.
    • Same-container remux: same path (temp→rename strategy keeps it atomic).
    """
    p = Path(input_path)
    if decision.output_extension and decision.output_extension != p.suffix.lower():
        return str(p.parent / (p.stem + decision.output_extension))
    return input_path


def _pick_temp_dir(reference_path: str) -> str:
    """
    Pick the best directory for a temp output file.

    Prefers TEMP_DIR (often RAM-backed tmpfs on Unraid — fast, avoids array
    I/O during encoding).  Falls back to the directory that contains the
    reference path (the output/source file on the array) when TEMP_DIR does
    not have enough free space.

    Why this matters: tmpfs on Unraid is sized to a fraction of system RAM.
    A large video file (2–4 GB) can easily exhaust it, producing the
    misleading "No space left on device" error even though the array has
    plenty of room.  Checking first and falling back keeps the fast-path
    benefit while safely handling files that exceed available RAM.
    """
    import shutil as _shutil
    preferred = app_settings.TEMP_DIR
    try:
        os.makedirs(preferred, exist_ok=True)
        # How large is the reference file?  Use it as the size estimate for
        # the temp output (remuxed output is typically similar size to input).
        try:
            needed = os.path.getsize(reference_path)
        except OSError:
            needed = 0
        # Add 10 % headroom; always require at least 256 MB free.
        needed = max(int(needed * 1.1), 256 * 1024 * 1024)
        free = _shutil.disk_usage(preferred).free
        if free >= needed:
            return preferred
        logger.warning(
            "TEMP_DIR %s only has %.1f MB free (need %.1f MB for %s); "
            "falling back to source directory",
            preferred, free / 1024 / 1024, needed / 1024 / 1024,
            os.path.basename(reference_path),
        )
    except Exception as exc:
        logger.warning("Could not check TEMP_DIR space (%s); falling back", exc)

    # Fall back: write temp file next to the final output (on the array).
    fallback = os.path.dirname(reference_path)
    return fallback if fallback else "."


# ── Executor — combined remux + subtitle extraction ───────────────────────────


async def execute_ffmpeg_combined(
    input_path:           str,
    output_path:          str,
    decision:             ProcessingDecision,
    all_tracks:           list[dict],
    subtitle_extractions: list[tuple[int, str]],  # (stream_index, srt_dest_path)
    job_id:               int,
    progress_callback:    Callable[[FFmpegProgress], Awaitable[None]] | None = None,
    timeout_seconds:      float | None = None,
) -> tuple[FFmpegResult, list[ExtractionResult]]:
    """
    Single-pass combined remux + subtitle extraction.

    Reads the source file ONCE and writes all outputs simultaneously:
      • The remuxed media file (to TEMP_DIR, then moved to output_path)
      • Each subtitle .srt file  (to TEMP_DIR, then moved to its dest path)

    On HDD arrays this halves the read I/O vs. the two-pass approach (one
    FFmpeg call for extraction + one for remux), which is the dominant cost
    for "Extract N subtitles to external SRT" jobs.

    Returns (FFmpegResult, [ExtractionResult, ...]) — one ExtractionResult
    per entry in subtitle_extractions, in the same order.

    Thin adapter over run_staged_subprocess() for the main output, with a
    manual SRT-move loop below to preserve partial-success semantics.

    WHY the SRT temps are NOT passed to run_staged_subprocess:
    run_staged_subprocess has all-or-nothing move semantics — if any temp is
    missing, ALL outputs are cleaned up and failure is returned. But this
    function's contract is different for SRTs: a missing SRT temp after a
    successful main-file move reports only THAT SRT as failed; the
    already-moved main file and any other SRTs that moved successfully are
    unaffected. Preserving this behaviour requires keeping the SRT move loop
    here rather than delegating it to the shared executor.
    """
    tmp_dir   = _pick_temp_dir(input_path)
    os.makedirs(tmp_dir, exist_ok=True)

    # ── Build the combined command ─────────────────────────────────────────
    # See execute_ffmpeg's docstring for why this is derived from job_id
    # rather than the destination filename.
    temp_main = os.path.join(tmp_dir, f"job_{job_id}.remuxarr_tmp")

    # Base command up to (and including) the format/movflags flags
    main_cmd = build_ffmpeg_command(input_path, temp_main, decision, all_tracks)

    # Append subtitle output specs after the main output.
    # Each gets its own temp path in the same tmp_dir. stream_idx is
    # already unique per subtitle track, distinguishing multiple SRT
    # extractions within the same job from each other and from the main
    # video temp above.
    srt_temps: list[str] = []
    for stream_idx, srt_dest in subtitle_extractions:
        srt_tmp = os.path.join(tmp_dir, f"job_{job_id}_srt_{stream_idx}.remuxarr_tmp")
        srt_temps.append(srt_tmp)
        main_cmd += [
            "-map", f"0:{stream_idx}",
            "-c:s", "srt",
            "-f", "srt",
            srt_tmp,
        ]

    logger.info("FFmpeg command (combined):\n  %s", " ".join(main_cmd))

    # ── Progress adapter ───────────────────────────────────────────────────
    duration       = await probe_duration(input_path)
    current_action = _describe_action(decision)

    async def on_progress_line(progress_kv: dict[str, str]) -> None:
        if not progress_callback or not duration:
            return
        secs  = parse_out_time_seconds(progress_kv)
        pct   = min(100.0, (secs / duration * 100)) if duration > 0 else 0.0
        await progress_callback(FFmpegProgress(
            percent=pct,
            current_time=secs,
            speed=progress_kv.get("speed", "?x"),
            current_action=current_action,
        ))

    # ── Run via shared executor (main output only) ─────────────────────────
    try:
        result = await run_staged_subprocess(
            main_cmd,
            [StagedOutput(temp_path=temp_main, final_path=output_path)],
            on_progress_line=on_progress_line,
            stderr_tail_lines=30,
            timeout_seconds=timeout_seconds,
        )
    except Exception:
        # run_staged_subprocess already cleaned temp_main before re-raising.
        # Clean up the SRT temps it doesn't know about, then re-raise so the
        # caller's except block (in worker.py) marks the job failed.
        for t in srt_temps:
            cleanup_temp_file(t)
        raise

    if not result.success:
        # Clean up SRT temps (run_staged_subprocess already handled temp_main).
        for t in srt_temps:
            cleanup_temp_file(t)
        # Log only for genuine FFmpeg failures (rc != 0), not for the
        # missing-temp edge case — matches original asymmetric logging.
        if result.returncode is not None and result.returncode != 0:
            logger.error(
                "FFmpeg (combined) failed (rc=%d):\n%s", result.returncode, result.error
            )
        fail = FFmpegResult(
            success=False, output_path=None, error=result.error, output_size=None
        )
        srt_fails = [
            ExtractionResult(success=False, output_path=None, error=result.error)
            for _ in subtitle_extractions
        ]
        return fail, srt_fails

    output_size = os.path.getsize(output_path)
    logger.info("FFmpeg success → %s (%.1f MB)", output_path, output_size / 1024 / 1024)
    main_result = FFmpegResult(
        success=True, output_path=output_path, error=None, output_size=output_size
    )

    # ── Move subtitle outputs (partial-success semantics) ──────────────────
    # Each SRT is moved independently. A missing SRT temp reports only that
    # one SRT as failed — it does NOT roll back the already-moved main file
    # or prevent other SRTs from being moved successfully.
    srt_results: list[ExtractionResult] = []
    for srt_tmp, (_, srt_dest) in zip(srt_temps, subtitle_extractions):
        if not os.path.exists(srt_tmp):
            srt_results.append(ExtractionResult(
                success=False, output_path=None,
                error="Temp .srt file missing after FFmpeg completed",
            ))
            continue
        if os.path.exists(srt_dest):
            os.remove(srt_dest)
        shutil.move(srt_tmp, srt_dest)
        logger.info("Subtitle extracted → %s", srt_dest)
        srt_results.append(ExtractionResult(success=True, output_path=srt_dest, error=None))

    return main_result, srt_results


# ── Internal helpers ───────────────────────────────────────────────────────────


def _describe_action(decision: ProcessingDecision) -> str:
    has_transcode  = any(a.action_type == "transcode_track" for a in decision.actions)
    has_container  = any(a.action_type == "change_container" for a in decision.actions)
    has_faststart  = any(a.action_type == "add_faststart" for a in decision.actions)
    if has_transcode and has_container:
        return "Remuxing to MP4 & transcoding AAC 5.1 → AC3"
    if has_transcode:
        return "Transcoding AAC 5.1 → AC3 5.1"
    if has_container:
        return "Remuxing to MP4"
    if has_faststart:
        return "Adding fast start (optimising for streaming)"
    return "Remuxing tracks"
