"""
AC3 Forge
=========
On-demand "add AC3 5.1 alongside AAC 5.1" per-file transcoding.

Key differences from the main remux pipeline
---------------------------------------------
• The original AAC 5.1 track is KEPT — not replaced.
• A new AC3 5.1 track is APPENDED to the end of the audio stream list.
• The operation is reversible: the appended AC3 carries a marker
  (FORGE_AC3_HANDLER_NAME), and undo re-locates it by that marker against
  a fresh probe, or by its being the LAST audio track where the add left
  no marker. The add-time index IS stored (Ac3ForgeJob.audio_track_count)
  but undo does NOT use it — see resolve_forge_ac3_for_undo for the false
  "undone" that trusting it produced, and for the AC3 the file already
  had that the last-track rule alone removed.

FFmpeg strategy
---------------
  Add :  -map 0 -c copy                       keep everything as-is
          -map 0:{aac_stream_index}             also map the AAC 5.1 again
          -c:a:{audio_track_count} ac3          transcode that copy to AC3
          -b:a:{audio_track_count} 640k
          -ac:{audio_track_count}  6

  Undo:  -map 0                                keep everything
          -map -0:a:{ac3_audio_output_index}    REMOVE the AC3 we appended,
                                                at its CURRENT position
          -c copy
"""
import logging
import os
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from app.core.timeutil import utcnow

from sqlalchemy.orm import Session

from app.config import settings as app_settings
from app.core.ffmpeg import (
    KEEP_STREAM_FLAGS, CoverArt, cover_attach_args, find_matroska_covers,
)
from app.core.probe import ProbeError, extract_tracks, is_quicktime, probe_file
from app.database.session import SessionLocal, get_app_settings
from app.core.subprocess_runner import (
    STAGING_ACTION,
    StagedOutput,
    parse_out_time_seconds,
    probe_duration,
    run_staged_subprocess,
    staging_percent,
    subprocess_percent,
)

logger = logging.getLogger(__name__)

# Written as the handler_name of the AC3 an add appends, so that undo can
# tell it from an AC3 5.1 the file already had. Without it undo took the
# last audio track for the forged one, and once a job had dropped the
# forged AC3 that was a track of the original: undo removed it, recorded
# "undone", and on a file with no other audio left video alone.
#
# handler_name because it is the stream tag that survives every rewrite the
# marker must outlive, measured with the FFmpeg 8.1 the image ships: a job
# from Matroska to Matroska, Matroska to MP4 and MP4 to MP4, and a revert,
# which writes back the tags it recorded. A title was lost whenever an MP4
# was rewritten (titles are now written back for MP4: ffmpeg's title_of and
# _title_args here), and a tag of our own is lost when Matroska becomes MP4. AVI,
# MPEG-TS and ASF keep no handler_name at all, which is why an add only
# counts as marked once the probe after it has found the marker
# (finish_forge_job).
#
# Worded as a label rather than an identifier, in case a player shows it.
# None of probe's forced, SDH or dub patterns may match it: they are
# applied to every tag value.
FORGE_AC3_HANDLER_NAME = "AC3 5.1 (Remuxarr)"

_FORMAT_MAP = {
    "mkv":  "matroska",
    "mp4":  "mp4",
    "avi":  "avi",
    # No "m2ts" entry — see ffmpeg.py's own _CONTAINER_FORMAT comment for
    # why this is genuinely, provably unreachable, not just unlikely.
    "ts":   "mpegts",
    "wmv":  "asf",
    # Unreachable defensive key — _normalise_container can never return
    # "webm" (see ffmpeg.py's _CONTAINER_FORMAT comment). Kept for intent.
    "webm": "webm",
}


# ── Data classes ───────────────────────────────────────────────────────────────

@dataclass
class ForgeProgress:
    percent:      float
    current_time: float
    speed:        str
    action:       str


@dataclass
class ForgeResult:
    success:     bool
    output_path: str | None
    output_size: int | None
    error:       str | None


# ── Command builders ───────────────────────────────────────────────────────────

def _title_args(fmt: str, titles: dict[int, str] | None) -> list[str]:
    """
    -metadata arguments writing each title back, for an MP4 or QuickTime
    output.

    Both forge commands copy every stream, and in an MP4 FFmpeg reads a
    title as the tag "name" but writes one only from "title", so an add or
    an undo on an MP4 dropped every track's title. `titles` maps an output
    stream index to its title (titles_after_forge). The QuickTime muxer
    (ffmpeg.output_muxer) is the same code, libavformat/movenc.c, and drops
    them the same way: once a QuickTime file was written with it, keying
    this on "mp4" alone lost every title again. Matroska keeps titles
    through a copy, so nothing is written for it.
    """
    if fmt not in ("mp4", "mov") or not titles:
        return []
    args: list[str] = []
    for index, title in sorted(titles.items()):
        args += [f"-metadata:s:{index}", f"title={title}"]
    return args


def _cover_unmaps(covers: Sequence[CoverArt]) -> list[str]:
    """Negative maps leaving each Matroska cover out of a "-map 0" copy."""
    args: list[str] = []
    for cover in covers:
        args += ["-map", f"-0:{cover.stream_index}"]
    return args


def titles_after_forge(tracks: list[dict],
                       removed_audio_index: int | None = None) -> dict[int, str]:
    """
    Where each titled track will be in a forge command's output, with its
    title.

    Titles are written only for an MP4 or a QuickTime file (_title_args),
    which has no attachments or Matroska covers, so both commands map its streams in
    order and a stream keeps its index; an undo removes one audio stream,
    and every stream after it moves down one. `removed_audio_index` is that
    stream's position among the audio tracks (the -0:a:N of
    build_undo_command). The AC3 an add appends goes last and has no title.
    """
    removed: int | None = None
    if removed_audio_index is not None:
        audio = sorted(t["stream_index"] for t in tracks if t["track_type"] == "audio")
        if 0 <= removed_audio_index < len(audio):
            removed = audio[removed_audio_index]
    out: dict[int, str] = {}
    for t in tracks:
        index, title = t["stream_index"], t.get("title")
        if not title or index == removed:
            continue
        out[index - 1 if removed is not None and index > removed else index] = title
    return out


