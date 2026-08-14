"""
Capturing a revert point, from inside the staging window.

This runs at the one moment where the source file and the finished output
both exist — see run_staged_subprocess's before_staging hook. It probes
both, works out what the job destroyed, and cuts those streams into a
sidecar on the recycle volume.

Nothing here writes to the database. The sidecar is produced during the
window; the row that points at it is written by the worker only once the
job has actually succeeded, because a run can still fail after this
returns (staging can hit ENOSPC) and a revert point for a job that never
happened would be worse than none. The worker owns deleting the sidecar
on those paths.

Two kinds of "no revert point", deliberately treated differently
----------------------------------------------------------------
IMPOSSIBLE — the job destroyed nothing, or destroyed only attachments,
which Matroska cannot store on their own. Nothing is wrong and nothing
is failing; there is simply nothing to keep. These never block a job,
whatever revert_require_point says, because failing a job over a file
whose only loss was a font would be absurd.

UNAVAILABLE — the volume is not mounted, the disk is full, FFmpeg failed.
The user asked for a revert point and the system could not provide one.
That is what revert_require_point governs: leave it off and the job
proceeds without a revert point, turn it on and the job refuses while the
source file is still untouched.

Collapsing these two would mean either failing jobs over nothing, or
silently ignoring a broken recycle volume. Both have been chosen against.
"""

import asyncio
import json
import logging
import os
from dataclasses import dataclass

from app.config import settings as app_settings
from app.core.ffmpeg import SidecarUnsupported, build_sidecar_command
from app.core.probe import ProbeError, probe_file
from app.core.recycle import SIDECAR_SUFFIX, recycle_dir_status
from app.core.revert import build_manifest, find_lost_streams

logger = logging.getLogger(__name__)


@dataclass
class CapturedRevertPoint:
    """A written sidecar, waiting for the job to succeed before it is recorded."""
    sidecar_path: str
    sidecar_size: int
    manifest_json: str
    original_path: str
    original_container: str | None


class _Unavailable(Exception):
    """The recycle bin could not provide a revert point. Governed by the setting."""


def sidecar_path_for(file_id: int, job_id: int) -> str:
    """
    Name a sidecar after the file and job that produced it.

    Both parts are needed. file_id alone collides when a file is processed
    twice before retention sweeps the first sidecar, and the second write
    would silently overwrite a revert point another row still points at.
    """
    return os.path.join(
        app_settings.RECYCLE_DIR, f"{file_id}_{job_id}{SIDECAR_SUFFIX}"
    )


async def _run(cmd: list[str]) -> None:
    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.PIPE,
    )
    _, stderr = await proc.communicate()
    if proc.returncode != 0:
        raise _Unavailable(
            f"FFmpeg failed writing the sidecar (rc={proc.returncode}): "
            f"{(stderr or b'').decode(errors='replace').strip()[:300]}"
        )


async def capture(
    *,
    input_path: str,
    produced_path: str,
    file_id: int,
    job_id: int,
    app_cfg: dict,
) -> tuple[CapturedRevertPoint | None, str | None]:
    """
    Produce a sidecar for whatever this job destroyed.

    Returns (captured, error). A non-None error is returned straight to the
    staging hook and aborts the run with the source file untouched; it is
    only ever produced when revert_require_point is on.
    """
    if not app_cfg.get("revert_enabled"):
        return None, None

    require = bool(app_cfg.get("revert_require_point"))

    try:
        ready, reason = recycle_dir_status()
        if not ready:
            raise _Unavailable(reason)

        try:
            original_probe = probe_file(input_path)
            produced_probe = probe_file(produced_path)
        except ProbeError as exc:
            raise _Unavailable(f"Could not probe for a revert point: {exc}") from exc

        container = (original_probe.get("format", {})
                     .get("format_name", "").split(",")[0] or None)
        manifest = build_manifest(original_probe, original_path=input_path,
                                  original_container=container)
        lost = find_lost_streams(manifest, produced_probe)

        # IMPOSSIBLE, not UNAVAILABLE — see the module docstring.
        if not lost:
            logger.info(
                "Job %d: nothing was destroyed, no revert point needed", job_id
            )
            return None, None

        sidecar = sidecar_path_for(file_id, job_id)
        try:
            cmd = build_sidecar_command(input_path, sidecar, lost)
        except SidecarUnsupported as exc:
            logger.info(
                "Job %d: no revert point possible for %s — %s",
                job_id, input_path, exc,
            )
            return None, None

        await _run(cmd)

        try:
            size = os.path.getsize(sidecar)
        except OSError as exc:
            raise _Unavailable(f"Sidecar vanished after being written: {exc}") from exc

        logger.info(
            "Job %d: revert point captured (%d stream(s), %.1f MB) → %s",
            job_id, len(lost), size / 1024 / 1024, sidecar,
        )
        return CapturedRevertPoint(
            sidecar_path=sidecar,
            sidecar_size=size,
            manifest_json=json.dumps(manifest),
            original_path=input_path,
            original_container=container,
        ), None

    except _Unavailable as exc:
        if require:
            # Aborts the run. The source file has not been touched at this
            # point, so the cost is the wasted remux and nothing else —
            # which is the only reason refusing is a reasonable option.
            logger.error(
                "Job %d: refusing to process %s without a revert point — %s",
                job_id, input_path, exc,
            )
            return None, f"No revert point could be recorded: {exc}"

        logger.warning(
            "Job %d: proceeding without a revert point for %s — %s",
            job_id, input_path, exc,
        )
        return None, None
