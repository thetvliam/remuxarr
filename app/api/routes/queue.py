import json
import logging
import os
from datetime import datetime
from app.core.timeutil import utcnow

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.core.decision import SRT_CONVERTIBLE_SUBS, analyze_file
from app.core.scanner import (ScanStats, _file_info_for, _process_file,
                              _load_subtitle_overrides, _load_audio_language_overrides,
                              _load_subtitle_language_overrides, _load_track_answers,
                              _get_forged_ac3_audio_index, _track_to_dict,
                              _upsert_language_flags, descriptors_by_stream)
from app.core.probe import is_faststart_mp4
from app.database.models import (
    AudioLanguageFlag, MediaFile, PlannedAction, QueueItem,
    SubtitleLanguageFlag, Track,
)
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
    re-running the scanner's per-file evaluation with force_probe=True,
    OR re-evaluate a success / skipped item WITHOUT deleting its history
    record (surfaced in the UI as "RE-PROCESS" — see the delete guard
    below). Any exception from the re-evaluation is rolled back and
    reported as a 400 rather than a 500 (see the try/except below).

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
        # failed / cancelled / success / skipped — honor dry_run_mode as
        # it stands NOW.
        dry_run = _current_dry_run_mode(db)

    file_path        = media.path
    file_id          = media.id
    # Preserve arr IDs so the notification chain fires after the re-processed
    # job completes — Sonarr/Radarr run RescanSeries/RescanMovie, which tells
    # Plex the file changed.  Both are None for manually-scanned files, in
    # which case the notification is simply skipped as normal.
    sonarr_series_id = item.sonarr_series_id
    radarr_movie_id  = item.radarr_movie_id

    # Delete only STALE ATTEMPTS (failed / cancelled / dry-run previews) —
    # the re-probe below replaces them with a fresh item if one is needed.
    # A success or skipped item is a COMPLETED EVALUATION and a terminal
    # history record (a success row also carries real bytes-saved /
    # output-size stats), surfaced in the UI as "RE-PROCESS" rather than
    # "RETRY": re-evaluate the file WITHOUT erasing that record.
    # Previously every status was deleted unconditionally, so
    # re-processing an already-compliant success — the common case, since
    # the file is compliant *because* it succeeded — destroyed the
    # history row and its stats and created nothing to replace it.
    # Preserving is safe: _process_file's queue path clears only
    # ("skipped", "manual_review") stale records, never "success", so a
    # kept success row survives a re-probe that queues new work (yielding
    # two legitimate history rows for two operations), and a kept skipped
    # row is updated in place by the skip path.
    preserve_completed_record = item.status in ("success", "skipped")
    if not preserve_completed_record:
        # Removes the stale item and its PlannedActions via cascade.
        db.delete(item)
        db.flush()

    app_cfg = get_app_settings(db)
    stats   = ScanStats()
    try:
        _process_file(
            db, file_path, app_cfg,
            force_probe      = True,
            dry_run          = dry_run,
            stats            = stats,
            sonarr_series_id = sonarr_series_id,
            radarr_movie_id  = radarr_movie_id,
        )
    except Exception as exc:
        # Same failure class retry_all_failed guards against per-item
        # (e.g. the ValueError decision.py raises for genuinely unknown
        # container info). The bulk sibling collects these and continues;
        # a single explicit retry instead surfaces the reason directly.
        # Without this the exception propagated as an unhandled 500 with
        # no indication of what went wrong — and, for a stale attempt,
        # left the item already deleted above. Roll back so a failed
        # retry never destroys the item it was meant to re-queue, then
        # report the reason.
        logger.exception("Retry failed for %s", file_path)
        db.rollback()
        raise HTTPException(400, f"Retry failed: {exc}") from exc

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
    file_info = _file_info_for(media)
    faststart = (
        is_faststart_mp4(media.path)
        if (media.container or "").lower() == "mp4"
        else None
    )
    kwargs = dict(
        subtitle_overrides=_load_subtitle_overrides(media, tracks),
        audio_language_overrides=_load_audio_language_overrides(media, tracks),
        subtitle_language_overrides=_load_subtitle_language_overrides(media, tracks),
        has_faststart=faststart,
        forged_ac3_audio_index=_get_forged_ac3_audio_index(db, media.id),
    )
    return file_info, tracks, kwargs


