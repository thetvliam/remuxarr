"""
FFmpeg command builder and async executor.

build_ffmpeg_command()       — pure function, returns argv for the main remux
execute_ffmpeg()              — runs the main remux, streams progress via async callback
build_extract_subtitle_command() — pure function, argv for one SRT extraction
execute_subtitle_extraction()    — runs a single subtitle extraction to a sidecar .srt
determine_output_path()       — decides where to write the output file
"""

import logging
import os
import shutil
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path

from app.config import settings as app_settings
from app.core.decision import ProcessingDecision
from app.core.subprocess_runner import (
    StagedOutput,
    cleanup_temp_file,
    parse_out_time_seconds,
    probe_duration,
    run_staged_subprocess,
)

logger = logging.getLogger(__name__)


# ── Data classes ───────────────────────────────────────────────────────────────


@dataclass
class FFmpegProgress:
    percent: float  # 0–100
    current_time: float  # seconds processed so far
    speed: str  # "2.50x"
    current_action: str  # human label shown in the UI


@dataclass
class FFmpegResult:
    success: bool
    output_path: str | None
    error: str | None
    output_size: int | None


@dataclass
class ExtractionResult:
    success: bool
    output_path: str | None
    error: str | None


# ── Command builder — main remux ────────────────────────────────────────────────


# Normalised container name (probe._normalise_container) → FFmpeg muxer.
#
# Module-level rather than function-local so build_restore_command shares
# the one table: a revert writes the ORIGINAL container back, and a second
# copy of this map is a second place for the mkv/webm trap below to be got
# wrong.
_CONTAINER_FORMAT = {
    "mkv": "matroska",
    "mp4": "mp4",
    "avi": "avi",
    # No "m2ts" entry — _normalise_container (probe.py) can never
    # actually produce it: every real .m2ts file's ffprobe format_name
    # contains "mpegts", which the "ts" branch there always matches
    # first, before any fallback path could return "m2ts" literally.
    # Confirmed directly, including during the real .m2ts investigation
    # for F-B2 earlier — genuinely unreachable, not just unlikely.
    "ts": "mpegts",
    "wmv": "asf",
    # "webm" and "mov" are unreachable defensive keys, kept for
    # intent (matching probe.py's annotated copy): _normalise_container
    # can never return "webm" (its format_name always contains
    # "matroska", matched first) and maps "mov"→"mp4", and
    # _EXT_TO_CONTAINER maps .mov→"mp4" — so target_container is never
    # either value. Nothing reaches these; they just document the
    # intended format if that ever changes.
    "webm": "webm",
    "mov": "mov",
}


