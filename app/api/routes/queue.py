import json
import logging
import os
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.core.decision import analyze_file
from app.core.scanner import ScanStats, _process_file, _load_subtitle_overrides, _load_audio_language_overrides, _load_subtitle_language_overrides, _get_forged_ac3_audio_index, _track_to_dict, _upsert_language_flags
from app.core.probe import is_faststart_mp4
from app.database.models import MediaFile, PlannedAction, QueueItem, Track
from app.database.session import get_app_settings, get_db

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/queue", tags=["queue"])


# ── Shared helpers ─────────────────────────────────────────────────────────────

def _current_dry_run_mode(db: Session) -> bool:
    """
    Read the global dry_run_mode setting as it stands RIGHT NOW.

    Used whenever an item transitions back to "pending" for (re)processing
    via a user action (approve, resolve-subtitles, retry) — the item's
    is_dry_run flag must reflect the setting at the moment processing
    actually happens, not whatever the setting was when the item was
    originally queued by a scan. Otherwise an item queued during a dry-run
    scan stays a "dry run" forever, even after the user turns dry run off
    and explicitly approves/retries it for real processing.
    """
    return get_app_settings(db).get("dry_run_mode", False)


def _retry_with_reprobe(db: Session, item: QueueItem) -> dict:
    """
    Re-queue a failed / cancelled / dry-run item by deleting it and
    re-running the scanner's per-file evaluation with force_probe=True.

    Why re-probe on retry?
    -----------------------
    A "failed" item's planned actions (and the Track rows they were derived
    from) were computed from the LAST probe of the file. If the failure was
    caused by something the decision engine or FFmpeg command builder
    mishandled — e.g. an embedded cover-art stream being mapped as a second
    video track — simply flipping status back to "pending" re-runs the
    exact same (broken) plan and fails identically, even after the
    underlying bug has been fixed. Re-probing picks up:

      • Application bugfixes (corrected track extraction, decision logic, …)
      • Settings changes made since the original scan
      • Any on-disk changes to the file itself

    If re-probing determines no action is needed at all (e.g. the file is
    now fully compliant), no new QueueItem is created — that is itself a
    valid and correct outcome of "retry".
    """
    media = item.media_file
    if not media:
        raise HTTPException(404, "Associated media file not found")
    if not os.path.exists(media.path):
        raise HTTPException(400, f"File no longer exists on disk: {media.path}")

    if item.status == "dry_run":
        # Explicit "Process Now" override — regardless of current setting.
        dry_run = False
    else:
        # failed / cancelled — honor dry_run_mode as it stands NOW.
        dry_run = _current_dry_run_mode(db)

    file_path        = media.path
    file_id          = media.id
    # Preserve arr IDs so the notification chain fires after the re-processed
    # job completes — Sonarr/Radarr run RescanSeries/RescanMovie, which tells
    # Plex the file changed.  Both are None for manually-scanned files, in
    # which case the notification is simply skipped as normal.
    sonarr_series_id = item.sonarr_series_id
    radarr_movie_id  = item.radarr_movie_id

    # Remove the stale item (and its PlannedActions, via cascade) — the
    # re-probe below creates a fresh QueueItem if one is actually needed.
    db.delete(item)
    db.flush()

    app_cfg = get_app_settings(db)
    stats   = ScanStats()
    _process_file(
        db, file_path, app_cfg,
        force_probe      = True,
        dry_run          = dry_run,
        stats            = stats,
        sonarr_series_id = sonarr_series_id,
        radarr_movie_id  = radarr_movie_id,
    )
    db.commit()  # ensure the deletion above is persisted even on early-return paths

    new_item = (
        db.query(QueueItem)
        .filter(QueueItem.file_id == file_id)
        .order_by(QueueItem.created_at.desc())
        .first()
    )
    if new_item and new_item.status in ("pending", "manual_review"):
        return _serialize(new_item, include_actions=True)

    media_after = db.get(MediaFile, file_id)
    return {
        "success": True,
        "message": "File re-evaluated — no further action needed.",
        "media_status": media_after.status if media_after else None,
    }


