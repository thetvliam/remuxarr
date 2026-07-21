"""
Background Worker
=================
A long-running asyncio task that polls the queue for pending jobs and runs
them concurrently up to the configured max_concurrent_jobs limit.

Key controls (set at runtime, no restart needed):
  pause_worker()  / resume_worker()  — stop/start picking up new jobs
  is_worker_paused()                 — current pause state
  get_active_job_count()             — how many jobs are running right now
"""
import asyncio
import json
import logging
import os
import shutil
from dataclasses import replace as dc_replace
from datetime import datetime

from app.config import settings as app_settings
from app.core.decision import ProcessingDecision, analyze_file
from app.core.email_notify import send_breaker_tripped_email, send_failure_email
from app.core.ffmpeg import FFmpegProgress, determine_output_path, execute_ffmpeg, execute_ffmpeg_combined, execute_subtitle_extraction, _pick_temp_dir
from app.core.probe import is_faststart_mp4, probe_file, extract_format_info, extract_tracks, ProbeError
from app.core.plex import notify_plex_new_file
from app.core.radarr import notify_radarr
from app.core.scanner import _load_subtitle_overrides, _load_audio_language_overrides, _load_subtitle_language_overrides, _get_forged_ac3_audio_index, _track_to_dict, _upsert_language_flags
from app.core.sonarr import notify_sonarr
from app.database.models import MediaFile, NotificationState, PlannedAction, PlexAnalyzeBacklog, QueueItem, Track
from app.database.session import SessionLocal, get_app_settings

logger = logging.getLogger(__name__)

# ── Module-level state ─────────────────────────────────────────────────────────

_worker_task: asyncio.Task | None = None
_running      = False
_paused       = False          # set by pause_worker() / resume_worker()
_active_jobs: set[int] = set() # job IDs currently being processed
# Mirrors _loop()'s local active_tasks dict — updated in lockstep — so
# abort_job() (called from the API route) can find and cancel a specific
# running job's asyncio.Task from outside the loop. _active_jobs above
# only tracks which IDs are active; this additionally holds the Task
# object itself, which is what .cancel() needs to act on.
_active_task_registry: dict[int, asyncio.Task] = {}


def pause_worker() -> None:
    global _paused
    _paused = True
    logger.info("Worker paused — no new jobs will be claimed until resumed")


def resume_worker() -> None:
    global _paused
    _paused = False
    logger.info("Worker resumed")


def is_worker_paused() -> bool:
    return _paused


def get_active_job_count() -> int:
    return len(_active_jobs)


def abort_job(job_id: int) -> bool:
    """
    Cancel a currently-processing job immediately, AND stop the worker
    from claiming the next pending job.

    The second half matters as much as the first: the scenario this
    exists for is a new user seeing the very first file do the wrong
    thing (bad settings, wrong language, etc.) — cancelling that one file
    alone doesn't help if the worker just claims the next item on its very
    next loop tick. Calling pause_worker() here sets the actual in-memory
    flag the live loop checks (_paused), which is distinct from the
    auto_start_jobs database setting the API route also updates — that
    setting is only ever read at container startup and before a new scan
    begins, so writing it alone has no effect on a worker loop that's
    already running. Both are needed: pause_worker() stops the current
    session immediately, auto_start_jobs prevents auto-resuming on a
    future restart or scan.

    Marks the DB row "cancelled" (reusing the existing status rather than
    inventing a new one, so every existing filter/tab/badge that already
    understands "cancelled" — e.g. the Failed tab, Retry All — picks this
    up with no further changes) BEFORE cancelling the task, so that by the
    time _run_and_broadcast's finally block reads the final state, it
    already reflects the abort rather than racing to overwrite it with
    "failed" via the emergency-cleanup safety net.

    Returns True if a matching running task was found and cancelled, False
    if the job wasn't actually running (already finished, or never
    existed) — the caller uses this to return 404 vs success.

    The actual subprocess kill happens inside run_staged_subprocess's
    explicit CancelledError handler once the cancellation propagates down
    to wherever the task is currently awaiting — see subprocess_runner.py.
    """
    task = _active_task_registry.get(job_id)
    if task is None or task.done():
        return False

    pause_worker()

    with SessionLocal() as db:
        job = db.get(QueueItem, job_id)
        if job and job.status == "processing":
            job.status         = "cancelled"
            job.error_message  = "Aborted by user"
            job.completed_at   = datetime.utcnow()
            if job.media_file:
                # Reset the delta-scan sentinels alongside the status, the
                # same as cancel_item / clear_pending / clear_dry_run and
                # the history clear/delete paths. An aborted file's bytes
                # are unchanged on disk (staging never completed), so the
                # scanner's size/mtime delta check would read it as
                # "nothing to do" and never re-evaluate it — leaving it
                # reachable only via Retry or a forced full rescan. The
                # abort scenario is exactly "fix the wrong setting and let
                # it re-process," so the file must resurface on the next
                # delta scan. abort_job was the one cancel path that reset
                # status but not the sentinels.
                job.media_file.size   = -1
                job.media_file.mtime  = -1.0
                job.media_file.status = "skipped"
            db.commit()
            logger.info("Job %d marked cancelled — aborting task now", job_id)

    task.cancel()
    return True


# ── Lifecycle ──────────────────────────────────────────────────────────────────

async def start_worker() -> None:
    global _worker_task, _running, _paused
    _running = True

    # Initialise the pause state from the auto_start_jobs setting.
    # auto_start_jobs = True  → start unpaused (process immediately)
    # auto_start_jobs = False → start paused (user must click Resume)
    with SessionLocal() as db:
        cfg = get_app_settings(db)
        _paused = not cfg.get("auto_start_jobs", True)

        # Reset any jobs that are still in "processing" state — these were
        # left behind by a crash, SIGKILL, or an unclean container restart.
        # Without this they stay in "processing" forever and block the UI.
        stuck = db.query(QueueItem).filter(QueueItem.status == "processing").all()
        if stuck:
            for job in stuck:
                job.status        = "failed"
                job.error_message = "Interrupted by container restart or crash"
                job.completed_at  = datetime.utcnow()
                if job.media_file:
                    job.media_file.status = "error"
            db.commit()
            logger.info(
                "Reset %d interrupted 'processing' job(s) to 'failed' on startup",
                len(stuck),
            )

    _worker_task = asyncio.create_task(_loop(), name="remuxarr-worker")
    logger.info("Background worker started (paused=%s)", _paused)


async def stop_worker() -> None:
    # _worker_task is only READ here, never reassigned — no `global`
    # needed for it specifically (only _running, which IS reassigned
    # below, actually requires the declaration).
    global _running
    _running = False
    if _worker_task and not _worker_task.done():
        _worker_task.cancel()
        try:
            await _worker_task
        except asyncio.CancelledError:
            pass
    logger.info("Background worker stopped")


# ── Main loop ──────────────────────────────────────────────────────────────────

async def _loop() -> None:
    """
    Concurrent job-pool loop.

    Each iteration:
      1. Prune completed tasks from the active set.
      2. Read max_concurrent_jobs from settings.
      3. While not paused and active < max, claim and launch a new job.
      4. Sleep briefly before the next iteration.
    """
    from app.api.ws_manager import ws_manager   # avoid circular import

    loop         = asyncio.get_running_loop()
    active_tasks: dict[int, asyncio.Task] = {}  # job_id → Task
    forge_task:   asyncio.Task | None = None

    while _running:
        try:
            # ── Prune finished tasks ───────────────────────────────────────
            done_ids = [jid for jid, t in active_tasks.items() if t.done()]
            for jid in done_ids:
                del active_tasks[jid]
                _active_jobs.discard(jid)
                _active_task_registry.pop(jid, None)

            if forge_task and forge_task.done():
                forge_task = None

            # ── Read current settings ──────────────────────────────────────
            with SessionLocal() as db:
                cfg = get_app_settings(db)
            max_jobs = max(1, int(cfg.get("max_concurrent_jobs", 1)))

            if not _paused:
                total_active = len(active_tasks) + (1 if forge_task is not None else 0)

                # ── Forge gets first priority on any available slot ─────────
                # Checked whenever there's room for at least one more task
                # overall (regular + forge combined), not only when ALL
                # regular slots are simultaneously empty. At
                # max_concurrent_jobs > 1, requiring complete emptiness of
                # active_tasks meant the exact starvation this check exists
                # to prevent could still happen: under a continuous
                # main-queue backlog, the very same iteration's regular-claim
                # loop below refilled any freed slot the moment
                # len(active_tasks) < max_jobs held true — which is true
                # the instant even ONE slot frees up, well before
                # active_tasks could ever become fully empty. Forge
                # priority effectively never fired above
                # max_concurrent_jobs=1, reappearing under a different
                # concurrency setting. Tracking total_active explicitly
                # (rather than checking active_tasks in isolation) fixes
                # this at any concurrency level.
                if total_active < max_jobs and forge_task is None:
                    forge_result = await loop.run_in_executor(None, _has_pending_forge)
                    if forge_result:
                        forge_task = asyncio.create_task(
                            _process_next_forge(ws_manager)
                        )
                        total_active += 1

                # ── Claim regular jobs up to the concurrency limit ─────────
                # total_active already accounts for a forge task started
                # above this iteration, so regular claims correctly stop
                # one slot short of max_jobs when forge just took one —
                # rather than the old skip-entirely-if-forge-started
                # approach, which under-utilised remaining slots at
                # max_concurrent_jobs > 2 (forge taking 1 of 3 slots should
                # still leave room to claim 2 regular jobs the same
                # iteration, not zero).
                while total_active < max_jobs:
                    job_id = await loop.run_in_executor(None, _claim_next)
                    if job_id is None:
                        break
                    logger.info("Worker picked up job %d", job_id)
                    _active_jobs.add(job_id)
                    await ws_manager.broadcast_json({"event": "job_started", "job_id": job_id})
                    task = asyncio.create_task(_run_and_broadcast(job_id, ws_manager, loop))
                    active_tasks[job_id] = task
                    _active_task_registry[job_id] = task
                    total_active += 1

        except asyncio.CancelledError:
            break
        except Exception:
            logger.exception("Unhandled error in worker loop — backing off 5 s")
            await asyncio.sleep(5)
            continue

        await asyncio.sleep(1)


