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
from app.database.models import Ac3ForgeJob, AudioLanguageFlag, MediaFile, PlannedAction, PlexAnalyzeBacklog, QueueItem, SubtitleLanguageFlag, Track
from app.database.session import get_app_settings

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
    cancelled:     bool = False   # True if the scan was stopped early by the user


# ── Public API ─────────────────────────────────────────────────────────────────

def scan_library(
    db:          Session,
    paths:       list[str],
    force_probe: bool = False,
    progress_callback=None,   # optional: callable(scanned: int, total: int)
    cancel_check=None,        # optional: callable() -> bool, checked per-file; True = stop
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
    cancel_check : callable | None
        Called after each file, same checkpoint as progress_callback. If it
        returns truthy, the scan stops cleanly after the current file —
        whatever's already been processed stays committed exactly as-is,
        since _process_file() commits per-file already; nothing about
        cancelling mid-scan can leave a partial or corrupt state. Sets
        ScanStats.cancelled so the caller can distinguish an early stop
        from a normal, complete finish.
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

    scanned   = 0
    cancelled = False
    for scan_path in paths:
        if cancelled:
            break
        if not os.path.isdir(scan_path):
            logger.warning("Scan path not found or not a directory: %s", scan_path)
            continue

        for root, dirs, files in os.walk(scan_path, followlinks=True):
            if cancelled:
                break
            dirs[:] = [d for d in dirs if not d.startswith(".")]   # skip hidden

            for filename in files:
                if cancelled:
                    break
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

                if cancel_check and cancel_check():
                    cancelled = True
                    logger.info(
                        "Scan cancelled by user after %d/%d files",
                        scanned, total_files,
                    )

                # Yield to Python's thread scheduler so HTTP handler threads
                # (sync route handlers) can run between files.  Without this
                # the scanner thread can hold the GIL or block on I/O for
                # long stretches, starving other threads.
                time.sleep(0)

    stats.cancelled = cancelled

    # ── Cleanup pass ───────────────────────────────────────────────────────
    # After the directory walk, remove DB rows for files that no longer exist
    # on disk — but only if the setting is enabled (default: True), and only
    # if the scan actually completed. Skipped on cancellation: not because
    # it would be incorrect to run (it checks each file's real existence on
    # disk directly, independent of how far the scan itself got), but
    # because the user explicitly asked to stop, and running one more
    # library-wide pass right after that doesn't honor that request fully.
    if not cancelled and app_cfg.get("auto_cleanup_on_scan", True):
        stats.removed = cleanup_deleted_files(db, [p for p in paths if os.path.isdir(p)])

    logger.info(
        "Scan %s — total=%d new=%d changed=%d unchanged=%d "
        "queued=%d review=%d skipped=%d errors=%d removed=%d",
        "cancelled by user" if cancelled else "done",
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


def _delete_media_file_and_related(db: Session, media: MediaFile) -> None:
    """
    Fully remove a MediaFile row and every row across the codebase that
    references it — does NOT commit; the caller controls the transaction.

    tracks and queue_items (and queue_items' own planned_actions) DO
    cascade correctly via SQLAlchemy's own cascade="all, delete-orphan"
    on those specific relationships. Ac3ForgeJob, PlexAnalyzeBacklog,
    AudioLanguageFlag, and SubtitleLanguageFlag do NOT have that
    configured, and their ondelete="CASCADE" foreign keys are not
    actually enforced either — SQLite only respects that when PRAGMA
    foreign_keys=ON is set per-connection, which this project does not
    do. Deleting all four explicitly here, rather than assuming cascade
    behavior that doesn't actually apply to them, is what prevents them
    from being silently orphaned — confirmed this was a real,
    pre-existing gap for PlexAnalyzeBacklog and AudioLanguageFlag
    specifically: this function was written before either table existed
    and was never updated when they were added. SubtitleLanguageFlag was
    added directly alongside this comment specifically to avoid
    repeating that exact mistake a third time.
    """
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
    db.query(PlexAnalyzeBacklog).filter(
        PlexAnalyzeBacklog.file_id == media.id
    ).delete(synchronize_session=False)
    db.query(AudioLanguageFlag).filter(
        AudioLanguageFlag.file_id == media.id
    ).delete(synchronize_session=False)
    db.query(SubtitleLanguageFlag).filter(
        SubtitleLanguageFlag.file_id == media.id
    ).delete(synchronize_session=False)
    db.query(Track).filter(
        Track.file_id == media.id
    ).delete(synchronize_session=False)
    db.delete(media)


def _clear_stale_status_records(db: Session, file_id: int, statuses: tuple[str, ...]) -> None:
    """
    Delete any existing QueueItem(s) for this file matching the given
    as-of-this-scan statuses — call this right before creating a
    genuinely new record with a DIFFERENT status for the same file.

    "skipped" and "manual_review" share the same defining trait: each
    one's entire meaning is "as of THIS scan, here's the situation,"
    not a genuine historical event — that meaning becomes actively
    false, not just stale, the moment a LATER scan decides differently
    for the same file (e.g. after a settings change, or — for
    manual_review specifically — the underlying condition resolving
    itself via a full rescan or webhook re-trigger rather than the
    dedicated Approve/Resolve endpoints, which is the one path that
    already updates things correctly). Without this, the old record
    just sits there forever alongside the new, genuine one, so the same
    file ends up visibly listed under two tabs in the History panel at
    once.

    This function originally only ever cleared "skipped" records — an
    independent code review caught that manual_review has the exact
    same character and was missing the same treatment, a gap in my own
    original scoping of this fix, not a separate, unrelated bug.

    Deliberately does NOT apply to completed/failed/cancelled/dry_run —
    those represent real, historical events that actually happened and
    should stay in history regardless of the file's current state; only
    "skipped" and "manual_review" can have their entire claim become
    false out from under them like this.
    """
    db.query(QueueItem).filter(
        QueueItem.file_id == file_id,
        QueueItem.status.in_(statuses),
    ).delete(synchronize_session=False)


def _upsert_language_flags(db: Session, media_file: MediaFile, decision) -> None:
    """
    Upsert or clear AudioLanguageFlag/SubtitleLanguageFlag for this file
    based on a freshly-computed decision's audio_language_mismatch /
    subtitle_language_mismatch fields.

    This was originally inline within _process_file, called only during
    a scan. Extracted into a shared helper specifically so worker.py can
    also call it right after computing its own fresh decision at
    job-pickup time (_load_job_data) — without that, a file whose
    manual-review cause gets resolved via Approve (rather than being
    re-discovered by a normal scan) would compute the correct
    audio_language_mismatch/subtitle_language_mismatch the whole time,
    at every single point along the way (approval, worker pickup), but
    never actually persist it to either flag table until some LATER,
    separate scan happened to re-evaluate the file from scratch —
    reported directly: files approved from manual review, whose jobs
    succeeded and correctly logged the "audio language tag is likely
    wrong" warning, didn't show up in Audio Language Review until an
    entirely separate full scan ran afterward.

    Never applies to a file the user has explicitly confirmed is correct
    via Ignore, and never blocks processing either way — this is purely
    bookkeeping for the Audio/Subtitle Language Review sections.
    """
    existing_flag = (
        db.query(AudioLanguageFlag)
        .filter(AudioLanguageFlag.file_id == media_file.id)
        .first()
    )
    if decision.audio_language_mismatch and not media_file.audio_language_ignored:
        mismatch = decision.audio_language_mismatch
        if existing_flag:
            existing_flag.stream_index      = mismatch["stream_index"]
            existing_flag.detected_language = mismatch["language"]
        else:
            db.add(AudioLanguageFlag(
                file_id=media_file.id,
                stream_index=mismatch["stream_index"],
                detected_language=mismatch["language"],
            ))
    elif existing_flag:
        # No longer mismatched (or now ignored) — clear any stale flag.
        db.delete(existing_flag)

    existing_sub_flag = (
        db.query(SubtitleLanguageFlag)
        .filter(SubtitleLanguageFlag.file_id == media_file.id)
        .first()
    )
    if decision.subtitle_language_mismatch and not media_file.subtitle_language_ignored:
        mismatch = decision.subtitle_language_mismatch
        if existing_sub_flag:
            existing_sub_flag.stream_index      = mismatch["stream_index"]
            existing_sub_flag.detected_language = mismatch["language"]
        else:
            db.add(SubtitleLanguageFlag(
                file_id=media_file.id,
                stream_index=mismatch["stream_index"],
                detected_language=mismatch["language"],
            ))
    elif existing_sub_flag:
        db.delete(existing_sub_flag)


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
        _delete_media_file_and_related(db, media)
        removed += 1

    if removed:
        db.commit()
        logger.info("Cleanup: removed %d deleted file(s) from database", removed)

    return removed


def find_orphaned_media_files(db: Session, scan_paths: list[str]) -> list[MediaFile]:
    """
    Return every MediaFile row whose path does NOT fall under any
    currently-configured scan path.

    This is the inverse of cleanup_deleted_files' own scoping: that
    function is deliberately scoped to ONLY ever touch files inside
    scan_paths, to avoid accidentally reaching outside the configured
    library. That safety property has a real consequence, though — if a
    scan path is ever removed from settings after files under it were
    scanned, the resulting MediaFile rows become permanently invisible
    to cleanup, since it will never again consider a path outside the
    current configuration. This function surfaces exactly those rows,
    regardless of whether the underlying file still exists on disk —
    membership outside the configured library is the criterion here,
    not whether the file happens to still be there.
    """
    prefixes = tuple(
        p if p.endswith(os.sep) else p + os.sep
        for p in scan_paths
    )
    all_media: list[MediaFile] = db.query(MediaFile).all()
    return [
        m for m in all_media
        if not scan_paths or not any(m.path.startswith(p) for p in prefixes)
    ]


def remove_orphaned_media_files(db: Session, file_ids: list[int]) -> int:
    """
    Remove specific MediaFile rows by ID, using the same complete
    deletion helper cleanup_deleted_files relies on. Intended for
    orphaned rows found via find_orphaned_media_files above — deliberately
    does NOT check scan_paths membership or disk existence itself, since
    the caller has already made that determination (and the whole point
    of this path is to remove rows for files outside the configured
    library, which by definition cleanup_deleted_files can never reach).
    """
    removed = 0
    for file_id in file_ids:
        media = db.get(MediaFile, file_id)
        if not media:
            continue
        logger.info("Orphaned file cleanup: removing %s (id=%d)", media.path, media.id)
        _delete_media_file_and_related(db, media)
        removed += 1

    if removed:
        db.commit()

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
            # File is identical on disk. media_file.status == "queued" is
            # set in TWO different situations that look identical from
            # this field alone: (a) the file is simply still pending its
            # first attempt (queued for real processing, hasn't run yet
            # — set in this same function when the file is first queued),
            # or (b) a dry-run job already finished and _finish_job
            # deliberately left it as "queued" so the next real scan
            # re-evaluates it with dry_run_mode as it now stands.
            #
            # Checking the file's most recent QueueItem disambiguates —
            # its own status is "dry_run" for a genuinely completed
            # preview, or "pending" for a file that's simply still
            # waiting its turn. Only the former should trigger a
            # re-probe here; treating an ordinary pending item the same
            # way wastes a probe and logs a misleading "post-dry-run"
            # message for something that never actually happened.
            if existing.status == "queued":
                latest_item = (
                    db.query(QueueItem)
                    .filter(QueueItem.file_id == existing.id)
                    .order_by(QueueItem.id.desc())
                    .first()
                )
                if latest_item and latest_item.status == "dry_run":
                    logger.info(
                        "Post-dry-run re-evaluation (file unchanged on disk): %s", path
                    )
                else:
                    stats.unchanged += 1
                    return
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
            # codec_long/raw_ffprobe/sample_rate/bit_rate deliberately NOT
            # populated — confirmed genuinely write-only (nothing in the
            # codebase ever reads them). Columns kept in the schema in
            # case a future feature wants them (raw_ffprobe especially,
            # for debugging), but there's no reason to spend the write
            # cost until something actually consumes them.
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
        "und_audio_threshold_acknowledged": media_file.und_audio_threshold_acknowledged,
    }
    overrides = _load_subtitle_overrides(media_file)
    audio_lang_overrides = _load_audio_language_overrides(media_file)
    subtitle_lang_overrides = _load_subtitle_language_overrides(media_file)
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
        audio_language_overrides=audio_lang_overrides,
        subtitle_language_overrides=subtitle_lang_overrides,
        has_faststart=faststart,
        forged_ac3_audio_index=forged_ac3_audio_index,
    )

    # ── Manual review ──────────────────────────────────────────────────────
    if decision.is_manual_review:
        media_file.status = "manual_review"
        _clear_stale_status_records(db, media_file.id, ("skipped",))

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

    # ── Audio language mismatch flag ─────────────────────────────────────────
    # Runs regardless of whether the file below ends up skipped or queued —
    # a mismatch can exist on either path (e.g. a file that's otherwise
    # fully correct but has a mistagged audio track sitting on the "skip"
    # path). Never applies to a file the user has explicitly confirmed is
    # correct via Ignore, and never blocks processing either way — this is
    # purely bookkeeping for the Audio Language Review section.
    _upsert_language_flags(db, media_file, decision)

    # ── Skip ───────────────────────────────────────────────────────────────
    if not decision.should_process:
        media_file.status = "skipped"
        stats.skipped += 1
        _clear_stale_status_records(db, media_file.id, ("manual_review",))

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

    _clear_stale_status_records(db, media_file.id, ("skipped", "manual_review"))
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
            queue_item_id    = qi.id,
            order            = i,
            action_type      = action.action_type,
            description      = action.description,
            track_type       = action.track_type,
            stream_index     = action.stream_index,
            target_language  = getattr(action, "target_language", None),
        ))

    db.commit()
    stats.queued += 1
    logger.info("Queued [%d] %s — %s", qi.id, os.path.basename(path), decision.reason)