def _build_analysis_inputs(db: Session, media: MediaFile):
    """
    Build (file_info, tracks, analyze_file-kwargs) for re-running the
    decision engine on a stored MediaFile — the exact inputs
    scanner._process_file and worker._load_job_data construct for the
    same file, in one place.

    Extracted from three near-identical inline copies in
    resolve_subtitles / resolve_subtitles_bulk / approve_manual_review.
    Independent review confirmed the triplication had already produced
    real divergence: all three copies were missing has_faststart (which
    scanner and worker both pass), so an MP4 in manual review whose only
    remaining work was add_faststart resolved to "no changes needed" →
    skipped — and the miss was STICKY, because the file's size/mtime
    never change on disk, so no delta scan ever re-evaluates it; the
    file stayed un-optimised until a forced full rescan. A scan of the
    identical file would have queued it.
    """
    tracks_raw = db.query(Track).filter(Track.file_id == media.id).all()
    tracks = [_track_to_dict(t) for t in tracks_raw]
    file_info = {
        "path": media.path, "container": media.container,
        "video_codec": media.video_codec,
        "und_audio_threshold_acknowledged": media.und_audio_threshold_acknowledged,
    }
    faststart = (
        is_faststart_mp4(media.path)
        if (media.container or "").lower() == "mp4"
        else None
    )
    kwargs = dict(
        subtitle_overrides=_load_subtitle_overrides(media),
        audio_language_overrides=_load_audio_language_overrides(media),
        subtitle_language_overrides=_load_subtitle_language_overrides(media),
        has_faststart=faststart,
        forged_ac3_audio_index=_get_forged_ac3_audio_index(db, media.id),
    )
    return file_info, tracks, kwargs


def _apply_decision_to_item(db: Session, item: QueueItem, media: MediaFile,
                             decision) -> None:
    """
    Apply a freshly-computed decision to a manual-review item — the
    shared three-outcome block (stay in review / skipped / pending)
    previously triplicated across resolve_subtitles /
    resolve_subtitles_bulk / approve_manual_review.

    Does NOT commit — the caller controls the transaction (the bulk
    endpoint commits per item so one bad item can't roll back earlier
    successes; the single-item endpoints commit once).

    Carries two fixes the triplication had let diverge:
    • _upsert_language_flags runs for the skipped and pending outcomes —
      previously the endpoints computed decision.audio/subtitle_language_
      mismatch and then discarded them, so a skipped file with a mismatch
      never appeared in Language Review, and a resolved file's stale flag
      never got cleared (the worker covers pending items at pickup, but
      nothing covered skipped). Deliberately placed AFTER the
      manual_review early-return, mirroring scanner.py's ordering: a
      manual_review decision returns before mismatch detection ever runs,
      so its mismatch fields are always None, and calling the helper for
      that outcome would incorrectly CLEAR valid existing flags.
    • completed_at is stamped on the skipped transition, matching
      scanner's skip path — without it these rows sorted to the bottom
      of the Skipped tab (ORDER BY completed_at DESC puts NULLs last)
      and rendered "—" timestamps.
    """
    if decision.is_manual_review:
        # Still unresolved (or a different gate fired) — stay in review.
        item.reason = decision.reason
        item.review_subtitles = (
            json.dumps(decision.flagged_subtitles) if decision.flagged_subtitles else None
        )
        return

    _upsert_language_flags(db, media, decision)

    if not decision.should_process:
        item.status = "skipped"
        item.reason = decision.reason
        item.review_subtitles = None
        item.completed_at = datetime.utcnow()
        media.status = "skipped"
        return

    # Changes are needed — move to the normal queue with fresh planned actions
    db.query(PlannedAction).filter(PlannedAction.queue_item_id == item.id).delete()
    item.status = "pending"
    item.reason = decision.reason
    item.review_subtitles = None
    # Honor dry_run_mode as it stands NOW, not as it was when the file
    # was originally scanned.
    item.is_dry_run = _current_dry_run_mode(db)
    media.status = "queued"
    db.flush()
    for i, action in enumerate(decision.actions):
        db.add(PlannedAction(
            queue_item_id    = item.id,
            order            = i,
            action_type      = action.action_type,
            description      = action.description,
            track_type       = action.track_type,
            stream_index     = action.stream_index,
            target_language  = getattr(action, "target_language", None),
        ))


