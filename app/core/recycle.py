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

from app.config import settings as app_settings

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


def ensure_recycle_subdir(name: str) -> str:
    """
    Create and return a subdirectory of the recycle volume.

    Creating a LEAF under a directory that already exists is safe in a way
    that creating the root is not: the root's existence is what proves the
    volume is mounted, so by the time this runs there is a real volume
    underneath. Raises if the volume is not ready, rather than falling back
    to somewhere writable — a silent fallback is how sidecars would end up
    on a filesystem the user never sized for them.
    """
    ready, reason = recycle_dir_status()
    if not ready:
        raise RuntimeError(f"Recycle bin unavailable: {reason}")

    path = os.path.join(app_settings.RECYCLE_DIR, name)
    os.makedirs(path, exist_ok=True)
    return path


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
