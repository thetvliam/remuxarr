"""
Executing a revert — putting a file back the way it was.

Everything destructive about this feature lives here. Capture writes a
file to a volume nobody else touches; this overwrites the user's media.
The shape of the module follows from that:

  • Refuse loudly rather than restore approximately. Every precondition
    is checked before FFmpeg is started, and a failed check leaves the
    file exactly as it was.
  • Use the same staged write as everything else, so a crash mid-restore
    cannot leave a truncated file where a working one was.
  • Do the database work only after the file on disk is correct, and
    never let a bookkeeping failure turn a successful restore into a
    reported failure — the bytes are right, and a stale row is fixed by
    the next scan.

The sentinel check
------------------
processed_size/processed_mtime record the file as the job left it. If
they no longer match, something else has written to the file since —
Sonarr upgrading the episode is the obvious one — and the sidecar
describes tracks belonging to a different release. Muxing them in would
produce a file that plays and is quietly wrong. So a mismatch refuses,
and says which of the two changed.

This is deliberately not MediaFile.size/mtime, which several routes reset
to the -1/-1.0 dismissal sentinels and so cannot be trusted here.

Revert goes all the way back
---------------------------
A file has at most one revert point, describing the PRISTINE original,
extended by every job that touches it. So a revert restores the file as
it was before Remuxarr ever ran, not merely as it was before the most
recent job — and there is nothing left to revert afterwards, which is why
the point is consumed on success.

The delete below is still written as "every point for this file" rather
than "the one we used". Under the current model those are the same thing;
written the narrow way, a stray second row would survive with a sidecar
nothing could reach and a fingerprint that could never match again.

Where a revert writes
---------------------
To the file as it is called NOW, with the original extension — see
restore_destination. Not to the path recorded at capture: that is only
right while nobody has renamed the file, and a point matched back to a
renamed file still carries it. Writing there put the file back under its
old name, deleted the renamed one, and failed outright when the folder had
been renamed too.
"""

import json
import logging
import os
from dataclasses import dataclass

from app.config import settings as app_settings
from app.core.ffmpeg import (
    RestoreUnsupported,
    _pick_temp_dir,
    build_restore_command,
)
from app.core.probe import ProbeError, extract_format_info, extract_tracks, probe_file
from app.core.recycle import delete_sidecar
from app.core.subprocess_runner import StagedOutput, run_staged_subprocess
from app.core.timeutil import utcnow

logger = logging.getLogger(__name__)


@dataclass
class RestoreOutcome:
    success: bool
    error: str | None = None
    restored_path: str | None = None


def revert_blocked_reason(point, path: str) -> str | None:
    """
    Why this revert point cannot be used on `path` right now, or None.

    The checks on the revert point and the file it describes: is the
    sidecar there, and is the file still the one the job left. Capture
    uses this to decide whether a new job can extend the point, so it must
    say nothing about where a revert would write — see
    restore_blocked_reason, which adds that for the revert and the list.

    Deliberately covers ALL of them, not just the fingerprint. A missing
    sidecar is the likelier reason on a bin that retention has been
    through, and a version checking only the fingerprint reports those as
    restorable and is wrong in exactly the way this is meant to prevent.

    Cheap enough to run per row: two stats, no probing.
    """
    if not os.path.exists(point.sidecar_path or ""):
        return ("The stored tracks for this revert point are missing from the "
                "recycle volume.")

    if not os.path.exists(path):
        return f"{path} is no longer on disk."

    try:
        stat = os.stat(path)
    except OSError as exc:
        return f"{path} cannot be read: {exc}"

    name = os.path.basename(path)
    if point.processed_size is not None and stat.st_size != point.processed_size:
        return (
            f"{name} has changed size since it was processed "
            f"({point.processed_size} → {stat.st_size} bytes). It has probably "
            f"been replaced or upgraded, and these stored tracks belong to the "
            f"previous version."
        )
    if point.processed_mtime is not None and stat.st_mtime != point.processed_mtime:
        return (
            f"{name} has been modified since it was processed. These stored "
            f"tracks belong to the previous version."
        )
    return None


def recorded_path(point, manifest: dict | None = None) -> str | None:
    """Where the file lived when the point was captured."""
    if manifest is None:
        try:
            manifest = json.loads(point.manifest)
        except (TypeError, ValueError):
            manifest = {}
    return manifest.get("path") or point.original_path


def restore_destination(original_path: str, current_path: str) -> str:
    """
    Where a revert writes: the current file's folder and name, with the
    original file's extension.

    A job only ever changes the extension (determine_output_path), so for a
    file nobody has renamed this IS the recorded path, and an MKV turned
    into X.mp4 comes back as X.mkv. For a renamed file it keeps the name
    the user, or Sonarr, gave it.
    """
    return (os.path.splitext(current_path)[0]
            + os.path.splitext(original_path)[1])