def _load_int_keyed_json_overrides(media_file: MediaFile, attr: str) -> dict[int, str]:
    """
    Shared implementation for _load_subtitle_overrides,
    _load_audio_language_overrides, and _load_subtitle_language_overrides
    below — those three were previously byte-identical apart from which
    MediaFile column they read (their own docstrings already
    acknowledged this). Consolidated here as thin wrappers rather than
    updating every importer (worker.py, queue.py, audio_language.py,
    subtitle_language.py) to call this directly, so every existing call
    site keeps working unchanged. Caught by independent review.

    Parses a JSON dict with string keys (JSON object keys are always
    strings) into a dict[int, str] keyed by stream_index, as expected by
    analyze_file(). The warning message is built from `attr` itself,
    which reproduces each wrapper's own original, distinct wording
    exactly — preserved deliberately in case anything greps logs for a
    specific one of these three messages.
    """
    value = getattr(media_file, attr)
    if not value:
        return {}
    try:
        raw = json.loads(value)
        return {int(k): v for k, v in raw.items()}
    except (ValueError, AttributeError, TypeError):
        logger.warning(
            "Invalid %s JSON for file %d — ignoring", attr, media_file.id
        )
        return {}


def _load_subtitle_overrides(media_file: MediaFile) -> dict[int, str]:
    """
    Parse MediaFile.subtitle_overrides (JSON dict with string keys, since
    JSON object keys are always strings) into a dict[int, str] keyed by
    stream_index, as expected by analyze_file().
    """
    return _load_int_keyed_json_overrides(media_file, "subtitle_overrides")


def _load_audio_language_overrides(media_file: MediaFile) -> dict[int, str]:
    """
    Parse MediaFile.audio_language_overrides (same JSON-dict-with-string-keys
    shape as _load_subtitle_overrides above) into a dict[int, str] keyed by
    stream_index, as expected by analyze_file().
    """
    return _load_int_keyed_json_overrides(media_file, "audio_language_overrides")


def _load_subtitle_language_overrides(media_file: MediaFile) -> dict[int, str]:
    """Subtitle counterpart to _load_audio_language_overrides above — same
    shape, same parsing, different column."""
    return _load_int_keyed_json_overrides(media_file, "subtitle_language_overrides")


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


