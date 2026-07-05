"""
Decision Engine
===============
Given a file's format info, its tracks, and the current user settings, produces
a ProcessingDecision that lists every action that needs to happen (or explains
why the file should be skipped / sent to manual review).

Nothing in this module touches disk or the database — it is pure logic.
"""
import logging
from dataclasses import dataclass, field, replace as dc_replace
from pathlib import Path
from typing import Literal

from app.core.probe import _FORCED_RE, _SDH_RE

logger = logging.getLogger(__name__)

# ── MP4 compatibility tables ───────────────────────────────────────────────────
# Video codecs that can live inside an MP4/M4V container without transcoding.
MP4_COMPATIBLE_VIDEO = frozenset({
    "h264", "avc", "hevc", "h265", "mpeg4", "mpeg2video", "mjpeg",
})
# Audio codecs compatible with MP4.  Note: DTS, TrueHD, FLAC are NOT included.
# After our AAC-5.1 → AC3 transcode, AC3 is fine.
MP4_COMPATIBLE_AUDIO = frozenset({
    "aac", "ac3", "eac3", "mp3", "alac", "opus",
})
# Subtitle codecs that CANNOT go into MP4 (image-based or advanced text).
# If any KEPT subtitle track has one of these codecs, MP4 conversion is
# blocked entirely — the file stays in its current container.
MP4_INCOMPATIBLE_SUBS = frozenset({
    "hdmv_pgs_subtitle", "pgssub", "dvd_subtitle", "dvdsub",
    "ass", "ssa", "dvb_subtitle",
})

# Image-based (bitmap) subtitle codecs. These can never be converted to a
# text-based external SubRip (.srt) file — there's no text to extract.
IMAGE_BASED_SUBS = frozenset({
    "hdmv_pgs_subtitle", "pgssub", "dvd_subtitle", "dvdsub",
    "dvb_subtitle", "vobsub",
})

# Text-based subtitle codecs that FFmpeg can losslessly convert to an
# external SubRip (.srt) file via the "srt" subtitle encoder. mov_text
# (MP4), ASS/SSA, and SubRip itself all qualify.
SRT_CONVERTIBLE_SUBS = frozenset({
    "subrip", "srt", "mov_text", "ass", "ssa",
})

# ISO 639-2/B (3-letter, used by ffprobe/Matroska/MP4) → ISO 639-1
# (2-letter, used in Plex's external-subtitle naming convention, e.g.
# "Movie.en.srt"). Anything not listed falls back to its original code.
ISO_639_2_TO_1 = {
    "eng": "en", "fre": "fr", "fra": "fr", "ger": "de", "deu": "de",
    "spa": "es", "ita": "it", "por": "pt", "dut": "nl", "nld": "nl",
    "swe": "sv", "nor": "no", "nob": "no", "nno": "no", "dan": "da",
    "fin": "fi", "pol": "pl", "rus": "ru", "jpn": "ja", "chi": "zh",
    "zho": "zh", "kor": "ko", "ara": "ar", "heb": "he", "hin": "hi",
    "tha": "th", "tur": "tr", "cze": "cs", "ces": "cs", "gre": "el",
    "ell": "el", "hun": "hu", "rum": "ro", "ron": "ro", "ukr": "uk",
    "vie": "vi", "ind": "id", "may": "ms", "msa": "ms", "bul": "bg",
    "hrv": "hr", "srp": "sr", "slv": "sl", "est": "et", "lav": "lv",
    "lit": "lt", "slk": "sk", "slo": "sk", "cat": "ca",
}

ActionType = Literal[
    "copy_track", "drop_track", "transcode_track",
    "change_container", "flag_manual_review", "extract_subtitle",
    "add_faststart",
]


# ── Data classes ───────────────────────────────────────────────────────────────

