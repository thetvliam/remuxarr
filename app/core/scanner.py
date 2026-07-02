"""
Library Scanner
===============
scan_library()      — walk one or more directories, detect new/changed files
queue_single_file() — probe + enqueue a single path (called by webhook handler)

Delta scan logic
----------------
For each media file found on disk:
  1. Look up the existing MediaFile row by path.
  2. If row exists AND size + mtime match → skip (no ffprobe needed).
  3. Otherwise → run ffprobe, upsert the row, run the decision engine,
     and create a QueueItem + PlannedActions if anything needs doing.
"""
import json
import logging
import os
import time
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy.orm import Session

from app.config import settings as app_settings
from app.core.decision import analyze_file
from app.core.probe import (
    ProbeError,
    extract_format_info,
    extract_tracks,
    is_faststart_mp4,
    is_media_file,
    probe_file,
)
from app.database.models import Ac3ForgeJob, MediaFile, PlannedAction, QueueItem, Track
from app.database.session import SessionLocal, get_app_settings

logger = logging.getLogger(__name__)


# ── Stats ──────────────────────────────────────────────────────────────────────

@dataclass
class ScanStats:
    total:         int = 0
    new:           int = 0
    changed:       int = 0
    unchanged:     int = 0
    queued:        int = 0
    manual_review: int = 0
    skipped:       int = 0
    errors:        int = 0
    removed:       int = 0   # files removed from DB because they no longer exist on disk


# ── Public API ─────────────────────────────────────────────────────────────────

def scan_library(
    db:          Session,
    paths:       list[str],
    force_probe: bool = False,
    progress_callback=None,   # optional: callable(scanned: int, total: int)
) -> ScanStats:
    """
    Walk every path and process each media file.

    Parameters
    ----------
    force_probe : bool
        True  → run ffprobe on every file regardless of mtime/size (initial scan).
        False → only ffprobe files that changed since last scan (delta scan).
    progress_callback : callable | None
        Called after each file is processed with (scanned, total) ints.
        Used by the scan API route to broadcast WebSocket progress updates.
    """
    stats    = ScanStats()
    app_cfg  = get_app_settings(db)
    dry_run  = app_cfg.get("dry_run_mode", False)
    scan_label = "full" if force_probe else "delta"

    logger.info("Starting %s scan of %d path(s)", scan_label, len(paths))

    # Pre-count total media files across all paths so the progress callback
    # can report X/Y.  This is a fast directory listing pass (no I/O per file).
    total_files = 0
    for scan_path in paths:
        if not os.path.isdir(scan_path):
            continue
        for root, dirs, files in os.walk(scan_path, followlinks=True):
            dirs[:] = [d for d in dirs if not d.startswith(".")]
            total_files += sum(1 for f in files if is_media_file(f))

    scanned = 0
    for scan_path in paths:
        if not os.path.isdir(scan_path):
            logger.warning("Scan path not found or not a directory: %s", scan_path)
            continue

        for root, dirs, files in os.walk(scan_path, followlinks=True):
            dirs[:] = [d for d in dirs if not d.startswith(".")]   # skip hidden

            for filename in files:
                if not is_media_file(filename):
                    continue

                full_path = os.path.join(root, filename)
                stats.total += 1
                scanned += 1

                try:
                    _process_file(db, full_path, app_cfg, force_probe, dry_run, stats)
                except Exception:
                    logger.exception("Unexpected error processing %s", full_path)
                    stats.errors += 1

                if progress_callback:
                    progress_callback(scanned, total_files)

                # Yield to Python's thread scheduler so HTTP handler threads
                # (sync route handlers) can run between files.  Without this
                # the scanner thread can hold the GIL or block on I/O for
                # long stretches, starving other threads.
                time.sleep(0)

    # ── Cleanup pass ───────────────────────────────────────────────────────
    # After the directory walk, remove DB rows for files that no longer exist
    # on disk — but only if the setting is enabled (default: True).
    if app_cfg.get("auto_cleanup_on_scan", True):
        stats.removed = cleanup_deleted_files(db, [p for p in paths if os.path.isdir(p)])

    logger.info(
        "Scan done — total=%d new=%d changed=%d unchanged=%d "
        "queued=%d review=%d skipped=%d errors=%d removed=%d",
        stats.total, stats.new, stats.changed, stats.unchanged,
        stats.queued, stats.manual_review, stats.skipped, stats.errors,
        stats.removed,
    )
    return stats