def _muxer(container: str, quicktime: bool) -> str:
    """The muxer for a container _FORMAT_MAP knows, QuickTime told apart
    as ffmpeg.output_muxer does."""
    fmt = _FORMAT_MAP[container]
    return "mov" if quicktime and fmt == "mp4" else fmt


def build_add_ac3_command(
    input_path:        str,
    temp_path:         str,
    aac_stream_index:  int,     # global ffprobe stream index of the AAC 5.1 track
    audio_track_count: int,     # number of audio tracks BEFORE adding AC3
    container:         str = "mkv",
    add_faststart:     bool = True,
    titles:            dict[int, str] | None = None,
    covers:            Sequence[CoverArt] = (),
    quicktime:         bool = False,
) -> list[str]:
    """
    Copy all existing streams unchanged, then append a new AC3 5.1 track
    transcoded from the AAC 5.1 stream at aac_stream_index.

    quicktime says the file is a QuickTime movie, written back with
    FFmpeg's QuickTime muxer so it stays one (ffmpeg.output_muxer).

    The new AC3 always ends up at output audio index audio_track_count
    (0-based), making the undo index deterministic.

    Attachments are mapped after the AC3, not with the rest of "-map 0":
    an attachment copied ahead of an encoded stream makes the Matroska
    muxer stop with "Received a packet for an attachment stream" (FFmpeg
    8.1 and 6.1 alike), so every add on an MKV with fonts used to fail.
    The main pipeline maps them last too (build_ffmpeg_command).

    covers are the file's Matroska cover images, copied out by the worker
    (copy_out_cover_art). "-map 0" would take each as a real still-image
    video track, so each is left out of the map and attached again.
    """
    # Hard-fail on unknown containers rather than silently defaulting to
    # matroska — same file-corruption pattern just fixed in
    # ffmpeg.build_ffmpeg_command (see the comment there for the full
    # story). Note this map is even narrower than that one (no "mov"
    # entry), so without this a forge job on a .mov would have written
    # Matroska bytes into the .mov in place. Raising here is caught by
    # worker._run_forge_job's job-level exception handler and becomes a
    # visible failed forge job, before FFmpeg ever starts.
    if container not in _FORMAT_MAP:
        raise ValueError(
            f"Unsupported container {container!r} for AC3 Forge — "
            f"refusing to guess the mux format (supported: "
            f"{', '.join(sorted(_FORMAT_MAP))})"
        )
    fmt     = _muxer(container, quicktime)
    ac3_idx = audio_track_count  # output audio index for the new stream

    cmd = [
        app_settings.FFMPEG_PATH,
        "-i", input_path,
        "-y", "-v", "error", "-nostats", "-progress", "pipe:1",
        # ── Keep every existing stream unchanged ───────────────────────────
        # Attachments and covers come back after the AC3; see the docstring.
        "-map", "0",
        "-map", "-0:t",
        *_cover_unmaps(covers),
        "-c", "copy",
        # ── Map the AAC 5.1 a second time and transcode it to AC3 ─────────
        "-map", f"0:{aac_stream_index}",
        f"-c:a:{ac3_idx}", "ac3",
        f"-b:a:{ac3_idx}", "640k",
        f"-ac:{ac3_idx}", "6",
        # What tells this AC3 apart from one the file already had; it
        # replaces any handler name the copy inherits from the AAC. See
        # FORGE_AC3_HANDLER_NAME.
        f"-metadata:s:a:{ac3_idx}", f"handler_name={FORGE_AC3_HANDLER_NAME}",
        # Every copied stream keeps its own flags; see KEEP_STREAM_FLAGS.
        *KEEP_STREAM_FLAGS,
        # The AC3 takes its flags from the AAC it is encoded from; all but
        # default, so it never makes a second default audio track. With
        # two, the MP4 muxer gives every track its own alternate group, so the
        # AAC and the AC3 are both enabled and no longer alternatives.
        # Keeping the copy through the next scan is decision.py's work, as
        # Keep Default Audio no longer keeps it.
        f"-disposition:a:{ac3_idx}", "-default",
        "-map", "0:t?",
        *cover_attach_args(covers),
        *_title_args(fmt, titles),
        # ── Output ────────────────────────────────────────────────────────
        "-f", fmt,
    ]

    # Apply +faststart when writing MP4, unless add_faststart_to_mp4 is off.
    # With the setting ON this matters because forge rewrites the whole
    # container: without the flag every file forge touches loses faststart
    # (even if the original had it), and the next library scan re-queues it
    # purely to re-add faststart — wasted work.
    #
    # With the setting OFF the flag is suppressed here too. Forge is a
    # separate pipeline from the main remux, but the setting reads as a
    # global policy for this application, so leaving forge emitting the flag
    # would mean "off" still produced faststart output for anyone who uses
    # AC3 forge — the same partial-off behaviour that made the setting
    # ineffective before.
    if container == "mp4" and add_faststart:
        cmd += ["-movflags", "+faststart"]

    cmd.append(temp_path)
    return cmd


