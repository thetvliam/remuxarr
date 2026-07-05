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
import os

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.core.scanner import ScanStats, _process_file, _load_audio_language_overrides
from app.database.models import AudioLanguageFlag, MediaFile, QueueItem
from app.database.session import get_app_settings, get_db

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

    Mirrors _retry_with_reprobe's approach exactly: delete any existing
    QueueItem for the file (regardless of its current status — success,
    skipped, whatever) and re-run _process_file with force_probe=True,
    since the file's bytes haven't changed on disk and a normal (non-
    force) evaluation would otherwise just skip it without ever seeing
    the new override.
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

        # Merge into the persisted override set — same JSON-dict-with-
        # string-keys shape as subtitle_overrides.
        existing_overrides = _load_audio_language_overrides(media)
        existing_overrides[flag.stream_index] = lang
        media.audio_language_overrides = json.dumps(
            {str(k): v for k, v in existing_overrides.items()}
        )
        # A previous Ignore shouldn't stick once the user has explicitly
        # chosen a language — that's a more specific, more recent decision.
        media.audio_language_ignored = False

        # Clear any existing QueueItem so _process_file starts fresh,
        # exactly as _retry_with_reprobe does for the same reason.
        existing_item = (
            db.query(QueueItem)
            .filter(QueueItem.file_id == file_id)
            .first()
        )
        if existing_item:
            db.delete(existing_item)
        db.flush()

        stats = ScanStats()
        _process_file(
            db, media.path, app_cfg,
            force_probe=True,
            dry_run=dry_run,
            stats=stats,
        )
        results["applied"] += 1

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