def build_ffmpeg_command(
    input_path: str,
    output_path: str,
    decision: ProcessingDecision,
    all_tracks: list[dict],
) -> list[str]:
    """
    Return the full FFmpeg argv for this decision.

    Strategy
    --------
    • All video streams  → always mapped, always copy
    • Kept audio streams → copy, OR transcode (only ever via the
      corrupt-audio retry path — worker.py's _make_audio_transcode_decision)
    • Kept sub streams   → copy (subtitles being extracted to external SRT,
      or dropped entirely, are excluded from the map list)
    • -progress pipe:1   → structured key=value progress on stdout
    • -nostats -v error  → suppress noisy stderr; errors still appear
    """
    dropped = {
        a.stream_index for a in decision.actions if a.action_type == "drop_track"
    }
    # Subtitles being extracted to an external .srt are removed from the
    # muxed output entirely — treat them the same as "dropped" for mapping.
    extracted = {
        a.stream_index for a in decision.actions if a.action_type == "extract_subtitle"
    }
    # transcode_track actions only ever come from worker.py's corrupt-audio
    # retry path (_make_audio_transcode_decision) — re-encoding audio when
    # a lossless copy failed because the source frames themselves are
    # corrupt. decision.py's own normal path never produces this directly;
    # there's no setting that does anymore.
    transcode_map = {
        a.stream_index: a
        for a in decision.actions
        if a.action_type == "transcode_track"
    }
    # Tracks whose language metadata should be overwritten — set by
    # decision.py's language-fix pass when fix_undefined_language is enabled.
    language_map = {
        a.stream_index: a.target_language
        for a in decision.actions
        if getattr(a, "target_language", None)
    }

    cmd = [
        app_settings.FFMPEG_PATH,
        "-i", input_path,
        "-y",
        "-v", "error",  # suppress info/warnings but keep error messages
        "-nostats",
        "-progress", "pipe:1",  # structured progress → stdout
    ]

    video_tracks = [t for t in all_tracks if t["track_type"] == "video"]
    audio_tracks = [t for t in all_tracks if t["track_type"] == "audio"]
    sub_tracks   = [t for t in all_tracks if t["track_type"] == "subtitle"]

    # ── Video: always copy ─────────────────────────────────────────────────
    for t in video_tracks:
        cmd += ["-map", f"0:{t['stream_index']}"]
    if video_tracks:
        cmd += ["-c:v", "copy"]

    # ── Audio ──────────────────────────────────────────────────────────────
    kept_audio = [t for t in audio_tracks if t["stream_index"] not in dropped]
    for out_idx, t in enumerate(kept_audio):
        si = t["stream_index"]
        cmd += ["-map", f"0:{si}"]
        action = transcode_map.get(si)
        if action and action.track_type == "audio":
            cmd += [f"-c:a:{out_idx}", action.output_codec]
            for opt_k, opt_v in action.output_codec_options.items():
                # e.g. -ac:0 6 — used by the corrupt-audio retry path when
                # it needs to force a specific channel count; normally
                # empty, which preserves the source's own channel layout.
                cmd += [f"-{opt_k}:{out_idx}", str(opt_v)]
        else:
            cmd += [f"-c:a:{out_idx}", "copy"]
        if si in language_map:
            cmd += [f"-metadata:s:a:{out_idx}", f"language={language_map[si]}"]

    # ── Subtitles ──────────────────────────────────────────────────────────
    # Anything dropped or extracted to external SRT is excluded from the
    # muxed output. Everything else (only possible when SRT extraction is
    # disabled) is copied as-is — no transcoding.
    kept_subs = [
        t for t in sub_tracks
        if t["stream_index"] not in dropped and t["stream_index"] not in extracted
    ]
    for out_idx, t in enumerate(kept_subs):
        cmd += ["-map", f"0:{t['stream_index']}"]
        if t["stream_index"] in language_map:
            cmd += [f"-metadata:s:s:{out_idx}", f"language={language_map[t['stream_index']]}"]
    if kept_subs:
        cmd += ["-c:s", "copy"]

    # ── Output format & flags ──────────────────────────────────────────────
    # Always pass -f explicitly: the temp file ends in .remuxarr_tmp which
    # FFmpeg doesn't recognise, so it would otherwise refuse to mux.
    # Hard-fail on any container this map doesn't know, rather than
    # silently defaulting to matroska. The old `.get(..., "matroska")`
    # default was a genuine file-corruption bug: MEDIA_EXTENSIONS accepts
    # containers (e.g. .flv) that _normalise_container passes through
    # unchanged and this map has no entry for — those files got Matroska
    # bytes written into their original path, in place, with the original
    # deleted after "success". analyze_file's own container guard can't
    # catch this case (the container string is present and non-empty; it
    # just isn't muxable-as-itself here). Raising is caught by the
    # worker's job-level exception handler and becomes a visible failed
    # job with this message — and, critically, it fires before FFmpeg
    # ever starts, so the source file is never touched.
    if decision.target_container not in _CONTAINER_FORMAT:
        raise ValueError(
            f"Unsupported output container {decision.target_container!r} — "
            f"refusing to guess the mux format (supported: "
            f"{', '.join(sorted(_CONTAINER_FORMAT))})"
        )
    out_fmt = _CONTAINER_FORMAT[decision.target_container]
    logger.info(
        "build_ffmpeg_command: decision.target_container=%r -> out_fmt=%r "
        "(output_path=%s)",
        decision.target_container, out_fmt, output_path,
    )
    cmd += ["-f", out_fmt]

    # Attachments — fonts for styled subtitles, cover art, posters.
    #
    # Without this map they are silently destroyed on every single remux,
    # including a pure metadata fix that changes nothing else. The maps
    # above are built from extract_tracks(), which returns only video,
    # audio and subtitle streams, so no attachment has ever been named in
    # a -map argument; FFmpeg's default stream selection does not pick
    # them up, and the operation reports success. A file loses its fonts
    # and the only evidence is that styled subtitles start rendering in a
    # fallback typeface some time later.
    #
    # The "?" makes the map optional: without it, FFmpeg exits non-zero
    # on any file that has no attachments, which is most of them.
    # Verified both branches directly — attachments preserved when
    # present, exit 0 when absent.
    #
    # Gated on the output format, not skipped defensively. Mapping an
    # attachment into MP4 is not a no-op, it is a hard failure at header
    # write ("Could not find tag for codec ttf"), so this must fire only
    # for containers that can hold one. An MKV → MP4 conversion therefore
    # still loses attachments, which is a real property of MP4 rather
    # than something to work around here.
    if out_fmt in ("matroska", "webm"):
        cmd += ["-map", "0:t?"]

    # Apply +faststart when the output is MP4 AND the add_faststart_to_mp4
    # setting is on. The setting is an absolute off switch: with it disabled
    # the flag is never emitted here, on any path. Three cases can otherwise
    # call for it:
    #   1. Container conversion (MKV → MP4): web-optimise the new file. A
    #      genuinely new MP4 should normally be web-optimised — but not when
    #      the user has turned the feature off.
    #   2. add_faststart action: rewriting an EXISTING MP4 that was
    #      missing it. decision.py only generates that action when
    #      add_faststart_to_mp4 is enabled AND the existing file genuinely
    #      needs it, so this case was already gated.
    #   3. source_already_faststart: the source was ALREADY MP4 and
    #      ALREADY faststart-optimised — preserve that on ANY remux,
    #      regardless of why this remux is happening (a language
    #      correction, a track drop, anything). Confirmed directly: a
    #      plain FFmpeg remux that doesn't explicitly include this flag
    #      silently rebuilds the container with the moov atom at the
    #      end, even for a pure, lossless stream-copy with nothing
    #      re-encoded — so with the setting ON, dropping this case would
    #      quietly undo an already-correct file's optimisation as a side
    #      effect, only for a later scan to "discover" it's missing
    #      again and have to re-add it.
    #
    # Cases 1 and 3 used to fire regardless of the setting, so switching it
    # off still produced +faststart on almost every MP4 output — the only
    # thing it actually suppressed was case 2. decision.faststart_enabled now
    # gates all three. With the setting off, case 3 no longer fires and an
    # already-optimised MP4 loses faststart the next time anything remuxes
    # it; that is the intended meaning of "never apply it", and nothing
    # re-adds it, since needs_faststart in decision.py is gated on the same
    # setting and so raises no work.
    #
    # Before either change, this only checked target_container == "mp4",
    # which is true for every MP4 output regardless of the setting's value or
    # whether an add_faststart action was ever generated — meaning the
    # setting had no effect at all and every MP4 got +faststart
    # unconditionally, including plain in-place edits (e.g. a pure
    # language-tag fix) on files that already had it correctly disabled.
    has_container_conversion = any(a.action_type == "change_container" for a in decision.actions)
    has_faststart_action     = any(a.action_type == "add_faststart"    for a in decision.actions)
    if decision.target_container == "mp4" and decision.faststart_enabled and (
        has_container_conversion
        or has_faststart_action
        or decision.source_already_faststart
    ):
        cmd += ["-movflags", "+faststart"]

    cmd.append(output_path)
    return cmd