async def _run_and_broadcast(
    job_id: int, ws_manager, loop: asyncio.AbstractEventLoop
) -> None:
    """Wrap _run_job with the completed broadcast."""
    try:
        await _run_job(job_id, ws_manager, loop)
    except asyncio.CancelledError:
        # User abort: abort_job() marked the DB row "cancelled" and then
        # called task.cancel(), which raised this here at _run_job's await
        # point. We re-raise so the task still ends cancelled — the
        # broadcast itself happens in the finally below, which runs to
        # completion even under cancellation (see the note there). This
        # branch exists so an abort is explicit and logged rather than an
        # unlabelled BaseException silently skipping past `except
        # Exception`.
        logger.info("Job %d cancelled by user abort — finalising", job_id)
        raise
    except Exception:
        logger.exception("_run_job raised for job %d — attempting emergency cleanup", job_id)
    finally:
        # This block runs on EVERY exit path — normal return, exception,
        # and cancellation. It is safe under cancellation specifically
        # because abort_job() issues a single task.cancel(): the resulting
        # CancelledError is delivered once, at _run_job's await, so by the
        # time control reaches here the cancellation is already consumed
        # and the awaits below (executor calls + the broadcast) complete
        # normally. That's what guarantees EVERY connected client gets the
        # "cancelled" job_completed event, not just the one that clicked
        # abort (which also re-fetches over REST). Verified empirically.
        post_job = await loop.run_in_executor(None, _load_post_job_data, job_id)
        if post_job:
            final = post_job["final"]
            # Safety net: if the job is still "processing" after _run_job
            # returned (e.g. _finish_job's commit failed and the rollback
            # left the row unchanged), force it to "failed" via a minimal
            # fresh session so the frontend doesn't stay stuck at 100%.
            if final.get("status") == "processing":
                logger.warning(
                    "Job %d still in 'processing' state after completion — "
                    "forcing to 'failed' via emergency cleanup",
                    job_id,
                )
                await loop.run_in_executor(
                    None, _emergency_fail_job, job_id,
                    "Job did not complete cleanly (finalisation failed)",
                )
                final = final.copy()
                final["status"] = "failed"
                final["error"]  = "Finalisation failed — check container logs"

            await ws_manager.broadcast_json({
                "event":    "job_completed",
                "job_id":   job_id,
                "status":   final["status"],
                "filename": final["filename"],
                "error":    final["error"],
            })

            # Post-job *arr notifications — only for real successes.
            # sonarr/radarr data was already read in the same session as
            # `final` above (see _load_post_job_data) rather than each
            # opening its own separate connection here.
            if final.get("status") == "success":
                if post_job["sonarr"]:
                    asyncio.create_task(
                        _trigger_arr_notify(post_job["sonarr"], loop, notify_sonarr, "Sonarr")
                    )
                if post_job["radarr"]:
                    asyncio.create_task(
                        _trigger_arr_notify(post_job["radarr"], loop, notify_radarr, "Radarr")
                    )

                # Plex — independent of Sonarr/Radarr. Fires a lightweight
                # refresh for every successful job; reprocessed files
                # additionally get queued for a delayed explicit Analyze
                # (see _load_plex_notify_data for why both are needed).
                plex_data = await loop.run_in_executor(
                    None, _load_plex_notify_data, job_id,
                )
                if plex_data:
                    asyncio.create_task(_trigger_plex_notify(plex_data, loop))

            # Email — runs for BOTH success and failure, unlike the blocks
            # above. A success quietly resets the consecutive-failure
            # breaker (even if email is currently disabled, so the state
            # stays accurate if it's re-enabled later); a failure may
            # trigger an email depending on the breaker's current state.
            # See _load_email_notify_data's docstring for the full logic.
            if final.get("status") in ("success", "failed", "dry_run"):
                email_data = await loop.run_in_executor(
                    None, _load_email_notify_data, job_id,
                )
                if email_data:
                    asyncio.create_task(_trigger_email_notify(email_data, loop))


def _is_corrupt_audio_copy_failure(error: str | None) -> bool:
    """
    Return True if the error is specifically caused by corrupt audio frames
    in a stream that was being copied (-c:a copy) rather than transcoded.

    All four conditions must be present:
      "aost#"      — audio output stream (not vost# video, not sost# subtitle)
      "/copy"      — the codec was copy, not a transcoder like aac or ac3
      "error submitting a packet to the muxer" — the muxer rejected the packet
      "invalid data found" — the packet data itself is corrupt/malformed

    This combination uniquely identifies corrupt source audio frames. It cannot
    trigger for:
      - Video errors (vost# not aost#)
      - Subtitle errors (sost# not aost#)
      - Transcoding failures (show /aac or /ac3, not /copy)
      - Disk / permission errors (different error text entirely)

    Note: the subtitle encoding failure handler (_is_subtitle_encoding_failure)
    runs BEFORE this check in _run_job.  A subtitle-caused cascade that also
    produces an aost#/copy line is already caught there and never reaches here.

    Safe to retry: the retry uses -c:a aac (no /copy), so even if the retry
    also fails the error will not match this pattern, preventing any loop.
    """
    if not error:
        return False
    lower = error.lower()
    return (
        "aost#" in lower
        and "/copy" in lower
        and "error submitting a packet to the muxer" in lower
        and "invalid data found" in lower
    )


def _is_unknown_timestamp_audio_failure(error: str | None) -> bool:
    """
    Sibling to _is_corrupt_audio_copy_failure above — same remediation
    (retry with the audio transcoded instead of copied), but a genuinely
    different root cause, so kept as a separate, narrowly-scoped function
    rather than folded into that one's conditions, which specifically and
    accurately claim to identify corrupt packet DATA — not this.

    This one is source audio packets that simply have no usable timestamp
    at all — most often seen in older/non-standard AVI files, where the
    container itself doesn't reliably carry per-packet timing the way
    more modern formats do. A pure stream copy has nothing to preserve in
    that case; transcoding sidesteps the problem entirely, since the
    decoder generates fresh, internally-consistent timestamps of its own
    rather than needing to trust absent source ones. Confirmed via
    verified reports that -fflags +genpts is NOT a reliable fix for this
    specific error text — including an FFmpeg maintainer's own
    acknowledgment that this class of H.264/timestamp issue is a known,
    difficult-to-fix deficiency — which is why this goes straight to the
    transcode retry rather than trying a timestamp-generation flag first.

    All three conditions must be present:
      "aost#"      — audio output stream (not vost# video, not sost# subtitle)
      "/copy"      — the codec was copy, not a transcoder like aac or ac3
      "error submitting a packet to the muxer" — the muxer rejected the packet
      "can't write packet with unknown timestamp" — the specific reason:
        no usable timestamp, not corrupt data (that's the sibling function)

    Safe to retry for the same reason as the sibling function: the retry
    uses -c:a aac (no /copy), so even if the retry also fails, the error
    will not match this pattern either, preventing any loop.
    """
    if not error:
        return False
    lower = error.lower()
    return (
        "aost#" in lower
        and "/copy" in lower
        and "error submitting a packet to the muxer" in lower
        and "can't write packet with unknown timestamp" in lower
    )