# ── Endpoints ──────────────────────────────────────────────────────────────────

@router.get("/")
def list_queue(db: Session = Depends(get_db)):
    """All pending + processing items (the queue panel)."""
    items = (
        db.query(QueueItem)
        .filter(QueueItem.status.in_(["pending", "processing"]))
        .order_by(QueueItem.priority.asc(), QueueItem.created_at.asc())
        .all()
    )
    return [_serialize(item) for item in items]


@router.get("/active")
def get_active(db: Session = Depends(get_db)):
    """All currently-processing items (active panel of the UI)."""
    items = (
        db.query(QueueItem)
        .filter(QueueItem.status == "processing")
        .order_by(QueueItem.started_at.asc())
        .all()
    )
    return [_serialize(item, include_actions=True) for item in items]


@router.get("/manual-review")
def list_manual_review(db: Session = Depends(get_db)):
    """Items waiting for human approval."""
    items = (
        db.query(QueueItem)
        .filter(QueueItem.status == "manual_review")
        .order_by(QueueItem.created_at.asc())
        .all()
    )
    return [_serialize(item, include_actions=True) for item in items]


@router.get("/stats")
def queue_stats(db: Session = Depends(get_db)):
    """Quick counts for the UI header badges."""
    rows = (
        db.query(QueueItem.status, func.count(QueueItem.id))
        .group_by(QueueItem.status)
        .all()
    )
    return {status: count for status, count in rows}


@router.get("/{item_id}")
def get_queue_item(item_id: int, db: Session = Depends(get_db)):
    """Single item with full planned-action breakdown (modal detail view)."""
    item = db.get(QueueItem, item_id)
    if not item:
        raise HTTPException(404, "Queue item not found")
    return _serialize(item, include_actions=True)


@router.delete("/")
def clear_pending(db: Session = Depends(get_db)):
    """
    Cancel all pending (not yet started) items.

    Also sets MediaFile.status = "skipped" for every affected file,
    matching cancel_item's single-item behavior — the previous version
    only bulk-updated QueueItem.status, never touching MediaFile.status,
    so files were left stranded at "queued" with no pending item behind
    them. That state didn't self-heal on its own: a delta scan sees
    unchanged size/mtime and returns early without re-evaluating, and
    _process_file's "queued"-status disambiguation only special-cases a
    latest item of dry_run, not cancelled — so the file stayed
    (incorrectly) marked "queued" until a forced full scan happened to
    touch it. Caught by independent review.

    Also resets size/mtime to the delta-scan sentinels, matching
    cancel_item — see its docstring for the full rationale. The
    frontend's copy for this action ("They re-appear on the next scan"
    — useActions.clearQueue) was false for delta scans without it.
    """
    file_ids = [
        row[0] for row in
        db.query(QueueItem.file_id)
        .filter(QueueItem.status == "pending")
        .all()
    ]
    count = (
        db.query(QueueItem)
        .filter(QueueItem.status == "pending")
        .update({"status": "cancelled"})
    )
    if file_ids:
        db.query(MediaFile).filter(
            MediaFile.id.in_(file_ids)
        ).update(
            {"status": "skipped", "size": -1, "mtime": -1.0},
            synchronize_session=False,
        )
    db.commit()
    return {"cancelled": count}


