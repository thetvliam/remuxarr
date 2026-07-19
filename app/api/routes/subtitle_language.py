"""
Subtitle Language Review API
=============================
GET  /api/subtitle-language-review/         — paginated, searchable list of flagged files
POST /api/subtitle-language-review/apply    — set a language on selected files and reprocess
POST /api/subtitle-language-review/ignore   — confirm selected files are fine left undefined

Subtitle counterpart to /api/audio-language-review — same mechanics
throughout, mirrored deliberately rather than sharing an implementation,
since the two operate on genuinely independent MediaFile columns and flag
tables (a file can have an audio flag, a subtitle flag, both, or neither).

One real difference worth being explicit about: every row here originates
from an UNDEFINED ("und") tag that fix_undefined_language's "always_ask"
mode flagged for a human decision — there's no "defined but wrong
subtitle language" detection the way Audio Language Review has for audio
(see subtitle_language_mismatch's docstring on ProcessingDecision for why).
The resolution flow is identical either way though: pick the correct
language and reprocess, or confirm it's fine to leave as-is.
"""
import json
import logging
import os

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.core.scanner import ScanStats, _process_file, _load_subtitle_language_overrides
from app.database.models import SubtitleLanguageFlag, MediaFile, QueueItem
from app.database.session import get_app_settings, get_db

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/subtitle-language-review", tags=["subtitle-language-review"])


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
    Paginated, searchable list of files with a flagged subtitle language
    needing a decision. Search matches filename, case-insensitive
    substring — e.g. "king of the hill" returns every flagged episode
    across every season, ready to select-all and apply in one action.
    """
    query = (
        db.query(SubtitleLanguageFlag)
        .join(SubtitleLanguageFlag.media_file)
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
    Set target_language on the flagged subtitle track for every file in
    file_ids, persist it as an override, and reprocess each file
    immediately so the correction actually gets written.

    Mirrors /api/audio-language-review/apply exactly, including the fix
    for its status-filtered active-item delete — see that endpoint's
    docstring and its delete's own comment for the full rationale on
    both why the override commit is separate from the reprocess
    attempt, and why an unfiltered "any status" QueueItem lookup here
    was a real, silent bug rather than a harmless simplification.
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
            db.query(SubtitleLanguageFlag)
            .filter(SubtitleLanguageFlag.file_id == file_id)
            .first()
        )
        if not flag:
            results["errors"].append({"file_id": file_id, "error": "No flag found for this file"})
            continue

        existing_overrides = _load_subtitle_language_overrides(media)
        existing_overrides[flag.stream_index] = lang
        media.subtitle_language_overrides = json.dumps(
            {str(k): v for k, v in existing_overrides.items()}
        )
        media.subtitle_language_ignored = False
        db.commit()

        # Skip files with a live running job, then clear any WAITING
        # QueueItem(s) — see audio_language.py's apply_language for the
        # full rationale on both: why deleting a "processing" row out
        # from under a running FFmpeg is a real concurrent-write hazard
        # (an earlier version of this code did exactly that), and why
        # the remaining delete must be status-filtered rather than an
        # unfiltered .first().
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

        # Same reasoning as audio_language.py's apply_language for capturing
        # arr IDs before deleting — see that endpoint for the full rationale.
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
            logger.exception("Failed to apply subtitle language to %s", media.path)
            results["errors"].append({"file_id": file_id, "error": str(exc)})
            db.rollback()

    db.commit()
    return results


@router.post("/ignore")
def ignore_flags(body: IgnoreRequest, db: Session = Depends(get_db)):
    """
    Confirm it's fine to leave the current subtitle track undefined for
    every file in file_ids. No reprocessing happens: nothing about the
    file needs to change, this just permanently stops it being flagged
    again on future scans.
    """
    count = 0
    for file_id in body.file_ids:
        media = db.get(MediaFile, file_id)
        if not media:
            continue
        media.subtitle_language_ignored = True

        flag = (
            db.query(SubtitleLanguageFlag)
            .filter(SubtitleLanguageFlag.file_id == file_id)
            .first()
        )
        if flag:
            db.delete(flag)
        count += 1

    db.commit()
    return {"ignored": count}