def _needs_audio_transcode_retry(result) -> bool:
    """
    True when a failed FFmpeg attempt should be retried with AAC
    transcoding instead of a plain audio copy — either genuinely corrupt
    source packet data, or source packets with no usable timestamp at
    all (common in older/non-standard AVI files); same remediation
    either way.

    Previously this exact predicate (result.success check plus both
    _is_corrupt_audio_copy_failure / _is_unknown_timestamp_audio_failure
    checks) was duplicated identically across _run_job's combined-pass
    and two-pass branches. Only the predicate is hoisted here — the
    actual retry call, log wording, and _make_audio_transcode_decision
    rebuild stay inline at each call site deliberately: the two branches
    call genuinely different underlying functions with different return
    shapes (execute_ffmpeg_combined returns a tuple, execute_ffmpeg
    doesn't), and the log wording usefully distinguishes which of the
    two retry paths actually fired.
    """
    return not result.success and (
        _is_corrupt_audio_copy_failure(result.error)
        or _is_unknown_timestamp_audio_failure(result.error)
    )


def _make_audio_transcode_decision(decision: ProcessingDecision) -> ProcessingDecision:
    """
    Return a copy of the decision where every audio copy_track action is
    replaced with a transcode_track action targeting AAC.

    output_codec_options is intentionally left empty so build_ffmpeg_command
    does NOT emit a -ac:N channel-count override — this preserves the source
    channel layout (stereo stays stereo, 5.1 stays 5.1) rather than forcing
    a fixed count.  The existing AAC-5.1→AC3 transcode uses
    output_codec_options={"b:a": "640k", "ac": "6"} specifically to force 5.1;
    that path is unaffected by this function.
    """
    new_actions = [
        dc_replace(
            a,
            action_type  = "transcode_track",
            description  = f"{a.description} → re-encoded AAC (corrupt source frames)",
            output_codec = "aac",
            output_codec_options = {},
        )
        if (a.action_type == "copy_track" and a.track_type == "audio")
        else a
        for a in decision.actions
    ]
    return dc_replace(decision, actions=new_actions)


def _is_subtitle_encoding_failure(error: str | None) -> bool:
    """
    Return True if the error string looks like a subtitle-encoding failure
    rather than a video/audio or filesystem problem.

    Used by BOTH execution paths in _run_job to decide whether to flag for
    manual review instead of failing the job outright:
      • combined pass — a subtitle decode failure cascades and kills the
        whole multi-output command even though the video/audio was fine;
      • two-pass extraction loop — the same underlying file fails its
        standalone `-map 0:N -c:s srt` command instead.
    Either way, the user should get the per-track Keep/Remove review
    rather than a raw failure (or, worse, a silent drop).

    Patterns are encoding/decoding-specific and won't false-positive on
    video/audio, filesystem, or container-capability failures — "sist#"
    only appears in the combined multi-output form, while "invalid utf-8"
    and "sub_charenc" match the single-command form. FFmpeg's canonical
    message for the exact production case this exists for is "Invalid
    UTF-8 in decoded subtitles text; maybe missing -sub_charenc option",
    which matches on two of the three patterns. A bare "subtitle"
    catch-all was deliberately removed — see the note at the return.
    """
    if not error:
        return False
    lower = error.lower()
    # Patterns are encoding/decoding-specific and won't false-positive on
    # video/audio, filesystem, or container-capability failures. Two
    # bare-substring catch-alls have been removed for exactly that
    # reason: "subtitle" (matched "...WebVTT subtitles are supported for
    # WebM", a container rejection) and "mov_text" (would match a
    # container/mux error naming that codec — e.g. muxing a kept mov_text
    # track into a non-MP4 output). The canonical encoding failure
    # ("Invalid UTF-8 in decoded subtitles text; maybe missing
    # -sub_charenc option") still matches via both "invalid utf-8" and
    # "sub_charenc", so no real coverage is lost.
    return any(pat in lower for pat in (
        "invalid utf-8",     # the actual decode diagnostic for non-UTF-8 text subs
        "sub_charenc",       # FFmpeg's hint for the encoding issue
        "sist#",             # FFmpeg's notation for subtitle input streams in combined commands
    ))


def _flag_subtitle_encoding_review(
    job_id: int,
    subtitle_pairs: list[tuple[int, str]],
    tracks: list[dict],
) -> None:
    """
    Transition a job that failed due to subtitle encoding issues from
    "processing" to "manual_review" so the user can decide whether to
    keep the problematic subtitle tracks embedded or drop them.

    Mirrors exactly what the scanner does when it encounters image-based
    subtitle tracks (PGS/VOBSUB): sets MediaFile.status = "manual_review",
    transitions the QueueItem to status = "manual_review", and populates
    review_subtitles with the track details for the Review page UI.
    """
    from app.database.models import MediaFile

    failed_stream_indices = {si for si, _ in subtitle_pairs}

    # Build the review_subtitles payload from the track metadata we already
    # have in memory — same structure the Review page's per-track Keep/Remove
    # UI expects, matching what decision.py produces for image-based subtitles.
    flagged = [
        {
            "stream_index": t["stream_index"],
            "language":     t.get("language") or "und",
            "codec":        t.get("codec") or "",
            "is_forced":    bool(t.get("is_forced", False)),
            "title":        t.get("title"),
        }
        for t in tracks
        if t.get("track_type") == "subtitle"
        and t["stream_index"] in failed_stream_indices
    ]

    track_desc = ", ".join(
        f"{(f['language'] or 'und').upper()} {f['codec']}"
        f"{' (forced)' if f['is_forced'] else ''}"
        for f in flagged
    )
    n = len(flagged)
    reason = (
        f"Contains {n} subtitle track{'s' if n > 1 else ''} "
        f"({track_desc}) with non-UTF-8 encoded characters that cannot be "
        f"extracted to external SRT — manual review required to decide "
        f"whether to keep {'them' if n > 1 else 'it'} embedded or remove "
        f"{'them' if n > 1 else 'it'}."
    )

    with SessionLocal() as db:
        job = db.get(QueueItem, job_id)
        if job:
            file = db.get(MediaFile, job.file_id)
            job.status           = "manual_review"
            job.reason           = reason
            job.review_subtitles = json.dumps(flagged) if flagged else None
            job.error_message    = None
            job.progress         = 0.0
            job.current_action   = None
            job.started_at       = None
            if file:
                file.status = "manual_review"
        try:
            db.commit()
        except Exception:
            db.rollback()
            raise


def _has_pending_forge() -> bool:
    """
    Quick check: are there any forge jobs waiting to be processed?

    Must match the statuses claim_next_forge_job() actually claims
    (["pending", "undo_pending"]) — checking only "pending" here was a bug:
    undo jobs would be set to "undo_pending" by the API endpoint but this
    gate would never see them as pending work, so the worker loop would
    never create a forge_task and the job would sit at "undo_pending"
    (shown as "REMOVING…" in the UI) forever.
    """
    from app.database.models import Ac3ForgeJob
    with SessionLocal() as db:
        return db.query(Ac3ForgeJob).filter(
            Ac3ForgeJob.status.in_(["pending", "undo_pending"])
        ).first() is not None


# ── Job execution ──────────────────────────────────────────────────────────────