@router.delete("/dry-run")
def clear_dry_run(db: Session = Depends(get_db)):
    """
    Remove all dry-run preview items.

    dry_run is a separate terminal status set by _finish_job — it is NOT
    "pending", so clear_pending() above never touches these. Without this
    endpoint there was no way to discard a dry-run batch the user reviewed
    and decided against; they'd sit in the History panel's Dry Run tab
    indefinitely (or until the same files got re-scanned, which overwrites
    them one at a time rather than clearing the batch).

    Deletes the QueueItem rows outright (rather than marking them
    cancelled) since a discarded preview has no ongoing significance to
    keep around — unlike a real cancelled job, there's no "this file used
    to be queued" history worth preserving.
    """
    items = db.query(QueueItem).filter(QueueItem.status == "dry_run").all()
    count = len(items)
    for item in items:
        if item.media_file:
            # Reset size/mtime to sentinel values (real files never have a
            # negative size or mtime) so the scanner's delta check in
            # _process_file — which compares ONLY size/mtime against the
            # current on-disk stat(), and has no awareness of .status at
            # all — cannot see them as "unchanged" and skip re-evaluation.
            #
            # Without this, a plain re-scan (force_probe=False) would see
            # the file's actual bytes are identical to what was stamped
            # during the dry-run probe and return immediately, never
            # calling analyze_file() again — so the cleared preview would
            # simply never reappear on any future scan until the file's
            # bytes genuinely changed on disk.
            item.media_file.size   = -1
            item.media_file.mtime  = -1.0
            item.media_file.status = "skipped"
        db.delete(item)
    db.commit()
    return {"cleared": count}


@router.delete("/{item_id}")
def cancel_item(item_id: int, db: Session = Depends(get_db)):
    """
    Cancel a specific pending or manual-review item.

    Resets the file's size/mtime to sentinel values — the same pattern
    clear_dry_run (above) and history's clear/delete endpoints use, and
    for the same reason: the scanner's delta check compares ONLY
    size/mtime against the on-disk stat(), so without the reset a
    cancelled file's unchanged bytes read as "nothing to do" and it is
    never re-evaluated by any delta scan. That directly contradicted
    the frontend's own copy for this action ("it will re-appear on the
    next library scan" — useActions.dismissQueueItem), which was only
    true for forced full scans. The sibling endpoints that faced the
    identical problem all reset the sentinels; this one and
    clear_pending were missed. Caught by independent review.

    For a manual_review item ("Skip" in the Review page) this means the
    review flag also resurfaces on the next DELTA scan — deliberately
    so: full scans already re-flag it (the underlying condition still
    holds), so this makes the two scan types consistent rather than
    changing what "skip" means. Permanent suppression has its own
    dedicated mechanism (Approve sets und_audio_threshold_acknowledged).
    """
    item = db.get(QueueItem, item_id)
    if not item:
        raise HTTPException(404, "Queue item not found")
    if item.status not in ("pending", "manual_review"):
        raise HTTPException(400, f"Cannot cancel item with status '{item.status}'")

    item.status = "cancelled"
    if item.media_file:
        item.media_file.size   = -1
        item.media_file.mtime  = -1.0
        item.media_file.status = "skipped"
    db.commit()
    return {"success": True}