def restore_blocked_reason(point, path: str,
                           manifest: dict | None = None) -> str | None:
    """
    Why a revert of this point onto `path` would be refused, or None.

    Shared by the revert itself and by the listing the UI renders, and that
    sharing is the point: written twice they drift, and the drift is
    invisible in the direction that matters — the list keeps offering
    Revert on entries the revert then refuses, so the button appears
    broken rather than the file appearing changed.

    Adds one check to revert_blocked_reason: that the destination, when it
    is not the file itself, is free. Something already there is a file the
    user has, and the staged write would replace it without asking. The
    message leads with the name because the failure toast keeps only the
    first 60 characters.

    Cheap enough to run per row: a few stats, no probing.
    """
    problem = revert_blocked_reason(point, path)
    if problem:
        return problem

    original = recorded_path(point, manifest)
    if not original:
        return "This revert point does not record where the file came from."

    destination = restore_destination(original, path)
    if destination != path and os.path.lexists(destination):
        return (
            f'Another file already exists as "{os.path.basename(destination)}", '
            f"and reverting would overwrite it. Move or rename that file, "
            f"then revert."
        )
    return None


def _remove_created_files(manifest: dict) -> None:
    """
    Take away the files the job put next to the media, now that their
    content is back inside it.

    Subtitle extraction writes .srt files AND removes those subtitles from
    the mux. A revert re-embeds them, so leaving the files behind gives
    the user every extracted subtitle twice — which players show as
    duplicate tracks.

    Only files this job CREATED are listed (the worker checks before
    running), and each is removed only if it is still byte-for-byte what
    the job wrote. Someone who has since edited or replaced an extracted
    .srt clearly wants it, and a revert taking it would be destroying
    something Remuxarr did not make.

    Never raises. The media file is already restored at this point; a
    subtitle file that cannot be removed is untidy, not a failure, and
    reporting it as one would send the user to retry a revert that has
    already happened.
    """
    for entry in manifest.get("created_files") or []:
        path = entry.get("path")
        if not path:
            continue
        try:
            stat = os.stat(path)
        except OSError:
            continue    # already gone; nothing to do

        if (entry.get("size") is not None and stat.st_size != entry["size"]) or \
           (entry.get("mtime") is not None and stat.st_mtime != entry["mtime"]):
            logger.info(
                "Leaving %s alone: it has changed since the job wrote it",
                path,
            )
            continue

        try:
            os.remove(path)
            logger.info("Removed %s, extracted by the job being reverted", path)
        except OSError as exc:
            logger.warning("Could not remove %s: %s", path, exc)


@dataclass
class _Plan:
    """Everything needed to run a restore, read out of the database once."""
    point_id: int
    file_id: int
    current_path: str
    sidecar_path: str
    manifest: dict
    original_path: str
    destination: str


def _plan(db, point_id: int) -> tuple[_Plan | None, str | None]:
    """Validate a revert point and read out what running it needs."""
    from app.database.models import MediaFile, RevertPoint

    point = db.get(RevertPoint, point_id)
    if point is None:
        return None, "That revert point no longer exists."

    media = db.get(MediaFile, point.file_id)
    if media is None:
        return None, "The file this revert point belongs to is no longer tracked."

    try:
        manifest = json.loads(point.manifest)
    except (TypeError, ValueError) as exc:
        return None, f"This revert point's manifest is unreadable: {exc}"

    # ── Preconditions ───────────────────────────────────────────────────
    problem = restore_blocked_reason(point, media.path, manifest)
    if problem:
        return None, problem

    original_path = recorded_path(point, manifest)
    return _Plan(
        point_id=point.id,
        file_id=media.id,
        current_path=media.path,
        sidecar_path=point.sidecar_path,
        manifest=manifest,
        original_path=original_path,
        destination=restore_destination(original_path, media.path),
    ), None


def _timeout_seconds(app_cfg: dict) -> float | None:
    """
    The FFmpeg time limit for a revert: job_timeout_minutes, as jobs and
    forge runs use. A revert reads and writes the whole file as a job does,
    and without a limit a stalled FFmpeg — a share that stops answering —
    kept the file locked against every other writer until a restart.
    0 or empty means no limit, as it does for jobs.
    """
    minutes = app_cfg.get("job_timeout_minutes", 120)
    return float(minutes) * 60 if minutes else None