async def _run_job(job_id: int, ws_manager, loop: asyncio.AbstractEventLoop) -> None:
    """Load job data, run FFmpeg (or dry-run), then persist the result."""
    job_data = await loop.run_in_executor(None, _load_job_data, job_id)
    if job_data is None:
        return

    job_dict, file_dict, tracks, app_cfg, decision = job_data

    extract_actions = [
        a for a in decision.actions if a.action_type == "extract_subtitle"
    ]

    # ── Dry run ────────────────────────────────────────────────────────────
    if job_dict["is_dry_run"]:
        logger.info("[DRY RUN] Would process: %s", file_dict["path"])
        for action in extract_actions:
            logger.info("[DRY RUN]   would extract subtitle → %s", action.external_path)
        await asyncio.sleep(0.3)
        await loop.run_in_executor(None, _finish_job, job_id, True, None, None, None)
        return

    input_path  = file_dict["path"]
    output_path = determine_output_path(input_path, decision)

    # ── Disk space pre-flight ──────────────────────────────────────────────
    # Check before spawning FFmpeg to give a clear, immediate error rather
    # than a cryptic mid-encode failure.  Skipped if stat() is unavailable
    # (e.g. some NFS mounts) — warning is logged but the job proceeds.
    file_size = file_dict.get("size") or 0
    if file_size > 0:
        temp_dir   = _pick_temp_dir(input_path)
        output_dir = os.path.dirname(os.path.abspath(output_path))
        for label, directory in [("temp dir", temp_dir), ("output dir", output_dir)]:
            try:
                os.makedirs(directory, exist_ok=True)
                free = shutil.disk_usage(directory).free
                if free < file_size:
                    def _fmt(n: int) -> str:
                        for unit in ("B", "KB", "MB", "GB", "TB"):
                            if n < 1024 or unit == "TB":
                                return f"{n:.1f} {unit}"
                            n /= 1024
                    msg = (
                        f"Insufficient disk space in {label} "
                        f"({directory}) — need {_fmt(file_size)}, "
                        f"have {_fmt(free)} free"
                    )
                    logger.error("Job %d: %s", job_id, msg)
                    await loop.run_in_executor(
                        None, _finish_job, job_id, False, None, None, msg
                    )
                    return
            except OSError as exc:
                logger.warning(
                    "Job %d: disk space check failed for %s (%s) — proceeding",
                    job_id, directory, exc,
                )

    # ── Timeout ────────────────────────────────────────────────────────────
    timeout_minutes  = app_cfg.get("job_timeout_minutes", 120)
    timeout_seconds  = float(timeout_minutes) * 60 if timeout_minutes else None

    # ── Progress callback ──────────────────────────────────────────────────
    async def on_progress(prog: FFmpegProgress) -> None:
        # Fire-and-forget DB write (non-blocking)
        # run_in_executor schedules the sync DB write on the thread pool
        # and returns a Future immediately — no create_task wrapper needed.
        loop.run_in_executor(
            None, _update_progress, job_id, prog.percent, prog.current_action
        )
        await ws_manager.broadcast_json({
            "event":          "job_progress",
            "job_id":         job_id,
            "progress":       round(prog.percent, 1),
            "current_action": prog.current_action,
            "speed":          prog.speed,
        })

    # ── Execute ────────────────────────────────────────────────────────────
    #
    # Single-pass optimisation: when the job includes BOTH subtitle
    # extractions AND a main remux (the overwhelming common case), run a
    # single FFmpeg command that reads the source file once and writes all
    # outputs simultaneously.  This halves the read I/O vs. the previous
    # two-pass approach, which is the dominant cost on HDD arrays.
    #
    # Falls back to the original two-pass approach for jobs that only have
    # subtitle extractions or only have a main remux (no combined work).
    non_extract_actions = [
        a for a in decision.actions if a.action_type != "extract_subtitle"
    ]
    use_combined = bool(extract_actions) and bool(non_extract_actions)

    try:
        if use_combined:
            subtitle_pairs = [
                (a.stream_index, a.external_path) for a in extract_actions
            ]
            result, srt_results = await execute_ffmpeg_combined(
                input_path          = input_path,
                output_path         = output_path,
                decision            = decision,
                all_tracks          = tracks,
                subtitle_extractions= subtitle_pairs,
                job_id              = job_id,
                progress_callback   = on_progress,
                timeout_seconds     = timeout_seconds,
            )

            if not result.success and _is_subtitle_encoding_failure(result.error):
                # The subtitle decode failure cascaded to kill the entire
                # combined command even though the video/audio was fine.
                # This happens with mov_text (and similar text-based codecs)
                # that contain non-UTF-8 encoded characters — FFmpeg can't
                # decode them to SRT, and the error kills all outputs in the
                # same invocation.
                #
                # Rather than silently falling back to remux-only (which
                # would drop the subtitle entirely without telling the user),
                # transition this job to manual_review so the user can
                # explicitly choose to keep the subtitle embedded as-is or
                # drop it. This matches how image-based subtitle conflicts
                # are handled (PGS/VOBSUB gate in decision.py).
                logger.warning(
                    "Combined pass failed due to subtitle encoding issue for job %d "
                    "(%s) — flagging for manual review (subtitle stream(s): %s)",
                    job_id, file_dict["path"],
                    ", ".join(str(si) for si, _ in subtitle_pairs),
                )
                await loop.run_in_executor(
                    None, _flag_subtitle_encoding_review,
                    job_id, subtitle_pairs, tracks,
                )
                return

            if _needs_audio_transcode_retry(result):
                # Two distinct root causes land here — genuinely corrupt
                # packet data, or source packets with no usable timestamp
                # at all (common in older/non-standard AVI files) — but
                # the same fix resolves both: retry the combined pass with
                # AAC transcoding for all audio tracks instead of copying
                # them, since transcoding generates fresh timestamps of
                # its own regardless of which problem the source had.
                logger.warning(
                    "Job %d (%s): audio copy failed (corrupt frames or "
                    "unusable source timestamps) — retrying combined pass "
                    "with AAC transcoding",
                    job_id, file_dict["path"],
                )
                retry_decision = _make_audio_transcode_decision(decision)
                result, srt_results = await execute_ffmpeg_combined(
                    input_path           = input_path,
                    output_path          = output_path,
                    decision             = retry_decision,
                    all_tracks           = tracks,
                    subtitle_extractions = subtitle_pairs,
                    job_id               = job_id,
                    progress_callback    = on_progress,
                    timeout_seconds      = timeout_seconds,
                )

            # All-or-nothing staging: result.success now guarantees every
            # extracted .srt landed alongside the main file (per-SRT
            # logging happens inside execute_ffmpeg_combined). A missing
            # or unstageable SRT fails the whole run with the source file
            # untouched — the previous partial-success contract here could
            # record job SUCCESS while a subtitle had silently vanished
            # (removed from the mux, never written as a sidecar, only a
            # log warning to show for it). srt_results is retained in the
            # return shape for per-track detail, but no partial-failure
            # branch exists anymore.
        else:
            # Two-pass fallback: subtitle extractions first, then remux.
            #
            # Encoding failures here get the SAME manual-review routing as
            # the combined path's _is_subtitle_encoding_failure handler —
            # previously only the combined path had it, so the identical
            # underlying file (non-UTF-8 text subtitle) produced two
            # different outcomes depending on which internal execution
            # path happened to run: per-track Keep/Remove review when a
            # remux was also needed, but a raw failed job when the only
            # work was extraction.
            #
            # One deliberate asymmetry with the combined path remains: a
            # combined command's cascade can't be attributed to a specific
            # stream, so that path flags EVERY extraction stream for
            # review. Here each stream runs as its own command, so only
            # the stream(s) that actually failed are flagged — strictly
            # better information, not a divergence. Encoding failures are
            # accumulated across the loop (rather than flagging on the
            # first one) so a file whose subtitles share the same bad
            # charset resolves in ONE review visit instead of one per
            # track; any NON-encoding failure (disk, permissions, missing
            # stream) still fails the job immediately, exactly as before.
            encoding_failed_pairs: list[tuple[int, str]] = []
            for i, action in enumerate(extract_actions, start=1):
                label = f"Extracting subtitle to SRT ({i}/{len(extract_actions)})"
                logger.info("%s — stream %d → %s",
                            label, action.stream_index, action.external_path)
                loop.run_in_executor(None, _update_progress, job_id, 0.0, label)
                await ws_manager.broadcast_json({
                    "event": "job_progress", "job_id": job_id,
                    "progress": 0.0, "current_action": label, "speed": "",
                })
                ext_result = await execute_subtitle_extraction(
                    input_path     = input_path,
                    stream_index   = action.stream_index,
                    output_srt_path= action.external_path,
                    job_id         = job_id,
                )
                if not ext_result.success:
                    if _is_subtitle_encoding_failure(ext_result.error):
                        logger.warning(
                            "Subtitle extraction failed with an encoding "
                            "error for job %d (%s), stream %d — will flag "
                            "for manual review: %s",
                            job_id, file_dict["path"],
                            action.stream_index, ext_result.error,
                        )
                        encoding_failed_pairs.append(
                            (action.stream_index, action.external_path)
                        )
                        continue
                    await loop.run_in_executor(
                        None, _finish_job, job_id, False, None, None,
                        f"Subtitle extraction failed (stream {action.stream_index}): {ext_result.error}",
                    )
                    return

            if encoding_failed_pairs:
                # Flag and stop BEFORE the remux — the remux would remove
                # these tracks from the muxed output (extracted streams are
                # excluded from the map list) with no sidecar to replace
                # them, which is exactly the silent loss this routing
                # exists to prevent. Any sidecars from streams that DID
                # extract successfully above are left in place — same
                # rationale as the failed-remux case below: they're valid,
                # standalone files next to the (unmodified) original, and
                # the post-review re-run simply re-extracts over them.
                logger.warning(
                    "Two-pass extraction hit encoding failures for job %d "
                    "(%s) — flagging for manual review (subtitle stream(s): %s)",
                    job_id, file_dict["path"],
                    ", ".join(str(si) for si, _ in encoding_failed_pairs),
                )
                await loop.run_in_executor(
                    None, _flag_subtitle_encoding_review,
                    job_id, encoding_failed_pairs, tracks,
                )
                return

            result = await execute_ffmpeg(
                input_path        = input_path,
                output_path       = output_path,
                decision          = decision,
                all_tracks        = tracks,
                job_id            = job_id,
                progress_callback = on_progress,
                timeout_seconds   = timeout_seconds,
            )

            if _needs_audio_transcode_retry(result):
                logger.warning(
                    "Job %d (%s): audio copy failed (corrupt frames or "
                    "unusable source timestamps) — retrying with AAC "
                    "transcoding",
                    job_id, file_dict["path"],
                )
                retry_decision = _make_audio_transcode_decision(decision)
                result = await execute_ffmpeg(
                    input_path        = input_path,
                    output_path       = output_path,
                    decision          = retry_decision,
                    all_tracks        = tracks,
                    job_id            = job_id,
                    progress_callback = on_progress,
                    timeout_seconds   = timeout_seconds,
                )

    except Exception as exc:
        logger.exception("FFmpeg raised an exception for job %d", job_id)
        await loop.run_in_executor(
            None, _finish_job, job_id, False, None, None, str(exc)
        )
        return

    if result.success:
        # If the container changed, delete the now-replaced original
        if output_path != input_path and os.path.exists(input_path):
            try:
                os.remove(input_path)
                logger.info("Removed original after container change: %s", input_path)
            except OSError as exc:
                logger.warning("Could not remove original %s: %s", input_path, exc)

        await loop.run_in_executor(
            None, _finish_job, job_id, True, result.output_path, result.output_size, None
        )
    else:
        # The main remux failed AFTER subtitles were already extracted to
        # disk. Leave the extracted .srt files in place — they're valid,
        # standalone sidecars and harmless next to the (unmodified) original.
        # A retry will simply re-extract (overwriting) and try the remux again.
        await loop.run_in_executor(
            None, _finish_job, job_id, False, None, None, result.error
        )