def _force_rescan(media: MediaFile) -> None:
    """
    Make the next delta scan re-probe this file, whatever its bytes say.

    A delta scan compares the stored size and mtime against what is on disk
    and skips anything unchanged. Every path that retires a file WITHOUT
    touching it therefore has to invalidate that stamp, or the file is never
    re-evaluated: its bytes are identical to what was stamped, the scan
    returns immediately, and analyze_file is never called again until
    something genuinely edits the file on disk.

    That has been fixed twice already, once in clear_pending and once in
    cancel_item, and the two fixes were separate copies of these two lines.
    Clearing an acknowledgement is the third caller, so it is a function now
    rather than a third copy to keep in step.

    Deliberately does not set status: the callers disagree about it
    ("skipped" for a cancelled item, untouched for a cleared
    acknowledgement) and folding it in here would make this do two things.
    """
    media.size  = -1
    media.mtime = -1.0


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
        # Which gate fired, so the bulk resolvers do not have to work it out
        # from review_subtitles — see QueueItem.review_reason.
        item.review_reason = decision.review_reason
        return

    _upsert_language_flags(db, media, decision)

    if not decision.should_process:
        item.status = "skipped"
        item.reason = decision.reason
        item.review_subtitles = None
        item.review_reason = None
        item.completed_at = utcnow()
        media.status = "skipped"
        return

    # Changes are needed — move to the normal queue with fresh planned actions
    db.query(PlannedAction).filter(PlannedAction.queue_item_id == item.id).delete()
    item.status = "pending"
    item.reason = decision.reason
    item.review_subtitles = None
    item.review_reason = None
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


def _review_heading(path: str, scan_paths: list[str]) -> str:
    """
    What a card calls the folder its files are in.

    Derived from the path, never parsed out of the filename. The longest
    matching scan path wins, so adding a broader library root later does not
    silently rewrite every heading: with /media and /media/tv both configured,
    a file under /media/tv is named relative to /media/tv.

    Matched by whole path components, so /media/tv is not a root for
    /media/tvshows.

    A file under no scan path at all — webhooks do not check them, and
    scanner.find_orphaned_media_files reports rows outside every one of them —
    falls back to the last two segments of its folder. That names the folder
    without asserting a root nobody configured, at the cost of a film under an
    unconfigured folder reading "movies / The Movie".

    Display only. Grouping always keys on the full directory, so two folders
    that render the same heading never merge.
    """
    parent = os.path.dirname(path)
    roots = [r.rstrip(os.sep) for r in scan_paths if r]
    matching = [r for r in roots
                if parent == r or parent.startswith(r + os.sep)]
    if matching:
        root = max(matching, key=len)
        relative = parent[len(root):].strip(os.sep)
        # A file sitting directly in its library root has nothing relative to
        # show, so the root's own folder name is the most it can be called.
        parts = relative.split(os.sep) if relative else [os.path.basename(root)]
    else:
        parts = [p for p in parent.split(os.sep) if p][-2:]
    return " / ".join(parts)


def _flagged_signature(flagged: list[dict], font_attachments: int | None):
    """
    What makes two files the same question.

    The flagged tracks in stream order as (codec, language, forced, reason),
    plus the file's font count, which decides whether keeping a styled track
    costs the styling. Episodes 1-10 carrying jpn ass + eng ass and 11-12
    also carrying a PGS commentary are two questions, and one card cannot
    offer a toggle for a track half its files do not have.

    Not the track descriptor answers are stored against (scanner.py). That
    one leaves language out, because language gets corrected, and includes
    the title, because it identifies a track within its own file. Here
    language is exactly what makes two cards different questions, and titles
    vary per episode — including them would split a season into one card per
    file.
    """
    return (
        tuple((t.get("codec"), t.get("language"), bool(t.get("is_forced")),
               t.get("reason"))
              for t in flagged),
        font_attachments or 0,
    )


