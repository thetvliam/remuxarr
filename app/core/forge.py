"""
AC3 Forge
=========
On-demand "add AC3 5.1 alongside AAC 5.1" per-file transcoding.

Key differences from the main remux pipeline
---------------------------------------------
• The original AAC 5.1 track is KEPT — not replaced.
• A new AC3 5.1 track is APPENDED to the end of the audio stream list.
• The operation is fully reversible: the AC3 track can be removed later
  because its output audio index is stored at job-creation time.

FFmpeg strategy
---------------
  Add :  -map 0 -c copy                       keep everything as-is
          -map 0:{aac_stream_index}             also map the AAC 5.1 again
          -c:a:{audio_track_count} ac3          transcode that copy to AC3
          -b:a:{audio_track_count} 640k
          -ac:{audio_track_count}  6

  Undo:  -map 0                                keep everything
          -map -0:a:{audio_track_count}         REMOVE the AC3 we appended
          -c copy
"""
import logging
import os
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy.orm import Session

from app.config import settings as app_settings
from app.database.session import SessionLocal, get_app_settings
from app.core.subprocess_runner import (
    StagedOutput,
    parse_out_time_seconds,
    probe_duration,
    run_staged_subprocess,
)

logger = logging.getLogger(__name__)

_FORMAT_MAP = {
    "mkv":  "matroska",
    "mp4":  "mp4",
    "avi":  "avi",
    "ts":   "mpegts",
    "m2ts": "mpegts",
    "wmv":  "asf",
    "webm": "webm",
}


# ── Data classes ───────────────────────────────────────────────────────────────

@dataclass
class ForgeProgress:
    percent:      float
    current_time: float
    speed:        str
    action:       str


@dataclass
class ForgeResult:
    success:     bool
    output_path: str | None
    output_size: int | None
    error:       str | None


# ── Command builders ───────────────────────────────────────────────────────────

def build_add_ac3_command(
    input_path:        str,
    temp_path:         str,
    aac_stream_index:  int,     # global ffprobe stream index of the AAC 5.1 track
    audio_track_count: int,     # number of audio tracks BEFORE adding AC3
    container:         str = "mkv",
) -> list[str]:
    """
    Copy all existing streams unchanged, then append a new AC3 5.1 track
    transcoded from the AAC 5.1 stream at aac_stream_index.

    The new AC3 always ends up at output audio index audio_track_count
    (0-based), making the undo index deterministic.
    """
    # Hard-fail on unknown containers rather than silently defaulting to
    # matroska — same file-corruption pattern just fixed in
    # ffmpeg.build_ffmpeg_command (see the comment there for the full
    # story). Note this map is even narrower than that one (no "mov"
    # entry), so without this a forge job on a .mov would have written
    # Matroska bytes into the .mov in place. Raising here is caught by
    # _process_next_forge's job-level exception handler and becomes a
    # visible failed forge job, before FFmpeg ever starts.
    if container not in _FORMAT_MAP:
        raise ValueError(
            f"Unsupported container {container!r} for AC3 Forge — "
            f"refusing to guess the mux format (supported: "
            f"{', '.join(sorted(_FORMAT_MAP))})"
        )
    fmt     = _FORMAT_MAP[container]
    ac3_idx = audio_track_count  # output audio index for the new stream

    cmd = [
        app_settings.FFMPEG_PATH,
        "-i", input_path,
        "-y", "-v", "error", "-nostats", "-progress", "pipe:1",
        # ── Keep every existing stream unchanged ───────────────────────────
        "-map", "0",
        "-c", "copy",
        # ── Map the AAC 5.1 a second time and transcode it to AC3 ─────────
        "-map", f"0:{aac_stream_index}",
        f"-c:a:{ac3_idx}", "ac3",
        f"-b:a:{ac3_idx}", "640k",
        f"-ac:{ac3_idx}", "6",
        # ── Output ────────────────────────────────────────────────────────
        "-f", fmt,
    ]

    # Apply +faststart when writing MP4 — mirrors the main remux pipeline's
    # unconditional rule for any MP4 output. Without this, every file forge
    # touches loses faststart (even if the original had it), and the next
    # library scan re-queues it purely to re-add faststart — wasted work.
    if container == "mp4":
        cmd += ["-movflags", "+faststart"]

    cmd.append(temp_path)
    return cmd


