"""
Audio Language Review API
==========================
GET  /api/audio-language-review/         — paginated, searchable list of flagged files
POST /api/audio-language-review/apply    — set a language on selected files and reprocess
POST /api/audio-language-review/ignore   — confirm selected files are already correct

Distinct from the existing /api/queue/manual-review workflow: a file
flagged here is fully processed and playable the whole time — nothing is
held back waiting for a decision. The flag is purely informational,
surfaced so a human can optionally correct a wrong-but-defined audio
language tag (e.g. an English show mistagged "dut") or confirm the
existing tag is already correct (e.g. anime that's genuinely, correctly
Japanese) at their own pace.
"""
import json
import logging
import os

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.core.scanner import ScanStats, _process_file, _load_audio_language_overrides
from app.database.models import AudioLanguageFlag, MediaFile, QueueItem
from app.database.session import get_app_settings, get_db

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/audio-language-review", tags=["audio-language-review"])


class ApplyRequest(BaseModel):
    file_ids: list[int]
    target_language: str


class IgnoreRequest(BaseModel):
    file_ids: list[int]


@router.get("/")
def list_flags(
    search: str = "",
    limit:  int = 50,
    offset: int = 0,
    db: Session = Depends(get_db),
):
    """
    Paginated, searchable list of files with a flagged audio language
    mismatch. Search matches filename, case-insensitive substring — e.g.
    "king of the hill" returns every flagged episode across every season,
    ready to select-all and apply in one action.
    """
    query = (
        db.query(AudioLanguageFlag)
        .join(AudioLanguageFlag.media_file)
    )
    if search.strip():
        query = query.filter(MediaFile.filename.ilike(f"%{search.strip()}%"))

    total = query.count()
    flags = (
        query
        .order_by(MediaFile.filename.asc())
        .offset(offset)
        .limit(limit)
        .all()
    )

    items = []
    for flag in flags:
        media = flag.media_file
        if not media:
            continue
        items.append({
            "id":                flag.id,
            "file_id":           flag.file_id,
            "filename":          media.filename,
            "path":              media.path,
            "stream_index":      flag.stream_index,
            "detected_language": flag.detected_language,
        })

    return {"total": total, "items": items}


