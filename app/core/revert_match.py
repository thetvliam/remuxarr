"""
Matching a detached revert point back to a file, by hand.

A revert point loses its file whenever the scanner stops seeing that path
— usually a rename, since Sonarr changing a naming scheme moves a whole
library at once. The point survives (see models.RevertPoint.file_id), but
nothing automatic can reattach it: the scanner has no move detection, and
guessing from filenames is exactly the sort of thing that would quietly
put one episode's tracks into another.

So the user proposes the match and the code checks it.

Two tiers, and the distinction is the whole point
-------------------------------------------------
EXACT — the candidate's size and mtime are what the point recorded. That
is not a heuristic: the file is byte-for-byte what the job left behind, so
this is provably the same file under a new name. The rename case lands
here, which means the everyday reason for detaching is also the safe one.
Nothing is being bypassed; the user is supplying the file identity the
scanner lost, and the ordinary fingerprint then confirms it.

COMPATIBLE — the fingerprint differs, but every stream the manifest says
should still be in the file is present and matching, and the runtimes
agree. That is consistent with being the right file and does not prove it.
A re-encode at the same resolution, or a different release of the same
episode, can look exactly like this. Attaching one requires explicit
confirmation, because reverting afterwards would mux the stored tracks in
regardless and produce a file that plays and is quietly wrong.

Anything else is refused with the specific reason, because "no" without a
reason sends people to try the next point in the list at random.

Attaching re-establishes the fingerprint from the file as it is now, so
every later revert runs with its full sentinel intact. The manual step
happens once, under explicit consent, rather than leaving a point that
skips the check forever.
"""

import json
import logging
import os
from dataclasses import dataclass, field

from app.core.probe import ProbeError, probe_file
from app.core.revert import match_streams

logger = logging.getLogger(__name__)


# Two files whose runtimes differ by more than this are not the same
# content. Generous on purpose: a remux can shift the reported duration by
# a frame or two, and container overhead differs between MKV and MP4. The
# check is meant to separate different releases, not to police rounding.
DURATION_TOLERANCE_SECONDS = 2.0

EXACT = "exact"
COMPATIBLE = "compatible"
INCOMPATIBLE = "incompatible"


@dataclass
class MatchAssessment:
    tier: str
    reasons: list[str] = field(default_factory=list)
    # Original stream index → index in the candidate, for the streams that
    # should still be present. Recomputed against the candidate rather
    # than trusted from the manifest, because the annotations were
    # resolved against a file we can no longer assume this is.
    resolved: dict[int, int] = field(default_factory=dict)


def assess(point, candidate_path: str, candidate_probe: dict) -> MatchAssessment:
    """
    Decide whether `candidate_path` can be the file this point belongs to.
    """
    try:
        manifest = json.loads(point.manifest)
    except (TypeError, ValueError) as exc:
        return MatchAssessment(INCOMPATIBLE,
                               [f"This revert point's manifest is unreadable: {exc}"])

    reasons: list[str] = []

    # ── Every stream that should still be there, must still be there ────
    #
    # This is not only an identity check: a stream the manifest expects in
    # the file and which is not in the sidecar either has nowhere to come
    # from, and build_restore_command would refuse anyway. Checking here
    # turns that into an explanation before the user commits.
    matches = match_streams(manifest, candidate_probe)
    resolved: dict[int, int] = {}
    missing = []
    for stream, index in matches:
        if index is not None:
            resolved[stream["index"]] = index
        elif stream.get("sidecar_index") is None:
            missing.append(stream)

    if missing:
        described = ", ".join(
            f"{s.get('type')} {s.get('codec')}"
            + (f" ({s['language']})" if s.get("language") else "")
            for s in missing[:3]
        )
        reasons.append(
            f"{len(missing)} stream(s) the original still had are not in this "
            f"file: {described}. This is not the same content."
        )

    # ── Runtime ─────────────────────────────────────────────────────────
    expected = manifest.get("duration")
    actual = _duration(candidate_probe)
    if expected and actual and abs(expected - actual) > DURATION_TOLERANCE_SECONDS:
        reasons.append(
            f"Runtime differs by {abs(expected - actual):.1f}s "
            f"({expected:.1f}s expected, {actual:.1f}s found) — this looks "
            f"like a different release."
        )

    if reasons:
        return MatchAssessment(INCOMPATIBLE, reasons)

    # ── Fingerprint ─────────────────────────────────────────────────────
    try:
        stat = os.stat(candidate_path)
    except OSError as exc:
        return MatchAssessment(INCOMPATIBLE, [f"{candidate_path}: {exc}"])

    if (point.processed_size == stat.st_size
            and point.processed_mtime == stat.st_mtime):
        return MatchAssessment(EXACT, resolved=resolved)

    return MatchAssessment(
        COMPATIBLE,
        ["This file is not byte-for-byte what the job produced, so it cannot "
         "be confirmed as the same one. Its streams and runtime are "
         "consistent with the original, but a re-encode or a different "
         "release of the same episode would look the same."],
        resolved=resolved,
    )


