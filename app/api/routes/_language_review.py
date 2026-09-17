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
says so: a subtitle flag always originates from an UNDEFINED tag that
fix_undefined_language's "always_ask" mode declined to guess, while an audio
flag has two causes — a DEFINED but non-preferred language, or an undefined
one that the same always_ask mode flagged. (This paragraph used to claim an
audio flag was always the defined case; that stopped being true when
always_ask started flagging audio, and AudioLanguageFlag.detected_language
can hold "und".) That difference lives in LanguageReviewKind, including the
endpoint descriptions, so the OpenAPI schema still explains each one on its
own terms rather than generically.
"""
import json
import logging
import os
import re
from collections.abc import Callable
from dataclasses import dataclass
from types import SimpleNamespace

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import func, or_
from sqlalchemy.orm import Session

from app.core.scanner import ScanStats, _process_file
from app.core.decision import ISO_639_2_TO_1
from app.database.models import MediaFile, QueueItem, RevertPoint
from app.database.session import get_app_settings, get_db

logger = logging.getLogger(__name__)


class ApplyRequest(BaseModel):
    # Flags, not files. A file can have several undefined subtitle tracks
    # and each needs its own answer, so a file id can no longer say which
    # one is meant. Audio flags are per track as well.
    flag_ids: list[int]
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
    # (origin, MediaFile boolean column) pairs, for a flag table whose rows
    # say where they came from. Empty means every flag answers to
    # ignored_attr. A tuple rather than a dict so the frozen dataclass stays
    # hashable. See _switches_for.
    origin_switches: tuple[tuple[str, str], ...] = ()


def _switches_for(kind: LanguageReviewKind, flags) -> set[str]:
    """
    The MediaFile columns that record an answer to these flags.

    Audio flags record where they came from (AudioLanguageFlag.origin), and
    each origin has its own switch: a language mismatch answers to
    audio_language_ignored, the undefined-audio threshold to
    und_audio_threshold_acknowledged. They are kept apart so that taking
    back one kind of answer — Clear acknowledged, for the threshold — cannot
    undo the other on the same file, such as a Japanese track confirmed as
    correctly Japanese.

    With no flags left (the page was open across a rescan that cleared
    them), the answer falls back to ignored_attr, as it did before origins
    existed: there is nothing to say which question the user meant, and the
    file they selected is still honoured.

    An origin missing from the kind's pairs raises KeyError rather than
    being guessed at. Only code writes the column, so a new origin arriving
    without its switch is a bug for the tests to find.
    """
    if not kind.origin_switches or not flags:
        return {kind.ignored_attr}
    by_origin = dict(kind.origin_switches)
    return {by_origin[flag.origin] for flag in flags}


def _rename_extracted_subtitle(flag, lang: str, db=None) -> str | None:
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
    # named Show.und.forced.srt has to become Show.en.forced.srt and keep
    # being the forced one.
    #
    # Written as the 2-letter ISO 639-1 code, via the same map
    # _build_srt_path uses, NOT the 3-letter code the user picked. Both
    # paths that can fix an extracted subtitle's language have to agree on
    # the filename or the same track ends up named two different ways
    # depending on which one got there — and this function exists solely to
    # make Plex read the correction, so writing the form the other path
    # deliberately avoids defeats its own purpose. "und" is unmapped and
    # passes through, which is how it got into the name to begin with.
    lang_tag = ISO_639_2_TO_1.get(lang, lang)

    parts = name.split(".")
    # The extracted name may carry either form of the detected code, so try
    # the raw one and its mapping before giving up. In practice subtitle
    # flags are always "und" (unmapped, identical both ways); an audio-side
    # caller with a real code would otherwise silently fail to find it.
    detected = flag.detected_language or "und"
    index = None
    for needle in (detected, ISO_639_2_TO_1.get(detected, detected)):
        try:
            index = len(parts) - 1 - parts[::-1].index(needle)
            break
        except ValueError:
            continue
    if index is None:
        logger.info(
            "Not renaming %s: its name does not carry the language it was "
            "extracted under", old_path,
        )
        return None

    parts[index] = lang_tag
    new_path = os.path.join(directory, ".".join(parts))
    if new_path == old_path:
        return None

    if os.path.exists(new_path):
        # Something is already there. Overwriting it would destroy a
        # subtitle nobody asked to replace — quite possibly one the user
        # downloaded for exactly this language.
        logger.warning(
            "Not renaming %s: %s already exists", old_path, new_path,
        )
        return None

    try:
        os.rename(old_path, new_path)
    except OSError as exc:
        logger.warning(
            "Could not rename %s to %s: %s", old_path, new_path, exc,
        )
        return None

    # Kept even though the only production caller deletes this row moments
    # later, so the value is never read back. It is not there for that
    # caller: it keeps the in-memory object agreeing with the filesystem,
    # so a future caller that renames WITHOUT answering the flag does not
    # end up holding a row that points at a path no longer on disk. The
    # cost is one assignment; the cost of it being absent is a bug that
    # only appears once someone adds that caller.
    flag.extracted_path = new_path
    logger.info("Renamed %s → %s", old_path, new_path)

    if db is not None:
        _repoint_revert_points(db, flag.file_id, old_path, new_path)
    return new_path


def _repoint_revert_points(db, file_id: int, old_path: str, new_path: str) -> None:
    """
    Follow the rename in any revert point that recorded this file.

    A revert removes the subtitle files its job created, matching them by
    the path recorded at capture. Rename one without telling the revert
    point and the match fails, so reverting re-embeds the subtitle and
    leaves the sidecar behind — the user ends up with it twice, which is
    the exact duplication that cleanup exists to prevent.

    Only the path is updated. os.rename preserves size and mtime, so the
    fingerprint recorded alongside it is still accurate and still protects
    a file the user has since edited.

    Never raises. The rename has already happened and the language choice
    is already committed; a manifest that cannot be updated means one
    stale sidecar after a revert, which is untidy rather than harmful.
    """
    try:
        # Filtered by file_id for cost, not for safety: the path match
        # below is what prevents another file's records being touched, and
        # extracted subtitle paths are derived from the media filename so
        # two files cannot record the same one. Without the filter this
        # would load and JSON-parse every revert point in the bin on every
        # language correction.
        points = (
            db.query(RevertPoint)
            .filter(RevertPoint.file_id == file_id)
            .all()
        )
        for point in points:
            try:
                manifest = json.loads(point.manifest)
            except (TypeError, ValueError):
                continue

            entries = manifest.get("created_files") or []
            hits = [e for e in entries if e.get("path") == old_path]
            if not hits:
                continue
            for entry in hits:
                entry["path"] = new_path
            point.manifest = json.dumps(manifest)
            logger.info(
                "Revert point %d now tracks the renamed subtitle", point.id,
            )
    except Exception:
        logger.exception(
            "Could not update revert points for the renamed subtitle %s",
            new_path,
        )


def build_language_review_router(kind: LanguageReviewKind) -> APIRouter:
    """Construct the review router for one flag type."""
    router = APIRouter(prefix=kind.prefix, tags=[kind.tag])
    Flag = kind.flag_model

    @router.get("/", description=kind.list_description)
    def list_flags(
        search:   str = "",
        language: str = "",
        # Bounded, as history.py, forge.py and logs.py all bound theirs.
        # Unbounded, limit=-1 reached SQLAlchemy's .limit(), which reads a
        # negative as no limit at all, so one request returned every flagged
        # row in the library. The ceiling matters as much as the floor: the
        # page size is the only thing between one request and the whole table.
        limit:    int = Query(default=50, ge=1, le=10000),
        offset:   int = Query(default=0, ge=0),
        db: Session = Depends(get_db),
    ):
        base = (
            db.query(Flag)
            .join(Flag.media_file)
        )

        # Counted before anything narrows it, so it is the size of the
        # backlog rather than the size of the current match.
        #
        # `total` below is the filtered figure and has to be: pagination
        # depends on it, and the "n of m" on the select-all row is built from
        # it. The section heading's badge was reading the same number, so
        # typing a show name took the badge from 57 to 2 and the overall
        # figure was then nowhere on the page — the nav tab's count is
        # manual-review QUEUE items, which is a different thing.
        #
        # The facets cannot stand in for this. They honour `search` on
        # purpose, so the dropdown only offers tags the current search has,
        # and summing them returns the filtered total again.
        total_unfiltered = base.count()

        # icontains(autoescape=True) rather than an ilike f-string
        # pattern — see history.py's list_history for the full reasoning.
        # It matters more here: the facet counts below are built from
        # this same query, and "select all" acts on what the server
        # returned, so an unescaped `_` pulls in files the user never
        # searched for and the next click tags every one of them.
        if search.strip():
            base = base.filter(
                MediaFile.filename.icontains(search.strip(), autoescape=True)
            )

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
        wanted = language.strip().lower()
        if wanted:
            if wanted == "und":
                # The facets report a null detected_language as "und", so the
                # dropdown offers "und (1)" for it. In SQL a null is not equal
                # to anything, so comparing the column to the string returned
                # nothing while the count beside the option still said 1 —
                # the endpoint advertising a choice it would not honour.
                #
                # Latent, and expected to stay that way: the scanner writes
                # "und" literally for subtitles and `t["language"] or "und"`
                # for audio, so production has no null rows. Closed because
                # the facet and the filter disagreeing about the same row is
                # the kind of thing that only surfaces once something else
                # starts writing nulls.
                query = query.filter(
                    or_(Flag.detected_language == "und",
                        Flag.detected_language.is_(None))
                )
            else:
                query = query.filter(Flag.detected_language == wanted)

        total = query.count()
        flags = (
            query
            # By file first, then by track, so a file's rows arrive
            # together and in a stable order — the page groups them, and
            # they would otherwise be split or reshuffled between loads.
            .order_by(MediaFile.filename.asc(), Flag.stream_index.asc())
            .offset(offset)
            .limit(limit)
            .all()
        )

        items = []
        for flag in flags:
            media = flag.media_file
            if not media:
                # Not dead code, though it reads that way: `base` inner-joins
                # media_file, so an orphan flag that already existed is
                # excluded from total, items and languages alike.
                #
                # What this catches is narrower. `total` is one query, the page
                # is a second, and media_file is NOT eager-loaded by either —
                # it lazy-loads here, on attribute access, a third query. A
                # delete landing in that window leaves the row counted in
                # total and resolving to None right here, and without the
                # guard the next line raises AttributeError on NoneType.
                # Demonstrated: total says 1, the page returns 0.
                #
                # usePaginatedFetch's `newItems.length > 0` guard cites this
                # case by name, and is right to: it is exactly how a page
                # comes back empty while total still counts the row.
                #
                # Deliberately untested, which is why this comment is long.
                # The window needs a delete to land between two queries inside
                # one request, and nothing in the suite can hold it open. A
                # mutant that deletes this guard passes every test. A test was
                # written asserting media_file stays unloaded, and dropped: it
                # built its own query, so making THIS one eager-load left it
                # green. It looked like cover for the premise and was not.
                continue
            items.append({
                "id":                flag.id,
                "file_id":           flag.file_id,
                "filename":          media.filename,
                "path":              media.path,
                "stream_index":      flag.stream_index,
                "detected_language": flag.detected_language,
                # Present when this track was extracted. The UI shows it
                # because it is the thing the answer will rename, and
                # "which of these three is the forced one" is otherwise
                # unanswerable from a stream index.
                "extracted_path":    getattr(flag, "extracted_path", None),
                # Audio only: where the flag came from, "mismatch" or
                # "threshold" (AudioLanguageFlag.origin). Subtitle flags have
                # no such column and report None.
                "origin":            getattr(flag, "origin", None),
            })

        return {"total": total, "total_unfiltered": total_unfiltered,
                "items": items, "languages": languages}

    @router.post("/apply", description=kind.apply_description)
    def apply_language(body: ApplyRequest, db: Session = Depends(get_db)):
        lang = body.target_language.strip().lower()
        if not lang:
            raise HTTPException(400, "target_language cannot be empty")
        # The field behind this is a free-text box with placeholder "eng" and
        # no pattern, so whatever was typed went straight through: persisted
        # as the override, interpolated into the extracted subtitle's
        # filename, and handed to FFmpeg as -metadata:s:a:N language=... .
        # That filename is what Plex reads, so "english" quietly mislabels
        # the track it was meant to fix.
        #
        # A shape check rather than a whitelist. ISO_639_2_TO_1 is a 49-entry
        # convenience map for the common codes, not the standard — checking
        # against it would refuse Welsh. Two or three ASCII letters covers
        # every valid ISO 639-1 and 639-2 code and excludes everything that
        # has no business in a filename.
        if not re.fullmatch(r"[a-z]{2,3}", lang):
            raise HTTPException(
                400,
                "target_language must be a 2- or 3-letter language code, "
                f"not {body.target_language!r}",
            )

        app_cfg = get_app_settings(db)
        dry_run = app_cfg.get("dry_run_mode", False)
        # Reported back so the caller can say so. Everything below still
        # happens under dry run except the two steps that touch the disk or
        # discard the question, which leaves an apply that looks from the
        # response alone exactly like a real one.
        results = {"applied": 0, "errors": [], "dry_run": dry_run}

        # Group the flags by file before doing anything.
        #
        # The request is a list of flags, but almost everything below is
        # per-FILE work: one override blob, one queue-item cleanup, one
        # reprocess. Iterating flags directly did all of it once per flag,
        # so three undefined subtitles in one file meant three ffprobes and
        # three queue-item churns to apply one file's worth of correction —
        # each iteration deleting the pending item the previous one had just
        # created. It also made "applied" count flags while the UI's toast
        # says files, reporting "3 files" for one.
        #
        # Insertion order is preserved so errors still surface in the order
        # the user's selection implied.
        by_file: dict[int, list] = {}
        for flag_id in body.flag_ids:
            flag = db.get(Flag, flag_id)
            if not flag:
                # Already answered, or answered by another client. Not an
                # error worth surfacing: the row is gone because the
                # question it asked has been settled.
                continue
            by_file.setdefault(flag.file_id, []).append(flag)

        for file_id, flags in by_file.items():
            media = db.get(MediaFile, file_id)
            if not media:
                results["errors"].append({"file_id": file_id, "error": "File not found"})
                continue
            if not os.path.exists(media.path):
                results["errors"].append({"file_id": file_id, "error": "File no longer exists on disk"})
                continue

            # Persist the override and commit it on its own, separately from
            # the reprocess attempt below. The user's language CHOICE should
            # stick even if this specific attempt to act on it fails for some
            # unrelated reason (a transient probe error, a genuinely broken
            # file, etc.) — a later retry, or the next scheduled scan, will
            # then pick the override up automatically without the user
            # needing to re-select it.
            existing_overrides = kind.load_overrides(media)
            for flag in flags:
                existing_overrides[flag.stream_index] = lang
            setattr(media, kind.overrides_attr, json.dumps(
                {str(k): v for k, v in existing_overrides.items()}
            ))
            # A previous Ignore shouldn't stick once the user has explicitly
            # chosen a language — that's a more specific, more recent decision.
            # Only the switch for the questions answered here: choosing a
            # language for a mismatch says nothing about the threshold.
            for attr in _switches_for(kind, flags):
                setattr(media, attr, False)
            db.commit()

            # Both of the steps below are skipped under dry run, and they
            # have to be skipped together.
            #
            # The rename is the obvious half: Dry Run Mode's own description
            # is "do NOT execute FFmpeg or modify any files", and it ships
            # ON so a new install's first scan is a complete preview. dry_run
            # used to reach _process_file and nothing else, so answering a
            # language renamed a sidecar on disk in the one mode that
            # promises not to.
            #
            # Deleting the rows is the half that is easy to leave behind, and
            # skipping only the rename is worse than doing neither. The row
            # is what keeps the question being asked, and by review time the
            # track has been extracted OUT of the mux — a rescan reports no
            # mismatch, so nothing recreates it. The sidecar would keep "und"
            # in its name permanently with no remaining way to correct it,
            # which is a preview quietly consuming the thing it previewed.
            #
            # The override a few lines up is deliberately NOT gated. It is a
            # decision rather than a file, it is already committed on its
            # own, and it is what makes the correction land once dry run is
            # turned off. Nor is the _process_file call below: populating the
            # queue with planned actions is what dry run is FOR.
            if not dry_run:
                # Rename the extracted sidecars, if these tracks have any.
                #
                # The override alone cannot fix it. An extracted subtitle has
                # been taken OUT of the mux, so the reprocess below has no
                # track left to re-extract under the corrected name — the
                # file keeps "und" in its name permanently, which is what
                # Plex reads. Renaming here is the only point at which the
                # correction can reach it.
                for flag in flags:
                    # The return value is deliberately discarded. It used to
                    # be collected into results["renamed"], which nothing has
                    # ever read — not the two callers, not the frontend, not
                    # a test, and no endpoint description mentions it. The
                    # helper logs every outcome itself: the rename, the
                    # collision it declined, and the OSError it swallowed.
                    _rename_extracted_subtitle(flag, lang, db)

                # The questions have been answered, so stop asking them. Left
                # in place the rows would survive every later scan — rows
                # whose sidecar still exists are deliberately kept now,
                # because the track itself is gone from the file and only the
                # filename is still correctable.
                for flag in flags:
                    db.delete(flag)
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
                # _process_file handles its own failures rather than raising:
                # a file it cannot stat or cannot probe is logged, recorded in
                # the ScanStats it was handed, and returned from. Nothing
                # reaches the except below, so incrementing unconditionally
                # reported a clean {"applied": 1, "errors": []} for a file
                # where no queue item was created and nothing happened. The
                # ScanStats was constructed, passed in, and never read — this
                # is the signal that was being discarded.
                #
                # "applied" feeds a toast that says "on N files", so it has to
                # mean files that were really re-evaluated.
                #
                # Exact rather than approximate: both of _process_file's error
                # branches return immediately, and its "unchanged" early
                # returns sit inside `if existing and not force_probe`, which
                # this caller never reaches. So on return the file has either
                # errored or genuinely landed on queued, manual_review or
                # skipped.
                if stats.errors:
                    # Deliberately generic. _process_file puts the real reason
                    # in the log and returns nothing, so the alternative is
                    # changing a contract the whole scan path shares.
                    results["errors"].append({
                        "file_id": file_id,
                        "error": "Could not re-read the file — see the log for details",
                    })
                else:
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
        dry_run = get_app_settings(db).get("dry_run_mode", False)
        if dry_run:
            # Dry Run Mode covers this too, which is not obvious: ignoring
            # writes no files and runs no FFmpeg, so "do NOT modify any files"
            # does not reach it on the wording alone.
            #
            # It reaches it because an ignore cannot be undone. The ignored
            # column is written True here and False in exactly one other
            # place, inside apply_language — and ignoring deletes every flag
            # row for the file, while the scanner refuses to create new ones
            # for a file already marked. So the one route back is closed by
            # the same action that opens the door. No endpoint, no setting
            # and no control clears it. (The threshold's switch does have a
            # way back, Clear acknowledged, but this returns before writing
            # either.)
            #
            # A one-way door has no business being reachable in the mode that
            # ships ON and promises nothing will change.
            #
            # Reports 0 rather than a would-be count, so "ignored" keeps one
            # meaning in both modes: files this call marked. dry_run is what
            # explains the zero. Apply differs on purpose — it still creates
            # its queue items, because previewing them is what dry run is FOR,
            # so its count still describes work that happened.
            return {"ignored": 0, "dry_run": True}

        count = 0
        for file_id in body.file_ids:
            media = db.get(MediaFile, file_id)
            if not media:
                continue
            # Every flag for this file, not the first one — see below. Read
            # before marking, because the rows are what say which switch
            # each answer belongs to.
            flags = (
                db.query(Flag)
                .filter(Flag.file_id == file_id)
                .all()
            )
            for attr in _switches_for(kind, flags):
                setattr(media, attr, True)
            # Counted here, with the write, because this is the thing the
            # endpoint does. Marking is unconditional and deliberate: the
            # column is idempotent and is what stops a future scan flagging
            # the file, so a file the user selected is honoured whether or not
            # it still has rows to clear.
            #
            # This reverses an earlier decision, so the reasoning that was
            # here is worth stating rather than deleting. The count used to
            # sit inside the `if flags:` below, on the view that a file with
            # no rows "was not ignored" and counting it reported work that had
            # not happened. But the write above happens regardless, so the
            # endpoint was marking three files and reporting one — a page left
            # open across a rescan answered "Ignoring 0 files" while
            # permanently suppressing every file in the list. Zero is the
            # reading a user acts on: click again, or assume it missed. The
            # two halves disagreed, and the write is the defensible one.
            #
            # Counts FILES, matching the file_ids this endpoint is given.
            # Counting rows would report "ignored 3" for one ignored file and
            # put the caller back in a units mismatch.
            count += 1

            # Every flag for this file, not the first one: all of them are
            # deleted.
            #
            # SubtitleLanguageFlag is UNIQUE(file_id, stream_index), so a
            # file with three undefined subtitles has three rows. Taking
            # .first() cleared one and left the file sitting on the review
            # page it had just been ignored from — and worse, applying a
            # language to one of the survivors sets ignored back to False,
            # silently undoing the ignore. The shared router was written
            # against the audio table, which was one row per file then, and
            # where .first() happened to be complete.
            for flag in flags:
                db.delete(flag)

        db.commit()
        return {"ignored": count, "dry_run": False}

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