# ── Sync DB helpers (run in thread executor) ───────────────────────────────────

def _claim_next() -> int | None:
    """Atomically claim the highest-priority pending job. Returns its ID or None."""
    db = SessionLocal()
    try:
        job = (
            db.query(QueueItem)
            .filter(QueueItem.status == "pending")
            .order_by(QueueItem.priority.asc(), QueueItem.created_at.asc())
            .first()
        )
        if job is None:
            return None

        job.status     = "processing"
        job.started_at = datetime.utcnow()
        db.commit()
        return job.id
    except Exception:
        db.rollback()
        logger.exception("Failed to claim next job")
        return None
    finally:
        db.close()


def _load_job_data(job_id: int):
    """
    Load everything _run_job needs, re-running the decision engine fresh —
    and APPLY that fresh decision's outcome, exactly as queue.py's
    _apply_decision_to_item does for its three call sites:

      • is_manual_review  → item transitions to "manual_review" (with the
        fresh reason and flagged subtitles), returns None — no execution.
      • should_process=False → item transitions to "skipped" (+ completed_at),
        returns None — no execution.
      • otherwise → PlannedActions refreshed to the FRESH decision's
        actions, returns (job_dict, file_dict, tracks, app_cfg, decision)
        for execution. Everything returned is plain Python — no ORM
        objects cross the thread boundary.

    The first two outcomes were previously unhandled entirely — any
    decision was returned for execution unconditionally. A pending item
    whose circumstances changed between queuing and pickup (settings
    edited, a threshold crossed, an override landing mid-queue) could
    compute a manual-review or nothing-to-do decision here, and instead
    of transitioning cleanly, the job would "execute" it: no-op decisions
    carry target_container=None, which build_ffmpeg_command now
    hard-rejects (deliberately, after the silent-matroska corruption
    fix), so the job failed with a baffling "Unsupported output container
    None" instead of just being marked skipped. Manual-review decisions
    were worse: executed as if approved, silently bypassing the gate.

    On returning None for either no-op outcome: _run_and_broadcast's
    finally block already broadcasts the item's final status whatever it
    is, and its stuck-job safety net only force-fails items still at
    "processing" — so transitioning here integrates with the existing
    machinery with no extra broadcast plumbing needed.

    _upsert_language_flags placement — AFTER the manual-review
    early-return, deliberately, mirroring both scanner.py's ordering and
    _apply_decision_to_item's: a manual-review decision returns before
    mismatch detection ever runs, so its mismatch fields are always None,
    and upserting on that outcome would incorrectly CLEAR valid existing
    flags (the exact hazard this function previously had — it called the
    upsert unconditionally, on every decision including manual-review
    ones). For the other two outcomes the upsert is what makes
    corrections resolved via Approve show up in Language Review without
    waiting for a later full scan (reported directly, fixed earlier);
    that behavior is preserved, now correctly guarded.
    """
    db = SessionLocal()
    try:
        job: QueueItem | None = db.get(QueueItem, job_id)
        if job is None:
            logger.error("Job %d not found", job_id)
            return None

        media: MediaFile | None = db.get(MediaFile, job.file_id)
        if media is None or not os.path.exists(media.path):
            _finish_job(job_id, False, None, None, "File not found on disk")
            return None

        tracks_raw: list[Track] = (
            db.query(Track).filter(Track.file_id == media.id).all()
        )
        tracks = [_track_to_dict(t) for t in tracks_raw]

        app_cfg    = get_app_settings(db)
        file_info  = {"path": media.path, "container": media.container,
                      "video_codec": media.video_codec,
                      "und_audio_threshold_acknowledged": media.und_audio_threshold_acknowledged}
        overrides  = _load_subtitle_overrides(media)
        audio_lang_overrides = _load_audio_language_overrides(media)
        subtitle_lang_overrides = _load_subtitle_language_overrides(media)
        faststart  = (
            is_faststart_mp4(media.path)
            if (media.container or "").lower() == "mp4"
            else None
        )
        forged_ac3_audio_index = _get_forged_ac3_audio_index(db, media.id)
        decision   = analyze_file(
            file_info, tracks, app_cfg,
            subtitle_overrides=overrides,
            audio_language_overrides=audio_lang_overrides,
            subtitle_language_overrides=subtitle_lang_overrides,
            has_faststart=faststart,
            forged_ac3_audio_index=forged_ac3_audio_index,
        )

        if decision.is_manual_review:
            job.status = "manual_review"
            job.reason = decision.reason
            job.review_subtitles = (
                json.dumps(decision.flagged_subtitles)
                if decision.flagged_subtitles else None
            )
            media.status = "manual_review"
            db.commit()
            return None

        _upsert_language_flags(db, media, decision)

        if not decision.should_process:
            job.status = "skipped"
            job.reason = decision.reason
            job.review_subtitles = None
            job.completed_at = datetime.utcnow()
            media.status = "skipped"
            db.commit()
            return None

        # Proceeding — refresh stored PlannedActions to match the FRESH
        # decision actually about to be executed. Previously the fresh
        # actions were executed while the stale scan-time rows stayed in
        # the DB, so the UI could show a plan that no longer matched what
        # the job was really doing.
        db.query(PlannedAction).filter(PlannedAction.queue_item_id == job.id).delete()
        db.flush()
        for i, action in enumerate(decision.actions):
            db.add(PlannedAction(
                queue_item_id    = job.id,
                order            = i,
                action_type      = action.action_type,
                description      = action.description,
                track_type       = action.track_type,
                stream_index     = action.stream_index,
                target_language  = getattr(action, "target_language", None),
            ))
        db.commit()

        return (
            {"id": job.id, "is_dry_run": job.is_dry_run},
            {"path": media.path, "filename": media.filename, "size": media.size},
            tracks,
            app_cfg,
            decision,
        )
    finally:
        db.close()


def _update_progress(job_id: int, percent: float, current_action: str) -> None:
    db = SessionLocal()
    try:
        job = db.get(QueueItem, job_id)
        if job:
            job.progress       = percent
            job.current_action = current_action
            db.commit()
    except Exception:
        db.rollback()
    finally:
        db.close()


