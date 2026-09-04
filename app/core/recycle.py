"""
Recycle bin — the storage layer behind "revert to original".

This module owns the conventions that everything else in the feature
depends on:

  • where a sidecar lives and what it is called
  • whether the recycle volume is actually usable right now
  • how a sidecar is removed, from every path that removes one

It deliberately does not know how to build or replay a sidecar; that
belongs with the FFmpeg layer.
"""

import logging
import os
import time
from datetime import timedelta

from app.config import settings as app_settings
from app.core.timeutil import utcnow_naive

logger = logging.getLogger(__name__)


# Sidecars are Matroska regardless of the source container — it is the only
# container that will hold an arbitrary mix of dropped audio, subtitle and
# attachment streams without complaint.
#
# The extension is NOT ".mkv" and NOT ".mka". Two separate reasons, both of
# which have bitten this codebase before in other forms:
#
#   • ".mkv" is in probe.MEDIA_EXTENSIONS. A sidecar that lands anywhere a
#     scan can see it would be probed, queued and processed as if it were a
#     library file. The recycle volume is not in scan_paths today, but a
#     suffix that is safe only because of a setting elsewhere is not safe.
#
#   • ".part", ".remuxarr_tmp" and ".forge_tmp" are what the startup orphan
#     sweep in main.py deletes. A sidecar must never match that list — it is
#     a retained file, not a work-in-progress one.
#
# FFmpeg cannot infer a muxer from this suffix, which is fine and matches
# what the pipeline already does for its temp files: the format is always
# passed explicitly with -f.
SIDECAR_SUFFIX = ".remuxarr_revert"


def recycle_dir_status() -> tuple[bool, str]:
    """
    Return (ready, reason) for the configured recycle directory.

    Ready means: it exists as a directory and we can write to it. The
    existence check is doing real work here rather than being a formality —
    Docker creates a bind mount's target directory inside the container, so
    the directory being present is the signal that the volume was actually
    mounted. The image must therefore never create it itself.

    A missing directory is reported, not created. See config.RECYCLE_DIR for
    why manufacturing it would be worse than failing.
    """
    path = app_settings.RECYCLE_DIR

    if not path:
        return False, "No recycle directory is configured."
    if not os.path.exists(path):
        return False, (
            f"{path} does not exist — the recycle volume does not appear to "
            f"be mounted. Add it to your container configuration."
        )
    if not os.path.isdir(path):
        return False, f"{path} exists but is not a directory."
    if not os.access(path, os.W_OK):
        return False, f"{path} is not writable by the container's user."

    return True, ""


def delete_sidecar(path: str | None) -> bool:
    """
    Remove one sidecar file. Returns True if a file was actually removed.

    Missing is success, not failure: every caller is deleting a revert point
    whose sidecar may already be gone (retention swept it, the user emptied
    the volume by hand, the write never completed). Raising there would abort
    a database cleanup over a file that is already in the state we want.

    Never raises. The database half of a revert point is always deleted by
    the caller regardless of what happens here, and an unremovable file
    should not be able to block that — a warning in the log is the correct
    outcome, because the alternative is a revert point that cannot be
    deleted at all.
    """
    if not path:
        return False
    try:
        os.remove(path)
        logger.debug("Removed revert sidecar: %s", path)
        return True
    except FileNotFoundError:
        return False
    except OSError as exc:
        logger.warning("Could not remove revert sidecar %s: %s", path, exc)
        return False


# A sidecar younger than this is left alone by the orphan pass. It exists
# because a sidecar is written during a job but its row is not recorded
# until that job finishes — so for the length of the staging copy there is
# legitimately a complete sidecar on the volume with nothing pointing at
# it. Sweeping that window would delete a live revert point out from under
# a running job. An hour is far beyond the gap on any plausible hardware,
# and the cost of being generous is only that a genuinely leaked file
# survives one extra sweep.
ORPHAN_GRACE_SECONDS = 3600