# ── Command builder — subtitle extraction ────────────────────────────────────────


def build_extract_subtitle_command(
    input_path: str,
    stream_index: int,
    output_srt_path: str,
) -> list[str]:
    """
    Return the FFmpeg argv to extract a single subtitle stream to an
    external SubRip (.srt) file.

    Works for any text-based subtitle codec FFmpeg can decode (SubRip,
    mov_text, ASS/SSA) — the "srt" subtitle encoder handles the conversion.
    """
    return [
        app_settings.FFMPEG_PATH,
        "-i", input_path,
        "-y",
        "-v", "error",
        "-map", f"0:{stream_index}",
        "-c:s", "srt",
        "-f", "srt",
        output_srt_path,
    ]


# ── Command builder — revert sidecar ─────────────────────────────────────────────


class SidecarUnsupported(Exception):
    """Raised when the destroyed streams cannot be stored in a sidecar."""


# Matroska cannot store MP4's mov_text. Copying one in fails at header
# write with "Subtitle codec ... is not supported", so it is converted to
# SubRip instead.
#
# build_restore_command INVERTS this, and must: restoring the converted
# stream with a plain copy tries to mux SubRip back into MP4, which
# FFmpeg refuses at header write. That was shipped, and it made every
# revert point for an MP4 whose job removed a text subtitle unusable —
# listed as restorable, failing only at the moment of use, permanently,
# since the point is not consumed on failure.
#
# The round trip is now exercised end to end rather than as two separate
# FFmpeg commands, which is how the gap survived: each half worked, and
# nothing ran them together.
#
# Listed rather than hardcoded at the call site so anything else found to
# need the same treatment lands in one place — and so restore's inverse
# stays keyed to the same table.
_SUBTITLE_TRANSCODE = {"mov_text": "srt"}


def build_sidecar_command(
    inputs: list[str],
    sidecar_path: str,
    sources: list[tuple[dict, int, int]],
) -> list[str]:
    """
    Return the FFmpeg argv to collect destroyed streams into a single
    Matroska sidecar.

    `sources` is (stream, input_number, stream_index) per stream, in the
    order they should appear in the sidecar. Two inputs rather than one
    because a revert point accumulates: a second job on the same file has
    to produce a sidecar holding both what THIS job destroyed (still
    present in the file it was handed) and what an earlier job destroyed
    (only in the previous sidecar). Neither source alone has everything.

    Matroska regardless of the source container: it is the only format
    that will hold an arbitrary mix of dropped audio, subtitles and
    attachments. Chapters are excluded — they survive a remux, so they
    are still in the processed file and storing a second copy here would
    only invite the two disagreeing.

    Raises SidecarUnsupported if the only losses are attachments. That
    is not a fussy guard: Matroska has no concept of a file with zero
    tracks, and FFmpeg writes one anyway and EXITS ZERO. The result is a
    file that looks written, has a plausible size, and cannot be opened —
    verified directly, ffmpeg rc=0 and ffprobe rc=1 on the same file. A
    revert point pointing at one of those is worse than no revert point,
    because nothing discovers it until someone tries to use it.
    """
    real_tracks = [s for s, _i, _x in sources
                   if s.get("type") in ("video", "audio", "subtitle")]
    if not real_tracks:
        raise SidecarUnsupported(
            "Nothing but attachments was lost; Matroska cannot store a "
            "file with no tracks."
        )

    cmd = [app_settings.FFMPEG_PATH]
    for path in inputs:
        cmd += ["-i", path]
    cmd += ["-y", "-v", "error"]

    # Output-side subtitle ordinal, which is what -c:s:N addresses. It
    # counts only the subtitles going INTO the sidecar, so it is not the
    # stream's index in any input.
    subtitle_ordinal = 0
    overrides: list[str] = []

    for stream, input_number, stream_index in sources:
        cmd += ["-map", f"{input_number}:{stream_index}"]
        if stream.get("type") == "subtitle":
            # Only streams coming from a real media file can still be
            # mov_text; anything already in a previous sidecar was
            # converted on its way in and is SubRip by now.
            target = _SUBTITLE_TRANSCODE.get(stream.get("codec"))
            if target and input_number == 0:
                overrides += [f"-c:s:{subtitle_ordinal}", target]
            subtitle_ordinal += 1

    # Base codec first, per-stream overrides after — later options win,
    # so the order here is what lets a single mov_text stream be
    # converted while everything else is still copied.
    cmd += ["-c", "copy"] + overrides
    cmd += ["-map_chapters", "-1", "-f", "matroska", sidecar_path]
    return cmd