@router.post("/apply")
def apply_language(body: ApplyRequest, db: Session = Depends(get_db)):
    """
    Set target_language on the flagged track for every file in file_ids,
    persist it as an override, and reprocess each file immediately so the
    correction actually gets written.

    Deletes any existing ACTIVE QueueItem for the file (pending,
    processing, or manual_review specifically — not any status) before
    re-running _process_file with force_probe=True, since the file's
    bytes haven't changed on disk and a normal (non-force) evaluation
    would otherwise just skip it without ever seeing the new override.
    Scoped to active statuses only, deliberately — see the comment at
    the actual delete below for why an unfiltered "any status" lookup
    is a real bug, not just an equivalent simplification.
    """
    lang = body.target_language.strip().lower()
    if not lang:
        raise HTTPException(400, "target_language cannot be empty")

    app_cfg = get_app_settings(db)
    dry_run = app_cfg.get("dry_run_mode", False)
    results = {"applied": 0, "errors": []}

    for file_id in body.file_ids:
        media = db.get(MediaFile, file_id)
        if not media:
            results["errors"].append({"file_id": file_id, "error": "File not found"})
            continue
        if not os.path.exists(media.path):
            results["errors"].append({"file_id": file_id, "error": "File no longer exists on disk"})
            continue

        flag = (
            db.query(AudioLanguageFlag)
            .filter(AudioLanguageFlag.file_id == file_id)
            .first()
        )
        if not flag:
            results["errors"].append({"file_id": file_id, "error": "No flag found for this file"})
            continue

        # Persist the override and commit it on its own, separately from
        # the reprocess attempt below. The user's language CHOICE should
        # stick even if this specific attempt to act on it fails for some
        # unrelated reason (a transient probe error, a genuinely broken
        # file, etc.) — a later retry, or the next scheduled scan, will
        # then pick the override up automatically without the user
        # needing to re-select it.
        existing_overrides = _load_audio_language_overrides(media)
        existing_overrides[flag.stream_index] = lang
        media.audio_language_overrides = json.dumps(
            {str(k): v for k, v in existing_overrides.items()}
        )
        # A previous Ignore shouldn't stick once the user has explicitly
        # chosen a language — that's a more specific, more recent decision.
        media.audio_language_ignored = False
        db.commit()

        # A file whose job is CURRENTLY RUNNING must be skipped, not
        # cleared: deleting a "processing" row does nothing to the
        # worker's already-running FFmpeg process (worker.abort_job
        # exists for that, and isn't called here) — the running job
        # would finish invisibly (its progress/finish updates find no
        # row), while _process_file below immediately creates a fresh
        # pending item the worker can claim WHILE the old FFmpeg is
        # still writing. Both stage to distinct temp names but move
        # onto the SAME final path, so the stale pre-override job can
        # finish last and overwrite the corrected output. An earlier
        # version of this code deleted "processing" rows here and
        # presented that as deliberate and safe — it was neither.
        #
        # Skipping is safe because the override was already committed
        # above: the running job rewrites the file (new mtime), so the
        # next delta scan re-evaluates it and picks the override up
        # automatically.
        processing = (
            db.query(QueueItem)
            .filter(QueueItem.file_id == file_id,
                    QueueItem.status == "processing")
            .first()
        )
        if processing:
            results["errors"].append({
                "file_id": file_id,
                "error": "File is currently being processed — the language "
                         "choice is saved and will apply automatically after "
                         "the running job finishes (next scan).",
            })
            continue

        # Clear any existing WAITING QueueItem(s) so _process_file starts
        # fresh. Filtered to "pending"/"manual_review" only — a file can
        # have several historical QueueItem rows (completed/failed/etc.
        # from past scans) alongside a current active one; an unfiltered,
        # unordered .first() could return a stale terminal row instead of
        # the live one, leaving the actual active item in place.
        # _process_file's own "in_progress" check (scanner.py) would then
        # find that surviving active item and silently skip creating a
        # new one — the language override gets saved to the DB, but the
        # reprocess that's supposed to actually apply it never runs, with
        # no error shown anywhere.
        #
        # Bulk-deletes every matching row rather than just one,
        # defensively. Deliberately does NOT touch completed/failed/
        # cancelled/skipped/dry_run rows (real historical records), and
        # NOT "processing" (live job — handled above).
        # Same reasoning as retry_all_failed for capturing arr IDs before
        # deleting: without this, an active item carrying Sonarr/Radarr
        # linkage (e.g. a webhook-originated pending item) loses that
        # linkage here, and the reprocessed job never fires
        # RescanSeries/RescanMovie on success. There's genuinely at most
        # one matching row in practice (existing "don't double-queue"
        # guards elsewhere), but ordered defensively in case that's ever
        # not true.
        active_items = (
            db.query(QueueItem)
            .filter(
                QueueItem.file_id == file_id,
                QueueItem.status.in_(["pending", "manual_review"]),
            )
            .order_by(QueueItem.created_at.desc())
            .all()
        )
        sonarr_series_id = active_items[0].sonarr_series_id if active_items else None
        radarr_movie_id  = active_items[0].radarr_movie_id  if active_items else None
        for active_item in active_items:
            db.delete(active_item)
        db.flush()

        try:
            stats = ScanStats()
            _process_file(
                db, media.path, app_cfg,
                force_probe=True,
                dry_run=dry_run,
                stats=stats,
                sonarr_series_id=sonarr_series_id,
                radarr_movie_id=radarr_movie_id,
            )
            results["applied"] += 1
        except Exception as exc:
            # Without this, one bad file (e.g. the ValueError decision.py
            # raises for genuinely unknown container info) kills the whole
            # request with an unhandled 500, silently abandoning every
            # file still selected behind it — defeating the per-file error
            # collection this endpoint is otherwise built around.
            logger.exception("Failed to apply language to %s", media.path)
            results["errors"].append({"file_id": file_id, "error": str(exc)})
            # Only undoes the delete-old-item step above plus whatever
            # _process_file started before raising — the override commit
            # a few lines up already landed and is unaffected by this.
            db.rollback()

    db.commit()
    return results


@router.post("/ignore")
def ignore_flags(body: IgnoreRequest, db: Session = Depends(get_db)):
    """
    Confirm the current audio language is correct for every file in
    file_ids, despite not matching keep_audio_languages — e.g. anime
    that's genuinely, correctly Japanese. No reprocessing happens: nothing
    about the file needs to change, this just permanently stops it being
    flagged again on future scans.
    """
    count = 0
    for file_id in body.file_ids:
        media = db.get(MediaFile, file_id)
        if not media:
            continue
        media.audio_language_ignored = True

        flag = (
            db.query(AudioLanguageFlag)
            .filter(AudioLanguageFlag.file_id == file_id)
            .first()
        )
        if flag:
            db.delete(flag)
        count += 1

    db.commit()
    return {"ignored": count}