def build_undo_command(
    input_path:            str,
    temp_path:             str,
    ac3_audio_output_index: int,   # resolved at UNDO time by resolve_forge_ac3_for_undo
    container:             str = "mkv",
    add_faststart:         bool = True,
    titles:                dict[int, str] | None = None,
    covers:                Sequence[CoverArt] = (),
    quicktime:             bool = False,
) -> list[str]:
    """
    Copy all streams EXCEPT the AC3 track added by the forge. quicktime as
    for build_add_ac3_command.
    Uses FFmpeg's negative map syntax: -map -0:a:N removes output audio
    stream N from the selection. covers are left out and attached again,
    as for build_add_ac3_command; a copy alone does not trip the muxer over
    attachments, so they stay where "-map 0" puts them.

    ac3_audio_output_index comes from resolve_forge_ac3_for_undo's fresh
    probe of the file, NOT from Ac3ForgeJob.audio_track_count — the
    stored add-time index goes stale whenever the main pipeline drops
    audio tracks between add and undo (see that function's docstring for
    the full failure modes the stale index caused).
    """
    # Same hard-fail as build_add_ac3_command — see the comment there.
    if container not in _FORMAT_MAP:
        raise ValueError(
            f"Unsupported container {container!r} for AC3 Forge undo — "
            f"refusing to guess the mux format (supported: "
            f"{', '.join(sorted(_FORMAT_MAP))})"
        )
    fmt = _muxer(container, quicktime)

    cmd = [
        app_settings.FFMPEG_PATH,
        "-i", input_path,
        "-y", "-v", "error", "-nostats", "-progress", "pipe:1",
        "-map", "0",
        "-map", f"-0:a:{ac3_audio_output_index}",   # strip the AC3 we added
        *_cover_unmaps(covers),
        "-c", "copy",
        *KEEP_STREAM_FLAGS,
        *cover_attach_args(covers),
        *_title_args(fmt, titles),
        "-f", fmt,
    ]

    # Same faststart rule as build_add_ac3_command — undo also rewrites the
    # whole file, so it re-applies faststart for the same reason when the
    # setting is on, and suppresses it entirely when off.
    if container == "mp4" and add_faststart:
        cmd += ["-movflags", "+faststart"]

    cmd.append(temp_path)
    return cmd


# ── Async executor ─────────────────────────────────────────────────────────────

async def run_forge_command(
    cmd:               list[str],
    input_path:        str,
    output_path:       str,
    temp_path:         str,
    action_label:      str,
    progress_callback: Callable[[ForgeProgress], Awaitable[None]] | None = None,
    timeout_seconds:   float | None = None,
    before_staging:    Callable[[str], Awaitable[str | None]] | None = None,
) -> ForgeResult:
    """
    Run a forge FFmpeg command with real-time progress tracking.

    before_staging, if given, is awaited with the path of the finished
    output before it replaces the file, exactly as for a queue job: a
    non-None return aborts the run with the file untouched. The worker
    uses it to capture a revert point.

    Writes to temp_path, then atomically renames to output_path on success.
    Always cleans up the temp file on failure.

    Thin adapter over run_staged_subprocess() in subprocess_runner.py.
    This function only handles what's forge-specific: building the
    ForgeProgress objects (note: ForgeProgress.action vs FFmpegProgress's
    .current_action — different field names, different dataclass) and
    translating the result into a ForgeResult. The subprocess spawn/drain/
    stage/cleanup machinery is shared with the main remux pipeline.

    NOTE: stderr_tail_lines=20 here vs 30 in the main pipeline — preserved
    intentionally, not unified.

    timeout_seconds was previously never wired through at all — forge
    jobs got none of the job_timeout_minutes protection the main remux
    pipeline has always had, meaning a hung forge job (e.g. against a
    genuinely broken source file) could run indefinitely with no
    recovery.
    """
    duration = await probe_duration(input_path)

    async def on_progress_line(progress_kv: dict[str, str]) -> None:
        if not progress_callback or not duration:
            return
        secs = parse_out_time_seconds(progress_kv)
        pct  = min(100.0, secs / duration * 100)
        await progress_callback(ForgeProgress(
            percent=subprocess_percent(pct),
            current_time=secs,
            speed=progress_kv.get("speed", "?x"),
            action=action_label,
        ))

    async def on_staging(fraction: float) -> None:
        # Same two-phase bar as the remux pipeline, and deliberately not the
        # same function: this builds a ForgeProgress, whose label field is
        # .action where FFmpegProgress uses .current_action. The split
        # itself is shared, in subprocess_runner.
        #
        # A forge job re-encodes audio rather than copying it, so FFmpeg
        # takes far longer here than in a remux — and the copy that follows
        # is the same size and the same speed either way, which is exactly
        # why a fixed share is the honest thing to give it.
        await progress_callback(ForgeProgress(
            percent=staging_percent(fraction),
            current_time=duration or 0.0,
            speed="",
            action=STAGING_ACTION,
        ))

    async def _before_staging() -> str | None:
        return await before_staging(temp_path)

    result = await run_staged_subprocess(
        cmd,
        [StagedOutput(temp_path=temp_path, final_path=output_path)],
        on_progress_line=on_progress_line,
        stderr_tail_lines=20,
        timeout_seconds=timeout_seconds,
        before_staging=_before_staging if before_staging else None,
        on_staging_progress=on_staging if progress_callback else None,
    )

    if not result.success:
        # No exit code means the run was stopped at its time limit: that is
        # the only result run_staged_subprocess returns without one. Logged
        # with its reason; it used to be either skipped or formatted with
        # %d, which raised inside logging and left a traceback instead.
        if result.returncode is None:
            logger.error("Forge FFmpeg failed: %s", result.error)
        elif result.returncode != 0:
            logger.error(
                "Forge FFmpeg failed (rc=%d):\n%s", result.returncode, result.error
            )
        return ForgeResult(
            success=False, output_path=None, output_size=None, error=result.error
        )

    size = os.path.getsize(output_path)
    logger.info("Forge success → %s (%.1f MB)", output_path, size / 1024 / 1024)
    return ForgeResult(
        success=True, output_path=output_path, output_size=size, error=None
    )