@router.post("/retry-all")
def retry_all_failed(db: Session = Depends(get_db)):
    """
    Re-queue every failed and cancelled item in one call.

    Each item is re-probed with force_probe=True — the same behaviour as
    single-item retry — so the retry picks up any settings changes, code
    fixes, or on-disk changes since the original failure.

    Items whose source file no longer exists on disk are silently skipped
    rather than failing the whole operation.
    """
    items = (
        db.query(QueueItem)
        .filter(QueueItem.status.in_(["failed", "cancelled"]))
        .all()
    )

    if not items:
        return {"retried": 0, "skipped": 0}

    app_cfg = get_app_settings(db)
    dry_run = _current_dry_run_mode(db)
    retried = 0
    skipped = 0
    errors: list[dict] = []

    for item in items:
        media = item.media_file
        if not media or not os.path.exists(media.path):
            skipped += 1
            continue

        file_path = media.path
        # Preserve arr IDs so the notification chain fires after the
        # re-processed job completes — identical to _retry_with_reprobe
        # (see that function for the full rationale). Previously this
        # deleted the item and re-processed with no arr IDs at all, so
        # "Retry All" on webhook-originated failures produced jobs that
        # would never fire RescanSeries/RescanMovie on success, even
        # though single-item retry preserved this correctly. Caught by
        # independent review.
        sonarr_series_id = item.sonarr_series_id
        radarr_movie_id  = item.radarr_movie_id
        db.delete(item)
        db.flush()

        try:
            _process_file(
                db, file_path, app_cfg,
                force_probe      = True,
                dry_run          = dry_run,
                stats            = ScanStats(),
                sonarr_series_id = sonarr_series_id,
                radarr_movie_id  = radarr_movie_id,
            )
            retried += 1
        except Exception as exc:
            # Mirrors scan_library()'s own per-file protection — without
            # this, one bad file (e.g. the ValueError decision.py raises
            # for genuinely unknown container info) kills the whole
            # request with an unhandled 500, silently abandoning every
            # item still queued behind it with no indication of where the
            # batch actually stopped.
            logger.exception("Retry failed for %s", file_path)
            errors.append({"path": file_path, "error": str(exc)})
            # Only undoes THIS item's own not-yet-committed partial work
            # (the delete above, plus whatever _process_file started
            # before raising) — every earlier item in this same loop
            # already committed internally inside _process_file, so their
            # results are unaffected.
            db.rollback()

    return {"retried": retried, "skipped": skipped, "errors": errors}


class SubtitleOverridesRequest(BaseModel):
    # Maps stream_index -> "keep" | "remove"
    overrides: dict[int, str]


@router.post("/{item_id}/resolve-subtitles")
def resolve_subtitles(
    item_id: int,
    body: SubtitleOverridesRequest,
    db: Session = Depends(get_db),
):
    """
    Apply per-track keep/remove choices for a manual_review item flagged
    because of non-convertible (image-based) subtitle tracks.

    The choices are merged into MediaFile.subtitle_overrides (persisted, so
    they survive future re-scans) and the decision engine is re-run
    immediately:

      • If unresolved flagged tracks remain (e.g. the user only resolved
        some of several), the item stays in manual_review with an updated
        flagged_subtitles list.
      • If the new decision requires changes, the item moves to "pending"
        with freshly-generated planned actions.
      • If the new decision needs no changes at all (e.g. the user chose
        "keep" and nothing else needs fixing), the item is marked "skipped".
    """
    item = db.get(QueueItem, item_id)
    if not item:
        raise HTTPException(404, "Queue item not found")
    if item.status != "manual_review":
        raise HTTPException(400, "Item is not in manual review")

    media = item.media_file
    if not media:
        raise HTTPException(404, "Associated media file not found")

    for stream_index, choice in body.overrides.items():
        if choice not in ("keep", "remove"):
            raise HTTPException(400, f"Invalid choice for stream {stream_index}: {choice!r} (expected 'keep' or 'remove')")

    # ── Merge new overrides into the persisted set ──────────────────────────
    # Written to media BEFORE the analysis below — _build_analysis_inputs
    # loads subtitle_overrides back off the media object, so it sees the
    # merged set including these new choices (in-session attribute read,
    # not a DB re-read).
    existing_overrides = _load_subtitle_overrides(media)
    existing_overrides.update(body.overrides)
    media.subtitle_overrides = json.dumps({str(k): v for k, v in existing_overrides.items()})

    # ── Re-run the decision engine with the updated overrides ───────────────
    app_cfg = get_app_settings(db)
    file_info, tracks, analysis_kwargs = _build_analysis_inputs(db, media)
    decision = analyze_file(file_info, tracks, app_cfg, **analysis_kwargs)

    _apply_decision_to_item(db, item, media, decision)
    db.commit()
    return _serialize(item, include_actions=True)