@router.get("/review/groups")
def list_review_groups(limit:  int = Query(default=25, ge=1, le=200),
                       offset: int = Query(default=0, ge=0),
                       db: Session = Depends(get_db)):
    """
    Files waiting in review, grouped into the cards the Review page shows,
    and paged by card.

    A backlog is thousands of files and a handful of questions: one release
    of one show is one question asked about twelve files. Paging by file
    would put a page boundary through the middle of a card, which cannot be
    rendered honestly — the count would be of what happened to fit.

    Grouped by directory and flagged-track signature; see _flagged_signature.
    Each group carries every one of its files, with the stream numbers that
    file has for each track slot the card shows. The slots are shared, the
    numbers are not: the same question in two files can sit at different
    stream indices, which is why the apply endpoint takes answers per file.

    Rows with no flagged tracks are not on this page. It asks one thing —
    which tracks that block MP4 to keep — and a row with nothing flagged is
    not that question.

    Grouped in Python rather than SQL: the flagged tracks are a JSON column,
    so a signature in SQL means JSON functions for no gain at this size. It
    reads the waiting rows once per request, which is a few thousand small
    rows on a page nobody loads in a loop.
    """
    rows = (
        db.query(QueueItem.file_id, QueueItem.review_subtitles,
                 MediaFile.path, MediaFile.filename, MediaFile.font_attachments)
        .join(MediaFile, QueueItem.file_id == MediaFile.id)
        .filter(QueueItem.status == "manual_review",
                QueueItem.review_subtitles.isnot(None))
        .order_by(MediaFile.path.asc())
        .all()
    )

    scan_paths = get_app_settings(db).get("scan_paths") or []
    groups: dict = {}
    for file_id, flagged_json, path, filename, fonts in rows:
        try:
            flagged = json.loads(flagged_json)
        except (ValueError, TypeError):
            continue
        if not flagged:
            continue

        directory = os.path.dirname(path)
        key = (directory, _flagged_signature(flagged, fonts))
        group = groups.get(key)
        if group is None:
            group = groups[key] = {
                "key":          f"{len(groups)}:{directory}",
                "heading":      _review_heading(path, scan_paths),
                "directory":    directory,
                "font_attachments": fonts or 0,
                # The slots the card puts one set of choices on. Titles are
                # this file's: they are not part of the signature, so files
                # in one group can name the same track differently.
                "tracks": [
                    {"codec":     t.get("codec"),
                     "language":  t.get("language"),
                     "is_forced": bool(t.get("is_forced")),
                     "reason":    t.get("reason"),
                     "title":     t.get("title")}
                    for t in flagged
                ],
                "files": [],
            }
        group["files"].append({
            "file_id":  file_id,
            "filename": filename,
            "path":     path,
            # One stream number per track slot above, in the same order.
            "streams":  [t.get("stream_index") for t in flagged],
        })

    ordered = list(groups.values())
    for group in ordered:
        group["file_count"] = len(group["files"])
    return {
        "groups":      ordered[offset:offset + limit],
        "total_groups": len(ordered),
        "total_files":  sum(g["file_count"] for g in ordered),
    }


class ReviewFileDecision(BaseModel):
    file_id: int
    # Maps stream_index -> "keep" | "remove" | "extract", for every flagged
    # track of that file. See apply_review_decisions.
    answers: dict[int, str]


class ReviewApplyRequest(BaseModel):
    files: list[ReviewFileDecision] = []
    skips: list[int] = []


def _flagged_streams(item: QueueItem) -> set:
    """
    The streams an item is flagged for, as the page was shown them.

    Answers have to name exactly this set. A file re-probed since then has a
    different one, which means the card the user answered described tracks
    this file may not have any more.
    """
    if not item.review_subtitles:
        return set()
    try:
        return {t["stream_index"] for t in json.loads(item.review_subtitles)}
    except (ValueError, TypeError, KeyError):
        return set()


PREVIEW_FILE_LIMIT = 500