# ── Sync DB helpers ────────────────────────────────────────────────────────────
# All functions below run inside thread-pool executors (called via
# loop.run_in_executor) so they must be regular synchronous functions.

def get_candidates(
    db:     Session,
    search: str = "",
    limit:  int = 50,
    offset: int = 0,
) -> dict:
    """
    Return a paginated page of MediaFiles that have at least one AAC 5.1
    audio track and no active or completed forge job.

    When searching, results are ordered by relevance (filename starts with
    search term first) so the most likely match surfaces immediately.
    Without a search term, results are alphabetical by filename.

    Returns {"total": N, "items": [...]}.
    """
    from app.database.models import Ac3ForgeJob, MediaFile, Track
    from sqlalchemy import case as sa_case

    # File IDs that have an AAC 5.1 track
    has_aac51 = (
        db.query(Track.file_id)
        .filter(
            Track.track_type == "audio",
            Track.codec      == "aac",
            Track.channels   == 6,
        )
        .scalar_subquery()
    )

    # File IDs with an active forge job, OR one whose outcome means the file
    # currently HAS an AC3 track that shouldn't be touched again:
    #   pending/processing  — job in flight
    #   success              — AC3 successfully added and present
    #   undo_pending         — undo in flight
    #   undo_failed          — undo attempt failed; AC3 track is STILL present
    #                          (the undo never completed) — if this status is
    #                          NOT excluded here, the file incorrectly looks
    #                          like a fresh candidate and clicking "+ADD AC3"
    #                          on it creates a duplicate/overwritten AC3 track
    #                          on a file that already has one.
    #
    # Deliberately NOT excluded:
    #   failed   — a failed ADD attempt never produced an AC3 track, so the
    #              file legitimately has none and should remain a valid
    #              candidate (this is how a failed add gets retried, since
    #              there's no separate retry button for it in the UI).
    #   undone   — a successful undo removed the AC3 track, so the file
    #              should become a candidate again if the user wants to
    #              re-add it.
    is_forged = (
        db.query(Ac3ForgeJob.file_id)
        .filter(Ac3ForgeJob.status.in_(
            ["pending", "processing", "success", "undo_pending", "undo_failed"]
        ))
        .scalar_subquery()
    )

    query = (
        db.query(MediaFile)
        .filter(
            MediaFile.id.in_(has_aac51),
            ~MediaFile.id.in_(is_forged),
        )
    )

    if search.strip():
        s = search.strip()
        # icontains/istartswith with autoescape rather than ilike
        # f-string patterns — see history.py's list_history for the
        # reasoning. This endpoint had no search test at all, which is
        # how it kept the same bug as its two siblings, and the
        # consequence here is the worst of the three: the next thing the
        # user does with a row is click ADD AC3, so a term whose `_`
        # quietly matched a different release offers to rewrite that
        # file's audio.
        query = query.filter(MediaFile.filename.icontains(s, autoescape=True))
        # Relevance ordering: filename-starts-with ranks above mid-word match
        relevance = sa_case(
            (MediaFile.filename.istartswith(s, autoescape=True),     0),
            (MediaFile.filename.icontains(" " + s, autoescape=True), 1),
            else_=2,
        )
        order_clause = [relevance, MediaFile.filename]
    else:
        order_clause = [MediaFile.filename]

    total = query.count()
    files = query.order_by(*order_clause).offset(offset).limit(limit).all()

    # Fetch every audio track for the whole page in ONE query, then group
    # in Python — instead of two extra queries per file (aac_track +
    # audio_count), i.e. 2N+1 for a 50-row page. Ordered by stream_index
    # so "first AAC 5.1" is deterministic and matches what the previous
    # per-file .first() returned (tracks are inserted in stream order, so
    # SQLite's implicit rowid order already ≈ stream_index order).
    from collections import defaultdict

    file_ids = [f.id for f in files]
    audio_by_file: dict[int, list] = defaultdict(list)
    if file_ids:
        audio_rows = (
            db.query(Track)
            .filter(Track.file_id.in_(file_ids), Track.track_type == "audio")
            .order_by(Track.file_id, Track.stream_index)
            .all()
        )
        for t in audio_rows:
            audio_by_file[t.file_id].append(t)

    result = []
    for f in files:
        file_audio = audio_by_file.get(f.id, [])
        audio_count = len(file_audio)
        aac_track = next(
            (t for t in file_audio if t.codec == "aac" and t.channels == 6),
            None,
        )
        # Defensive: the outer query already restricts to files with an
        # AAC 5.1 track, so this should always be truthy — but keep the
        # guard in case a track row changed between the two queries.
        if not aac_track:
            continue

        result.append({
            "id":                f.id,
            "filename":          f.filename,
            "path":              f.path,
            "size":              f.size,
            "duration":          f.duration,
            "container":         f.container,
            "aac_stream_index":  aac_track.stream_index,
            "audio_track_count": audio_count,
            "aac_track": {
                "stream_index":   aac_track.stream_index,
                "language":       aac_track.language,
                "channels":       aac_track.channels,
                "channel_layout": aac_track.channel_layout,
                "is_default":     aac_track.is_default,
            },
        })

    return {"total": total, "items": result}