@router.post("/resolve-subtitles-bulk")
def resolve_subtitles_bulk(db: Session = Depends(get_db)):
    """
    Re-run the decision engine for every manual_review item flagged
    specifically for non-convertible (image-based) subtitle tracks,
    letting image_subtitle_handling (when set to always_keep or
    always_remove) resolve them automatically — no per-item choice
    required. Built for exactly the scenario of a large backlog of these
    (e.g. hundreds) sitting in review, where clicking through each one
    individually isn't practical.

    Scoped specifically to review_subtitles IS NOT NULL — reliably,
    exclusively populated by the image-based-subtitle gate and no other
    manual-review trigger (e.g. the separate undefined-audio-count gate
    never touches it), so this can never accidentally resolve an
    unrelated manual-review item.

    If image_subtitle_handling is still "always_ask", every item re-runs
    and lands right back in manual_review, unresolved — harmless, but
    pointless; the frontend only surfaces this action once the setting is
    actually set to a resolving value.

    Commits per-item, same reasoning as retry_all_failed and
    apply_language: with a batch this size, one bad item raising an
    exception must not roll back every earlier item that already
    succeeded.
    """
    items = (
        db.query(QueueItem)
        .filter(
            QueueItem.status == "manual_review",
            QueueItem.review_subtitles.isnot(None),
        )
        .all()
    )

    app_cfg    = get_app_settings(db)
    resolved   = 0
    unresolved = 0
    errors: list[dict] = []

    for item in items:
        media = item.media_file
        if not media:
            continue

        try:
            file_info, tracks, analysis_kwargs = _build_analysis_inputs(db, media)
            decision = analyze_file(file_info, tracks, app_cfg, **analysis_kwargs)

            _apply_decision_to_item(db, item, media, decision)
            if decision.is_manual_review:
                unresolved += 1
            else:
                resolved += 1

            db.commit()

        except Exception as exc:
            logger.exception("Bulk subtitle resolve failed for item %d", item.id)
            errors.append({"item_id": item.id, "error": str(exc)})
            db.rollback()

    return {"resolved": resolved, "still_unresolved": unresolved, "errors": errors}