@router.post("/review/preview")
def preview_review_decisions(body: ReviewApplyRequest, db: Session = Depends(get_db)):
    """
    What the staged answers would do to each file, without doing any of it.

    The card's outcome line cannot be worked out from the answers alone.
    Keeping a track holds a file as MKV, but so does DTS audio, and a file
    whose container is already MP4 converts to nothing at all: checked
    against the engine, the rule "any keep means it stays MKV" was wrong for
    six of seven files. Only analyze_file knows, so the answer comes from
    analyze_file.

    Returns per file the container it is in, the container it would end in
    (null when the file would not be processed at all), whether it would be
    processed, whether it would still be waiting in review, and whether
    anything other than its subtitles keeps it out of MP4. The sentence
    on the card is built from that, plus the counts of kept, removed and
    extracted tracks, which the page already has because it staged them.

    Writes nothing. The staged answers are merged into a copy of the file's
    stored answers and handed to the engine as an argument; the file's row
    is not touched and nothing is committed.

    An answer that could never be carried out is refused here rather than at
    Apply, through the same checks Apply uses, so a card can say so while
    the user is still choosing. Files with nothing waiting in review, or
    whose flagged tracks have changed since the page was served, are
    reported the same way Apply reports them.

    Capped at PREVIEW_FILE_LIMIT files, because this costs a decision per
    file — a couple of queries, and for an MP4 source a small read to see
    whether it is already fast-start. That read is kept rather than guessed
    at: without it the engine plans a fast-start pass for a file that
    already has one, and the card would promise work that is not needed. The
    page previews the card being edited, not the whole backlog.

    Skips are not previewed. A skipped file has no outcome to compute, and
    the page knows what it staged.
    """
    if len(body.files) > PREVIEW_FILE_LIMIT:
        raise HTTPException(
            400,
            f"Too many files to preview at once: {len(body.files)} "
            f"(limit {PREVIEW_FILE_LIMIT}). Preview one card at a time.")

    outcomes: list[dict] = []
    errors: list[dict] = []
    app_cfg = get_app_settings(db)

    for entry in body.files:
        item = (
            db.query(QueueItem)
            .filter(QueueItem.file_id == entry.file_id,
                    QueueItem.status == "manual_review")
            .order_by(QueueItem.created_at.asc())
            .first()
        )
        if item is None:
            errors.append({
                "file_id": entry.file_id,
                "error": "No file waiting in review — it may have been "
                         "answered or re-scanned already",
            })
            continue

        media = item.media_file
        flagged = _flagged_streams(item)
        if flagged != set(entry.answers):
            errors.append({
                "file_id": entry.file_id,
                "error": "The tracks flagged for this file have changed since "
                         "it was shown — nothing can be previewed for it",
            })
            continue

        try:
            _refuse_impossible_answers(db, item, media, entry.answers)
        except ReviewAnswerRefused as refused:
            errors.append({"file_id": entry.file_id, "error": str(refused)})
            continue

        file_info, tracks, kwargs = _build_analysis_inputs(db, media)
        # The staged answers on top of whatever this file already had
        # answered, as an argument. Nothing is written.
        kwargs["subtitle_overrides"] = {
            **kwargs["subtitle_overrides"], **entry.answers}
        decision = analyze_file(file_info, tracks, app_cfg, **kwargs)

        outcomes.append({
            "file_id":           entry.file_id,
            "current_container": (media.container or "").lower() or None,
            "target_container":  decision.target_container,
            "will_process":      decision.should_process,
            "still_in_review":   decision.is_manual_review,
            # Whether anything other than these subtitles keeps the file out
            # of MP4, so the card can say "delete it and this converts" only
            # when that is true.
            "blocked_beyond_subtitles": decision.mp4_blocked_beyond_subtitles,
        })

    return {"outcomes": outcomes, "errors": errors}