# ── Command builder — revert restore ─────────────────────────────────────────────


class RestoreUnsupported(Exception):
    """Raised when a manifest cannot be turned into a restore command."""


def build_restore_command(
    processed_path: str,
    sidecar_path: str,
    output_path: str,
    manifest: dict,
) -> list[str]:
    """
    Return the FFmpeg argv to rebuild the original file from the processed
    one plus its sidecar.

    Input 0 is the processed file, input 1 the sidecar. Every stream in
    the manifest is mapped from whichever holds it, in the manifest's own
    order — which is the ORIGINAL order, not the processed one. Getting
    that wrong produces a file that plays but whose track order has
    silently changed, which is the kind of difference nobody notices until
    a player picks the wrong default.

    Metadata is written back explicitly rather than left to stream copy.
    Copying preserves whatever the processed file happens to carry, and
    for any track a re-tagging job touched that is precisely the value
    being reverted. Language, title and dispositions therefore all come
    from the manifest, and are CLEARED where the manifest recorded none —
    an absent tag is a value, and leaving the job's version in place would
    make revert a partial undo that looks complete.

    Raises RestoreUnsupported if the manifest predates the index
    annotations or is internally inconsistent. Refusing is the only safe
    response: a restore built on guesses would write a plausible file with
    the wrong tracks in it.
    """
    streams = manifest.get("streams") or []
    if not streams:
        raise RestoreUnsupported("Manifest records no streams.")

    container = manifest.get("container")
    out_fmt = _CONTAINER_FORMAT.get(container)
    if not out_fmt:
        raise RestoreUnsupported(
            f"No FFmpeg muxer known for original container {container!r}."
        )

    cmd = [
        app_settings.FFMPEG_PATH,
        "-i", processed_path,
        "-i", sidecar_path,
        "-y",
        "-v", "error",
        "-nostats",
        "-progress", "pipe:1",
    ]

    maps: list[str] = []
    meta: list[str] = []
    codecs: list[str] = []

    # Output-side subtitle ordinal, which is what -c:s:N addresses. It
    # counts subtitles in the OUTPUT, so it is not the stream's index in
    # the manifest or in either input.
    subtitle_ordinal = 0

    for out_index, stream in enumerate(streams):
        sidecar_index = stream.get("sidecar_index")
        processed_index = stream.get("processed_index")

        # The sidecar wins when a stream is somehow in both. Capture makes
        # the two annotations mutually exclusive today, so this ordering
        # costs nothing now — but it is stated rather than left to chance,
        # because the direction matters the moment that changes. Matching
        # errs towards "lost", so an over-captured stream is one the
        # sidecar holds in its ORIGINAL codec and tagging while the
        # processed file holds the job's rewritten version. Preferring the
        # processed copy would quietly restore the very thing being
        # reverted — a MKV → MP4 conversion, for instance, would put the
        # mov_text subtitle back instead of the SubRip original.
        if sidecar_index is not None:
            maps += ["-map", f"1:{sidecar_index}"]
        elif processed_index is not None:
            maps += ["-map", f"0:{processed_index}"]
        else:
            # Neither annotation present. Either the manifest was written
            # before capture recorded them, or the stream was lost and
            # never made it into the sidecar. Both mean this file cannot
            # be rebuilt faithfully, and a partial rebuild is worse than
            # an honest refusal — it would report success while quietly
            # dropping a track the user asked to get back.
            raise RestoreUnsupported(
                f"Stream {stream.get('index')} is in neither the processed "
                f"file nor the sidecar."
            )

        # Undo any conversion capture applied on the way into the sidecar.
        #
        # Matroska cannot hold MP4's mov_text, so build_sidecar_command
        # converts it to SubRip. Restoring with a flat -c copy then tries
        # to mux SubRip into MP4, which FFmpeg refuses at header write —
        # so an MP4 whose job removed a text subtitle produced a revert
        # point that could never be used. That is close to routine under
        # shipped defaults: keep_subtitle_languages is ["eng"], and
        # extract_text_subtitles_to_srt removes every extracted text
        # subtitle from the mux.
        #
        # The manifest records the ORIGINAL codec, so the inverse is
        # available without probing the sidecar. Applied only to
        # sidecar-sourced streams: one still in the processed file was
        # never converted. It holds regardless of which capture did the
        # converting — the manifest always describes the pristine
        # original, so a stream carried through several sidecars still
        # names the codec to restore.
        if stream.get("type") == "subtitle":
            if (sidecar_index is not None
                    and stream.get("codec") in _SUBTITLE_TRANSCODE):
                codecs += [f"-c:s:{subtitle_ordinal}", stream["codec"]]
            subtitle_ordinal += 1

        # Clear this stream's metadata, then write back exactly what the
        # original carried. Clearing first is what removes tags the
        # ORIGINAL never had: a stream that survived the job arrives via
        # the processed container, and an MP4 round trip strips mkvmerge's
        # statistics tags and adds handler_name and vendor_id, which mean
        # nothing in Matroska.
        #
        # EVERY stream is handled here, attachments included, and that is
        # not tidiness. A single per-stream -map_metadata replaces FFmpeg's
        # default "copy all stream metadata" for the WHOLE output, not just
        # the stream named — verified directly. So the moment one stream is
        # cleared, every other stream's tags have to be written back by
        # hand or they are silently dropped. Attachments fail loudest,
        # because Matroska refuses to mux one without a filename tag, but
        # the quiet cases are worse: titles and languages would vanish from
        # streams nothing appeared to touch.
        meta += [f"-map_metadata:s:{out_index}", "-1"]
        for key, value in (stream.get("tags") or {}).items():
            meta += [f"-metadata:s:{out_index}", f"{key}={value}"]

        # Language, title and disposition are not meaningful on an
        # attachment, and its identity lives entirely in the tags above.
        if stream.get("type") == "attachment":
            continue

        # Language and title are written last and unconditionally, so they
        # win over anything in the recorded tag set. Empty values are
        # deliberate: they clear a tag the job added, which is the one case
        # the tag set cannot express — it records what WAS there, and what
        # was there is nothing.
        meta += [f"-metadata:s:{out_index}", f"language={stream.get('language') or ''}"]
        meta += [f"-metadata:s:{out_index}", f"title={stream.get('title') or ''}"]

        flags = stream.get("disposition") or []
        # "0" is FFmpeg's spelling for "no flags at all". Omitting the
        # option entirely would instead inherit whatever the copied stream
        # carries, which is the job's version, not the original's.
        meta += [f"-disposition:{out_index}", "+".join(flags) if flags else "0"]

    cmd += maps
    # Base codec first, per-stream overrides after — later options win.
    cmd += ["-c", "copy"] + codecs
    cmd += meta
    # Chapters survive a remux, so the processed file still has them.
    cmd += ["-map_chapters", "0"]
    cmd += ["-f", out_fmt, output_path]
    return cmd