def _finish_job(
    job_id:      int,
    success:     bool,
    output_path: str | None,
    output_size: int | None,
    error:       str | None,
) -> None:
    db = SessionLocal()
    try:
        job: QueueItem | None = db.get(QueueItem, job_id)
        if job is None:
            return

        # Dry runs get their own terminal status — they must never be
        # indistinguishable from a real "success" in the History panel,
        # since no file was actually touched.
        if job.is_dry_run:
            job.status = "dry_run" if success else "failed"
        else:
            job.status = "success" if success else "failed"

        job.completed_at  = datetime.utcnow()
        if success:
            job.progress = 100.0   # leave progress untouched on failure
        job.output_path   = output_path
        job.output_size   = output_size
        job.error_message = error

        media: MediaFile | None = db.get(MediaFile, job.file_id)
        if media:
            if success:
                if job.is_dry_run:
                    # Dry run finished — leave status as "queued" so the
                    # next real scan re-evaluates and queues for real processing.
                    media.status = "queued"
                else:
                    media.status         = "processed"
                    media.last_processed = datetime.utcnow()
                    # Track new path if container changed (e.g. MKV → MP4)
                    if output_path and output_path != media.path:
                        # A stale MediaFile row from a previous processing
                        # cycle (dismiss → re-copy original → re-scan) may
                        # already own the target path.  The file it pointed
                        # to no longer exists on disk, so delete it — along
                        # with its Track rows (cascade) — before updating
                        # the current row to avoid a UNIQUE constraint error.
                        stale = (
                            db.query(MediaFile)
                            .filter(
                                MediaFile.path == output_path,
                                MediaFile.id   != media.id,
                            )
                            .first()
                        )
                        if stale:
                            logger.info(
                                "Removing stale MediaFile row for %s "
                                "(left from a previous dismiss/re-scan cycle)",
                                output_path,
                            )
                            db.delete(stale)
                            db.flush()   # ensure deletion lands before the UPDATE

                        media.path      = output_path
                        media.filename  = os.path.basename(output_path)
                        media.directory = os.path.dirname(output_path)
                        # Update stored container so the history panel
                        # shows the correct format (e.g. MP4 not MKV)
                        new_ext = os.path.splitext(output_path)[1].lower()
                        _EXT_TO_CONTAINER = {
                            ".mp4": "mp4", ".m4v": "mp4", ".mov": "mp4",
                            ".mkv": "mkv", ".avi": "avi", ".ts": "ts",
                            ".m2ts": "ts", ".wmv": "wmv", ".webm": "webm",
                        }
                        new_container = _EXT_TO_CONTAINER.get(new_ext)
                        if new_container:
                            media.container = new_container

                    # Refresh stored size + mtime to the processed file's
                    # on-disk stats.  Without this, the DB still holds the
                    # ORIGINAL file's values.  If the user later dismisses
                    # the job, deletes the processed file, and restores the
                    # original (preserving its timestamps), size+mtime match
                    # the stale DB values exactly — the scanner sees no
                    # change and skips the file even though it needs
                    # re-processing.  Updating here ensures any restored
                    # original (different size or mtime) triggers a re-probe.
                    final_path = output_path if output_path else media.path
                    try:
                        st = os.stat(final_path)
                        media.size  = st.st_size
                        media.mtime = st.st_mtime
                    except OSError:
                        pass  # non-critical — worst case next scan re-probes

                    # Re-probe and replace this file's Track rows to match
                    # what's actually on disk now.
                    #
                    # This step exists because of a real, confirmed gap:
                    # syncing size/mtime above (needed to fix a DIFFERENT
                    # bug — a restored original file looking "unchanged")
                    # is exactly what makes every future DELTA scan (the
                    # default mode, and what the scheduler always uses)
                    # see this file as unchanged and skip re-probing it —
                    # permanently, until someone runs a full/forced
                    # rescan. Without refreshing Track rows here, they'd
                    # keep describing the PRE-processing file forever.
                    #
                    # Concrete impact this was causing: AC3 Forge's own
                    # candidate query (files with an AAC 5.1 track and no
                    # existing forge job) depends entirely on the Track
                    # table's codec/channels fields staying accurate. A
                    # stale row can point that query wrong in either
                    # direction — still offering a file whose audio isn't
                    # actually AAC anymore, or failing to offer one that
                    # now is, depending on what the pipeline just changed.
                    #
                    # A probe failure here is non-critical in the same
                    # spirit as the size/mtime OSError above — the actual
                    # FFmpeg work already succeeded and the file is
                    # correctly on disk; this is best-effort bookkeeping,
                    # not something that should turn a real success into
                    # a failure. Worst case, a future full rescan corrects it.
                    try:
                        probe_data = probe_file(final_path, app_settings.FFPROBE_PATH)
                        fmt_info   = extract_format_info(probe_data)
                        track_list = extract_tracks(probe_data)

                        db.query(Track).filter(Track.file_id == media.id).delete()
                        for td in track_list:
                            db.add(Track(
                                file_id        = media.id,
                                stream_index   = td["stream_index"],
                                track_type     = td["track_type"],
                                codec          = td["codec"],
                                language       = td["language"],
                                channels       = td.get("channels"),
                                channel_layout = td.get("channel_layout"),
                                is_default          = td.get("is_default", False),
                                is_forced           = td.get("is_forced", False),
                                is_hearing_impaired = td.get("is_hearing_impaired", False),
                                is_dub              = td.get("is_dub", False),
                                title          = td.get("title"),
                                # codec_long/raw_ffprobe/sample_rate/bit_rate
                                # deliberately not populated — see the matching
                                # comment in scanner.py's own Track creation.
                            ))

                        primary_video_codec = next(
                            (t["codec"] for t in track_list if t["track_type"] == "video"),
                            None,
                        )
                        media.duration    = fmt_info.get("duration")
                        media.video_codec = primary_video_codec
                        # Always wins over the extension-based guess above
                        # (used when the path changed) — an actual fresh
                        # probe is strictly more reliable than inferring
                        # the container from a filename extension.
                        fresh_container = fmt_info.get("container")
                        if fresh_container:
                            media.container = fresh_container
                    except ProbeError as exc:
                        logger.warning(
                            "Post-job track refresh failed for %s: %s — "
                            "Track rows may be stale until the next full "
                            "rescan",
                            final_path, exc,
                        )
            else:
                media.status = "error"

        db.commit()
        logger.info("Job %d → %s%s", job_id,
                    "success" if success else "failed",
                    f" ({error})" if error else "")
    except Exception as exc:
        db.rollback()
        logger.exception("Failed to finalise job %d", job_id)
        # Emergency fallback: open a FRESH connection (not contaminated by
        # the failed transaction above) and do a minimal status update so
        # the job doesn't stay "processing" indefinitely in the UI.
        _emergency_fail_job(
            job_id,
            f"Finalisation failed: {type(exc).__name__}: {exc}",
        )
    finally:
        db.close()


def _emergency_fail_job(job_id: int, reason: str) -> None:
    """
    Minimal last-resort update: mark a job as 'failed' using a brand-new
    database session that is not contaminated by any previous transaction.

    Called when _finish_job's main commit fails (e.g. database is locked)
    to ensure the job always leaves the "processing" state and the UI does
    not get stuck showing 100% progress indefinitely.
    """
    try:
        with SessionLocal() as db:
            job = db.get(QueueItem, job_id)
            if job and job.status == "processing":
                job.status        = "failed"
                job.error_message = reason[:500]
                job.completed_at  = datetime.utcnow()
                if job.media_file:
                    job.media_file.status = "error"
                db.commit()
                logger.info(
                    "Emergency cleanup: job %d marked 'failed' via fresh session",
                    job_id,
                )
    except Exception:
        # At this point there is nothing more we can do — the startup
        # cleanup in start_worker() will catch it on the next restart.
        logger.exception(
            "Emergency cleanup also failed for job %d — "
            "job will be reset to 'failed' on next restart",
            job_id,
        )


def _load_post_job_data(job_id: int) -> dict | None:
    """
    Consolidated read for everything _run_and_broadcast needs after a job
    finishes: the final status/filename/error for the WebSocket broadcast,
    plus Sonarr and Radarr notification data if applicable.

    This replaces what used to be three separate sequential SessionLocal()
    opens (_load_final_state, then _load_arr_notify_data called once for
    Sonarr and again for Radarr) with one. All three were pure reads with
    no mutation, so merging them changes no observable behavior — it only
    reduces how many times this, the highest-frequency code path in the
    app (runs after every single completed job), checks a connection out
    of the pool. Under a busy scan with several jobs completing close
    together, each additional sequential session open is one more chance
    to collide with SQLite's single-writer lock and sit blocked holding a
    pool slot for up to the 30s busy_timeout — cutting three opens down to
    one directly reduces that exposure.

    Sonarr/Radarr data is computed unconditionally here (previously it was
    only fetched when the job succeeded) since the extra in-session reads
    are effectively free once the session is already open — the caller
    still only acts on them when final["status"] == "success", so this
    changes no observable output, only when the read happens.
    """
    with SessionLocal() as db:
        job: QueueItem | None = db.get(QueueItem, job_id)
        if job is None:
            return None

        final = {
            "status":      job.status,
            "filename":    job.media_file.filename if job.media_file else "",
            "error":       job.error_message,
            "is_new_file": job.is_new_file,
            "output_path": job.output_path,
        }

        cfg = get_app_settings(db)

        def _arr_data(id_attr, enabled_key, url_key, api_key_setting, service_name):
            if not getattr(job, id_attr):
                return None
            if not cfg.get(enabled_key, False):
                return None
            url     = (cfg.get(url_key) or "").rstrip("/")
            api_key = (cfg.get(api_key_setting) or "")
            if not url or not api_key:
                logger.warning(
                    "%s notification skipped for job %d: %s or %s not configured",
                    service_name, job_id, url_key, api_key_setting,
                )
                return None
            return {
                "entity_id": getattr(job, id_attr),
                "url":       url,
                "api_key":   api_key,
            }

        sonarr = _arr_data(
            "sonarr_series_id", "sonarr_enabled",
            "sonarr_url", "sonarr_api_key", "Sonarr",
        )
        radarr = _arr_data(
            "radarr_movie_id", "radarr_enabled",
            "radarr_url", "radarr_api_key", "Radarr",
        )

        return {"final": final, "sonarr": sonarr, "radarr": radarr}