def queue_single_file(
    db: Session,
    path: str,
    sonarr_series_id: int | None = None,
    radarr_movie_id:  int | None = None,
) -> QueueItem | None:
    """
    Probe and (re-)queue a single file immediately.
    Used by the webhook handler after debounce expires.
    Returns the new QueueItem or None if the file was skipped.
    """
    app_cfg = get_app_settings(db)
    dry_run = app_cfg.get("dry_run_mode", False)
    stats   = ScanStats()

    _process_file(
        db, path, app_cfg,
        force_probe=True, dry_run=dry_run, stats=stats,
        sonarr_series_id=sonarr_series_id,
        radarr_movie_id=radarr_movie_id,
    )

    media = db.query(MediaFile).filter(MediaFile.path == path).first()
    if not media:
        return None

    return (
        db.query(QueueItem)
        .filter(
            QueueItem.file_id == media.id,
            QueueItem.status.in_(["pending", "manual_review"]),
        )
        .order_by(QueueItem.created_at.desc())
        .first()
    )


def cleanup_deleted_files(db: Session, scan_paths: list[str]) -> int:
    """
    Remove MediaFile rows (and all related rows) for files that no longer
    exist on disk, within the given scan_paths directories.

    Scoped to scan_paths to prevent accidentally touching files outside the
    configured library.  Files with a currently-processing QueueItem are
    skipped — the running job will fail naturally when it tries to open the
    missing file, which produces a cleaner error than deleting mid-job.

    Returns the number of MediaFile rows removed.
    """
    if not scan_paths:
        return 0

    # Normalise prefixes to end with os.sep so "/media/tv2" never matches
    # paths that merely start with "/media/tv" (without the separator).
    prefixes = tuple(
        p if p.endswith(os.sep) else p + os.sep
        for p in scan_paths
    )

    # Fetch all MediaFile rows whose paths fall under our scan directories.
    # Python-level prefix check is fine here — the full table easily fits
    # in memory for any realistic media library.
    all_media: list[MediaFile] = db.query(MediaFile).all()
    candidates = [m for m in all_media if any(m.path.startswith(p) for p in prefixes)]

    removed = 0
    for media in candidates:
        if os.path.exists(media.path):
            continue   # still on disk — leave it alone

        # Skip files whose job is currently running — deleting mid-job would
        # cause confusing errors in the worker.  The job will fail naturally.
        processing = (
            db.query(QueueItem)
            .filter(
                QueueItem.file_id == media.id,
                QueueItem.status  == "processing",
            )
            .first()
        )
        if processing:
            logger.debug(
                "Cleanup: skipping %s — job %d is still processing",
                media.path, processing.id,
            )
            continue

        logger.info("Cleanup: removing deleted file from DB — %s", media.path)

        # Delete child rows before the parent (FK constraints)
        db.query(PlannedAction).filter(
            PlannedAction.queue_item_id.in_(
                db.query(QueueItem.id).filter(QueueItem.file_id == media.id)
            )
        ).delete(synchronize_session=False)
        db.query(QueueItem).filter(
            QueueItem.file_id == media.id
        ).delete(synchronize_session=False)
        db.query(Ac3ForgeJob).filter(
            Ac3ForgeJob.file_id == media.id
        ).delete(synchronize_session=False)
        db.query(Track).filter(
            Track.file_id == media.id
        ).delete(synchronize_session=False)
        db.delete(media)
        removed += 1

    if removed:
        db.commit()
        logger.info("Cleanup: removed %d deleted file(s) from database", removed)

    return removed