def _settle_forge_jobs(db, file_id: int, tracks: list[dict]) -> None:
    """
    Bring the file's AC3 Forge jobs in line with the restored file.

    A revert removes an AC3 forged after processing: it is not part of the
    original. Its forge job still said "success", so the Forge page listed
    the file as forged and refused to queue it again until an undo found
    nothing to remove. An AC3 forged BEFORE processing is part of the
    original, though, and comes back — its "success" is still true.

    So the restored file decides, through resolve_forge_ac3_for_undo, the
    test the undo itself uses: "absent" settles the file's finished forge
    jobs as undone, the state a completed undo leaves; "found" leaves them
    alone; "mismatch" — an AC3 5.1 where the forge never puts one — is
    left alone too, as the undo refuses to guess about it.

    "undo_failed" is settled with "success": the AC3 that undo could not
    remove is gone now as well. Pending and running jobs cannot be here —
    a revert is refused while the file has one (routes/revert.restore).

    tracks is the probe _apply has just made of the restored file, so a
    probe that failed never reaches here and the jobs stay as they were.
    """
    from app.core.forge import resolve_forge_ac3_for_undo
    from app.database.models import Ac3ForgeJob

    outcome, _index = resolve_forge_ac3_for_undo(tracks)
    if outcome == "mismatch":
        logger.warning(
            "Forge jobs for file %d left as they are after its revert: the "
            "restored file has an AC3 5.1 that is not its last audio track",
            file_id,
        )
        return
    if outcome == "found":
        return

    jobs = (db.query(Ac3ForgeJob)
              .filter(Ac3ForgeJob.file_id == file_id,
                      Ac3ForgeJob.status.in_(("success", "undo_failed")))
              .all())
    for job in jobs:
        job.status = "undone"
        job.is_undo = True
        job.completed_at = utcnow()
        job.error_message = None
    if jobs:
        logger.info(
            "Marked %d AC3 Forge job(s) for file %d undone: the revert "
            "removed the forged track", len(jobs), file_id,
        )


def _apply(db, plan: _Plan, restored_path: str) -> None:
    """
    Bring the database back in line with the file that is now on disk.

    Deliberately mirrors _finish_job's post-success bookkeeping, for the
    same reasons documented there — most importantly the Track refresh,
    without which every future delta scan skips this file and its rows
    describe a version that no longer exists.

    Never raises. The restore has already succeeded and the bytes on disk
    are correct; a bookkeeping failure is fixed by the next full rescan
    and must not be reported to the user as a failed revert.
    """
    from app.database.models import MediaFile, RevertPoint, Track

    media = db.get(MediaFile, plan.file_id)
    if media is None:
        return

    if restored_path != media.path:
        # Same stale-row hazard _finish_job handles: a MediaFile row from
        # an earlier cycle may already own this path, and the UNIQUE
        # constraint would reject the update.
        stale = (
            db.query(MediaFile)
            .filter(MediaFile.path == restored_path, MediaFile.id != media.id)
            .first()
        )
        if stale:
            logger.info("Removing stale MediaFile row for %s", restored_path)
            db.delete(stale)
            db.flush()

        media.path = restored_path
        media.filename = os.path.basename(restored_path)
        media.directory = os.path.dirname(restored_path)

    # The delta-scan sentinels, not the restored file's real stats.
    #
    # The file now looks the way it did before the job, so the next scan
    # should evaluate it on its merits — and evaluating it is the whole
    # point of the workflow this feature exists for: revert a rule that
    # turned out wrong, change the rule, rescan.
    #
    # Status alone does not achieve that. _process_file's delta check
    # compares ONLY size and mtime against the on-disk stat and has no
    # awareness of .status, so writing the restored file's real stats
    # here is precisely what makes every future delta scan — the default,
    # and what the scheduler always uses — see "unchanged" and return
    # before probing. The file was reachable only through a forced full
    # rescan.
    #
    # This mirrored _finish_job, where the same two lines are correct:
    # there the file genuinely IS processed, so skipping it is the right
    # outcome. A revert inverts that, and the copied lines came with a
    # consequence that only made sense at the place they were copied
    # from.
    #
    # -1/-1.0 is the established spelling for "re-evaluate this" —
    # cancel_item, clear_pending, clear_dry_run, abort_job,
    # clear_history and delete_history_item all write it, for this exact
    # reason. Real files never carry a negative size or mtime, so the
    # value cannot be mistaken for a measurement.
    media.size = -1
    media.mtime = -1.0

    # "unprocessed", matching the history dismissal paths rather than the
    # queue cancellation ones. Both resurface a file; they differ in what
    # they claim happened to it, and a revert has put the file back to
    # never-having-been-processed, which is what last_processed=None below
    # already records. "pending" was assigned nowhere else in the app and
    # is a QueueItem status, not a MediaFile one.
    media.status = "unprocessed"
    media.last_processed = None

    try:
        probe_data = probe_file(restored_path, app_settings.FFPROBE_PATH)
        fmt_info = extract_format_info(probe_data)
        track_list = extract_tracks(probe_data)

        db.query(Track).filter(Track.file_id == media.id).delete()
        for td in track_list:
            db.add(Track(
                file_id             = media.id,
                stream_index        = td["stream_index"],
                track_type          = td["track_type"],
                codec               = td["codec"],
                language            = td["language"],
                channels            = td.get("channels"),
                channel_layout      = td.get("channel_layout"),
                is_default          = td.get("is_default", False),
                is_forced           = td.get("is_forced", False),
                is_hearing_impaired = td.get("is_hearing_impaired", False),
                is_dub              = td.get("is_dub", False),
                title               = td.get("title"),
            ))

        media.duration = fmt_info.get("duration")
        media.font_attachments = fmt_info.get("font_attachments")
        media.video_codec = next(
            (t["codec"] for t in track_list if t["track_type"] == "video"), None
        )
        if fmt_info.get("container"):
            media.container = fmt_info["container"]
        _settle_forge_jobs(db, media.id, track_list)
    except ProbeError as exc:
        logger.warning(
            "Post-revert track refresh failed for %s: %s — Track rows may be "
            "stale until the next full rescan", restored_path, exc,
        )

    # The point has been spent: the file is back to the original it
    # described, so there is nothing left to restore. Deleting by file_id
    # rather than by id is deliberate — see the module docstring.
    for point in db.query(RevertPoint).filter(RevertPoint.file_id == media.id).all():
        delete_sidecar(point.sidecar_path)
        db.delete(point)