def queue_forge_job(db: Session, file_id: int):
    """Create and persist a pending forge job. Returns the new Ac3ForgeJob."""
    from app.database.models import Ac3ForgeJob, MediaFile, Track

    media = db.get(MediaFile, file_id)
    if not media:
        raise ValueError(f"MediaFile {file_id} not found")

    aac_track = (
        db.query(Track)
        .filter(
            Track.file_id    == file_id,
            Track.track_type == "audio",
            Track.codec      == "aac",
            Track.channels   == 6,
        )
        # Ordered for the same reason get_candidates orders its own scan:
        # "the first AAC 5.1 track" has to mean the same thing in both places.
        # This was a bare .first(), i.e. whatever the query planner returned —
        # in practice rowid order, which usually matches stream order but is
        # not guaranteed to, and stops matching after rows are deleted and
        # re-inserted by a rescan.
        #
        # A mismatch is not cosmetic: the candidate list shows the user one
        # track's language and channel layout, and the job then forges a
        # different track. On a file with an English and a commentary AAC 5.1,
        # that means an AC3 track built from the commentary.
        .order_by(Track.stream_index)
        .first()
    )
    if not aac_track:
        raise ValueError(f"No AAC 5.1 track found for file {file_id}")

    audio_count = (
        db.query(Track)
        .filter(Track.file_id == file_id, Track.track_type == "audio")
        .count()
    )

    job = Ac3ForgeJob(
        file_id           = file_id,
        status            = "pending",
        aac_stream_index  = aac_track.stream_index,
        audio_track_count = audio_count,
        original_size     = media.size,
    )
    db.add(job)
    db.commit()
    db.refresh(job)
    return job


def claim_next_forge_job() -> int | None:
    """
    Claim the next pending or undo_pending forge job.
    Returns the job ID, or None if the forge queue is empty.

    Not one for a file another writer holds — a revert in progress, or a
    queue job in progress, which can run beside a forge task once
    max_concurrent_jobs is above 1. See revert_lock, THREE WRITERS. The
    job is skipped, not the queue: it is claimed on a later pass.
    """
    from app.core import revert_lock

    with revert_lock.CLAIM_LOCK:
        return _claim_next_forge_job_locked(revert_lock.reverting_file_id())


def _claim_next_forge_job_locked(reverting: int | None) -> int | None:
    from app.database.models import Ac3ForgeJob, QueueItem

    db = SessionLocal()
    try:
        query = (
            db.query(Ac3ForgeJob)
            .filter(Ac3ForgeJob.status.in_(["pending", "undo_pending"]))
        )
        if reverting is not None:
            query = query.filter(Ac3ForgeJob.file_id != reverting)
        processing = (db.query(QueueItem.file_id)
                        .filter(QueueItem.status == "processing"))
        query = query.filter(Ac3ForgeJob.file_id.notin_(processing))

        job = query.order_by(Ac3ForgeJob.created_at.asc()).first()
        if job is None:
            return None

        job.status     = "processing"
        job.started_at = utcnow()
        db.commit()
        return job.id
    except Exception:
        db.rollback()
        logger.exception("Failed to claim forge job")
        return None
    finally:
        db.close()


def resolve_forge_ac3_for_undo(
    tracks: list[dict],
    *,
    marked: bool = False,
) -> tuple[str, int | None]:
    """
    Locate the forge-added AC3 5.1 track in a FRESHLY-probed track list,
    for undo. Pure function — takes probe.extract_tracks() output,
    touches nothing.

    marked is the job's Ac3ForgeJob.ac3_marked: whether the probe after its
    add found FORGE_AC3_HANDLER_NAME on the new AC3. It decides which rule
    identifies the track.

    Returns (outcome, audio_output_index):
      ("found", N)        — the forge AC3 is audio output stream N
                            (0-based, i.e. the -0:a:{N} selector);
      ("absent", None)    — the forge AC3 is already gone (the main
                            pipeline legitimately drops it when its
                            inherited language isn't in the keep list), so
                            there is nothing to undo;
      ("mismatch", None)  — the file does not look like one this job
                            forged: refuse rather than guess;
      ("only_audio", None) — the track found is the file's only audio
                            track: refuse, as removing it would leave a
                            file with no sound.

    A marked job: the marker identifies the track. No audio track carrying
    it is "absent", whatever AC3 tracks the file has. One carrying it is
    "found", provided it still has the forge shape (AC3, six channels).
    More than one, or a marker on anything else, is "mismatch".

    An unmarked job (an add from before the marker existed, a container
    that keeps no handler_name, or an add whose closing probe failed) falls
    back to the rule undo used before: build_add_ac3_command APPENDS the
    new AC3 after every existing stream, and the main remux pipeline only
    ever drops or copies audio tracks in source order, so a forge AC3 that
    survives is still the last audio track. No AC3 5.1 anywhere is
    "absent", an AC3 5.1 last is "found", and one elsewhere is "mismatch":
    the path holds a different file, replaced in place under the same name.

    That rule cannot tell a dropped forge AC3 from a surviving one when the
    file already had an AC3 5.1 of its own. Candidates only require an AAC
    5.1, so a French AAC 5.1 beside an English AC3 5.1 can be forged; drop
    French audio afterwards and the job removes the AAC and the forged
    AC3, which inherited its language. The English AC3 is then the last
    audio track, and undo removed it, recorded "undone", and left a file
    with video alone (reproduced with real FFmpeg). The marker is what
    closes that. For unmarked jobs nothing can, short of refusing every
    undo, so "only_audio" at least stops the outcome that loses the file's
    sound. It applies to marked jobs too, where it guards a layout no job
    is known to produce: a forge AC3 outliving the AAC it came from.

    The add-time position, Ac3ForgeJob.audio_track_count, is used by
    neither rule. The forge rewrite changes the file's size/mtime, so the
    next delta scan re-evaluates it, and the main pipeline can drop audio
    tracks (e.g. a non-kept-language track) before the user ever clicks
    undo, which pushes the stored index past the end of the surviving
    audio. FFmpeg SILENTLY IGNORES a negative map that matches nothing
    (verified live: rc=0, no warning), so an undo that trusted it rewrote
    the entire file with every track kept and recorded the job as
    "undone", leaving the AC3 embedded while the UI reported it removed.

    (A same-layout replacement at the same path is indistinguishable in
    principle; normal *arr upgrades change the release filename, which
    deletes the MediaFile row and the forge job with it.)
    """
    audio = [t for t in tracks if t.get("track_type") == "audio"]

    def _is_forge_shape(t: dict) -> bool:
        return (t.get("codec") or "").lower() == "ac3" and t.get("channels") == 6

    if marked:
        carrying = [n for n, t in enumerate(audio)
                    if t.get("handler_name") == FORGE_AC3_HANDLER_NAME]
        if not carrying:
            return ("absent", None)
        if len(carrying) > 1 or not _is_forge_shape(audio[carrying[0]]):
            return ("mismatch", None)
        index = carrying[0]
    else:
        if not any(_is_forge_shape(t) for t in audio):
            return ("absent", None)
        if not _is_forge_shape(audio[-1]):
            return ("mismatch", None)
        index = len(audio) - 1

    if len(audio) == 1:
        return ("only_audio", None)
    return ("found", index)