@dataclass
class Action:
    action_type:  ActionType
    description:  str
    track_type:   str | None = None
    stream_index: int | None = None
    order:        int = 0
    # Extra fields for audio transcode actions (AAC 5.1 → AC3)
    output_codec:         str | None = None
    output_codec_options: dict       = field(default_factory=dict)
    # Extra fields for extract_subtitle actions
    external_path: str | None = None
    language:      str | None = None
    is_forced:     bool = False
    # Set by the language fix pass when an und track should be re-tagged.
    # The value is the ISO 639-2/B code to write (e.g. "eng").
    # ffmpeg.py reads this to emit -metadata:s:a:N / :s:s:N flags.
    target_language: str | None = None


@dataclass
class ProcessingDecision:
    should_process:   bool
    reason:           str
    actions:          list[Action] = field(default_factory=list)
    is_manual_review: bool = False
    target_container: str | None = None   # None = keep current
    output_extension: str | None = None   # e.g. ".mp4"
    # Populated only for is_manual_review=True decisions caused by
    # non-convertible (image-based) subtitle tracks. Each entry:
    #   {stream_index, language, codec, is_forced, title}
    # The UI uses this to render per-track Keep/Remove choices.
    flagged_subtitles: list[dict] | None = None


# ── Main function ──────────────────────────────────────────────────────────────

