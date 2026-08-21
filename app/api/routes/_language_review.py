"""
Language Review API — shared implementation
===========================================
Builds the router served at both /api/audio-language-review and
/api/subtitle-language-review.

WHY THIS EXISTS
---------------
audio_language.py and subtitle_language.py were separate modules whose logic
was, measured by AST with docstrings stripped, 100 lines each differing by a
single log message. Everything else that differed was mechanical: which flag
table to query, which MediaFile column to write, and the route prefix.

That is not a cosmetic complaint. The comments in those files record the same
bug being found and fixed twice — the "processing" QueueItem hazard below, and
the arr-ID preservation below that — because a fix applied to one copy had no
effect on the other. The duplication was actively costing correctness.

WHAT IS STILL SEPARATE
----------------------
The two review types are genuinely different things and their documentation
says so: an audio flag always means a DEFINED but non-preferred language,
while a subtitle flag always originates from an UNDEFINED tag that
fix_undefined_language's "always_ask" mode declined to guess. That difference
lives in LanguageReviewKind, including the endpoint descriptions, so the
OpenAPI schema still explains each one on its own terms rather than
generically.
"""
import json
import logging
import os
from collections.abc import Callable
from dataclasses import dataclass
from types import SimpleNamespace

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.core.scanner import ScanStats, _process_file
from app.database.models import MediaFile, QueueItem
from app.database.session import get_app_settings, get_db

logger = logging.getLogger(__name__)


class ApplyRequest(BaseModel):
    file_ids: list[int]
    target_language: str


class IgnoreRequest(BaseModel):
    file_ids: list[int]


@dataclass(frozen=True)
class LanguageReviewKind:
    """Everything that genuinely differs between the two review types."""

    slug: str                          # "audio" | "subtitle" — used in log lines
    prefix: str                        # route prefix
    tag: str                           # OpenAPI tag
    flag_model: type                   # AudioLanguageFlag | SubtitleLanguageFlag
    load_overrides: Callable[[MediaFile], dict[int, str]]
    overrides_attr: str                # MediaFile column holding the JSON overrides
    ignored_attr: str                  # MediaFile boolean column for "confirmed correct"
    list_description: str
    apply_description: str
    ignore_description: str


def _rename_extracted_subtitle(flag, lang: str) -> str | None:
    """
    Rename a flagged track's extracted .srt to carry the chosen language.

    Returns the new path when a file was renamed, or None.

    Only ever acts on the path recorded when the file was extracted. The
    name cannot be reconstructed at this point: extraction removes the
    track from the mux, so the forced / SDH / dub suffixes that shaped the
    filename are gone from the file along with it, and guessing would
    rename someone else's subtitle.

    Never raises. The language override has already been committed and is
    the part that matters; a sidecar that will not rename is a wrong
    filename, not a lost choice, and failing the request would leave the
    user unsure whether their answer was recorded at all.
    """
    old_path = getattr(flag, "extracted_path", None)
    if not old_path or not os.path.exists(old_path):
        return None

    directory, name = os.path.split(old_path)
    # The language sits between the base name and any suffixes the
    # extraction added, so only that one component is replaced — a file
    # named Show.und.forced.srt has to become Show.eng.forced.srt and keep
    # being the forced one.
    parts = name.split(".")
    try:
        index = len(parts) - 1 - parts[::-1].index(flag.detected_language or "und")
    except ValueError:
        logging.getLogger(__name__).info(
            "Not renaming %s: its name does not carry the language it was "
            "extracted under", old_path,
        )
        return None

    parts[index] = lang
    new_path = os.path.join(directory, ".".join(parts))
    if new_path == old_path:
        return None

    if os.path.exists(new_path):
        # Something is already there. Overwriting it would destroy a
        # subtitle nobody asked to replace — quite possibly one the user
        # downloaded for exactly this language.
        logging.getLogger(__name__).warning(
            "Not renaming %s: %s already exists", old_path, new_path,
        )
        return None

    try:
        os.rename(old_path, new_path)
    except OSError as exc:
        logging.getLogger(__name__).warning(
            "Could not rename %s to %s: %s", old_path, new_path, exc,
        )
        return None

    flag.extracted_path = new_path
    logging.getLogger(__name__).info("Renamed %s → %s", old_path, new_path)
    return new_path