def resolve_forge_aac_for_add(tracks: list[dict]) -> tuple[int, int] | None:
    """
    Find the AAC 5.1 track a forge add works from, in a FRESHLY probed track
    list. Pure function: takes probe.extract_tracks() output, touches
    nothing.

    Returns (aac_stream_index, audio_track_count): the global stream index
    of the first AAC 5.1 track by stream index, the rule queue_forge_job
    and get_candidates apply to the stored rows, and the number of audio
    tracks, which is the output audio index the appended AC3 takes. None
    when the file has no AAC 5.1 track.

    The add used to run on the values stored when it was queued. A job
    that removed an audio track in between shifted the streams, and the
    stored index then named a different track: on a file whose French
    stereo track was dropped, the forge copied the Commentary track in
    place of building an AC3, and reported success.
    """
    audio = [t for t in tracks if t.get("track_type") == "audio"]
    aac = sorted(
        (t for t in audio if t.get("codec") == "aac" and t.get("channels") == 6),
        key=lambda t: t["stream_index"],
    )
    if not aac:
        return None
    return aac[0]["stream_index"], len(audio)


def settle_forge_jobs(db, file_id: int, tracks: list[dict], *, after: str) -> None:
    """
    Bring the file's finished AC3 Forge jobs in line with the file, after
    something other than the forge rewrote it. Does not commit.

    Two things do, and each can take the forged AC3 away:

      • a revert (after="revert", revert_restore._apply): an AC3 forged
        after processing is not part of the original and goes. One forged
        BEFORE processing is part of it, and comes back;
      • a job (after="job", worker._finish_job): the forged AC3 inherits
        its AAC's language, so dropping that language from Keep Audio
        Languages drops both.

    Either way the forge job used to keep saying "success". The Forge page
    then listed the file as forged, left it out of the candidates and
    refused to queue it again, until an undo that had nothing to remove.

    tracks is the probe the caller has just made of the rewritten file, so
    a probe that failed never reaches here and the jobs stay as they were.
    The file decides, through resolve_forge_ac3_for_undo, the test the
    undo itself uses, applied to each job with that job's own ac3_marked,
    as its undo would apply it: "absent" settles the job as undone, the
    state a completed undo leaves; "found" leaves it alone; "mismatch" (an
    AC3 5.1 where the forge never puts one) and "only_audio" are left
    alone too, as the undo refuses to act on either.

    Per job, because the rule differs. Under the last-track rule alone, a
    file that carries its own AC3 5.1 last reads as "found" once the
    forged one has gone: the job stays "success", and its undo would then
    remove the file's own AC3. A marked job looks for its marker, which
    that track does not carry.

    "undo_failed" is settled with "success": the AC3 that undo could not
    remove is gone now as well. No other status is touched. A revert is
    refused while the file has a pending or running forge job
    (routes/revert.restore). After a job, a pending add or undo re-reads
    the file when it starts, and goes by what it finds there
    (load_forge_job_data).

    after names the caller, for the log.
    """
    from app.database.models import Ac3ForgeJob

    jobs = (db.query(Ac3ForgeJob)
              .filter(Ac3ForgeJob.file_id == file_id,
                      Ac3ForgeJob.status.in_(("success", "undo_failed")))
              .all())
    settled = 0
    for job in jobs:
        outcome, _index = resolve_forge_ac3_for_undo(
            tracks, marked=bool(job.ac3_marked))
        if outcome in ("mismatch", "only_audio"):
            logger.warning(
                "AC3 Forge job %d for file %d left as it is after the %s: "
                "its undo would refuse the file (%s)",
                job.id, file_id, after, outcome,
            )
            continue
        if outcome == "found":
            continue
        job.status = "undone"
        job.is_undo = True
        job.completed_at = utcnow()
        job.error_message = None
        settled += 1
    if settled:
        logger.info(
            "Marked %d AC3 Forge job(s) for file %d undone: the %s removed "
            "the forged track", settled, file_id, after,
        )