@router.post("/review/apply")
def apply_review_decisions(body: ReviewApplyRequest, db: Session = Depends(get_db)):
    """
    Apply a page's worth of subtitle-review decisions in one call: the files
    answered, and the files skipped.

    Files are named by file_id, not by queue item. The decision has always
    been about the file — subtitle_overrides lives on MediaFile, and the
    item is only the current evaluation of it — and item ids do not survive
    staging: answering an audio language deletes a file's items and re-runs
    the scan path, which creates a new one. Anime routinely has both an
    audio and a subtitle question, so that is a normal thing to happen
    between a page loading and its Apply. Each file is resolved to its
    current manual_review item here instead.

    Returns an outcome per file — the status it landed on, its reason, and
    whether the job that follows is a dry run — plus refusals. One summary
    is built from that, rather than a toast per file.

    Answers have to name exactly the tracks the file is flagged for. A file
    re-probed since the page was served has a different set, which means the
    card the user answered described tracks this file may not have any more;
    applying part of it would record answers for a question nobody was
    asked. That file is reported and left alone.

    Committed per file, for the reason retry_all_failed and the language
    apply share: with a page of decisions, one refusal must not roll back
    the files already applied.

    Everything here is a row change, so there is no dry-run guard. A dry run
    is about what a job writes to disk, and the decision this records is
    what a later job — dry run or not — will act on.
    """
    outcomes: list[dict] = []
    errors: list[dict] = []

    # Whole-request validation first, before anything is written: a choice
    # this endpoint does not have, or a file both answered and skipped, is a
    # caller that has built the request wrong rather than one file's problem.
    for entry in body.files:
        for stream_index, choice in entry.answers.items():
            if choice not in ("keep", "remove", "extract"):
                raise HTTPException(
                    400,
                    f"Invalid choice for file {entry.file_id} stream "
                    f"{stream_index}: {choice!r} (expected 'keep', 'remove' "
                    f"or 'extract')")
    both = sorted({e.file_id for e in body.files} & set(body.skips))
    if both:
        raise HTTPException(
            400,
            f"Answered and skipped in the same request: "
            f"{', '.join(str(i) for i in both)}")

    def _review_item(file_id: int):
        return (
            db.query(QueueItem)
            .filter(QueueItem.file_id == file_id,
                    QueueItem.status == "manual_review")
            .order_by(QueueItem.created_at.asc())
            .first()
        )

    for entry in body.files:
        item = _review_item(entry.file_id)
        if item is None:
            errors.append({
                "file_id": entry.file_id,
                "error": "No file waiting in review — it may have been "
                         "answered or re-scanned already",
            })
            continue

        if _flagged_streams(item) != set(entry.answers):
            errors.append({
                "file_id": entry.file_id,
                "error": "The tracks flagged for this file have changed since "
                         "it was shown — nothing was applied to it",
            })
            continue

        try:
            _answer_subtitle_review(db, item, item.media_file, entry.answers)
        except ReviewAnswerRefused as refused:
            db.rollback()
            errors.append({"file_id": entry.file_id, "error": str(refused)})
            continue

        db.commit()
        outcomes.append({
            "file_id":    entry.file_id,
            "status":     item.status,
            "reason":     item.reason,
            "is_dry_run": item.is_dry_run,
        })

    for file_id in body.skips:
        item = _review_item(file_id)
        if item is None:
            errors.append({
                "file_id": file_id,
                "error": "No file waiting in review — it may have been "
                         "answered or re-scanned already",
            })
            continue

        _cancel_item_row(item)
        db.commit()
        outcomes.append({
            "file_id":    file_id,
            "status":     item.status,
            "reason":     item.reason,
            "is_dry_run": item.is_dry_run,
        })

    return {"outcomes": outcomes, "errors": errors}


@router.get("/stats")
def queue_stats(db: Session = Depends(get_db)):
    """
    Quick counts for the UI header badges.

    Queue statuses come back at the top level keyed by status. The
    language-review backlog is nested under "language_review" rather than
    added as two more top-level keys, because it counts FLAG ROWS and not
    QueueItems: a caller walking this dict as a status map must not pick
    them up as though they were statuses.

    Those figures may be added to the manual_review count without double
    counting a file. Every is_manual_review decision returns early (see the
    three gates in decision.py) and constructs its ProcessingDecision
    without audio_language_mismatch or subtitle_language_mismatches, so
    both default to empty — and _upsert_language_flags deletes any existing
    rows when they are. A file is therefore either in manual review or
    carrying language flags, never both. That is what makes the sum the UI
    does a count of files rather than an overcount.

    Counted in SQL rather than by loading rows: the caller wants a number,
    and the list endpoints that return the rows themselves are paginated,
    so their totals are not free to reuse here.
    """
    rows = (
        db.query(QueueItem.status, func.count(QueueItem.id))
        .group_by(QueueItem.status)
        .all()
    )
    # No "or 0" fallback: count() returns 0 on an empty table, never None,
    # so the fallback was unreachable — it survived mutation as an
    # equivalent, which is what flagged it as dead.
    audio = db.query(func.count(AudioLanguageFlag.id)).scalar()
    subtitle = db.query(func.count(SubtitleLanguageFlag.id)).scalar()
    return {
        **{status: count for status, count in rows},
        "language_review": {"audio": audio, "subtitle": subtitle},
    }