def _duration(probe_data: dict) -> float | None:
    try:
        return float((probe_data.get("format") or {}).get("duration"))
    except (TypeError, ValueError):
        return None


@dataclass
class AttachOutcome:
    success: bool
    tier: str | None = None
    error: str | None = None
    reasons: list[str] = field(default_factory=list)


def attach(point_id: int, file_id: int, *, confirm_mismatch: bool = False
           ) -> AttachOutcome:
    """
    Reattach a detached revert point to a media file.

    Refuses a COMPATIBLE match unless confirm_mismatch is set. That flag
    is the user having been shown what could not be verified and saying
    yes anyway — it must come from an explicit choice, never a default,
    because the failure it permits is a file that plays and is wrong.
    """
    from app.database.models import MediaFile, RevertPoint
    from app.database.session import SessionLocal

    with SessionLocal() as db:
        point = db.get(RevertPoint, point_id)
        if point is None:
            return AttachOutcome(False, error="That revert point no longer exists.")
        if point.file_id is not None:
            return AttachOutcome(
                False,
                error="That revert point is already attached to a file.",
            )

        media = db.get(MediaFile, file_id)
        if media is None:
            return AttachOutcome(False, error="That file is no longer tracked.")
        if not os.path.exists(media.path):
            return AttachOutcome(False, error=f"{media.path} is not on disk.")

        try:
            candidate_probe = probe_file(media.path)
        except ProbeError as exc:
            return AttachOutcome(False, error=f"Could not read {media.path}: {exc}")

        assessment = assess(point, media.path, candidate_probe)

        if assessment.tier == INCOMPATIBLE:
            return AttachOutcome(False, tier=INCOMPATIBLE,
                                 error="This revert point does not belong to "
                                       "this file.",
                                 reasons=assessment.reasons)

        if assessment.tier == COMPATIBLE and not confirm_mismatch:
            return AttachOutcome(False, tier=COMPATIBLE,
                                 error="This match could not be confirmed.",
                                 reasons=assessment.reasons)

        # Re-resolve the annotations against the file actually being
        # attached. Carried over unchanged they would describe stream
        # positions in a file we have just stopped assuming this is.
        manifest = json.loads(point.manifest)
        for stream in manifest["streams"]:
            stream["processed_index"] = assessment.resolved.get(stream["index"])

        stat = os.stat(media.path)
        point.manifest = json.dumps(manifest)
        point.file_id = media.id
        point.detached_at = None
        # Fingerprint taken from the file as it is NOW, so every later
        # revert runs with a full sentinel. The manual step is spent here
        # rather than leaving a point that skips the check forever.
        point.processed_size = stat.st_size
        point.processed_mtime = stat.st_mtime
        db.commit()

        logger.info(
            "Revert point %d attached to %s (%s match)",
            point_id, media.path, assessment.tier,
        )
        return AttachOutcome(True, tier=assessment.tier,
                             reasons=assessment.reasons)


