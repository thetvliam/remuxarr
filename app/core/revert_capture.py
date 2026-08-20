"""
Capturing a revert point, from inside the staging window.

This runs at the one moment where the source file and the finished output
both exist — see run_staged_subprocess's before_staging hook. It probes
both, works out what the job destroyed, and cuts those streams into a
sidecar on the recycle volume.

One revert point per file, anchored to the original
--------------------------------------------------
A revert point describes the PRISTINE file — the way it was before
Remuxarr ever touched it — not the state before the most recent job. A
second job on the same file extends the existing point rather than
adding another: it works out what is missing relative to that stored
original, and rebuilds the sidecar to hold all of it.

The alternative, one point per job, was tried first and is broken in a
way that is easy to miss. Each point fingerprints the file as its own job
left it, so the next job invalidates the previous point simply by
rewriting the file — even a job that destroys nothing, like a language
re-tag. The dropped subtitles would still be sitting on the recycle
volume, intact and permanently unreachable, and the user would be told
the file "has been modified since it was processed", blaming an outside
change for something Remuxarr did itself.

Rebuilding the sidecar needs two inputs, because neither alone has
everything: what THIS job destroyed is still in the file it was handed,
while what an EARLIER job destroyed exists only in the previous sidecar.

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
from app.core.probe import ProbeError, extract_format_info, probe_file
from app.core.recycle import SIDECAR_SUFFIX, recycle_dir_status
from app.core.revert import build_manifest, match_streams
from app.core.revert_restore import revert_blocked_reason

logger = logging.getLogger(__name__)


@dataclass
class CapturedRevertPoint:
    """A written sidecar, waiting for the job to succeed before it is recorded."""
    sidecar_path: str
    sidecar_size: int
    manifest_json: str
    original_path: str
    original_container: str | None
    # Set when this extends an existing revert point rather than creating
    # one. The worker updates that row instead of inserting a second, and
    # unlinks the superseded sidecar once the new row is safely recorded —
    # in that order, so a crash in between leaves a file the orphan sweep
    # collects rather than a row pointing at nothing.
    replaces_point_id: int | None = None
    replaces_sidecar_path: str | None = None


@dataclass
class _ExistingPoint:
    """
    The revert point already held for this file.

    `manifest` is None when the row exists but cannot be built on — an
    older manifest layout, unreadable JSON, a sidecar that is no longer on
    the volume. That is deliberately NOT the same as there being no point
    at all, and conflating the two was a bug: capture created a second row
    beside the unusable one, so the file ended up with two revert points,
    two sidecars, and a revert that deleted both.
    """
    point_id: int
    sidecar_path: str
    manifest: dict | None = None

    @property
    def usable(self) -> bool:
        return self.manifest is not None


def _load_existing_point(file_id: int, current_path: str) -> _ExistingPoint | None:
    """
    The revert point already held for this file, if any.

    There is at most one by design. It describes the PRISTINE original,
    not the state before the most recent job, and every later job extends
    it rather than adding another — see the module docstring.

    A point that cannot be built on — older manifest layout, unreadable
    JSON, sidecar gone from the volume, or a fingerprint that no longer
    matches the file — comes back with manifest=None rather than as
    nothing at all.

    The fingerprint check is the one that stops a point being extended
    onto content it does not describe, and it covers two quite different
    routes to the same corruption:

      • Something replaced the file between jobs. A Sonarr upgrade is the
        obvious one. The stored manifest describes the previous release
        and its sidecar holds that release's tracks, so extending would
        build a sidecar mixing two releases together.
      • The row is a leftover pointing at a REUSED id. clear_database
        wipes media_files without enforced foreign keys, so a surviving
        revert point keeps a file_id that the next scanned file inherits.
        Extending then reads another file's manifest entirely.

    Either way the point describes a file that is not this one, so it is
    superseded rather than built upon. Capture then starts a fresh manifest
    anchored to the current file but still SUPERSEDES that row, taking it
    over and unlinking its sidecar.

    Returning None for those cases instead is what produced duplicates:
    the unusable row survived, a second was created beside it, and the
    file had two revert points claiming to restore it. Only one could
    ever work — the older row's fingerprint stops matching the moment the
    new job rewrites the file — and a revert deleted both, so the counts
    dropped by two.
    """
    from app.core.revert import MANIFEST_VERSION
    from app.database.models import RevertPoint
    from app.database.session import SessionLocal

    with SessionLocal() as db:
        point = (
            db.query(RevertPoint)
            .filter(RevertPoint.file_id == file_id)
            .order_by(RevertPoint.created_at.desc())
            .first()
        )
        if point is None:
            return None

        try:
            manifest = json.loads(point.manifest)
        except (TypeError, ValueError):
            logger.warning(
                "Revert point %d has an unreadable manifest; replacing it",
                point.id,
            )
            return _ExistingPoint(point.id, point.sidecar_path)

        if manifest.get("version") != MANIFEST_VERSION:
            logger.warning(
                "Revert point %d uses manifest version %r, not %r; replacing it",
                point.id, manifest.get("version"), MANIFEST_VERSION,
            )
            return _ExistingPoint(point.id, point.sidecar_path)

        # The same rule the revert itself applies, for the same reason:
        # this point is only usable if it still describes the file in
        # front of us. Covers the missing sidecar too.
        problem = revert_blocked_reason(point, current_path)
        if problem:
            logger.warning(
                "Revert point %d no longer describes %s (%s); replacing it",
                point.id, current_path, problem,
            )
            return _ExistingPoint(point.id, point.sidecar_path)

        return _ExistingPoint(point_id=point.id,
                              sidecar_path=point.sidecar_path,
                              manifest=manifest)


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


async def _off_loop(fn, *args):
    """
    Run blocking work on a thread instead of the event loop.

    capture() is awaited from inside run_staged_subprocess's hook, which
    runs on the main loop alongside every job's progress broadcasts and
    every HTTP handler. ffprobe on a spun-down array takes hundreds of
    milliseconds, and capture does two probes plus a database query — all
    of it synchronous, all of it previously on the loop.

    Measured at 412 ms of stalled loop per probe against 12 ms through an
    executor. That is not a slow revert; it is every OTHER job's progress
    freezing while one job finishes.
    """
    return await asyncio.get_running_loop().run_in_executor(None, fn, *args)


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


def _plan_sources(
    lost: list[dict], *, has_previous_sidecar: bool,
) -> list[tuple[dict, int, int]]:
    """
    Work out where each lost stream can be read from RIGHT NOW.

    Input 0 is the file this job was handed; input 1, when there is one,
    is the revert point's previous sidecar. Streams THIS job destroyed are
    still in input 0, at the index the previous capture recorded for them.
    Streams an EARLIER job destroyed are only in input 1.

    The sidecar is checked first. Under the current annotations the two
    are mutually exclusive — a stream the previous capture put in the
    sidecar has processed_index None — so the order costs nothing today.
    It is stated rather than left to chance because the direction matters
    the moment that stops holding: the sidecar copy is the one that came
    out of the pristine original, while the copy in input 0 has been
    through however many jobs since and may have been re-tagged or
    re-encoded on the way.

    Raises _Unavailable for a stream in neither. Refusing beats writing a
    sidecar that silently omits a track while the manifest claims it is
    there — restore would then map a slot that holds something else.

    Attachments are placed LAST, and that ordering is load-bearing rather
    than cosmetic. sidecar_index is positional — the nth source becomes
    output stream n — but the Matroska muxer does not write streams in map
    order: it emits every real track first and attachments afterwards.
    Feed it [subtitle, font, cover-art] and it writes [subtitle, cover-art,
    font], so every index from the first attachment onward points at the
    wrong stream.

    That is not hypothetical. It is what shipped, and it corrupted exactly
    the files where a non-attachment stream follows an attachment in the
    original — Matroska cover art, which the demuxer surfaces as an
    attached_pic video stream after all the fonts. Sorting here makes map
    order and file order the same by construction, and capture verifies it
    against the written file rather than trusting this comment.
    """
    ordered = ([s for s in lost if s.get("type") != "attachment"]
               + [s for s in lost if s.get("type") == "attachment"])

    sources: list[tuple[dict, int, int]] = []
    for stream in ordered:
        sidecar_index = stream.get("sidecar_index")
        processed_index = stream.get("processed_index")

        if has_previous_sidecar and sidecar_index is not None:
            sources.append((stream, 1, sidecar_index))
        elif processed_index is not None:
            sources.append((stream, 0, processed_index))
        elif not has_previous_sidecar:
            # A file's first job: nothing has been re-indexed yet, so the
            # stream is still where the original said it was.
            sources.append((stream, 0, stream["index"]))
        else:
            raise _Unavailable(
                f"Stream {stream.get('index')} of the original is in "
                f"neither the current file nor the previous sidecar."
            )
    return sources


def _reannotate(
    matches: list[tuple[dict, int | None]],
    sources: list[tuple[dict, int, int]],
) -> None:
    """
    Rewrite every stream's processed_index/sidecar_index in place, now
    that both the sidecar and the processed file have changed.

    Restore could re-derive this by matching the manifest against the
    processed file again, but it should not have to: that file may have
    been re-tagged, re-scanned or partially rewritten by then, and
    re-running fuzzy matching against a moved target is exactly how a
    revert puts the wrong track back. Resolved once, here, where both
    files are known-good and one line from being swapped.

    sidecar_index is positional: build_sidecar_command maps `sources` in
    order, so the nth entry is output stream n.

    Old annotations are CLEARED, not overwritten. Overwriting alone would
    leave a stale sidecar_index on any stream that was lost before and is
    matched now — and restore prefers the sidecar when both are present,
    so it would source that track from whatever happens to sit at that
    slot in the new sidecar.
    """
    new_sidecar_indices = {id(stream): n
                           for n, (stream, _input, _index) in enumerate(sources)}
    for stream, produced_index in matches:
        stream["processed_index"] = produced_index
        stream.pop("sidecar_index", None)
        if id(stream) in new_sidecar_indices:
            stream["sidecar_index"] = new_sidecar_indices[id(stream)]


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
        ready, reason = await _off_loop(recycle_dir_status)
        if not ready:
            raise _Unavailable(reason)

        try:
            produced_probe = await _off_loop(probe_file, produced_path)
        except ProbeError as exc:
            raise _Unavailable(f"Could not probe for a revert point: {exc}") from exc

        existing = await _off_loop(_load_existing_point, file_id, input_path)
        # A row that exists but cannot be built on is superseded, not
        # ignored: `extend` decides what to build FROM, `existing` decides
        # which row to write back to.
        extend = existing is not None and existing.usable

        if not extend:
            # Either this file's first job, or one whose existing point
            # cannot be built on. Both take the file they were handed as
            # the original — for a first job that is exactly right, and for
            # a superseded point it is the best available, since whatever
            # the unusable row described can no longer be reconstructed.
            try:
                original_probe = await _off_loop(probe_file, input_path)
            except ProbeError as exc:
                raise _Unavailable(
                    f"Could not probe for a revert point: {exc}"
                ) from exc
            # extract_format_info, not format_name.split(",")[0]. ffprobe
            # reports "mov,mp4,m4a,3gp,3g2,mj2" for every MP4, so taking
            # the first element yields "mov" — a real muxer, but not the
            # one this file was, and restore would write a MOV where an
            # MP4 belongs. _normalise_container already owns that
            # translation and has the matroska/webm trap documented
            # alongside it.
            container = extract_format_info(original_probe).get("container")
            manifest = build_manifest(original_probe, original_path=input_path,
                                      original_container=container)
        else:
            # A later job. The manifest is NOT rebuilt from input_path —
            # that file is already a processed version, and treating it as
            # the original is exactly the bug this design replaces: each
            # job would anchor to the last one's output, so reverting
            # would only ever undo the most recent job while the earlier
            # losses stayed gone.
            manifest = existing.manifest
            container = manifest.get("container")

        matches = match_streams(manifest, produced_probe)
        lost = [stream for stream, index in matches if index is None]

        # IMPOSSIBLE, not UNAVAILABLE — see the module docstring.
        if not lost:
            logger.info(
                "Job %d: nothing is missing from the original, no revert "
                "point needed", job_id,
            )
            return None, None

        # Where each lost stream can be read from RIGHT NOW. Streams this
        # job destroyed are still in the file it was handed (input 0, at
        # the index the previous capture recorded). Streams an earlier job
        # destroyed exist only in the previous sidecar (input 1).
        inputs = [input_path]
        if extend:
            inputs.append(existing.sidecar_path)

        sources = _plan_sources(lost, has_previous_sidecar=extend)

        sidecar = sidecar_path_for(file_id, job_id)
        # Staged through a .part like every other write in this codebase.
        #
        # It was not, and that made two things untrue at once. The startup
        # orphan sweep claims to collect crashed sidecar writes from the
        # recycle volume, but nothing ever put a .part there, so it swept
        # a volume that could not contain what it was looking for. And a
        # crash mid-write left a TRUNCATED file at the real sidecar path,
        # which the sweep deliberately will not touch and the retention
        # orphan pass only collects an hour later.
        #
        # Writing to .part and renaming means a partial sidecar is always
        # named as one, and a complete sidecar appears atomically.
        staged = sidecar + ".part"
        try:
            cmd = build_sidecar_command(inputs, staged, sources)
        except SidecarUnsupported as exc:
            logger.info(
                "Job %d: no revert point possible for %s — %s",
                job_id, input_path, exc,
            )
            return None, None

        await _run(cmd)

        # Check the sidecar we just wrote actually has the layout the
        # indices assume. sidecar_index is positional, and _plan_sources
        # orders attachments last so map order and file order coincide —
        # but that is a claim about the Matroska muxer, not a guarantee,
        # and it is the exact claim that was wrong before. A mismatch means
        # every index past the divergence points at the wrong stream, which
        # produces a revert that succeeds and rebuilds the file with tracks
        # and metadata shuffled. Refusing is the only safe answer.
        try:
            written = await _off_loop(probe_file, staged)
        except ProbeError as exc:
            raise _Unavailable(f"Could not read the sidecar just written: {exc}") from exc

        expected = [stream.get("type") for stream, _i, _x in sources]
        actual = [s.get("codec_type") for s in written.get("streams", [])]
        if expected != actual:
            raise _Unavailable(
                f"The sidecar was written with a different stream order than "
                f"requested ({actual} rather than {expected}); refusing to "
                f"record indices that would point at the wrong streams."
            )

        # Re-resolve where every original stream lives, now that both the
        # sidecar and the processed file have changed.
        #
        # Restore could re-derive this by matching the manifest against the
        # processed file again, but it should not have to: that file may
        # have been re-tagged, re-scanned or partially rewritten by then,
        # and re-running fuzzy matching against a moved target is exactly
        # how a revert puts the wrong track back. Resolved once, here,
        # where both files are known-good and one line from being swapped.
        #
        # sidecar_index is positional: build_sidecar_command maps `sources`
        # in order, so the nth entry is output stream n. Stale annotations
        # from the previous capture are cleared rather than left to be
        # read as if they still pointed somewhere real.
        # Renamed only once the layout check has passed, so a sidecar at
        # the real path is always one that was written completely AND
        # verified.
        try:
            await _off_loop(os.replace, staged, sidecar)
        except OSError as exc:
            raise _Unavailable(f"Could not stage the sidecar into place: {exc}") from exc

        _reannotate(matches, sources)

        try:
            size = await _off_loop(os.path.getsize, sidecar)
        except OSError as exc:
            raise _Unavailable(f"Sidecar vanished after being written: {exc}") from exc

        logger.info(
            "Job %d: revert point %s (%d stream(s) from the original, "
            "%.1f MB) → %s",
            job_id,
            "extended" if extend else ("replaced" if existing else "captured"),
            len(lost), size / 1024 / 1024, sidecar,
        )
        return CapturedRevertPoint(
            sidecar_path=sidecar,
            sidecar_size=size,
            manifest_json=json.dumps(manifest),
            original_path=manifest.get("path") or input_path,
            original_container=container,
            replaces_point_id=existing.point_id if existing else None,
            replaces_sidecar_path=existing.sidecar_path if existing else None,
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