# ── Executor — main remux ───────────────────────────────────────────────────────


async def execute_ffmpeg(
    input_path: str,
    output_path: str,
    decision: ProcessingDecision,
    all_tracks: list[dict],
    job_id: int,
    progress_callback: Callable[[FFmpegProgress], Awaitable[None]] | None = None,
    timeout_seconds: float | None = None,
    before_staging: Callable[[str], Awaitable[str | None]] | None = None,
) -> FFmpegResult:
    """
    Run FFmpeg asynchronously.

    • Writes to a temp file in TEMP_DIR (typically a RAM-backed or fast
      cache location), then moves to output_path on success.  This keeps
      FFmpeg I/O off the main array while it is running, avoiding contention
      with other array activity.  The final move is a sequential write to
      the array and does not compete with encode I/O.
    • Parses -progress pipe:1 output to emit FFmpegProgress objects.
    • Always cleans up the temp file on failure.

    Thin adapter over the shared run_staged_subprocess() executor in
    subprocess_runner.py — this function only handles what's specific to
    the main remux: building the temp path, building the FFmpeg command,
    describing the current action, and translating raw progress snapshots
    into FFmpegProgress objects. The subprocess spawn/drain/stage/cleanup
    machinery itself lives in the shared module (also used by forge.py).
    """
    # Stage in TEMP_DIR (e.g. /tmp/remuxarr) when space allows; fall back
    # to the output file's own directory when TEMP_DIR is too full.
    #
    # The temp filename is derived from job_id, NOT the source/output
    # filename — deliberately. Appending ".remuxarr_tmp" (13 bytes) to an
    # already-long filename can push it past the 255-byte NAME_MAX most
    # Linux filesystems enforce per path component, even when the FINAL
    # filename (without the suffix) is comfortably under that limit. A
    # multi-episode file with several episode titles joined together by
    # Sonarr's naming format is exactly the kind of filename this hits —
    # confirmed in production: a 247-byte original filename failed with
    # "File name too long" purely because of the 260-byte temp version.
    # job_id is always short and always unique, so this eliminates the
    # whole class of failure rather than just raising the threshold.
    tmp_dir     = _pick_temp_dir(input_path)
    temp_output = os.path.join(tmp_dir, f"job_{job_id}.remuxarr_tmp")
    os.makedirs(tmp_dir, exist_ok=True)
    cmd = build_ffmpeg_command(input_path, temp_output, decision, all_tracks)

    logger.info("FFmpeg command:\n  %s", " ".join(cmd))

    # Get duration once for percentage calculation
    duration = await probe_duration(input_path)
    current_action = _describe_action(decision)

    async def on_progress_line(progress_kv: dict[str, str]) -> None:
        if not progress_callback or not duration:
            return
        secs  = parse_out_time_seconds(progress_kv)
        pct   = min(100.0, (secs / duration * 100)) if duration > 0 else 0.0
        speed = progress_kv.get("speed", "?x")
        await progress_callback(
            FFmpegProgress(
                percent=pct,
                current_time=secs,
                speed=speed,
                current_action=current_action,
            )
        )

    # Handed temp_output, not output_path — see execute_ffmpeg_combined's
    # identical wrapper for why the distinction matters on an in-place remux.
    async def _before_staging() -> str | None:
        return await before_staging(temp_output)

    result = await run_staged_subprocess(
        cmd,
        [StagedOutput(temp_path=temp_output, final_path=output_path)],
        on_progress_line=on_progress_line,
        stderr_tail_lines=30,
        timeout_seconds=timeout_seconds,
        before_staging=_before_staging if before_staging else None,
    )

    if not result.success:
        # Original only logged the genuine-FFmpeg-failure case (non-zero
        # exit), not the "temp file missing after a clean exit" edge case
        # — preserve that distinction. A clean exit (rc=0) combined with
        # success=False uniquely identifies the missing-temp-file case.
        if result.returncode != 0:
            logger.error("FFmpeg failed (rc=%d):\n%s", result.returncode, result.error)
        return FFmpegResult(
            success=False, output_path=None, error=result.error, output_size=None
        )

    output_size = os.path.getsize(output_path)
    logger.info(
        "FFmpeg success → %s (%.1f MB)", output_path, output_size / 1024 / 1024
    )
    return FFmpegResult(
        success=True, output_path=output_path, error=None, output_size=output_size
    )