def _process_file(
    db:          Session,
    path:        str,
    app_cfg:     dict,
    force_probe: bool,
    dry_run:     bool,
    stats:       ScanStats,
    sonarr_series_id: int | None = None,
    radarr_movie_id:  int | None = None,
) -> None:
    # ── Stat the file ──────────────────────────────────────────────────────
    try:
        st = os.stat(path)
    except OSError:
        logger.warning("Cannot stat %s — skipping", path)
        stats.errors += 1
        return

    current_size  = st.st_size
    current_mtime = st.st_mtime

    # ── Delta check ────────────────────────────────────────────────────────
    existing: MediaFile | None = (
        db.query(MediaFile).filter(MediaFile.path == path).first()
    )
    # Captured here, before `existing` is reused as `media_file` below.
    # True only the very first time this exact path has ever been probed —
    # used by the post-job Plex notification to choose refresh vs analyze.
    is_new_file = existing is None

    if existing and not force_probe:
        size_match  = existing.size == current_size
        mtime_match = abs(existing.mtime - current_mtime) < 1.0

        if size_match and mtime_match:
            # File is identical on disk.  If its status is still "queued" it
            # means the last run was a dry run — fall through so it gets
            # re-queued with the current dry_run setting (which may now be off).
            if existing.status == "queued":
                logger.info(
                    "Post-dry-run re-evaluation (file unchanged on disk): %s", path
                )
            else:
                stats.unchanged += 1
                return

        stats.changed += 1
    else:
        stats.new += 1

    # ── Run ffprobe ────────────────────────────────────────────────────────
    try:
        probe_data = probe_file(path, app_settings.FFPROBE_PATH)
    except ProbeError as exc:
        logger.error("ffprobe failed for %s: %s", path, exc)
        stats.errors += 1
        return

    fmt_info  = extract_format_info(probe_data)
    track_list = extract_tracks(probe_data)

    # ── Upsert MediaFile ───────────────────────────────────────────────────
    primary_video_codec = next(
        (t["codec"] for t in track_list if t["track_type"] == "video"), None
    )

    if existing:
        # Drop old tracks — we'll re-insert them fresh
        db.query(Track).filter(Track.file_id == existing.id).delete()
        existing.size          = current_size
        existing.mtime         = current_mtime
        existing.container     = fmt_info.get("container")
        existing.duration      = fmt_info.get("duration")
        existing.video_codec   = primary_video_codec
        existing.last_scanned  = datetime.utcnow()
        media_file = existing
    else:
        media_file = MediaFile(
            path        = path,
            filename    = os.path.basename(path),
            directory   = os.path.dirname(path),
            size        = current_size,
            mtime       = current_mtime,
            container   = fmt_info.get("container"),
            duration    = fmt_info.get("duration"),
            video_codec = primary_video_codec,
            last_scanned = datetime.utcnow(),
        )
        db.add(media_file)

    db.flush()  # ensure media_file.id is populated

    # ── Insert tracks ──────────────────────────────────────────────────────
    for td in track_list:
        db.add(Track(
            file_id        = media_file.id,
            stream_index   = td["stream_index"],
            track_type     = td["track_type"],
            codec          = td["codec"],
            language       = td["language"],
            channels       = td.get("channels"),
            channel_layout = td.get("channel_layout"),
            is_default          = td.get("is_default", False),
            is_forced           = td.get("is_forced", False),
            is_hearing_impaired = td.get("is_hearing_impaired", False),
            is_dub              = td.get("is_dub", False),
            title          = td.get("title"),
            # ── Write-only — removal candidates (see models.py Track) ─────
            codec_long  = td.get("codec_long"),
            raw_ffprobe = td.get("raw_ffprobe"),
            sample_rate = td.get("sample_rate"),
            bit_rate    = td.get("bit_rate"),
        ))

    db.flush()

    # ── Clear stale dry-run results ─────────────────────────────────────────
    # This file is being re-evaluated right now (it changed, or its previous
    # run was a dry run and we're re-checking it). Any earlier dry-run
    # preview for it — reason text, planned actions, flagged subtitles — was
    # computed against the OLD settings/file-state and is now superseded by
    # whatever decision we're about to make (queue, skip, or manual review).
    # Without this, a dry-run "would do X" entry sits in the Dry Run tab
    # forever, even after a later real scan supersedes it.
    stale_dry_run_items = (
        db.query(QueueItem)
        .filter(QueueItem.file_id == media_file.id, QueueItem.status == "dry_run")
        .all()
    )
    for stale_item in stale_dry_run_items:
        db.delete(stale_item)
    if stale_dry_run_items:
        db.flush()

    # ── Decision engine ────────────────────────────────────────────────────
    file_info_dict = {
        "path":        path,
        "container":   fmt_info.get("container"),
        "video_codec": primary_video_codec,
    }
    overrides = _load_subtitle_overrides(media_file)
    forged_ac3_audio_index = _get_forged_ac3_audio_index(db, media_file.id)

    # Detect fast-start for MP4 files — cheap (reads < 100 bytes), no
    # ffprobe needed. None for non-MP4 containers or on I/O error.
    fmt_container = fmt_info.get("container", "")
    faststart = (
        is_faststart_mp4(path)
        if fmt_container == "mp4"
        else None
    )

    decision = analyze_file(
        file_info_dict, track_list, app_cfg,
        subtitle_overrides=overrides,
        has_faststart=faststart,
        forged_ac3_audio_index=forged_ac3_audio_index,
    )

    # ── Manual review ──────────────────────────────────────────────────────
    if decision.is_manual_review:
        media_file.status = "manual_review"

        # Only create one manual-review item per file
        already = db.query(QueueItem).filter(
            QueueItem.file_id == media_file.id,
            QueueItem.status  == "manual_review",
        ).first()

        if not already:
            db.add(QueueItem(
                file_id    = media_file.id,
                status     = "manual_review",
                is_dry_run = dry_run,
                reason     = decision.reason,
                original_size = current_size,
                review_subtitles = (
                    json.dumps(decision.flagged_subtitles)
                    if decision.flagged_subtitles else None
                ),
                sonarr_series_id = sonarr_series_id,
                radarr_movie_id  = radarr_movie_id,
            ))
            stats.manual_review += 1

        db.commit()
        return

    # ── Skip ───────────────────────────────────────────────────────────────
    if not decision.should_process:
        media_file.status = "skipped"
        stats.skipped += 1

        # Create or update a skipped QueueItem so the file is visible in
        # the History panel with the reason it needed no changes.
        # If one already exists (e.g. from a previous scan), update its
        # reason in-place — the reason can change if settings changed —
        # so each file has at most one skipped record at a time.
        existing_skip = (
            db.query(QueueItem)
            .filter(
                QueueItem.file_id == media_file.id,
                QueueItem.status  == "skipped",
            )
            .first()
        )
        if existing_skip:
            existing_skip.reason       = decision.reason
            existing_skip.completed_at = datetime.utcnow()
        else:
            db.add(QueueItem(
                file_id       = media_file.id,
                status        = "skipped",
                is_dry_run    = False,
                reason        = decision.reason,
                original_size = current_size,
                completed_at  = datetime.utcnow(),
            ))

        db.commit()
        return

    # ── Queue ──────────────────────────────────────────────────────────────
    # Don't double-queue files already pending or being processed
    in_progress = db.query(QueueItem).filter(
        QueueItem.file_id == media_file.id,
        QueueItem.status.in_(["pending", "processing"]),
    ).first()

    if in_progress:
        db.commit()
        return

    media_file.status = "queued"

    qi = QueueItem(
        file_id          = media_file.id,
        status           = "pending",
        is_dry_run       = dry_run,
        reason           = decision.reason,
        original_size    = current_size,
        priority         = 5,
        sonarr_series_id = sonarr_series_id,
        radarr_movie_id  = radarr_movie_id,
        is_new_file      = is_new_file,
    )
    db.add(qi)
    db.flush()

    for i, action in enumerate(decision.actions):
        db.add(PlannedAction(
            queue_item_id = qi.id,
            order         = i,
            action_type   = action.action_type,
            description   = action.description,
            track_type    = action.track_type,
            stream_index  = action.stream_index,
        ))

    db.commit()
    stats.queued += 1
    logger.info("Queued [%d] %s — %s", qi.id, os.path.basename(path), decision.reason)