def build_language_review_router(kind: LanguageReviewKind) -> APIRouter:
    """Construct the review router for one flag type."""
    router = APIRouter(prefix=kind.prefix, tags=[kind.tag])
    Flag = kind.flag_model

    @router.get("/", description=kind.list_description)
    def list_flags(
        search:   str = "",
        language: str = "",
        limit:    int = 50,
        offset:   int = 0,
        db: Session = Depends(get_db),
    ):
        base = (
            db.query(Flag)
            .join(Flag.media_file)
        )
        if search.strip():
            base = base.filter(MediaFile.filename.ilike(f"%{search.strip()}%"))

        # Facet counts honour `search` but deliberately ignore `language`.
        # Faceting on the language filter itself would collapse the dropdown to
        # whichever option was selected, leaving no way to switch to another
        # without clearing first.
        language_counts = (
            base.with_entities(
                Flag.detected_language,
                func.count(Flag.id),
            )
            .group_by(Flag.detected_language)
            .order_by(func.count(Flag.id).desc())
            .all()
        )
        languages = [
            {"language": lang or "und", "count": count}
            for lang, count in language_counts
        ]

        query = base
        if language.strip():
            query = query.filter(
                Flag.detected_language == language.strip().lower()
            )

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

        return {"total": total, "items": items, "languages": languages}

    @router.post("/apply", description=kind.apply_description)
    def apply_language(body: ApplyRequest, db: Session = Depends(get_db)):
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
                db.query(Flag)
                .filter(Flag.file_id == file_id)
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
            existing_overrides = kind.load_overrides(media)
            existing_overrides[flag.stream_index] = lang
            setattr(media, kind.overrides_attr, json.dumps(
                {str(k): v for k, v in existing_overrides.items()}
            ))
            # A previous Ignore shouldn't stick once the user has explicitly
            # chosen a language — that's a more specific, more recent decision.
            setattr(media, kind.ignored_attr, False)
            db.commit()

            # Rename the extracted sidecar, if this track has one.
            #
            # The override alone cannot fix it. An extracted subtitle has
            # been taken OUT of the mux, so the reprocess below has no
            # track left to re-extract under the corrected name — the
            # file keeps "und" in its name permanently, which is what
            # Plex reads. Renaming here is the only point at which the
            # correction can reach it.
            renamed = _rename_extracted_subtitle(flag, lang)
            if renamed:
                results.setdefault("renamed", []).append(renamed)

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
                logger.exception("Failed to apply %s language to %s",
                                 kind.slug, media.path)
                results["errors"].append({"file_id": file_id, "error": str(exc)})
                # Only undoes the delete-old-item step above plus whatever
                # _process_file started before raising — the override commit
                # a few lines up already landed and is unaffected by this.
                db.rollback()

        db.commit()
        return results

    @router.post("/ignore", description=kind.ignore_description)
    def ignore_flags(body: IgnoreRequest, db: Session = Depends(get_db)):
        count = 0
        for file_id in body.file_ids:
            media = db.get(MediaFile, file_id)
            if not media:
                continue
            setattr(media, kind.ignored_attr, True)

            flag = (
                db.query(Flag)
                .filter(Flag.file_id == file_id)
                .first()
            )
            # count only when a flag was actually cleared. It previously
            # incremented for any file that merely existed, so re-submitting a
            # stale selection — a list another client had already resolved, or
            # a page left open across a rescan — reported "Ignoring 12 files"
            # having ignored none of them. The count is the only feedback this
            # action gives, so an inflated one is the whole signal being wrong.
            if flag:
                db.delete(flag)
                count += 1

        db.commit()
        return {"ignored": count}

    # Exposed so each module can re-export them under their original names.
    #
    # Before the merge these were module-level functions, and the existing
    # tests call them directly — list_flags(db=db, search=..., limit=...) —
    # rather than going through HTTP. Leaving them as closures would have
    # meant rewriting eighteen tests to accommodate a refactor, which is
    # exactly backwards: those tests are the evidence the merge preserved
    # behaviour, so they must keep running unmodified.
    router.handlers = SimpleNamespace(
        list_flags     = list_flags,
        apply_language = apply_language,
        ignore_flags   = ignore_flags,
    )
    return router