# ── Executor — subtitle extraction ──────────────────────────────────────────────


async def execute_subtitle_extraction(
    input_path: str,
    stream_index: int,
    output_srt_path: str,
    job_id: int,
) -> ExtractionResult:
    """
    Extract a single subtitle stream to an external .srt file.

    Stages through TEMP_DIR so the intermediate file never lands on the
    array during extraction — consistent with the main remux executor.
    On success, moves the completed .srt to output_srt_path.

    Thin adapter over run_staged_subprocess() — this function only handles
    what's specific to subtitle extraction: temp path, command building, and
    translating the result into an ExtractionResult. The subprocess machinery
    lives in subprocess_runner.py.

    NOTE: unlike execute_ffmpeg(), exceptions are caught and returned as
    ExtractionResult(success=False) rather than re-raised. worker.py's
    two-pass fallback path calls this in a loop and checks the result
    object — it expects a result, not a raised exception.
    """
    # See execute_ffmpeg's docstring for why the temp name is derived from
    # job_id rather than the destination filename. stream_index (already
    # unique per subtitle track) distinguishes multiple SRT extractions
    # within the same job from each other.
    tmp_dir     = _pick_temp_dir(input_path)
    temp_output = os.path.join(tmp_dir, f"job_{job_id}_srt_{stream_index}.remuxarr_tmp")
    os.makedirs(tmp_dir, exist_ok=True)
    cmd = build_extract_subtitle_command(input_path, stream_index, temp_output)

    logger.info("Subtitle extraction command:\n  %s", " ".join(cmd))

    try:
        result = await run_staged_subprocess(
            cmd,
            [StagedOutput(temp_path=temp_output, final_path=output_srt_path)],
            # No on_progress_line — build_extract_subtitle_command does not
            # include -progress pipe:1 so stdout is empty. run_staged_subprocess
            # always drains stdout, but it just reaches EOF immediately here.
            on_progress_line=None,
            stderr_tail_lines=30,
        )

        if not result.success:
            # Only log for genuine FFmpeg failures (rc != 0), not for the
            # "temp file missing after a clean exit" edge case — matches the
            # original asymmetric logging behaviour.
            if result.returncode is not None and result.returncode != 0:
                logger.error(
                    "Subtitle extraction failed (stream %d, rc=%d): %s",
                    stream_index, result.returncode, result.error,
                )
            return ExtractionResult(success=False, output_path=None, error=result.error)

        logger.info("Subtitle extracted → %s", output_srt_path)
        return ExtractionResult(success=True, output_path=output_srt_path, error=None)

    except Exception as exc:
        # run_staged_subprocess already cleaned up temp_output before re-raising.
        cleanup_temp_file(temp_output)
        return ExtractionResult(success=False, output_path=None, error=str(exc))


# ── Path helpers ───────────────────────────────────────────────────────────────