def _load_subtitle_overrides(media_file: MediaFile) -> dict[int, str]:
    """
    Parse MediaFile.subtitle_overrides (JSON dict with string keys, since
    JSON object keys are always strings) into a dict[int, str] keyed by
    stream_index, as expected by analyze_file().
    """
    if not media_file.subtitle_overrides:
        return {}
    try:
        raw = json.loads(media_file.subtitle_overrides)
        return {int(k): v for k, v in raw.items()}
    except (ValueError, AttributeError, TypeError):
        logger.warning(
            "Invalid subtitle_overrides JSON for file %d — ignoring", media_file.id
        )
        return {}


def _track_to_dict(t: Track) -> dict:
    """
    Serialise a Track ORM row to the plain dict format consumed by
    analyze_file() and the decision engine.

    Single source of truth for this mapping — previously duplicated in
    worker.py (_load_job_data) and queue.py (resolve_subtitles). Both now
    call this function so any future Track field addition only needs to be
    made here.
    """
    return {
        "stream_index":        t.stream_index,
        "track_type":          t.track_type,
        "codec":               t.codec,
        "language":            t.language,
        "channels":            t.channels,
        "channel_layout":      t.channel_layout or "",
        "is_default":          t.is_default,
        "is_forced":           t.is_forced,
        "is_hearing_impaired": t.is_hearing_impaired,
        "is_dub":              t.is_dub,
        "title":               t.title,
    }


def _get_forged_ac3_audio_index(db: Session, file_id: int) -> int | None:
    """
    If this file has a completed (or undo-in-progress/undo-failed) AC3
    forge job, return Ac3ForgeJob.audio_track_count — the 0-based
    audio-track-relative index of the AC3 track it added. This is the same
    index the undo command uses for its -map -0:a:{N} selector, and audio
    tracks from extract_tracks() are naturally ordered by ascending
    ffprobe stream_index, matching FFmpeg's own 0:a:N addressing — so
    indexing into the file's audio_tracks list at this position reliably
    identifies the forge-derived track.

    Statuses checked: "success" (AC3 present), "undo_pending" (AC3 still
    present, removal in flight), "undo_failed" (AC3 still present, removal
    never completed). "pending"/"processing"/"failed"/"undone"/"cancelled"
    are excluded — in each of those the AC3 track either doesn't exist yet
    or has already been removed, so there's nothing to exclude.

    Returns None if no such job exists, so analyze_file() falls back to
    its normal (unmodified) und-audio counting.
    """
    from app.database.models import Ac3ForgeJob

    job = (
        db.query(Ac3ForgeJob)
        .filter(
            Ac3ForgeJob.file_id == file_id,
            Ac3ForgeJob.status.in_(["success", "undo_pending", "undo_failed"]),
        )
        .order_by(Ac3ForgeJob.created_at.desc())
        .first()
    )
    return job.audio_track_count if job else None