def build_undo_command(
    input_path:            str,
    temp_path:             str,
    ac3_audio_output_index: int,   # = audio_track_count from the original forge job
    container:             str = "mkv",
) -> list[str]:
    """
    Copy all streams EXCEPT the AC3 track added by the forge.
    Uses FFmpeg's negative map syntax: -map -0:a:N removes output audio
    stream N from the selection.
    """
    # Same hard-fail as build_add_ac3_command — see the comment there.
    if container not in _FORMAT_MAP:
        raise ValueError(
            f"Unsupported container {container!r} for AC3 Forge undo — "
            f"refusing to guess the mux format (supported: "
            f"{', '.join(sorted(_FORMAT_MAP))})"
        )
    fmt = _FORMAT_MAP[container]

    cmd = [
        app_settings.FFMPEG_PATH,
        "-i", input_path,
        "-y", "-v", "error", "-nostats", "-progress", "pipe:1",
        "-map", "0",
        "-map", f"-0:a:{ac3_audio_output_index}",   # strip the AC3 we added
        "-c", "copy",
        "-f", fmt,
    ]

    # Same faststart rule as build_add_ac3_command — undo also rewrites the
    # whole file, so it needs to re-apply faststart for the same reason.
    if container == "mp4":
        cmd += ["-movflags", "+faststart"]

    cmd.append(temp_path)
    return cmd


# ── Async executor ─────────────────────────────────────────────────────────────

async def run_forge_command(
    cmd:               list[str],
    input_path:        str,
    output_path:       str,
    temp_path:         str,
    action_label:      str,
    progress_callback: Callable[[ForgeProgress], Awaitable[None]] | None = None,
    timeout_seconds:   float | None = None,
) -> ForgeResult:
    """
    Run a forge FFmpeg command with real-time progress tracking.

    Writes to temp_path, then atomically renames to output_path on success.
    Always cleans up the temp file on failure.

    Thin adapter over run_staged_subprocess() in subprocess_runner.py.
    This function only handles what's forge-specific: building the
    ForgeProgress objects (note: ForgeProgress.action vs FFmpegProgress's
    .current_action — different field names, different dataclass) and
    translating the result into a ForgeResult. The subprocess spawn/drain/
    stage/cleanup machinery is shared with the main remux pipeline.

    NOTE: stderr_tail_lines=20 here vs 30 in the main pipeline — preserved
    intentionally, not unified.

    timeout_seconds was previously never wired through at all — forge
    jobs got none of the job_timeout_minutes protection the main remux
    pipeline has always had, meaning a hung forge job (e.g. against a
    genuinely broken source file) could run indefinitely with no
    recovery. Caught by independent review.
    """
    duration = await probe_duration(input_path)

    async def on_progress_line(progress_kv: dict[str, str]) -> None:
        if not progress_callback or not duration:
            return
        secs = parse_out_time_seconds(progress_kv)
        pct  = min(100.0, secs / duration * 100)
        await progress_callback(ForgeProgress(
            percent=pct,
            current_time=secs,
            speed=progress_kv.get("speed", "?x"),
            action=action_label,
        ))

    result = await run_staged_subprocess(
        cmd,
        [StagedOutput(temp_path=temp_path, final_path=output_path)],
        on_progress_line=on_progress_line,
        stderr_tail_lines=20,
        timeout_seconds=timeout_seconds,
    )

    if not result.success:
        if result.returncode is not None and result.returncode != 0:
            logger.error(
                "Forge FFmpeg failed (rc=%d):\n%s", result.returncode, result.error
            )
        return ForgeResult(
            success=False, output_path=None, output_size=None, error=result.error
        )

    size = os.path.getsize(output_path)
    logger.info("Forge success → %s (%.1f MB)", output_path, size / 1024 / 1024)
    return ForgeResult(
        success=True, output_path=output_path, output_size=size, error=None
    )


# ── Sync DB helpers ────────────────────────────────────────────────────────────
# All functions below run inside thread-pool executors (called via
# loop.run_in_executor) so they must be regular synchronous functions.