def sweep_retention() -> tuple[int, int]:
    """
    Enforce the retention window. Returns (points_removed, bytes_freed).

    Three passes, in this order:

      1. Age — anything older than revert_retention_days.
      2. Size — oldest first, until the total is under
         revert_retention_max_gb. Both bounds exist because either alone
         fails a common case: a days-only window has no ceiling during a
         big library sweep, and a size-only cap keeps one stale sidecar
         forever on a quiet library.
      3. Orphans — files on the volume with no row pointing at them. The
         only way to produce one is a crash between writing the sidecar
         and recording the row; every ordinary failure path already
         deletes its own. Without this pass those bytes are invisible:
         nothing scans the volume, no row names them, and they are not
         counted against the cap, so the bin silently exceeds its limit
         by however much has leaked.

    Runs regardless of revert_enabled. Turning the feature off should stop
    new revert points being created, not strand the existing ones on disk
    forever — the opposite would leave a user who disabled the feature
    with 20GB they cannot explain and no UI that mentions it.

    Sizes come from the stored sidecar_size rather than stat() so the size
    pass is one query rather than a filesystem round trip per row.
    """
    ready, reason = recycle_dir_status()
    if not ready:
        logger.debug("Retention sweep skipped: %s", reason)
        return 0, 0

    # Imported here rather than at module scope: this module is imported by
    # scanner.py, which the database layer does not depend on, and a
    # top-level import would tie the two together for one function.
    from app.database.models import RevertPoint
    from app.database.session import SessionLocal, get_app_settings

    removed = 0
    freed = 0

    with SessionLocal() as db:
        cfg = get_app_settings(db)
        days = int(cfg.get("revert_retention_days", 7) or 0)
        max_gb = float(cfg.get("revert_retention_max_gb", 20) or 0)

        points = db.query(RevertPoint).order_by(RevertPoint.created_at.desc()).all()

        doomed: list = []
        keep: list = []

        # ── 1. Age ──────────────────────────────────────────────────────
        if days > 0:
            # utcnow_naive, not utcnow: created_at comes back from the
            # column without tzinfo, and comparing it to an aware value
            # raises. That failure only surfaces once a row is old enough
            # to be compared, i.e. days after the code ships.
            cutoff = utcnow_naive() - timedelta(days=days)
            for p in points:
                (doomed if (p.created_at and p.created_at < cutoff) else keep).append(p)
        else:
            keep = list(points)

        # ── 2. Size cap ─────────────────────────────────────────────────
        if max_gb > 0:
            budget = max_gb * 1024 * 1024 * 1024
            running = 0
            survivors = []
            spent = False
            # keep is newest-first, so once the budget is gone everything
            # remaining is older than the point that exhausted it and goes
            # too. The `spent` latch is what makes this an eviction rather
            # than a pack: without it the loop keeps testing each point
            # individually and lets smaller OLDER ones slip in behind a
            # large recent one that did not fit.
            #
            # That was the shipped behaviour, and it made "which revert
            # points do I still have" depend on file sizes rather than on
            # age. With a 20GB cap and points of 19GB, 2GB and 1GB it kept
            # the newest and the OLDEST and evicted the middle.
            for p in keep:
                size = p.sidecar_size or 0
                if spent or running + size > budget:
                    spent = True
                    doomed.append(p)
                else:
                    running += size
                    survivors.append(p)
            keep = survivors

        for p in doomed:
            delete_sidecar(p.sidecar_path)
            freed += p.sidecar_size or 0
            removed += 1
            db.delete(p)
        db.commit()

        known = {p.sidecar_path for p in keep}

    # ── 3. Orphans ──────────────────────────────────────────────────────
    cutoff_mtime = time.time() - ORPHAN_GRACE_SECONDS
    try:
        entries = os.scandir(app_settings.RECYCLE_DIR)
    except OSError as exc:
        logger.warning("Could not scan the recycle volume: %s", exc)
        entries = []

    for entry in entries:
        if not entry.is_file() or not entry.name.endswith(SIDECAR_SUFFIX):
            continue
        if entry.path in known:
            continue
        try:
            stat = entry.stat()
        except OSError:
            continue
        if stat.st_mtime > cutoff_mtime:
            continue
        logger.warning(
            "Removing orphaned revert sidecar with no database row: %s", entry.path
        )
        if delete_sidecar(entry.path):
            freed += stat.st_size
            removed += 1

    if removed:
        logger.info(
            "Retention sweep removed %d revert point(s), freeing %.1f MB",
            removed, freed / 1024 / 1024,
        )
    return removed, freed
