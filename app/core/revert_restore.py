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

logger = logging.getLogger(__name__)


@dataclass
class RestoreOutcome:
    success: bool
    error: str | None = None
    restored_path: str | None = None


@dataclass
class _Plan:
    """Everything needed to run a restore, read out of the database once."""
    point_id: int
    file_id: int
    current_path: str
    sidecar_path: str
    manifest: dict
    original_path: str


def _plan(db, point_id: int) -> tuple[_Plan | None, str | None]:
    """Validate a revert point and read out what running it needs."""
    from app.database.models import MediaFile, RevertPoint

    point = db.get(RevertPoint, point_id)
    if point is None:
        return None, "That revert point no longer exists."

    media = db.get(MediaFile, point.file_id)
    if media is None:
        return None, "The file this revert point belongs to is no longer tracked."

    if not os.path.exists(point.sidecar_path):
        return None, (
            "The stored tracks for this revert point are missing from the "
            "recycle volume."
        )

    if not os.path.exists(media.path):
        return None, f"{media.path} is no longer on disk."

    # ── Sentinel ────────────────────────────────────────────────────────
    stat = os.stat(media.path)
    if point.processed_size is not None and stat.st_size != point.processed_size:
        return None, (
            f"{os.path.basename(media.path)} has changed size since it was "
            f"processed ({point.processed_size} → {stat.st_size} bytes). It "
            f"has probably been replaced or upgraded, and these stored tracks "
            f"belong to the previous version."
        )
    if point.processed_mtime is not None and stat.st_mtime != point.processed_mtime:
        return None, (
            f"{os.path.basename(media.path)} has been modified since it was "
            f"processed. These stored tracks belong to the previous version."
        )

    try:
        manifest = json.loads(point.manifest)
    except (TypeError, ValueError) as exc:
        return None, f"This revert point's manifest is unreadable: {exc}"

    original_path = manifest.get("path") or point.original_path
    if not original_path:
        return None, "This revert point does not record where the file came from."

    return _Plan(
        point_id=point.id,
        file_id=media.id,
        current_path=media.path,
        sidecar_path=point.sidecar_path,
        manifest=manifest,
        original_path=original_path,
    ), None


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

    try:
        stat = os.stat(restored_path)
        media.size = stat.st_size
        media.mtime = stat.st_mtime
    except OSError:
        pass

    # Back to "pending" rather than "processed": the file now looks the
    # way it did before the job, so the next scan should evaluate it on
    # its merits. Leaving it "processed" would hide a reverted file from
    # the very analysis that would tell the user what it needs.
    media.status = "pending"
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
        media.video_codec = next(
            (t["codec"] for t in track_list if t["track_type"] == "video"), None
        )
        if fmt_info.get("container"):
            media.container = fmt_info["container"]
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
    from app.database.session import SessionLocal

    with SessionLocal() as db:
        plan, error = _plan(db, point_id)
    if plan is None:
        logger.info("Revert point %d refused: %s", point_id, error)
        return RestoreOutcome(success=False, error=error)

    # Staged like every other write in this codebase: FFmpeg produces a
    # temp file, which is only swapped into place once it is complete. A
    # crash mid-restore therefore leaves the processed file intact rather
    # than a truncated one where a working file used to be.
    temp_output = os.path.join(
        _pick_temp_dir(plan.original_path), f"revert_{point_id}.remuxarr_tmp"
    )

    try:
        cmd = build_restore_command(
            plan.current_path, plan.sidecar_path, temp_output, plan.manifest,
        )
    except RestoreUnsupported as exc:
        return RestoreOutcome(success=False, error=str(exc))

    logger.info(
        "Reverting %s → %s", plan.current_path, plan.original_path,
    )
    result = await run_staged_subprocess(
        cmd,
        [StagedOutput(temp_path=temp_output, final_path=plan.original_path)],
        on_progress_line=on_progress,
        stderr_tail_lines=30,
    )

    if not result.success:
        return RestoreOutcome(success=False, error=result.error)

    # A container change during processing means the file lived under a
    # different name; the restored original is now beside it and the
    # processed copy is dead weight.
    if plan.original_path != plan.current_path and os.path.exists(plan.current_path):
        try:
            os.remove(plan.current_path)
        except OSError as exc:
            logger.warning(
                "Could not remove the processed file %s after reverting: %s",
                plan.current_path, exc,
            )

    try:
        with SessionLocal() as db:
            _apply(db, plan, plan.original_path)
            db.commit()
    except Exception:
        logger.exception(
            "Revert of %s succeeded on disk but its database update failed",
            plan.original_path,
        )

    logger.info("Reverted %s", plan.original_path)
    return RestoreOutcome(success=True, restored_path=plan.original_path)