# ── Acknowledged undefined-audio thresholds ──────────────────────────────────
#
# DECLARED BEFORE /{item_id}, and it has to be. FastAPI matches in
# declaration order, so with these below it a GET of /api/queue/acknowledged
# is caught by /{item_id}, which tries to parse "acknowledged" as an int and
# returns 422. /active, /manual-review and /stats sit above it for the same
# reason. Note that the OpenAPI schema lists both routes either way — the
# path is registered, just unreachable — so only a real request finds this.
#
# Approving a threshold review sets und_audio_threshold_acknowledged, and
# nothing ever set it back: one write site, True, and no route to False.
# The file is exempt from the gate for good.
#
# That matters more than a stray flag usually would. The Approve button used
# to say it would "process the file now, keeping every audio track", which
# was wrong whenever nothing else needed doing — the file was marked Skipped
# instead. So an unknown number of these acknowledgements were given on a
# false description, and until now there was no way to see them, let alone
# take one back.


class ClearAcknowledgedRequest(BaseModel):
    # Files, not queue items. The acknowledgement lives on MediaFile and
    # outlives every QueueItem the file has ever had, which is the whole
    # reason it is invisible.
    file_ids: list[int]


@router.get("/acknowledged")
def list_acknowledged(
    limit:  int = Query(default=50, ge=1, le=10000),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),
):
    """
    Files whose undefined audio tracks the user has confirmed are correct
    as they are: Confirm correct on a threshold flag in Audio Language
    Review, or a past Approve on a review the threshold raised while it
    still held files.

    Bounded like every other list in this codebase. There is no ceiling on
    how many files can carry this — it accumulates for the life of the
    library and nothing ever removes one — so an unbounded version would be
    the same mistake list_manual_review already carries.
    """
    base = db.query(MediaFile).filter(
        MediaFile.und_audio_threshold_acknowledged.is_(True)
    )
    total = base.count()
    rows = (
        base.order_by(MediaFile.path)
        .limit(limit)
        .offset(offset)
        .all()
    )
    return {
        "total": total,
        "files": [
            {
                "id":       m.id,
                "path":     m.path,
                "filename": m.filename,
                "status":   m.status,
            }
            for m in rows
        ],
    }


@router.post("/acknowledged/clear")
def clear_acknowledged(body: ClearAcknowledgedRequest,
                       db: Session = Depends(get_db)):
    """
    Take back an acknowledgement, so the file's undefined audio tracks are
    flagged again.

    Two steps, and the second is the one that is easy to forget. Clearing
    the column alone changes nothing a user can see: the file's bytes are
    untouched, so the next delta scan compares size and mtime, finds them
    identical, and never calls analyze_file. The acknowledgement would be
    gone from the database and the tracks would still never be flagged. So
    the scan stamp is invalidated too — the same pairing cancel_item needs,
    via the same helper.

    Only the threshold's answer is taken back. A language mismatch the user
    confirmed on the same file answers to its own switch and is untouched,
    which is why the two are kept apart.

    The file returns on the next scan rather than immediately. That matches
    Skip, whose wording users already know, and avoids the queue-item
    surgery apply_language needs to reprocess a file on the spot.

    Unknown ids are counted as misses rather than raising: this is driven by
    a multi-select whose list may have moved under the user, and failing the
    whole call because one row went stale would be worse than reporting it.
    """
    cleared = 0
    missing: list[int] = []
    for file_id in body.file_ids:
        media = db.get(MediaFile, file_id)
        if not media:
            missing.append(file_id)
            continue
        # Counted only when there was something to clear, so the number
        # describes work done rather than ids received.
        if media.und_audio_threshold_acknowledged:
            media.und_audio_threshold_acknowledged = False
            _force_rescan(media)
            cleared += 1
    db.commit()
    logger.info("Cleared %d undefined-audio acknowledgement(s)", cleared)
    return {"cleared": cleared, "missing": missing}


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
    touch it.

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
        # completed_at stamped for the same reason as cancel_item — see
        # its comment (NULLs sort last in the completed_at-DESC history).
        .update({"status": "cancelled", "completed_at": utcnow()})
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
            _force_rescan(item.media_file)
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
    clear_pending were missed.

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

    _cancel_item_row(item)
    db.commit()
    return {"success": True}