def get_candidates(
    db:     Session,
    search: str = "",
    limit:  int = 50,
    offset: int = 0,
) -> dict:
    """
    Return a paginated page of MediaFiles that have at least one AAC 5.1
    audio track and no active or completed forge job.

    When searching, results are ordered by relevance (filename starts with
    search term first) so the most likely match surfaces immediately.
    Without a search term, results are alphabetical by filename.

    Returns {"total": N, "items": [...]}.
    """
    from app.database.models import Ac3ForgeJob, MediaFile, Track
    from sqlalchemy import case as sa_case

    # File IDs that have an AAC 5.1 track
    has_aac51 = (
        db.query(Track.file_id)
        .filter(
            Track.track_type == "audio",
            Track.codec      == "aac",
            Track.channels   == 6,
        )
        .scalar_subquery()
    )

    # File IDs with an active forge job, OR one whose outcome means the file
    # currently HAS an AC3 track that shouldn't be touched again:
    #   pending/processing  — job in flight
    #   success              — AC3 successfully added and present
    #   undo_pending         — undo in flight
    #   undo_failed          — undo attempt failed; AC3 track is STILL present
    #                          (the undo never completed) — if this status is
    #                          NOT excluded here, the file incorrectly looks
    #                          like a fresh candidate and clicking "+ADD AC3"
    #                          on it creates a duplicate/overwritten AC3 track
    #                          on a file that already has one.
    #
    # Deliberately NOT excluded:
    #   failed   — a failed ADD attempt never produced an AC3 track, so the
    #              file legitimately has none and should remain a valid
    #              candidate (this is how a failed add gets retried, since
    #              there's no separate retry button for it in the UI).
    #   undone   — a successful undo removed the AC3 track, so the file
    #              should become a candidate again if the user wants to
    #              re-add it.
    is_forged = (
        db.query(Ac3ForgeJob.file_id)
        .filter(Ac3ForgeJob.status.in_(
            ["pending", "processing", "success", "undo_pending", "undo_failed"]
        ))
        .scalar_subquery()
    )

    query = (
        db.query(MediaFile)
        .filter(
            MediaFile.id.in_(has_aac51),
            ~MediaFile.id.in_(is_forged),
        )
    )

    if search.strip():
        s = search.strip()
        query = query.filter(MediaFile.filename.ilike(f"%{s}%"))
        # Relevance ordering: filename-starts-with ranks above mid-word match
        relevance = sa_case(
            (MediaFile.filename.ilike(f"{s}%"),   0),
            (MediaFile.filename.ilike(f"% {s}%"), 1),
            else_=2,
        )
        order_clause = [relevance, MediaFile.filename]
    else:
        order_clause = [MediaFile.filename]

    total = query.count()
    files = query.order_by(*order_clause).offset(offset).limit(limit).all()

    result = []
    for f in files:
        aac_track = (
            db.query(Track)
            .filter(
                Track.file_id    == f.id,
                Track.track_type == "audio",
                Track.codec      == "aac",
                Track.channels   == 6,
            )
            .first()
        )
        audio_count = (
            db.query(Track)
            .filter(Track.file_id == f.id, Track.track_type == "audio")
            .count()
        )
        if not aac_track:
            continue

        result.append({
            "id":                f.id,
            "filename":          f.filename,
            "path":              f.path,
            "size":              f.size,
            "duration":          f.duration,
            "container":         f.container,
            "aac_stream_index":  aac_track.stream_index,
            "audio_track_count": audio_count,
            "aac_track": {
                "stream_index":   aac_track.stream_index,
                "language":       aac_track.language,
                "channels":       aac_track.channels,
                "channel_layout": aac_track.channel_layout,
                "is_default":     aac_track.is_default,
            },
        })

    return {"total": total, "items": result}