def determine_output_path(input_path: str, decision: ProcessingDecision) -> str:
    """
    Return the target output path.

    • Genuine container conversion (a change_container action exists):
      same directory, extension derived from target_container.
    • Everything else: same path, byte-for-byte (temp→rename keeps it
      atomic) — including containers whose normalised name differs from
      their real extension.

    The rename is gated on an actual change_container ACTION, not on
    extension/suffix comparison. The previous implementation compared
    decision.output_extension (derived from the NORMALISED container
    name) against the file's real suffix and renamed on any mismatch —
    which silently renamed files whose extension legitimately differs
    from their normalised container name: a .m2ts (normalised "ts")
    processed for ANY reason — even a pure track drop — was written to
    Movie.ts, with no change_container action, no mention in the reason,
    and the original deleted at the old path after success; .m4v/.mov
    (normalised "mp4") were renamed to .mp4 the same way. Nothing
    informed Plex/Sonarr/Radarr of those renames.
    ProcessingDecision.output_extension was removed entirely at
    the same time, since carrying a second, derivable field alongside
    target_container is exactly what produced the divergence.
    """
    p = Path(input_path)
    has_container_change = any(
        a.action_type == "change_container" for a in decision.actions
    )
    if has_container_change:
        new_ext = f".{decision.target_container}"
        if new_ext != p.suffix.lower():
            return str(p.parent / (p.stem + new_ext))
    return input_path


def _pick_temp_dir(reference_path: str) -> str:
    """
    Pick the best directory for a temp output file.

    Prefers TEMP_DIR (often RAM-backed tmpfs on Unraid — fast, avoids array
    I/O during encoding).  Falls back to the directory that contains the
    reference path (the output/source file on the array) when TEMP_DIR does
    not have enough free space.

    Why this matters: tmpfs on Unraid is sized to a fraction of system RAM.
    A large video file (2–4 GB) can easily exhaust it, producing the
    misleading "No space left on device" error even though the array has
    plenty of room.  Checking first and falling back keeps the fast-path
    benefit while safely handling files that exceed available RAM.
    """
    preferred = app_settings.TEMP_DIR
    try:
        os.makedirs(preferred, exist_ok=True)
        # How large is the reference file?  Use it as the size estimate for
        # the temp output (remuxed output is typically similar size to input).
        try:
            needed = os.path.getsize(reference_path)
        except OSError:
            needed = 0
        # Add 10 % headroom; always require at least 256 MB free.
        needed = max(int(needed * 1.1), 256 * 1024 * 1024)
        free = shutil.disk_usage(preferred).free
        if free >= needed:
            return preferred
        logger.warning(
            "TEMP_DIR %s only has %.1f MB free (need %.1f MB for %s); "
            "falling back to source directory",
            preferred, free / 1024 / 1024, needed / 1024 / 1024,
            os.path.basename(reference_path),
        )
    except Exception as exc:
        logger.warning("Could not check TEMP_DIR space (%s); falling back", exc)

    # Fall back: write temp file next to the final output (on the array).
    fallback = os.path.dirname(reference_path)
    return fallback if fallback else "."


# ── Executor — combined remux + subtitle extraction ───────────────────────────