async def _trigger_arr_notify(
    data:         dict,
    loop:         asyncio.AbstractEventLoop,
    notify_fn,              # notify_sonarr or notify_radarr
    service_name: str,
) -> None:
    """Fire-and-forget task: calls the given *arr notify function in the thread pool."""
    try:
        await loop.run_in_executor(
            None, notify_fn,
            data["url"], data["api_key"], data["entity_id"],
        )
    except Exception:
        logger.exception(
            "%s post-job notification failed for entity %d",
            service_name, data["entity_id"],
        )


def _load_plex_notify_data(job_id: int) -> dict | None:
    """
    Load Plex notification parameters — an immediate lightweight library
    refresh is returned for EVERY successful job, regardless of whether it
    was classified as new or reprocessed, and regardless of the backlog
    toggle below. For reprocessed files, IF plex_analyze_backlog_enabled
    is on (off by default), this ALSO enqueues a PlexAnalyzeBacklog row so
    a delayed explicit Analyze runs later (during the configured window)
    to fix stream metadata that a plain refresh alone won't force Plex to
    re-read.

    The backlog is a separate, opt-in safety net rather than something
    that runs unconditionally: direct testing across a 1,300-item backlog
    showed the refresh below (combined with Plex's own scheduled
    maintenance) already catches the overwhelming majority of reprocessed
    files on its own. Most installs won't need the backlog at all.

    Returning the refresh data unconditionally (rather than only for
    is_new_file cases) is what protects against is_new_file being wrong —
    see the inline comment below for why that classification can diverge
    from Plex's actual indexed state.

    Unlike Sonarr/Radarr (which only fire for webhook-triggered jobs), Plex
    notification is considered for EVERY successful job when enabled — Plex
    needs to know about every file Remuxarr touches, not just *arr-originated
    ones.
    """
    with SessionLocal() as db:
        job = db.get(QueueItem, job_id)
        if not job:
            return None
        cfg = get_app_settings(db)
        if not cfg.get("plex_enabled", False):
            return None
        url   = (cfg.get("plex_url") or "").rstrip("/")
        token = cfg.get("plex_token") or ""
        if not url or not token:
            logger.warning(
                "Plex notification skipped for job %d: plex_url or "
                "plex_token not configured",
                job_id,
            )
            return None
        mappings = cfg.get("plex_path_mappings", [])
        if not mappings:
            logger.warning(
                "Plex notification skipped for job %d: no plex_path_mappings "
                "configured",
                job_id,
            )
            return None

        # Always fire the immediate lightweight refresh, regardless of
        # is_new_file. A plain refresh is a single cheap call (no full
        # section-item-listing fetch — that expense is unique to the
        # Analyze path below) so doing it unconditionally is safe even
        # during a large batch, and it's what actually fixes the case
        # where is_new_file was WRONG:
        #
        # is_new_file reflects "does Remuxarr's own DB already have a row
        # for this path" — a proxy for "has Plex already indexed this
        # file" that can diverge from reality (a stale row from an earlier
        # partial import, a previous version that got deleted and
        # re-downloaded, an earlier attempt that never actually reached
        # Plex, etc.). When that happens, the reprocessed branch below
        # would otherwise queue the file for the backlog and it would sit
        # there — possibly for hours — until the analyze window opens,
        # even though Plex never had anything to "re"-analyze in the
        # first place. Firing the refresh here means a misclassified new
        # file still gets Plex's automatic deep-scan immediately; the
        # backlog's later Analyze attempt (if is_new_file is genuinely
        # False) becomes a harmless, correctly-targeted no-op in that
        # case since the file will already be properly indexed by then.
        refresh_data = {
            "url":        url,
            "token":      token,
            "mappings":   mappings,
            "local_path": job.output_path,
        }

        if job.is_new_file:
            return refresh_data

        # Reprocessed file. Whether this ALSO queues for the backlog drain
        # is gated by its own separate toggle, off by default — direct
        # testing across a 1,300-item backlog showed the refresh above
        # (plus Plex's own scheduled maintenance) already catches the
        # overwhelming majority of reprocessed files on its own, so this
        # queue is an opt-in safety net rather than something that should
        # run unconditionally for every reprocess. See settings.py's
        # "Plex Analyze Backlog" group.
        if not cfg.get("plex_analyze_backlog_enabled", False):
            return refresh_data

        # Queue for the backlog drain so a delayed explicit Analyze runs
        # later to catch the case where Plex DOES already have this path
        # indexed with now-stale stream metadata (the refresh above alone
        # won't force that re-read). Dedup: skip if this file already has
        # a pending backlog entry (e.g. retried twice before the previous
        # entry drained).
        existing = (
            db.query(PlexAnalyzeBacklog)
            .filter(PlexAnalyzeBacklog.file_id == job.file_id)
            .first()
        )
        if not existing:
            # If this job involved a language-tag fix, record which
            # language was set so the drain loop can check whether Plex's
            # own scheduled maintenance already picked it up before
            # bothering with an explicit Analyze call. Confirmed (via
            # manual testing) that Plex's own maintenance does often catch
            # these on its own, just not reliably for every file — this
            # lets Remuxarr skip the redundant Analyze call for the files
            # Plex already got right, while still guaranteeing correctness
            # via the fallback for the ones it missed.
            lang_action = (
                db.query(PlannedAction)
                .filter(
                    PlannedAction.queue_item_id == job.id,
                    PlannedAction.target_language.isnot(None),
                )
                .first()
            )
            expected_language = lang_action.target_language if lang_action else None

            # Deliberately explicit rather than folded into the queued-for-
            # backlog message below — this is the one line that tells us,
            # unambiguously, whether the capture at THIS end worked. If
            # this ever logs "no target_language found on any PlannedAction"
            # for a job whose reason clearly says "Fix undefined language
            # tag", the bug is here (or upstream in how actions are
            # persisted) — not in the Plex-side verification check.
            logger.info(
                "Plex: backlog capture for job %d (reason=%r) — "
                "expected_language=%r",
                job.id, job.reason, expected_language,
            )

            db.add(PlexAnalyzeBacklog(
                file_id=job.file_id,
                expected_language=expected_language,
            ))
            db.commit()
            logger.info(
                "Plex: queued %s for backlog analyze (drains during the "
                "configured window)%s",
                job.output_path,
                f" — will verify language={expected_language} first" if expected_language else "",
            )
        return refresh_data


async def _trigger_plex_notify(data: dict, loop: asyncio.AbstractEventLoop) -> None:
    """
    Fire-and-forget task that fires the immediate lightweight refresh.
    Called for every successful job when Plex is enabled — both new and
    reprocessed files get this call; reprocessed files additionally get a
    PlexAnalyzeBacklog entry queued (see _load_plex_notify_data) for a
    delayed explicit Analyze on top of this refresh.
    """
    try:
        await loop.run_in_executor(
            None, notify_plex_new_file,
            data["url"], data["token"], data["mappings"], data["local_path"],
        )
    except Exception:
        logger.exception(
            "Plex post-job notification failed for %s", data["local_path"],
        )