@router.post("/retry-all")
def retry_all_failed(db: Session = Depends(get_db)):
    """
    Re-queue every failed item in one call.

    Each item is re-probed with force_probe=True — the same behaviour as
    single-item retry — so the retry picks up any settings changes, code
    fixes, or on-disk changes since the original failure.

    Items whose source file no longer exists on disk are silently skipped
    rather than failing the whole operation.

    Failed only, not cancelled, although the Failed tab lists both. A
    cancelled row is something the user removed on purpose: Skip in Review
    and dismissing a queued item (cancel_item), Clear queue (clear_pending),
    and Abort (worker.abort_job). This endpoint used to re-queue those too,
    so one press put every skipped file back in Review and every cleared file
    back in the queue — the opposite of what was asked for each of them. A
    single cancelled row can still be retried from its detail window
    (history.retry_history_item), and all three of those functions reset the
    delta-scan sentinels, so the file is re-evaluated on the next scan either
    way.

    history_summary reports failed_only for the same reason: the Retry All
    button is gated on it, so a tab holding only cancelled rows does not
    offer a button that would do nothing.
    """
    items = (
        db.query(QueueItem)
        .filter(QueueItem.status == "failed")
        .all()
    )

    if not items:
        return {"retried": 0, "skipped": 0}

    app_cfg = get_app_settings(db)
    dry_run = _current_dry_run_mode(db)
    # One shared ScanStats across the loop instead of a throwaway per item.
    # "retried" previously counted every item _process_file did not raise on,
    # which is not the same thing as re-queued: the decision engine re-runs
    # with force_probe=True and may legitimately decide the file now needs no
    # work (skipped) or needs a human (manual_review). A settings change that
    # made 40 of 50 failures a no-op still reported "50 requeued", and the
    # Queue then showed 10. The stats object already distinguishes these — it
    # was just being discarded.
    stats = ScanStats()
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
        # though single-item retry preserved this correctly.
        sonarr_series_id = item.sonarr_series_id
        radarr_movie_id  = item.radarr_movie_id
        db.delete(item)
        db.flush()

        try:
            _process_file(
                db, file_path, app_cfg,
                force_probe      = True,
                dry_run          = dry_run,
                stats            = stats,
                sonarr_series_id = sonarr_series_id,
                radarr_movie_id  = radarr_movie_id,
            )
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

    return {
        # Only items that actually became pending work.
        "retried":       stats.queued,
        # Source file gone at the top of the loop, plus files the re-run
        # decided need no work — both are "not re-queued", and the caller
        # renders them as one "skipped" figure.
        "skipped":       skipped + stats.skipped,
        # Reported separately because these are not finished: they are waiting
        # on the user, and folding them into either count above would hide
        # that. Absent before, so a retry that moved items to Review looked
        # like it had done nothing to them.
        "manual_review": stats.manual_review,
        "errors":        errors,
    }


class ReviewAnswerRefused(Exception):
    """
    An answer that cannot be carried out, named for the caller to report.

    One file's problem. The single-item endpoint turns it into a 400; the
    batch endpoint records it against that file and goes on with the rest,
    which is why it is not an HTTPException itself.
    """