def analyze_file(
    file_info: dict,
    tracks:    list[dict],
    settings:  dict,
    subtitle_overrides: dict[int, str] | None = None,
    has_faststart: bool | None = None,
    forged_ac3_audio_index: int | None = None,
) -> ProcessingDecision:
    """
    Analyse a media file and return a ProcessingDecision.

    Parameters
    ----------
    file_info : dict
        Keys: path, container, video_codec
    tracks : list[dict]
        Normalised track dicts from probe.extract_tracks()
    settings : dict
        App settings from session.get_app_settings()
    subtitle_overrides : dict[int, str] | None
        Per-track resolutions for previously-flagged non-convertible
        subtitle tracks, keyed by stream_index with value "keep" or
        "remove". Set by the user via the manual-review UI and persisted
        on MediaFile.subtitle_overrides. A track with an override skips
        the non-convertible-subtitle manual-review gate entirely and is
        either embedded as-is ("keep") or dropped ("remove").
    has_faststart : bool | None
        Result of is_faststart_mp4() for this file.  None means unknown
        (non-MP4 container, probe failed, or setting disabled).  Only
        relevant for existing MP4 files; new MP4 outputs from a container
        conversion always get +faststart from the FFmpeg command builder.
    forged_ac3_audio_index : int | None
        0-based audio-track-relative index of an AC3 track added by a
        completed (or undo-in-progress/undo-failed) Ac3ForgeJob for this
        file — i.e. Ac3ForgeJob.audio_track_count. When set, this specific
        track is excluded from the "multiple undefined-language audio
        tracks" manual-review threshold count: it's a known, intentional
        duplicate of an existing audio track (added by the AC3 forge
        feature for AVR passthrough), not a new genuinely-ambiguous
        source. Without this exclusion, any file where forge was used on
        an "und"-language source would trip manual review on every
        subsequent scan even though both tracks are always meant to be
        kept — there's nothing for a human to actually decide.
    """
    subtitle_overrides = subtitle_overrides or {}
    keep_audio_langs    = set(settings.get("keep_audio_languages",   ["eng"]))
    keep_sub_langs      = set(settings.get("keep_subtitle_languages", ["eng"]))
    keep_forced_subs    = settings.get("keep_forced_subtitles",   True)
    keep_default_audio  = settings.get("keep_default_audio",      True)
    transcode_aac_51    = settings.get("transcode_aac_51_to_ac3", True)
    prefer_mp4          = settings.get("prefer_mp4_container",    True)
    und_threshold       = settings.get("und_audio_threshold",     2)
    extract_subs_to_srt = settings.get("extract_text_subtitles_to_srt", True)
    add_faststart       = settings.get("add_faststart_to_mp4", True)

    current_container = (file_info.get("container") or "mkv").lower()

    video_tracks = [t for t in tracks if t["track_type"] == "video"]
    audio_tracks = [t for t in tracks if t["track_type"] == "audio"]
    sub_tracks   = [t for t in tracks if t["track_type"] == "subtitle"]

    # ── Manual-review gate: undefined-language audio ─────────────────────────
    und_audio = [t for t in audio_tracks
                 if (t["language"] or "und") in ("und", "")]

    # Exclude the forge-derived AC3 track (if any) from the threshold count.
    # See the forged_ac3_audio_index docstring above for why: it's a known
    # duplicate, not a new ambiguous source, so it shouldn't count toward
    # triggering manual review.
    if forged_ac3_audio_index is not None and forged_ac3_audio_index < len(audio_tracks):
        forged_track = audio_tracks[forged_ac3_audio_index]
        und_audio = [t for t in und_audio if t is not forged_track]

    if len(und_audio) >= und_threshold:
        msg = (
            f"Contains {len(und_audio)} audio tracks with undefined language — "
            f"manual review required to prevent accidental track deletion."
        )
        return ProcessingDecision(
            should_process=False,
            is_manual_review=True,
            reason=msg,
            actions=[Action(
                action_type="flag_manual_review",
                description=msg,
            )],
        )

    # Whether a subtitle track would be retained under the current language/
    # forced settings — shared by the manual-review gate below and the main
    # subtitle keep/drop loop further down.
    def _sub_is_kept(track: dict) -> bool:
        lang      = track["language"] or "und"
        is_forced = track.get("is_forced", False)
        return lang in keep_sub_langs or (keep_forced_subs and is_forced)

    # ── Manual-review gate: non-convertible kept subtitles ───────────────────
    # When SRT extraction is enabled, any KEPT subtitle track using an
    # image-based codec (PGS, VOBSUB, DVD, DVB) cannot be converted to
    # external SRT. Rather than silently leaving it embedded or dropping it,
    # halt processing so the user can decide whether to keep or remove it.
    #
    # A track with an entry in subtitle_overrides has already been resolved
    # by the user (via the manual-review UI) and is excluded from this gate
    # — it's handled directly in the main subtitle loop below.
    if extract_subs_to_srt:
        non_convertible = [
            t for t in sub_tracks
            if _sub_is_kept(t)
            and (t.get("codec") or "").lower() in IMAGE_BASED_SUBS
            and t["stream_index"] not in subtitle_overrides
        ]
        if non_convertible:
            details = ", ".join(
                f"{(t['language'] or 'und').upper()} {t.get('codec', '?')}"
                f"{' (forced)' if t.get('is_forced') else ''}"
                for t in non_convertible
            )
            n = len(non_convertible)
            msg = (
                f"Contains {n} image-based subtitle track{'s' if n > 1 else ''} "
                f"({details}) that cannot be converted to external SRT — "
                f"manual review required to decide whether to keep or remove "
                f"{'them' if n > 1 else 'it'}."
            )
            flagged = [
                {
                    "stream_index": t["stream_index"],
                    "language":     t["language"] or "und",
                    "codec":        t.get("codec") or "",
                    "is_forced":    t.get("is_forced", False),
                    "title":        t.get("title"),
                }
                for t in non_convertible
            ]
            return ProcessingDecision(
                should_process=False,
                is_manual_review=True,
                reason=msg,
                actions=[Action(
                    action_type="flag_manual_review",
                    description=msg,
                )],
                flagged_subtitles=flagged,
            )

    # ── Audio analysis ─────────────────────────────────────────────────────
    actions: list[Action] = []
    kept_audio: list[dict] = []
    order = 0

    # Pre-check: is there at least one audio track in the preferred language
    # list?  The keep_default_audio guard below uses this to limit its scope —
    # it should only fire as a safety net when NO preferred-language track
    # would otherwise be kept, not unconditionally.
    #
    # Without this, a file like:
    #   Stream 1: Italian EAC3 (default)
    #   Stream 2: English EAC3
    # would keep Italian because it's the default, even though English is
    # already being retained.  The original intent of keep_default_audio is
    # to prevent audio-less output on files where the only track has an
    # undefined or non-preferred language — not to override the language filter
    # when a preferred track is available.
    has_preferred_audio = any(
        (t["language"] or "und") in keep_audio_langs
        for t in audio_tracks
    )

    # Absolute last-resort fallback. The keep_default_audio guard above
    # protects files where the best-available track happens to also carry
    # the default flag — but that flag is only ever set by whatever
    # encoded/muxed the source file, and plenty of releases simply never
    # set it on any track. Confirmed in production: a single-track file
    # whose only audio was mistagged with a non-preferred, non-"und"
    # language (e.g. an English sitcom with its sole track mislabeled
    # "dan") AND never flagged default fails every check above — lang not
    # in keep list, lang not "und", and is_default is False on every
    # track — leaving the file with zero audio tracks after the drop
    # pass runs. That's not an acceptable outcome under any configuration.
    #
    # This only ever activates when NEITHER of the two normal tiers has
    # anything to offer — it does not override a real preferred-language
    # match or a genuine default flag, only fires when both are absent —
    # and force-keeps the first audio track by stream index as the
    # unconditional floor: a file may end up with the wrong language
    # audible, but it will never end up with none at all.
    has_default_audio = any(track.get("is_default") for track in audio_tracks)
    absolute_fallback_stream_index = None
    if audio_tracks and not has_preferred_audio and not has_default_audio:
        absolute_fallback_stream_index = min(
            t["stream_index"] for t in audio_tracks
        )
        logger.warning(
            "No preferred-language or default-flagged audio track found — "
            "force-keeping stream %d as a last resort to avoid a silent "
            "output file. This file should be checked: its audio language "
            "tag is likely wrong.",
            absolute_fallback_stream_index,
        )

    for track in audio_tracks:
        lang     = track["language"] or "und"
        codec    = (track["codec"] or "").lower()
        channels = track.get("channels") or 0

        should_keep = (
            lang in keep_audio_langs
            or lang == "und"
            # Only keep the default-flagged track when no preferred-language
            # track exists — preserves audio on edge-case files without
            # overriding the language filter when English (or any other
            # preferred language) is already present.
            or (keep_default_audio and track.get("is_default") and not has_preferred_audio)
            or track["stream_index"] == absolute_fallback_stream_index
        )

        if not should_keep:
            actions.append(Action(
                action_type="drop_track",
                description=(
                    f"Drop audio [{lang}] {codec} "
                    f"{track.get('channel_layout', '')} "
                    f"(stream {track['stream_index']}) — not in keep list"
                ),
                track_type="audio",
                stream_index=track["stream_index"],
                order=order,
            ))
            order += 1
            continue

        # Kept — check for the AAC 5.1 → AC3 transcode rule
        if transcode_aac_51 and codec == "aac" and channels == 6:
            actions.append(Action(
                action_type="transcode_track",
                description=(
                    f"Transcode AAC 5.1 → AC3 5.1 [{lang}] "
                    f"(stream {track['stream_index']})"
                ),
                track_type="audio",
                stream_index=track["stream_index"],
                order=order,
                output_codec="ac3",
                # "ac": "6" forces 5.1 channel count on the output — kept
                # here in output_codec_options rather than hardcoded in
                # build_ffmpeg_command so the corrupt-audio AAC retry can
                # use output_codec_options={} and naturally preserve the
                # source channel count without a separate code path.
                output_codec_options={"b:a": "640k", "ac": "6"},
            ))
        else:
            actions.append(Action(
                action_type="copy_track",
                description=(
                    f"Copy audio [{lang}] {codec} "
                    f"{track.get('channel_layout', '')} "
                    f"(stream {track['stream_index']})"
                ),
                track_type="audio",
                stream_index=track["stream_index"],
                order=order,
            ))
        kept_audio.append(track)
        order += 1

    # ── Subtitle keep/drop/extract decision ─────────────────────────────────
    # For each subtitle track:
    #   • Not in keep list (language/forced)        → drop_track
    #   • Kept, text-based, extraction enabled       → extract_subtitle
    #     (pulled out to an external .srt sidecar and removed from the
    #     muxed output entirely)
    #   • Kept, extraction disabled (or codec not
    #     SRT-convertible — image-based subs never
    #     reach here when extraction is on, see gate
    #     above)                                      → kept_subs (copied)
    kept_subs: list[dict] = []
    used_srt_paths: set[str] = set()

    for track in sub_tracks:
        lang      = track["language"] or "und"
        is_forced = track.get("is_forced", False)
        # Fallback for Track rows scanned before forced-from-name detection
        # was added: re-check the stored title for "Forced" / "English (Forced)".
        if not is_forced and track.get("title"):
            if _FORCED_RE.search(track["title"]):
                is_forced = True

        is_sdh = bool(track.get("is_hearing_impaired"))
        # Fallback for rows scanned before CC was added to the SDH regex.
        if not is_sdh and track.get("title"):
            if _SDH_RE.search(track["title"]):
                is_sdh = True

        is_dub = bool(track.get("is_dub"))
        codec     = (track.get("codec") or "").lower()
        si        = track["stream_index"]

        # ── User-resolved override (from a previous manual review) ────────
        # Takes precedence over the normal keep/drop logic entirely.
        override = subtitle_overrides.get(si)
        if override == "remove":
            actions.append(Action(
                action_type="drop_track",
                description=(
                    f"Drop subtitle [{lang}] {track.get('codec', '?')}"
                    f"{' (forced)' if is_forced else ''} "
                    f"(stream {si}) — removed via manual review"
                ),
                track_type="subtitle",
                stream_index=si,
                order=order,
            ))
            order += 1
            continue
        if override == "keep":
            actions.append(Action(
                action_type="copy_track",
                description=(
                    f"Copy subtitle [{lang}] {codec}"
                    f"{' (forced)' if is_forced else ''} "
                    f"(stream {si}) — kept via manual review"
                ),
                track_type="subtitle",
                stream_index=si,
                order=order,
            ))
            kept_subs.append(track)
            order += 1
            continue

        if not _sub_is_kept(track):
            actions.append(Action(
                action_type="drop_track",
                description=(
                    f"Drop subtitle [{lang}] {track.get('codec', '?')}"
                    f"{' (forced)' if is_forced else ''} "
                    f"(stream {track['stream_index']}) — not in keep list"
                ),
                track_type="subtitle",
                stream_index=track["stream_index"],
                order=order,
            ))
            order += 1
            continue

        if extract_subs_to_srt and codec in SRT_CONVERTIBLE_SUBS:
            srt_path = _build_srt_path(
                file_info.get("path", ""), lang, is_forced, is_sdh, is_dub, used_srt_paths
            )
            used_srt_paths.add(srt_path)
            tags = []
            if is_forced: tags.append("forced")
            if is_sdh:    tags.append("SDH")
            if is_dub:    tags.append("Dubtitle")
            tag_str = f" ({', '.join(tags)})" if tags else ""
            actions.append(Action(
                action_type="extract_subtitle",
                description=(
                    f"Extract subtitle [{lang}] {codec}{tag_str} "
                    f"(stream {track['stream_index']}) to external SRT: "
                    f"{Path(srt_path).name}"
                ),
                track_type="subtitle",
                stream_index=track["stream_index"],
                order=order,
                external_path=srt_path,
                language=lang,
                is_forced=is_forced,
            ))
            order += 1
            continue

        # Extraction disabled — keep this subtitle embedded as before.
        actions.append(Action(
            action_type="copy_track",
            description=(
                f"Copy subtitle [{lang}] {codec}"
                f"{' (forced)' if is_forced else ''} "
                f"(stream {track['stream_index']})"
            ),
            track_type="subtitle",
            stream_index=track["stream_index"],
            order=order,
        ))
        kept_subs.append(track)
        order += 1

    # ── Container decision ─────────────────────────────────────────────────
    # MP4 is blocked by:
    #   • any video codec outside MP4_COMPATIBLE_VIDEO
    #   • any kept audio codec outside MP4_COMPATIBLE_AUDIO (post AAC→AC3)
    #   • any kept (embedded, non-extracted) subtitle codec in
    #     MP4_INCOMPATIBLE_SUBS
    # Note: when SRT extraction is enabled, kept_subs is normally empty —
    # every text-based kept subtitle was extracted above, and any kept
    # image-based subtitle would have triggered the manual-review gate.
    # kept_subs is only non-empty when extraction is disabled, preserving
    # the original container-blocking behavior.
    video_audio_ok = _video_audio_mp4_compatible(video_tracks, kept_audio)

    subs_block_mp4 = any(
        (t.get("codec") or "").lower() in MP4_INCOMPATIBLE_SUBS
        for t in kept_subs
    )

    target_container = current_container
    output_extension = f".{current_container}"

    if prefer_mp4 and current_container != "mp4" and video_audio_ok and not subs_block_mp4:
        target_container = "mp4"
        output_extension = ".mp4"
        actions.append(Action(
            action_type="change_container",
            description=f"Convert container: {current_container.upper()} → MP4",
            order=order,
        ))
        order += 1

    # ── Fast-start (moov atom position) ────────────────────────────────────
    # Only relevant for files that ARE already MP4 — new MP4 outputs from a
    # container conversion above always get +faststart from the FFmpeg
    # command builder unconditionally, so we don't double-count.
    # has_faststart=False  → file confirmed missing fast-start → add it
    # has_faststart=True   → already optimised → nothing to do
    # has_faststart=None   → unknown (non-MP4, probe failed, setting off)
    needs_faststart = (
        add_faststart
        and has_faststart is False        # confirmed missing
        and current_container == "mp4"    # existing MP4 only
        and target_container == "mp4"     # not being converted away
    )
    if needs_faststart:
        actions.append(Action(
            action_type="add_faststart",
            description="Add fast start (move moov atom to front for streaming playback)",
            order=order,
        ))
        order += 1

    # ── Language fix pass ─────────────────────────────────────────────────
    # Runs after all track decisions are finalised so we only tag tracks
    # that are actually being kept (copy_track or transcode_track).
    # Video tracks are intentionally skipped — players don't use video
    # language tags for track selection.
    has_language_fix = False
    if settings.get("fix_undefined_language", False):
        lang_value = (settings.get("undefined_language_value") or "eng").strip() or "eng"
        lang_mode  = settings.get("undefined_language_mode", "all_undefined_per_type")

        dropped_si   = {a.stream_index for a in actions if a.action_type == "drop_track"}
        extracted_si = {a.stream_index for a in actions if a.action_type == "extract_subtitle"}

        for track_type in ("audio", "subtitle"):
            # Only kept tracks of this type (not dropped, not extracted to SRT)
            type_tracks = [
                t for t in tracks
                if t["track_type"] == track_type
                and t["stream_index"] not in dropped_si
                and t["stream_index"] not in extracted_si
            ]
            und_kept = [
                t for t in type_tracks
                if (t.get("language") or "und") == "und"
            ]
            if not und_kept:
                continue

            if lang_mode == "all_undefined":
                qualifying = {t["stream_index"] for t in und_kept}
            elif lang_mode == "all_undefined_per_type":
                # Tag only when ALL kept tracks of this type are und
                qualifying = (
                    {t["stream_index"] for t in und_kept}
                    if len(und_kept) == len(type_tracks)
                    else set()
                )
            elif lang_mode == "single_per_type":
                # Tag only when there is exactly one und kept track of this type
                qualifying = {und_kept[0]["stream_index"]} if len(und_kept) == 1 else set()
            else:
                qualifying = set()

            for i, action in enumerate(actions):
                if (
                    action.track_type == track_type
                    and action.stream_index in qualifying
                    and action.action_type in ("copy_track", "transcode_track")
                ):
                    actions[i] = dc_replace(action, target_language=lang_value)
                    has_language_fix = True

    # ── Is there anything to do? ───────────────────────────────────────────
    has_drops       = any(a.action_type == "drop_track" for a in actions)
    has_transcode   = any(a.action_type == "transcode_track" for a in actions)
    has_container   = any(a.action_type == "change_container" for a in actions)
    has_extract     = any(a.action_type == "extract_subtitle" for a in actions)
    has_faststart_a = any(a.action_type == "add_faststart" for a in actions)

    if not (has_drops or has_transcode or has_container or has_extract
            or has_faststart_a or has_language_fix):
        return ProcessingDecision(
            should_process=False,
            reason="File already meets all configured criteria — no changes needed.",
            actions=[],
        )

    # ── Build human-readable reason ────────────────────────────────────────
    parts: list[str] = []
    if has_drops:
        n_audio = sum(1 for a in actions
                      if a.action_type == "drop_track" and a.track_type == "audio")
        n_sub   = sum(1 for a in actions
                      if a.action_type == "drop_track" and a.track_type == "subtitle")
        if n_audio: parts.append(f"Remove {n_audio} audio track{'s' if n_audio > 1 else ''}")
        if n_sub:   parts.append(f"Remove {n_sub} subtitle track{'s' if n_sub > 1 else ''}")
    if has_transcode:
        parts.append("Transcode AAC 5.1 → AC3 5.1")
    if has_container:
        parts.append(f"Convert {current_container.upper()} → MP4")
    if has_extract:
        n = sum(1 for a in actions if a.action_type == "extract_subtitle")
        parts.append(f"Extract {n} subtitle{'s' if n > 1 else ''} to external SRT")
    if has_faststart_a:
        parts.append("Add fast start (web-optimised streaming)")
    if has_language_fix:
        n = sum(1 for a in actions if a.target_language)
        parts.append(f"Fix undefined language tag{'s' if n > 1 else ''} on {n} track{'s' if n > 1 else ''}")

    return ProcessingDecision(
        should_process=True,
        reason="; ".join(parts),
        actions=actions,
        target_container=target_container,
        output_extension=output_extension,
    )