@router.post("/{item_id}/approve")
def approve_manual_review(item_id: int, db: Session = Depends(get_db)):
    """
    Approve a manual-review item.

    Re-runs the decision engine immediately, mirroring resolve_subtitles'
    structure exactly (see that endpoint for the fuller explanation of
    why re-running rather than just flipping status matters):

      • If the new decision still requires manual review (e.g. a
        different gate — most plausibly the image-subtitle one — also
        independently applies to this file), the item stays in
        manual_review with an updated reason.
      • If the new decision needs no changes at all, the item is marked
        "skipped".
      • Otherwise it moves to "pending" with freshly-generated planned
        actions.

    Previously this only ever flipped status to "pending" without
    re-running anything — processing itself was never affected, since
    the worker always recomputes its own decision fresh at job-pickup
    time regardless of what's stored here, but the reason text and
    Planned Actions shown in the UI stayed stale (still describing "why
    this needs manual review") for however long the item sat in the
    queue before the worker actually got to it.

    Also persists an exemption when this item's review was caused
    specifically by the undefined-audio-count threshold gate
    (decision.py) — that gate has no per-track override the way the
    image-subtitle gate does (subtitle_overrides, resolved through the
    separate resolve_subtitles endpoint instead of this generic one), so
    without this, the fresh analyze_file() call below would immediately
    re-trigger the identical gate, since a track's language tag never
    changes on its own. This has to be set BEFORE the fresh decision is
    computed — the whole point is that analyze_file() needs to already
    see it acknowledged to correctly resolve past the gate.

    review_subtitles being null is the existing, established signal
    that this item's review came from the threshold gate rather than
    the image-subtitle one — confirmed via resolve_subtitles_bulk's own
    docstring, which notes the threshold gate never populates that
    field.
    """
    item = db.get(QueueItem, item_id)
    if not item:
        raise HTTPException(404, "Queue item not found")
    if item.status != "manual_review":
        raise HTTPException(400, "Item is not in manual review")

    media = item.media_file
    if not media:
        raise HTTPException(404, "Associated media file not found")

    if item.review_subtitles is None:
        media.und_audio_threshold_acknowledged = True

    # ── Re-run the decision engine now that the exemption is in place ───────
    # _build_analysis_inputs reads und_audio_threshold_acknowledged off the
    # media object, so the in-session flag set above is what the fresh
    # decision sees — which is the entire point of setting it first.
    app_cfg = get_app_settings(db)
    file_info, tracks, analysis_kwargs = _build_analysis_inputs(db, media)
    decision = analyze_file(file_info, tracks, app_cfg, **analysis_kwargs)

    _apply_decision_to_item(db, item, media, decision)
    db.commit()
    return _serialize(item, include_actions=True)


@router.post("/{item_id}/prioritize")
def prioritize_item(item_id: int, db: Session = Depends(get_db)):
    """
    Move a pending item to the top of the queue.

    Sets its priority to one below the current minimum so the worker's
    ORDER BY priority ASC picks it up before everything else.  Works
    regardless of how many times the button is clicked — each call
    recalculates the minimum across all OTHER pending items so repeated
    presses on different items always produce a deterministic order.
    """
    item = db.get(QueueItem, item_id)
    if not item:
        raise HTTPException(404, "Queue item not found")
    if item.status != "pending":
        raise HTTPException(400, "Only pending items can be moved to the top")

    current_min = (
        db.query(func.min(QueueItem.priority))
        .filter(QueueItem.status == "pending", QueueItem.id != item_id)
        .scalar()
    )
    # If no other pending items exist, reset to default priority 5.
    # Otherwise go one lower than the current minimum.
    item.priority = (current_min - 1) if current_min is not None else 5
    db.commit()
    return {"id": item_id, "priority": item.priority}


# ── Serialiser ─────────────────────────────────────────────────────────────────

def _serialize(item: QueueItem, include_actions: bool = False) -> dict:
    media = item.media_file

    flagged_subtitles = None
    if item.review_subtitles:
        try:
            flagged_subtitles = json.loads(item.review_subtitles)
        except (ValueError, TypeError):
            flagged_subtitles = None

    out: dict = {
        "id":             item.id,
        "status":         item.status,
        "is_dry_run":     item.is_dry_run,
        "reason":         item.reason,
        "progress":       item.progress,
        "current_action": item.current_action,
        "priority":       item.priority,
        "created_at":     _iso(item.created_at),
        "started_at":     _iso(item.started_at),
        "completed_at":   _iso(item.completed_at),
        "error_message":  item.error_message,
        "flagged_subtitles": flagged_subtitles,
        "file": {
            "id":        media.id,
            "filename":  media.filename,
            "path":      media.path,
            "container": media.container,
            "size":      media.size,
            "duration":  media.duration,
        } if media else None,
    }

    if include_actions:
        out["planned_actions"] = [
            {
                "order":        a.order,
                "action_type":  a.action_type,
                "description":  a.description,
                "track_type":   a.track_type,
                "stream_index": a.stream_index,
            }
            for a in item.planned_actions   # already ordered by PlannedAction.order
        ]

    return out


def _iso(dt: datetime | None) -> str | None:
    return dt.isoformat() if dt else None