def _refuse_impossible_answers(db: Session, item: QueueItem, media: MediaFile,
                               answers: dict[int, str]) -> None:
    """
    Refuse an answer that would be accepted and then not carried out.

    Its own function because the preview runs it without writing anything:
    a card can say an answer cannot be done while the user is still choosing,
    rather than at Apply. Recording an answer and checking one are the same
    rules, so they are the same code.
    """
    for stream_index, choice in answers.items():
        if choice not in ("keep", "remove", "extract"):
            raise ReviewAnswerRefused(
                f"Invalid choice for stream {stream_index}: {choice!r} "
                f"(expected 'keep', 'remove' or 'extract')")
        if choice != "extract":
            continue
        if item.review_reason == "subtitle_encoding":
            raise ReviewAnswerRefused(
                f"Cannot extract stream {stream_index}: extracting it is what "
                f"failed. Choose keep or remove.")
        track = (
            db.query(Track)
            .filter(Track.file_id == media.id,
                    Track.stream_index == stream_index,
                    Track.track_type == "subtitle")
            .first()
        )
        if track is None or (track.codec or "").lower() not in SRT_CONVERTIBLE_SUBS:
            raise ReviewAnswerRefused(
                f"Cannot extract stream {stream_index}: it is not a text "
                f"subtitle track that can be converted to SRT.")


def _answer_subtitle_review(db: Session, item: QueueItem, media: MediaFile,
                            answers: dict[int, str]) -> None:
    """
    Record a subtitle review's answers on the file and re-decide it.

    The one path from "the user chose" to "the item moved", shared by the
    single-item endpoint and the batch one so the two cannot drift: the
    validation below is what stops an answer being accepted and then not
    carried out, and it has to hold whichever door the answer came through.

    Does NOT commit. The batch endpoint commits per file, so one refusal
    cannot roll back the files already applied.
    """
    _refuse_impossible_answers(db, item, media, answers)

    # ── Merge new answers into the persisted set ────────────────────────────
    # Written to media BEFORE the analysis below — _build_analysis_inputs
    # loads subtitle_overrides back off the media object, so it sees the
    # merged set including these new choices (in-session attribute read,
    # not a DB re-read).
    #
    # Stored against what each track is, not the stream it arrived as: the
    # request names a position in the file as probed, and positions move
    # (scanner.descriptors_by_stream). A stream the file does not have gets
    # no descriptor, and an answer that cannot be recorded is refused rather
    # than dropped quietly.
    descriptors = descriptors_by_stream(
        [_track_to_dict(t) for t in
         db.query(Track).filter(Track.file_id == media.id).all()]
    )
    missing = sorted(set(answers) - set(descriptors))
    if missing:
        raise ReviewAnswerRefused(
            f"No such stream on this file: {', '.join(str(i) for i in missing)}. "
            f"It may have been re-probed since the page loaded.")

    existing_overrides = _load_track_answers(media, "subtitle_overrides")
    existing_overrides.update(
        {descriptors[si]: choice for si, choice in answers.items()})
    media.subtitle_overrides = json.dumps(existing_overrides)

    # ── Re-run the decision engine with the updated overrides ───────────────
    app_cfg = get_app_settings(db)
    file_info, tracks, analysis_kwargs = _build_analysis_inputs(db, media)
    decision = analyze_file(file_info, tracks, app_cfg, **analysis_kwargs)

    _apply_decision_to_item(db, item, media, decision)


def _cancel_item_row(item: QueueItem) -> None:
    """
    The transition Skip and Cancel make on one row. Does NOT commit.

    A helper because the batch review endpoint skips files through it too,
    and the three parts have to travel together: the status the Failed tab
    reads, the completed_at without which the row sinks to the bottom of
    that tab, the scan-stamp reset without which a delta scan never looks
    at the file again, and the file's own status. See cancel_item for the
    full story of each.
    """
    item.status = "cancelled"
    item.completed_at = utcnow()
    if item.media_file:
        _force_rescan(item.media_file)
        item.media_file.status = "skipped"


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
        # Which gate raised the review, so the UI can phrase it and offer
        # the matching bulk action. Null on items not in review, and on
        # rows written before the column existed — the UI reads a null
        # alongside a flagged payload the same way the bulk resolver does.
        "review_reason":     item.review_reason,
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