async def execute_ffmpeg_combined(
    input_path:           str,
    output_path:          str,
    decision:             ProcessingDecision,
    all_tracks:           list[dict],
    subtitle_extractions: list[tuple[int, str]],  # (stream_index, srt_dest_path)
    job_id:               int,
    progress_callback:    Callable[[FFmpegProgress], Awaitable[None]] | None = None,
    timeout_seconds:      float | None = None,
    before_staging:       Callable[[str], Awaitable[str | None]] | None = None,
) -> tuple[FFmpegResult, list[ExtractionResult]]:
    """
    Single-pass combined remux + subtitle extraction.

    Reads the source file ONCE and writes all outputs simultaneously:
      • The remuxed media file (to TEMP_DIR, then moved to output_path)
      • Each subtitle .srt file  (to TEMP_DIR, then moved to its dest path)

    On HDD arrays this halves the read I/O vs. the two-pass approach (one
    FFmpeg call for extraction + one for remux), which is the dominant cost
    for "Extract N subtitles to external SRT" jobs.

    Returns (FFmpegResult, [ExtractionResult, ...]) — one ExtractionResult
    per entry in subtitle_extractions, in the same order.

    before_staging, if given, is awaited with the path of the finished main
    output while it is still a temp file and every original is untouched —
    the only point where the source and the result both exist. Returning an
    error string from it aborts the whole run with nothing swapped into
    place. See run_staged_subprocess for the full contract.

    Thin adapter over run_staged_subprocess(): the main output AND every
    SRT sidecar are passed to it as one staged set, so all outputs land
    together or none do.

    WHY all-or-nothing (this is a deliberate contract change):
    an earlier version staged only the main output through
    run_staged_subprocess and moved the SRT temps itself with
    partial-success semantics — a missing SRT temp after a successful
    main-file move reported only that SRT as failed, a log warning was
    emitted, and the job still recorded SUCCESS. That combination was a
    silent-data-loss mechanism: extracted subtitles are removed from the
    muxed output, so "main file staged, sidecar missing" means the
    subtitle no longer exists anywhere — gone from the media file, never
    written to disk — with nothing but a log line to show for it (and,
    on a container change, the original deleted right after).

    The partial-success contract only ever made sense when moves were
    destructive (delete original, then move) — "rolling back" the main
    file was impossible, so salvaging it was the least-bad option. Since
    run_staged_subprocess switched to two-phase staging (verify every
    temp, stage every .part, THEN swap), nothing is touched until all
    outputs are known good: a missing or unstageable SRT now fails the
    whole run with the source file byte-for-byte untouched, and a plain
    retry gets a clean second attempt. All-or-nothing is strictly safer
    and no longer costs anything.
    """
    tmp_dir   = _pick_temp_dir(input_path)
    os.makedirs(tmp_dir, exist_ok=True)

    # ── Build the combined command ─────────────────────────────────────────
    # See execute_ffmpeg's docstring for why this is derived from job_id
    # rather than the destination filename.
    temp_main = os.path.join(tmp_dir, f"job_{job_id}.remuxarr_tmp")

    # Base command up to (and including) the format/movflags flags
    main_cmd = build_ffmpeg_command(input_path, temp_main, decision, all_tracks)

    # Append subtitle output specs after the main output.
    # Each gets its own temp path in the same tmp_dir. stream_idx is
    # already unique per subtitle track, distinguishing multiple SRT
    # extractions within the same job from each other and from the main
    # video temp above.
    srt_temps: list[str] = []
    for stream_idx, srt_dest in subtitle_extractions:
        srt_tmp = os.path.join(tmp_dir, f"job_{job_id}_srt_{stream_idx}.remuxarr_tmp")
        srt_temps.append(srt_tmp)
        main_cmd += [
            "-map", f"0:{stream_idx}",
            "-c:s", "srt",
            "-f", "srt",
            srt_tmp,
        ]

    logger.info("FFmpeg command (combined):\n  %s", " ".join(main_cmd))

    # ── Progress adapter ───────────────────────────────────────────────────
    duration       = await probe_duration(input_path)
    current_action = _describe_action(decision)

    async def on_progress_line(progress_kv: dict[str, str]) -> None:
        if not progress_callback or not duration:
            return
        secs  = parse_out_time_seconds(progress_kv)
        pct   = min(100.0, (secs / duration * 100)) if duration > 0 else 0.0
        await progress_callback(FFmpegProgress(
            percent=pct,
            current_time=secs,
            speed=progress_kv.get("speed", "?x"),
            current_action=current_action,
        ))

    # ── Run via shared executor — ALL outputs staged as one set ────────────
    # Main file first, then each SRT in subtitle_extractions order.
    # run_staged_subprocess owns every temp from here on: it verifies all
    # of them exist post-run, stages all of them as .part files, and only
    # then swaps them into place — so any missing/unstageable output
    # (including an SRT) fails the whole run with every original
    # untouched, and its cleanup paths (failure, timeout, cancellation,
    # exception) already cover the SRT temps too. No local cleanup needed.
    staged_outputs = [StagedOutput(temp_path=temp_main, final_path=output_path)]
    staged_outputs += [
        StagedOutput(temp_path=srt_tmp, final_path=srt_dest)
        for srt_tmp, (_, srt_dest) in zip(srt_temps, subtitle_extractions)
    ]

    # The hook is handed temp_main — the finished output, still in the temp
    # directory. The caller needs the produced file to compare against the
    # source, and output_path does not exist yet at this point in the run
    # (and for an in-place remux still holds the ORIGINAL, which is the
    # opposite of what a caller asking for "the output" wants).
    async def _before_staging() -> str | None:
        return await before_staging(temp_main)

    result = await run_staged_subprocess(
        main_cmd,
        staged_outputs,
        on_progress_line=on_progress_line,
        stderr_tail_lines=30,
        timeout_seconds=timeout_seconds,
        before_staging=_before_staging if before_staging else None,
    )

    if not result.success:
        # Log only for genuine FFmpeg failures (rc != 0), not for the
        # missing-temp edge case — matches original asymmetric logging.
        if result.returncode is not None and result.returncode != 0:
            logger.error(
                "FFmpeg (combined) failed (rc=%d):\n%s", result.returncode, result.error
            )
        fail = FFmpegResult(
            success=False, output_path=None, error=result.error, output_size=None
        )
        srt_fails = [
            ExtractionResult(success=False, output_path=None, error=result.error)
            for _ in subtitle_extractions
        ]
        return fail, srt_fails

    output_size = os.path.getsize(output_path)
    logger.info("FFmpeg success → %s (%.1f MB)", output_path, output_size / 1024 / 1024)
    main_result = FFmpegResult(
        success=True, output_path=output_path, error=None, output_size=output_size
    )

    # All-or-nothing: success above means every SRT was staged alongside
    # the main file — report and log them all as extracted.
    srt_results: list[ExtractionResult] = []
    for _, srt_dest in subtitle_extractions:
        logger.info("Subtitle extracted → %s", srt_dest)
        srt_results.append(ExtractionResult(success=True, output_path=srt_dest, error=None))

    return main_result, srt_results


# ── Internal helpers ───────────────────────────────────────────────────────────


def _describe_action(decision: ProcessingDecision) -> str:
    # has_transcode is only ever true here via worker.py's corrupt-audio
    # retry path — there's no setting that produces this on a normal pass.
    has_transcode  = any(a.action_type == "transcode_track" for a in decision.actions)
    has_container  = any(a.action_type == "change_container" for a in decision.actions)
    has_faststart  = any(a.action_type == "add_faststart" for a in decision.actions)
    if has_transcode:
        return "Re-encoding audio (recovering from corrupt source frames)"
    if has_container:
        return "Remuxing to MP4"
    if has_faststart:
        return "Adding fast start (optimising for streaming)"
    return "Remuxing tracks"