async def restore_revert_point(point_id: int, *, on_progress=None) -> RestoreOutcome:
    """
    Put a file back the way it was before the job that produced `point_id`.

    Validation, then a staged write, then the database. Any failure before
    the swap leaves the file untouched.
    """
    from app.database.session import SessionLocal, get_app_settings

    with SessionLocal() as db:
        plan, error = _plan(db, point_id)
        timeout_seconds = _timeout_seconds(get_app_settings(db))
    if plan is None:
        logger.info("Revert point %d refused: %s", point_id, error)
        return RestoreOutcome(success=False, error=error)

    # Staged like every other write in this codebase: FFmpeg produces a
    # temp file, which is only swapped into place once it is complete. A
    # crash mid-restore therefore leaves the processed file intact rather
    # than a truncated one where a working file used to be.
    #
    # Sized from the current file, which exists. The destination often does
    # not yet — X.mkv while the file is X.mp4 — and _pick_temp_dir reads a
    # missing file as zero bytes, so TEMP_DIR would be chosen however
    # little room it had. The fallback folder is the same either way.
    temp_output = os.path.join(
        _pick_temp_dir(plan.current_path), f"revert_{point_id}.remuxarr_tmp"
    )

    try:
        cmd = build_restore_command(
            plan.current_path, plan.sidecar_path, temp_output, plan.manifest,
        )
    except RestoreUnsupported as exc:
        return RestoreOutcome(success=False, error=str(exc))

    logger.info(
        "Reverting %s → %s", plan.current_path, plan.destination,
    )
    result = await run_staged_subprocess(
        cmd,
        [StagedOutput(temp_path=temp_output, final_path=plan.destination)],
        on_progress_line=on_progress,
        stderr_tail_lines=30,
        timeout_seconds=timeout_seconds,
        timeout_label="Revert",
    )

    if not result.success:
        return RestoreOutcome(success=False, error=result.error)

    # A container change during processing means the file lived under a
    # different extension; the restored original is now beside it and the
    # processed copy is dead weight.
    if plan.destination != plan.current_path and os.path.exists(plan.current_path):
        try:
            os.remove(plan.current_path)
        except OSError as exc:
            logger.warning(
                "Could not remove the processed file %s after reverting: %s",
                plan.current_path, exc,
            )

    # After the swap, so a failed restore leaves the extracted files where
    # they are — they are the user's only copy of those subtitles while
    # the processed file still lacks them.
    _remove_created_files(plan.manifest)

    try:
        with SessionLocal() as db:
            _apply(db, plan, plan.destination)
            db.commit()
    except Exception:
        logger.exception(
            "Revert of %s succeeded on disk but its database update failed",
            plan.destination,
        )

    logger.info("Reverted %s", plan.destination)
    return RestoreOutcome(success=True, restored_path=plan.destination)