def queue_forge_job(db: Session, file_id: int):
    """Create and persist a pending forge job. Returns the new Ac3ForgeJob."""
    from app.database.models import Ac3ForgeJob, MediaFile, Track

    media = db.get(MediaFile, file_id)
    if not media:
        raise ValueError(f"MediaFile {file_id} not found")

    aac_track = (
        db.query(Track)
        .filter(
            Track.file_id    == file_id,
            Track.track_type == "audio",
            Track.codec      == "aac",
            Track.channels   == 6,
        )
        .first()
    )
    if not aac_track:
        raise ValueError(f"No AAC 5.1 track found for file {file_id}")

    audio_count = (
        db.query(Track)
        .filter(Track.file_id == file_id, Track.track_type == "audio")
        .count()
    )

    job = Ac3ForgeJob(
        file_id           = file_id,
        status            = "pending",
        aac_stream_index  = aac_track.stream_index,
        audio_track_count = audio_count,
        original_size     = media.size,
    )
    db.add(job)
    db.commit()
    db.refresh(job)
    return job


def claim_next_forge_job() -> int | None:
    """
    Claim the next pending or undo_pending forge job.
    Returns the job ID, or None if the forge queue is empty.
    """
    from app.database.models import Ac3ForgeJob

    db = SessionLocal()
    try:
        job = (
            db.query(Ac3ForgeJob)
            .filter(Ac3ForgeJob.status.in_(["pending", "undo_pending"]))
            .order_by(Ac3ForgeJob.created_at.asc())
            .first()
        )
        if job is None:
            return None

        job.status     = "processing"
        job.started_at = datetime.utcnow()
        db.commit()
        return job.id
    except Exception:
        db.rollback()
        logger.exception("Failed to claim forge job")
        return None
    finally:
        db.close()


def load_forge_job_data(job_id: int) -> dict | None:
    """Return everything the worker needs to execute a forge job."""
    from app.database.models import Ac3ForgeJob, MediaFile

    db = SessionLocal()
    try:
        job: Ac3ForgeJob | None = db.get(Ac3ForgeJob, job_id)
        if not job:
            return None

        media: MediaFile | None = db.get(MediaFile, job.file_id)
        if not media or not os.path.exists(media.path):
            finish_forge_job(job_id, False, None, None, "File not found on disk")
            return None

        # job_timeout_minutes read here (rather than a separate fetch in
        # worker.py) since this function already has the DB session open —
        # mirrors _load_job_data's own pattern in worker.py exactly.
        app_cfg = get_app_settings(db)

        return {
            "job_id":             job.id,
            "is_undo":            job.is_undo,
            "file_path":          media.path,
            "filename":           media.filename,
            "container":          media.container or "mkv",
            "aac_stream_index":   job.aac_stream_index,
            "audio_track_count":  job.audio_track_count,
            "original_size":      job.original_size,
            "job_timeout_minutes": app_cfg.get("job_timeout_minutes", 120),
        }
    finally:
        db.close()


def update_forge_progress(job_id: int, percent: float, current_action: str) -> None:
    from app.database.models import Ac3ForgeJob

    db = SessionLocal()
    try:
        job = db.get(Ac3ForgeJob, job_id)
        if job:
            job.progress       = percent
            job.current_action = current_action
            db.commit()
    except Exception:
        db.rollback()
    finally:
        db.close()


def finish_forge_job(
    job_id:      int,
    success:     bool,
    output_path: str | None,
    output_size: int | None,
    error:       str | None,
) -> None:
    from app.database.models import Ac3ForgeJob

    db = SessionLocal()
    try:
        job: Ac3ForgeJob | None = db.get(Ac3ForgeJob, job_id)
        if not job:
            return

        job.completed_at  = datetime.utcnow()
        job.progress      = 100.0 if success else job.progress
        job.output_size   = output_size
        job.error_message = error

        if job.is_undo:
            job.status = "undone"    if success else "undo_failed"
        else:
            job.status = "success"   if success else "failed"

        db.commit()
        logger.info(
            "Forge job %d → %s%s", job_id,
            job.status, f" ({error})" if error else ""
        )
    except Exception:
        db.rollback()
        logger.exception("Failed to finalise forge job %d", job_id)
    finally:
        db.close()


def load_forge_final_state(job_id: int) -> dict | None:
    from app.database.models import Ac3ForgeJob

    db = SessionLocal()
    try:
        job = db.get(Ac3ForgeJob, job_id)
        if not job:
            return None
        return {
            "status":   job.status,
            "filename": job.media_file.filename if job.media_file else "",
            "error":    job.error_message,
        }
    finally:
        db.close()