def load_forge_job_data(job_id: int) -> dict | None:
    """
    Return everything the worker needs to execute a forge job.

    Both kinds re-probe the file first; a probe failure fails the job with
    the file untouched.

    An ADD resolves the AAC 5.1 track and the audio count from the probe
    via resolve_forge_aac_for_add, and stores them on the job in place of
    the values recorded when it was queued. No AAC 5.1 track left fails
    the job, file untouched.

    For UNDO jobs it resolves the forge AC3's CURRENT position via
    resolve_forge_ac3_for_undo (see its docstring for why the stored
    add-time index cannot be trusted), returning it as
    "undo_audio_output_index". Terminal outcomes are settled here,
    following the existing file-missing pattern (finish + return None):
      • AC3 already absent   → job marked "undone" WITHOUT running
                                FFmpeg — the desired end state already
                                holds (the main pipeline removed the
                                track), the file returns to the
                                candidates list, and re-running would
                                rewrite gigabytes to change nothing.
                                Deliberately NOT "undo_failed": that
                                status means "the AC3 is still present",
                                would exclude the file from candidates,
                                and every retry would fail identically —
                                a stuck state for an already-correct
                                file;
      • layout mismatch      → job failed with an explicit explanation,
                                file untouched;
      • only audio track     → the same: an undo never leaves a file
                                with no audio.
    """
    from app.database.models import Ac3ForgeJob, MediaFile

    db = SessionLocal()
    try:
        job: Ac3ForgeJob | None = db.get(Ac3ForgeJob, job_id)
        if not job:
            return None

        media: MediaFile | None = db.get(MediaFile, job.file_id)
        if not media or not os.path.exists(media.path):
            finish_forge_job(job_id, False, None, "File not found on disk")
            return None

        undo_audio_output_index: int | None = None
        try:
            probe_data   = probe_file(media.path, app_settings.FFPROBE_PATH)
            fresh_tracks = extract_tracks(probe_data)
        except ProbeError as exc:
            what = ("locate the AC3 track for undo" if job.is_undo
                    else "find the AAC 5.1 track to build the AC3 from")
            finish_forge_job(job_id, False, None, f"Could not probe file to {what}: {exc}")
            return None

        if not job.is_undo:
            found = resolve_forge_aac_for_add(fresh_tracks)
            if found is None:
                finish_forge_job(
                    job_id, False, None,
                    "The file no longer has an AAC 5.1 track to build the AC3 "
                    "from; it was removed after this forge job was queued. "
                    "The file was not changed.",
                )
                return None
            if found != (job.aac_stream_index, job.audio_track_count):
                logger.info(
                    "Forge add %d: the AAC 5.1 track is now stream %d with %d "
                    "audio tracks (queued as stream %s with %s); the file "
                    "changed since it was queued. Using the current layout.",
                    job_id, found[0], found[1],
                    job.aac_stream_index, job.audio_track_count,
                )
            # Stored, so the row describes the run that actually happens.
            job.aac_stream_index, job.audio_track_count = found
            db.commit()

        if job.is_undo:
            outcome, undo_audio_output_index = resolve_forge_ac3_for_undo(
                fresh_tracks, marked=bool(job.ac3_marked),
            )

            if outcome == "absent":
                logger.info(
                    "Forge undo %d: the forged AC3 5.1 track is no longer in "
                    "%s — it was already removed (most likely by the main "
                    "pipeline after the forge rewrite triggered a re-scan). "
                    "Marking undone without touching the file.",
                    job_id, media.path,
                )
                finish_forge_job(job_id, True, None, None)
                return None

            if outcome == "mismatch":
                where = ("more than one track carries the forge marker, or "
                         "it is on a track that is not AC3 5.1"
                         if job.ac3_marked else
                         "an AC3 5.1 track exists but is not the last audio "
                         "track")
                finish_forge_job(
                    job_id, False, None,
                    "The file's audio layout no longer matches what this "
                    f"forge job produced ({where}) — the file at this path "
                    "appears to have been replaced. Refusing to remove a "
                    "track from a file this job did not modify.",
                )
                return None

            if outcome == "only_audio":
                finish_forge_job(
                    job_id, False, None,
                    "The AC3 5.1 track this undo would remove is the file's "
                    "only audio track, so removing it would leave the file "
                    "with no sound. It may not be the one this forge job "
                    "added: that one may have been removed by later "
                    "processing. The file was not changed.",
                )
                return None

            if undo_audio_output_index != job.audio_track_count:
                logger.warning(
                    "Forge undo %d: AC3 track resolved at audio index %d, "
                    "but the add-time index was %d — audio tracks were "
                    "removed by later processing. Using the resolved index; "
                    "the stored one would have targeted the wrong track.",
                    job_id, undo_audio_output_index, job.audio_track_count,
                )

        # job_timeout_minutes read here (rather than a separate fetch in
        # worker.py) since this function already has the DB session open —
        # mirrors _load_job_data's own pattern in worker.py exactly.
        app_cfg = get_app_settings(db)

        return {
            "job_id":             job.id,
            "file_id":            job.file_id,
            "is_undo":            job.is_undo,
            "file_path":          media.path,
            "filename":           media.filename,
            # As stored, with no fallback. This was `or "mkv"`, so an empty
            # value had the forge write Matroska into the file whatever it
            # was, an .mp4 included. The scanner stores one only when
            # ffprobe reports no format name. The command builders refuse a
            # container they do not know, empty included, so the job fails
            # with the file untouched.
            "container":          media.container,
            # From the same probe: a QuickTime movie is written back as one.
            "quicktime":          is_quicktime(probe_data),
            "aac_stream_index":   job.aac_stream_index,
            "audio_track_count":  job.audio_track_count,
            "undo_audio_output_index": undo_audio_output_index,
            # Each track's title by its index in the output, which the
            # commands write back for an MP4 (_title_args).
            "titles":             titles_after_forge(fresh_tracks, undo_audio_output_index),
            # The Matroska covers and the attachment count, from the same
            # probe, for the worker to copy out (copy_out_cover_art).
            "cover_art":          find_matroska_covers(probe_data),
            "original_size":      job.original_size,
            "job_timeout_minutes": app_cfg.get("job_timeout_minutes", 120),
            # Read here for the same reason as job_timeout_minutes above:
            # this function already holds the session open. The command
            # builders need it to honour add_faststart_to_mp4.
            "add_faststart":       app_cfg.get("add_faststart_to_mp4", True),
            # The whole settings dict, for revert capture: it decides from
            # revert_enabled and revert_require_point, as it does for jobs.
            "app_cfg":             app_cfg,
        }
    finally:
        db.close()


def update_forge_progress(job_id: int, percent: float, current_action: str) -> None:
    from app.database.models import Ac3ForgeJob

    db = SessionLocal()
    try:
        job = db.get(Ac3ForgeJob, job_id)
        if job:
            job.progress       = percent
            job.current_action = current_action
            db.commit()
    except Exception:
        db.rollback()
    finally:
        db.close()