# ── Internal helpers ───────────────────────────────────────────────────────────

def _video_audio_mp4_compatible(
    video_tracks: list[dict],
    kept_audio:   list[dict],
) -> bool:
    """Return True only if every video/audio track is MP4-compatible."""
    for t in video_tracks:
        if (t.get("codec") or "").lower() not in MP4_COMPATIBLE_VIDEO:
            logger.debug("MP4 blocked by video codec: %s", t.get("codec"))
            return False

    for t in kept_audio:
        codec = (t.get("codec") or "").lower()
        # AAC 5.1 will become AC3 (compatible); plain AAC is also fine
        if codec not in MP4_COMPATIBLE_AUDIO:
            logger.debug("MP4 blocked by audio codec: %s", codec)
            return False

    return True


def _build_srt_path(
    media_path: str,
    language:   str,
    is_forced:  bool,
    is_sdh:     bool,
    is_dub:     bool,
    used_paths: set[str],
) -> str:
    """
    Build the external SRT sidecar path following Plex's naming convention:

        Over Your Dead Body (2026).en.srt
        Over Your Dead Body (2026).en.forced.srt
        Over Your Dead Body (2026).en.sdh.srt
        Over Your Dead Body (2026).en.dub.srt

    The 3-letter ffprobe language code is converted to the 2-letter ISO
    639-1 code Plex expects (e.g. "eng" → "en"); unmapped codes (including
    "und") are passed through unchanged.

    SDH and Dubtitle tracks each get a distinct suffix so a file with all
    three subtitle types produces three separate, clearly-named SRT files
    rather than falling back to numeric disambiguation.

    If the resulting path is still in use after applying all flags
    (e.g. two plain English subtitle tracks with no distinguishing flags),
    a numeric suffix is appended: Movie.en.2.srt, Movie.en.3.srt, ...
    """
    p = Path(media_path)
    lang_tag = ISO_639_2_TO_1.get(language, language)

    def _candidate(suffix: str | None) -> str:
        parts = [p.stem, lang_tag]
        if is_forced:
            parts.append("forced")
        if is_sdh:
            parts.append("sdh")
        # Only add .dub when the track isn't already distinguished by .forced
        # or .sdh.  Forced and SDH tracks serve a specific purpose that's more
        # important than the dub/sub distinction; combining e.g. "forced.dub"
        # adds noise without benefit.
        if is_dub and not is_forced and not is_sdh:
            parts.append("dub")
        if suffix:
            parts.append(suffix)
        return str(p.parent / (".".join(parts) + ".srt"))

    candidate = _candidate(None)
    if candidate not in used_paths:
        return candidate

    i = 2
    while True:
        candidate = _candidate(str(i))
        if candidate not in used_paths:
            return candidate
        i += 1