# MediaFile.size/mtime are reset to these by the queue's dismissal routes.
# A row carrying them is not describing a file on disk, so it can never be
# a fingerprint match — and would otherwise match every OTHER dismissed row
# exactly, which is the worst possible false positive.
_DISMISSED_SIZE = -1
_DISMISSED_MTIME = -1.0


def find_candidates(point_id: int, limit: int = 20) -> dict:
    """
    Files this detached revert point might belong to.

    Two kinds, and the first is not a guess.

    EXACT — a media file whose recorded size AND mtime are what the point
    fingerprinted. A rename does not touch a byte, so the renamed file
    still carries the fingerprint of the file the job produced. This is
    conclusive, and it is the common case, which is what makes the whole
    detach-on-rename design work rather than merely survive.

    NEARBY — files in the directory the original lived in. A pure guess,
    offered because a rename that also moves the file between libraries
    is rare, so this is a short list rather than the whole library. Every
    one still goes through assess() on attach.

    Deliberately cheap: database only, no probing. A library-wide scan
    that ffprobes every file to answer "which of these is it" would take
    minutes and is unnecessary — the fingerprint already answers it for
    the case that matters, and attach probes the one file chosen.
    """
    from app.database.models import MediaFile, RevertPoint
    from app.database.session import SessionLocal

    with SessionLocal() as db:
        point = db.get(RevertPoint, point_id)
        if point is None or point.file_id is not None:
            return {"exact": [], "nearby": []}

        exact = []
        if (point.processed_size is not None
                and point.processed_mtime is not None
                and point.processed_size != _DISMISSED_SIZE
                and point.processed_mtime != _DISMISSED_MTIME):
            exact = (
                db.query(MediaFile)
                .filter(MediaFile.size == point.processed_size,
                        MediaFile.mtime == point.processed_mtime)
                .limit(limit)
                .all()
            )

        exact_ids = {m.id for m in exact}
        directory = os.path.dirname(point.original_path or "")
        nearby = []
        if directory:
            nearby = [
                m for m in db.query(MediaFile)
                             .filter(MediaFile.directory == directory)
                             .limit(limit + len(exact_ids))
                             .all()
                if m.id not in exact_ids
            ][:limit]

        def describe(media):
            return {"id": media.id, "path": media.path,
                    "filename": media.filename, "size": media.size}

        return {"exact": [describe(m) for m in exact],
                "nearby": [describe(m) for m in nearby]}


def list_detached() -> list[dict]:
    """
    Detached revert points, newest first, described well enough to be
    recognised.

    original_path is what identifies one to a human — it is the name the
    file had when Remuxarr last saw it, which is exactly what a rename
    changed. The track summary is there because a user who has renamed a
    whole season needs something beyond the old name to tell two points
    apart.
    """
    from app.database.models import RevertPoint
    from app.database.session import SessionLocal

    out = []
    with SessionLocal() as db:
        points = (
            db.query(RevertPoint)
            .filter(RevertPoint.file_id.is_(None))
            .order_by(RevertPoint.detached_at.desc())
            .all()
        )
        for point in points:
            try:
                manifest = json.loads(point.manifest)
            except (TypeError, ValueError):
                manifest = {"streams": []}

            stored = [s for s in manifest.get("streams", [])
                      if s.get("sidecar_index") is not None]
            out.append({
                "id": point.id,
                "original_path": point.original_path,
                "original_filename": os.path.basename(point.original_path or ""),
                "detached_at": point.detached_at,
                "created_at": point.created_at,
                "sidecar_size": point.sidecar_size,
                "sidecar_present": bool(point.sidecar_path
                                        and os.path.exists(point.sidecar_path)),
                "duration": manifest.get("duration"),
                "stored_tracks": [
                    {"type": s.get("type"), "codec": s.get("codec"),
                     "language": s.get("language"), "title": s.get("title")}
                    for s in stored
                ],
            })
    return out