def probe_tracks_after_forge(path: str) -> list[dict] | None:
    """
    Probe a file the forge has just rewritten, for finish_forge_job's
    Track refresh.

    None when the probe fails. The rewrite itself succeeded, so the job is
    still a success and the stored rows are left as they were; the next
    scan re-probes the file regardless, because the rewrite changed its
    size and mtime and the stored ones are not updated here.
    """
    try:
        return extract_tracks(probe_file(path, app_settings.FFPROBE_PATH))
    except ProbeError as exc:
        logger.warning(
            "Could not probe %s after the forge rewrote it: %s. Its stored "
            "tracks are out of date until the next scan.", path, exc,
        )
        return None


def finish_forge_job(
    job_id:      int,
    success:     bool,
    output_size: int | None,
    error:       str | None,
    *,
    fresh_tracks: list[dict] | None = None,
) -> None:
    """
    Record a forge job's terminal state.

    fresh_tracks, on a successful run, is the rewritten file's probed track
    list (probe_tracks_after_forge), and replaces the stored Track rows in
    the same commit as the status change. See the comment at that write.
    Keyword-only, so a stale positional call with a fifth argument still
    raises rather than landing in it.

    Deliberately takes no output_path. Forge rewrites the media file in place
    (worker.py passes output_path=input_path to run_forge_command), so the
    "output path" is always the source path, already reachable from this row
    via media_file.path — and Ac3ForgeJob has no column to store it in anyway.
    The parameter existed but was never read, which read as though the path
    were being persisted alongside output_size when it never was.
    """
    from app.database.models import Ac3ForgeJob

    db = SessionLocal()
    try:
        job: Ac3ForgeJob | None = db.get(Ac3ForgeJob, job_id)
        if not job:
            return

        job.completed_at  = utcnow()
        job.progress      = 100.0 if success else job.progress
        job.output_size   = output_size
        job.error_message = error

        if job.is_undo:
            job.status = "undone"    if success else "undo_failed"
        else:
            job.status = "success"   if success else "failed"

        # An add or undo rewrites the file in place, which leaves the stored
        # Track rows describing a file that is gone. A queued job for this
        # file decides from those rows when it starts (worker._load_job_data):
        # after an add it would strip the new AC3, after an undo it would map
        # the removed one and fail. Same commit as the status change, because
        # the worker holds a queued job back only while this forge job is
        # "processing": rows written after that would leave a window for a
        # job to start on the old ones.
        if success and fresh_tracks is not None:
            from app.core.scanner import replace_track_rows
            if not job.is_undo:
                # Marked only once the rewritten file shows the marker where
                # a marked undo will look for it. The add always writes it,
                # but AVI, MPEG-TS and ASF keep no handler_name, and a job
                # marked on the strength of the command alone would find no
                # marker at undo and take its AC3 for already removed. With
                # no probe here the job stays unmarked: undo then uses the
                # last-track rule, as it does for adds from before the marker.
                job.ac3_marked = resolve_forge_ac3_for_undo(
                    fresh_tracks, marked=True)[0] == "found"
            if job.is_undo:
                _rekey_questions_after_undo(db, job.file_id, fresh_tracks)
            # An add needs nothing of the kind: it appends its AC3 after
            # every existing stream, so each keeps its position (verified
            # with the real command). Mapped the way an undo is, the extra
            # audio track would read as a count that does not add up, and
            # every audio question would be deleted.
            replace_track_rows(db, job.file_id, fresh_tracks)

        db.commit()
        logger.info(
            "Forge job %d → %s%s", job_id,
            job.status, f" ({error})" if error else ""
        )
    except Exception:
        db.rollback()
        logger.exception("Failed to finalise forge job %d", job_id)
    finally:
        db.close()


def _rekey_questions_after_undo(db, file_id: int, fresh_tracks: list[dict]) -> None:
    """
    Move the file's Language Review questions past the AC3 an undo removed.

    A job after the add writes video, then audio, then subtitles, so the
    AC3 that was appended last ends up in front of the subtitles, and
    removing it moves each of them down one: a subtitle's question left at
    its old position named the next subtitle along, or nothing.

    The removed track is found in the stored rows, which are what the
    questions are numbered against, by resolve_forge_ac3_for_undo's
    last-track rule. Stored rows keep no handler name, so the marker cannot
    be used here, and it is not needed: a forge AC3 that a marked undo
    found is a forge AC3 that survived, which the same invariant puts last.
    Rows that do not show it are left to
    job_stream_mapping's count check with nothing marked removed: a layout
    that is the same either side pairs one to one, and a type whose count
    does not add up loses its questions, which the next scan asks again,
    as the forge leaves the stored size behind.
    """
    from app.core.scanner import (_track_to_dict, job_stream_mapping,
                                  rekey_language_flags)
    from app.database.models import MediaFile, Track

    before = [_track_to_dict(t) for t in
              db.query(Track).filter(Track.file_id == file_id)
                             .order_by(Track.stream_index)]
    outcome, audio_index = resolve_forge_ac3_for_undo(before)
    audio = [t for t in before if t["track_type"] == "audio"]
    removed = {audio[audio_index]["stream_index"]} if outcome == "found" else set()
    media = db.get(MediaFile, file_id)
    mapping = job_stream_mapping(before, fresh_tracks, removed,
                                 media.path if media else str(file_id))
    rekey_language_flags(db, file_id, mapping, set())


def load_forge_final_state(job_id: int) -> dict | None:
    from app.database.models import Ac3ForgeJob

    db = SessionLocal()
    try:
        job = db.get(Ac3ForgeJob, job_id)
        if not job:
            return None
        return {
            "status":   job.status,
            "filename": job.media_file.filename if job.media_file else "",
            "error":    job.error_message,
        }
    finally:
        db.close()