def _load_email_notify_data(job_id: int) -> dict | None:
    """
    Decide whether a failure-notification email should be sent for this
    job, updating the consecutive-failure circuit breaker in the process.

    Called once per finished job, for every outcome — success, dry_run
    preview, or failed — never for cancelled/skipped/manual_review, since
    those statuses are set via entirely separate code paths that never
    reach _run_and_broadcast's completion block at all.

    Breaker semantics:
      success / dry_run  → counter resets to 0, breaker un-trips.
                            This happens even if email_enabled is False, so
                            the state stays accurate if email gets turned
                            on again later. A dry-run preview success
                            counts as a reset too — it demonstrates the
                            pipeline is currently working for that file.
      failed              → counter increments by 1, UNTIL the breaker
                            trips; once tripped the counter freezes and
                            stays frozen until a success resets it —
                            identically whether or not email is enabled
                            (so toggling email off after a trip can't
                            resume counting). The frozen value is the
                            "tripped at N" figure.
                            A dry-run preview FAILURE counts as a real
                            failure here too — if dry-run previews are
                            failing due to a config mistake, that's exactly
                            the kind of thing this feature should catch,
                            arguably more urgently, since the user is
                            actively testing a change at that point.

    Threshold crossing produces exactly ONE "tripped" email; every failure
    after that produces nothing at all until a success resets the breaker.

    Returns None (nothing to send) or a dict describing the one email to
    send — handed to _trigger_email_notify for the actual SMTP work.
    """
    with SessionLocal() as db:
        job = db.get(QueueItem, job_id)
        if not job or job.status not in ("success", "failed", "dry_run"):
            return None

        cfg = get_app_settings(db)

        state = db.get(NotificationState, 1)
        if state is None:
            state = NotificationState(id=1, consecutive_failures=0, breaker_tripped=False)
            db.add(state)
            db.flush()

        if job.status in ("success", "dry_run"):
            state.consecutive_failures = 0
            state.breaker_tripped      = False
            db.commit()
            return None

        # job.status == "failed"
        threshold = cfg.get("email_failure_threshold", 5)

        # Once tripped, the incident is already "open": no further
        # counting and no further emails until a success resets it —
        # identically whether or not email is enabled. Previously the
        # email-DISABLED branch kept incrementing past the threshold
        # while the email-ENABLED+tripped branch froze, so toggling email
        # off after a trip silently resumed counting and the two paths
        # disagreed about the same state. The frozen value is the
        # "tripped at N" figure; keeping it stable is what makes that
        # number meaningful.
        if state.breaker_tripped:
            return None

        state.consecutive_failures += 1
        if state.consecutive_failures >= threshold:
            state.breaker_tripped = True

        # Email off: breaker state is now updated and accurate (the
        # reason this runs even while disabled — so it's coherent if
        # email is re-enabled later); nothing to send.
        if not cfg.get("email_enabled", False):
            db.commit()
            return None

        if state.breaker_tripped:
            # Crossed the threshold on THIS failure → the one tripped
            # email (which doesn't include a filename).
            db.commit()
            return {"kind": "tripped", "count": state.consecutive_failures, "cfg": cfg}

        # Only the per-failure email below needs the filename, so resolve
        # it here rather than before the tripped branch that ignores it.
        media    = job.media_file
        filename = media.filename if media else "unknown file"
        db.commit()
        return {
            "kind":     "failure",
            "filename": filename,
            "error":    job.error_message,
            "count":    state.consecutive_failures,
            "cfg":      cfg,
        }


async def _trigger_email_notify(data: dict, loop: asyncio.AbstractEventLoop) -> None:
    """Fire-and-forget task: sends the email decided by _load_email_notify_data."""
    try:
        if data["kind"] == "tripped":
            await loop.run_in_executor(
                None, send_breaker_tripped_email, data["cfg"], data["count"],
            )
        else:
            await loop.run_in_executor(
                None, send_failure_email,
                data["cfg"], data["filename"], data["error"], data["count"],
            )
    except Exception:
        logger.exception("Email notification dispatch failed for job")


# ── Forge job processing ───────────────────────────────────────────────────────

async def _process_next_forge(ws_manager) -> bool:
    """Claim and run the next pending forge job. Returns True if a job ran."""
    from app.core.forge import (
        claim_next_forge_job, load_forge_job_data,
        build_add_ac3_command, build_undo_command,
        run_forge_command, ForgeProgress,
        update_forge_progress, finish_forge_job, load_forge_final_state,
    )

    loop   = asyncio.get_running_loop()
    job_id = await loop.run_in_executor(None, claim_next_forge_job)
    if job_id is None:
        return False

    logger.info("Forge worker picked up job %d", job_id)
    await ws_manager.broadcast_json({"event": "forge_job_started", "job_id": job_id})

    job_data = await loop.run_in_executor(None, load_forge_job_data, job_id)
    if job_data is None:
        # The job reached a terminal state inside load_forge_job_data
        # itself (file missing, probe failure, AC3 already absent →
        # undone, or a layout mismatch → failed) — broadcast that final
        # state now, same as the post-execution path below does.
        # Previously this returned silently, so a job settled at load
        # time looked stuck in the UI until the next poll happened by.
        final = await loop.run_in_executor(None, load_forge_final_state, job_id)
        if final:
            await ws_manager.broadcast_json({
                "event":    "forge_job_completed",
                "job_id":   job_id,
                "status":   final["status"],
                "filename": final["filename"],
                "error":    final.get("error"),
            })
        return True   # job was claimed and settled — counts as "ran"

    input_path = job_data["file_path"]

    # Mirrors the main pipeline's exact formula (_run_job, worker.py) —
    # see run_forge_command's docstring for why this is here at all.
    timeout_minutes = job_data.get("job_timeout_minutes", 120)
    timeout_seconds = float(timeout_minutes) * 60 if timeout_minutes else None

    # Stage the temp file in TEMP_DIR (RAM-backed tmpfs on Unraid) when
    # there's enough free space, falling back to the source file's own
    # directory otherwise — same space-aware logic the main remux pipeline
    # uses. Previously this just did `input_path + ".forge_tmp"`, writing
    # directly next to the source on the array for every add/undo, which
    # meant forge jobs got none of the benefit of staging through RAM and
    # added extra array I/O contention during processing.
    #
    # Named from job_id, not the original basename — mirrors
    # ffmpeg.execute_ffmpeg's own temp naming exactly. Using the original
    # basename here (even after the RAM-staging fix above) reintroduced
    # the exact NAME_MAX failure the main pipeline already fixed:
    # appending a suffix to an already-long Sonarr-style filename can push
    # it past the 255-byte filesystem component limit (confirmed in
    # production there: a 247-byte original filename failed). A job_id is
    # always short and unique, so this can never happen here.
    tmp_dir   = _pick_temp_dir(input_path)
    temp_path = os.path.join(tmp_dir, f"forge_{job_id}.forge_tmp")
    os.makedirs(tmp_dir, exist_ok=True)

    async def on_progress(prog: ForgeProgress) -> None:
        loop.run_in_executor(
            None, update_forge_progress, job_id, prog.percent, prog.action
        )
        await ws_manager.broadcast_json({
            "event":          "forge_job_progress",
            "job_id":         job_id,
            "progress":       round(prog.percent, 1),
            "current_action": prog.action,
            "speed":          prog.speed,
        })

    try:
        # Command building sits INSIDE the try deliberately — the builders
        # now raise ValueError on containers their format map doesn't know
        # (instead of silently corrupting the file with a matroska
        # default), and that failure must mark the forge job failed via
        # the handler below, not escape this function uncaught.
        if job_data["is_undo"]:
            action_label = "Removing AC3 5.1 track"
            cmd = build_undo_command(
                input_path              = input_path,
                # Resolved against a fresh probe at load time — NOT the
                # stored add-time audio_track_count. After any pipeline
                # drop that index points past the end, and FFmpeg
                # silently ignores an unmatched negative map: the old
                # undo rewrote the file unchanged and recorded a false
                # "undone" with the AC3 still embedded. See
                # resolve_forge_ac3_for_undo.
                ac3_audio_output_index  = job_data["undo_audio_output_index"],
                temp_path               = temp_path,
                container               = job_data["container"],
            )
        else:
            action_label = "Adding AC3 5.1 track"
            cmd = build_add_ac3_command(
                input_path        = input_path,
                temp_path         = temp_path,
                aac_stream_index  = job_data["aac_stream_index"],
                audio_track_count = job_data["audio_track_count"],
                container         = job_data["container"],
            )

        result = await run_forge_command(
            cmd           = cmd,
            input_path    = input_path,
            output_path   = input_path,   # overwrite in-place
            temp_path     = temp_path,
            action_label  = action_label,
            progress_callback = on_progress,
            timeout_seconds   = timeout_seconds,
        )
    except Exception as exc:
        logger.exception("Forge job %d raised an exception", job_id)
        await loop.run_in_executor(
            None, finish_forge_job, job_id, False, None, None, str(exc)
        )
    else:
        await loop.run_in_executor(
            None, finish_forge_job,
            job_id, result.success, result.output_path, result.output_size, result.error
        )

    final = await loop.run_in_executor(None, load_forge_final_state, job_id)
    if final:
        await ws_manager.broadcast_json({
            "event":    "forge_job_completed",
            "job_id":   job_id,
            "status":   final["status"],
            "filename": final["filename"],
            "error":    final.get("error"),
        })

    return True
