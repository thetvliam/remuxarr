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

from app.core.probe import _FORCED_RE, _SDH_RE, _DUB_RE

logger = logging.getLogger(__name__)

# ── MP4 compatibility tables ───────────────────────────────────────────────────
# Video codecs that can live inside an MP4/M4V container without transcoding.
# "avc"/"h265" are harmless slack, not live aliases — ffprobe's own
# codec_name is consistently "h264"/"hevc" across every real probe output
# seen throughout this project (confirmed directly, repeatedly); these two
# can never actually match. Kept rather than removed since a stray, unused
# alias here is genuinely harmless — flagged by independent review as
# worth a comment rather than worth the risk of deleting.
MP4_COMPATIBLE_VIDEO = frozenset({
    "h264", "avc", "hevc", "h265", "mpeg4", "mpeg2video", "mjpeg",
})
# Audio codecs compatible with MP4. Note: DTS, TrueHD, FLAC are NOT included.
MP4_COMPATIBLE_AUDIO = frozenset({
    "aac", "ac3", "eac3", "mp3", "alac", "opus",
})
# Subtitle codecs that CANNOT go into MP4 (image-based or advanced text).
# If any KEPT subtitle track has one of these codecs, MP4 conversion is
# blocked entirely — the file stays in its current container.
MP4_INCOMPATIBLE_SUBS = frozenset({
    "hdmv_pgs_subtitle", "pgssub", "dvd_subtitle", "dvdsub",
    "ass", "ssa", "dvb_subtitle",
    # Text codecs FFmpeg cannot STREAM-COPY into MP4 either — confirmed
    # empirically with a real subrip-in-MKV source: `-c:s copy -f mp4`
    # fails at header-write with "Could not find tag for codec subrip in
    # stream #1, codec not currently supported in container" (exit 234,
    # zero-byte output). MP4's only native text subtitle format is
    # mov_text; SubRip/WebVTT must be transcoded to it, and this
    # pipeline deliberately only ever copies subtitles (see the kept_subs
    # block in ffmpeg.build_ffmpeg_command). Without these entries, a
    # container conversion keeping a text subtitle — only reachable when
    # extract_text_subtitles_to_srt is disabled, since extraction
    # otherwise removes text subs from kept_subs before this check —
    # produced a guaranteed-failing FFmpeg command. With them, such
    # files simply stay in their current container, exactly like files
    # with kept image subtitles always have. mov_text itself is
    # deliberately NOT listed: it's MP4's native format and copies fine.
    # If conversion-with-text-subs is ever wanted, the upgrade path is
    # transcoding to mov_text during conversion, not removing these.
    "subrip", "srt", "webvtt",
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
    # Extra fields for transcode_track actions — currently only ever
    # populated by worker._make_audio_transcode_decision's corrupt-source-
    # audio retry path (transcodes to AAC), not by analyze_file itself.
    # Not tied to any specific codec pairing; whichever retry path sets
    # these determines the actual target.
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
    # NOTE: there is deliberately no output_extension field — the output
    # path is derived in ffmpeg.determine_output_path from target_container
    # plus the presence of a change_container action. An earlier version
    # carried a separate, derivable output_extension here, and the two
    # diverging is exactly what caused real silent file renames
    # (.m2ts → .ts, .m4v/.mov → .mp4) on incidental processing — see
    # determine_output_path's docstring.
    # Populated only for is_manual_review=True decisions caused by
    # non-convertible (image-based) subtitle tracks. Each entry:
    #   {stream_index, language, codec, is_forced, title}
    # The UI uses this to render per-track Keep/Remove choices.
    flagged_subtitles: list[dict] | None = None
    # Set when the file's surviving (kept) audio track has a DEFINED but
    # non-preferred language — e.g. "dut" on an English show. Shape:
    # {"stream_index": int, "language": str}. None means either everything
    # matched, or the surviving track is "und" (that case belongs to
    # fix_undefined_language, not this). The scanner reads this to
    # upsert/clear an AudioLanguageFlag row — informational only, never
    # blocks should_process the way is_manual_review does. stream_index is
    # included (not just the language) because the Audio Language Review
    # "apply" action needs to know exactly which track to target when
    # writing the corrected language — the language alone isn't enough to
    # reliably identify the track again later.
    audio_language_mismatch: dict | None = None

    # Subtitle-track counterpart to audio_language_mismatch above. Unlike
    # that field though, this one is ONLY ever populated by an undefined
    # ("und") tag that fix_undefined_language's "always_ask" mode flagged
    # for a human decision — there's no "defined but wrong subtitle
    # language" detection built (wasn't asked for, and subtitle tracks
    # don't have the same safety-net-survivor scenario audio does that
    # motivated that case for audio in the first place).
    subtitle_language_mismatch: dict | None = None

    # True specifically when the SOURCE file was already MP4 and already
    # faststart-optimised (i.e. has_faststart was True when this decision
    # was computed) — False whenever that isn't the case, including when
    # it isn't known or doesn't apply (non-MP4 source, probe failed,
    # etc), since "unknown" and "genuinely false" both correctly mean the
    # same thing here: don't force the flag. Exists so
    # build_ffmpeg_command can preserve an already-faststart source's optimisation on ANY
    # remux, not just ones that specifically needed to add faststart or
    # convert containers. Confirmed directly: a plain FFmpeg remux that
    # doesn't explicitly include -movflags +faststart silently rebuilds
    # the container with the moov atom at the end regardless of where it
    # was in the source — even for a pure, lossless stream-copy with no
    # video or audio re-encoding involved. Without this, any unrelated
    # remux (a language correction, a track drop, anything) on an
    # already-optimised MP4 would quietly undo that optimisation as a
    # side effect, only for a later scan to "discover" faststart is
    # missing again and re-add it — not a decision-logic bug, a real,
    # silent regression in the file itself.
    source_already_faststart: bool = False


# ── Main function ──────────────────────────────────────────────────────────────

def analyze_file(
    file_info: dict,
    tracks:    list[dict],
    settings:  dict,
    subtitle_overrides: dict[int, str] | None = None,
    audio_language_overrides: dict[int, str] | None = None,
    subtitle_language_overrides: dict[int, str] | None = None,
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
    audio_language_overrides : dict[int, str] | None
        Per-track language corrections from the Audio Language Review
        section, keyed by stream_index with value an ISO 639-2/B code
        (e.g. "eng"). Set by the user when a track has a DEFINED but
        wrong language — distinct from fix_undefined_language, which only
        ever touches "und" tracks. Persisted on
        MediaFile.audio_language_overrides.
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
    # A fresh copy, not a reference to the caller's own dict — the
    # image_subtitle_handling gate below may inject synthetic "keep"/
    # "remove" entries for always_keep/always_remove, and this module's
    # own docstring guarantees it touches nothing outside itself; mutating
    # a caller-owned dict would quietly violate that.
    subtitle_overrides = dict(subtitle_overrides) if subtitle_overrides else {}
    audio_language_overrides = audio_language_overrides or {}
    subtitle_language_overrides = subtitle_language_overrides or {}
    keep_audio_langs    = set(settings.get("keep_audio_languages",   ["eng"]))
    keep_sub_langs      = set(settings.get("keep_subtitle_languages", ["eng"]))
    keep_forced_subs    = settings.get("keep_forced_subtitles",   True)
    keep_default_audio  = settings.get("keep_default_audio",      True)
    prefer_mp4          = settings.get("prefer_mp4_container",    True)
    # Clamped to a minimum of 1 — a threshold of 0 would make "contains 0
    # or more undefined-language tracks" true for every file, including
    # ones with none at all, silently forcing the whole pipeline into
    # manual review. 0 is never a meaningful value for a ">=" comparison
    # like this one; 1 is the lowest threshold that actually means
    # something. Same defensive pattern already used for
    # max_concurrent_jobs elsewhere in this file.
    und_threshold       = max(1, int(settings.get("und_audio_threshold", 2)))
    extract_subs_to_srt = settings.get("extract_text_subtitles_to_srt", True)
    add_faststart       = settings.get("add_faststart_to_mp4", True)

    raw_container = file_info.get("container")
    if not raw_container:
        # We genuinely don't know this file's container — proceeding would
        # mean guessing, and a wrong guess here is exactly what can turn a
        # working file into a corrupted one (this replaces a previous
        # `or "mkv"` default that silently treated "unknown" identically
        # to "confirmed MKV" — plausible root cause of a real incident
        # where a genuinely-MP4 file was rewritten as Matroska while
        # keeping its .mp4 extension). Raising here is caught by the
        # scanner's per-file exception handler (logged, counted as an
        # error, scan continues) and by the worker's job-completion
        # handler (job marked failed with this message as the visible
        # error) — a clear, actionable failure instead of a silent wrong
        # guess producing a corrupted file.
        raise ValueError(
            f"Cannot determine container format for "
            f"{file_info.get('path', '<unknown path>')} — container info "
            f"is missing or empty. Refusing to guess rather than risk "
            f"writing the wrong format."
        )
    current_container = raw_container.lower()
    logger.info(
        "analyze_file: file_info.get('container')=%r -> current_container=%r "
        "(path=%s)",
        file_info.get("container"), current_container, file_info.get("path"),
    )

    video_tracks = [t for t in tracks if t["track_type"] == "video"]
    audio_tracks = [t for t in tracks if t["track_type"] == "audio"]

    # Subtitle dicts are normalised into upgraded COPIES here, before any
    # gating code runs: Track rows scanned before probe.py's
    # title-based detection existed have is_forced/is_hearing_impaired/
    # is_dub stored as False even when the title says otherwise (e.g.
    # title "English (Forced)"). An earlier version applied this
    # fallback only inside the main subtitle loop, into LOCAL variables
    # — so the keep/drop decision (_sub_is_kept reads the dict) and the
    # manual-review gate (runs before the loop entirely) never saw the
    # upgrade, and a legacy forced-by-title subtitle with a non-kept
    # language was dropped despite keep_forced_subtitles=True, defeating
    # the fallback's own stated purpose. It only ever affected
    # description strings and .forced.srt naming of tracks kept for
    # OTHER reasons. Caught by independent review.
    #
    # Copies, not in-place writes — analyze_file must never mutate its
    # caller's inputs. All three flags are upgraded to mirror probe.py's
    # own three-way title detection exactly (forced gates keep/drop;
    # SDH/dub feed .srt naming and descriptions, same legacy gap).
    # Fresh scans are unaffected either way: probe.py already bakes
    # title detection into the stored flags.
    def _normalise_sub(t: dict) -> dict:
        title = t.get("title") or ""
        if not title:
            return t
        upgraded = dict(t)
        if not upgraded.get("is_forced") and _FORCED_RE.search(title):
            upgraded["is_forced"] = True
        if not upgraded.get("is_hearing_impaired") and _SDH_RE.search(title):
            upgraded["is_hearing_impaired"] = True
        if not upgraded.get("is_dub") and _DUB_RE.search(title):
            upgraded["is_dub"] = True
        return upgraded

    sub_tracks = [_normalise_sub(t) for t in tracks if t["track_type"] == "subtitle"]

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

    if len(und_audio) >= und_threshold and not file_info.get("und_audio_threshold_acknowledged"):
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
    #
    # NOTE the deliberate asymmetry with audio: there is no "und" branch
    # here, so an undefined-language subtitle only survives by being
    # forced — while undefined-language AUDIO is always kept
    # unconditionally. Not an oversight; see the full rationale at the
    # `or lang == "und"` branch inside the audio should_keep condition
    # below (in short: a wrong guess costs a silent, ruined output on
    # the audio side and merely a lost optional extra on this one, so
    # each side defaults to whichever mistake is cheaper).
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
            image_subtitle_handling = settings.get("image_subtitle_handling", "always_ask")

            if image_subtitle_handling in ("always_keep", "always_remove"):
                # Inject synthetic overrides into the LOCAL copy (see the
                # comment where subtitle_overrides was copied above) rather
                # than building drop_track/copy_track actions directly here
                # — this reuses the exact same, already-correct keep/drop
                # logic further down instead of duplicating it, and these
                # synthetic entries are never confused with the user's own
                # explicit per-file choices since they're never persisted
                # to MediaFile.subtitle_overrides.
                choice = "keep" if image_subtitle_handling == "always_keep" else "remove"
                for t in non_convertible:
                    subtitle_overrides[t["stream_index"]] = choice
                # Falls through to the normal subtitle loop below — no
                # early return, no manual review.
            else:
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

        should_keep = (
            lang in keep_audio_langs
            # Undefined-language audio is ALWAYS kept — deliberately
            # asymmetric with subtitles, where _sub_is_kept (above) has no
            # equivalent branch and an "und" subtitle only survives by
            # being forced. The asymmetry is intentional, weighted by
            # what a wrong guess costs on each side: dropping the only
            # audio track because its tag happened to be missing would
            # produce a silent, effectively ruined output file (and
            # remuxes are destructive — the original is deleted after),
            # while dropping an und subtitle merely loses an optional
            # extra of unknowable relevance. "und" tells us nothing
            # either way, so each side defaults to whichever mistake is
            # cheaper to make: keep the audio, drop the subtitle. The
            # fix_undefined_language setting and the und_audio_threshold
            # manual-review gate both exist precisely to let und tags be
            # resolved properly rather than guessed at here.
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

        # Kept — always a lossless copy, never a transcode.
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
        # Title-based forced/SDH/dub fallbacks already applied — sub_tracks
        # entries are normalised copies (see _normalise_sub above), so the
        # dict values here are authoritative. No per-loop re-derivation.
        is_forced = track.get("is_forced", False)
        is_sdh    = bool(track.get("is_hearing_impaired"))
        is_dub    = bool(track.get("is_dub"))
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

    if prefer_mp4 and current_container != "mp4" and video_audio_ok and not subs_block_mp4:
        target_container = "mp4"
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
    #
    # has_faststart is only ever non-None when the SOURCE was already MP4
    # (see the "Detect fast-start" block that computes it, in scanner.py /
    # worker.py), which makes current_container == "mp4" below always true
    # whenever this whole expression could possibly matter — and, since
    # target_container only ever differs from current_container when
    # converting TO "mp4" (never away from it — there's no code path that
    # ever assigns anything else), checking target_container == "mp4" here
    # was always implied by current_container == "mp4" already being true.
    # Removed as a no-op condition, not a behavior change — confirmed by
    # tracing every assignment site to target_container in this file.
    needs_faststart = (
        add_faststart
        and has_faststart is False        # confirmed missing
        and current_container == "mp4"    # existing MP4 only
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
    has_language_fix    = False   # overall flag — either pass counts for should_process
    und_fixed_indices:      set[int] = set()   # tracks fixed by THIS pass specifically
    override_fixed_indices: set[int] = set()   # tracks fixed by the override pass below
    # Qualifying-but-not-auto-fixed tracks under always_ask — feed into the
    # mismatch detection blocks below rather than being tagged directly.
    und_flagged_audio:    set[int] = set()
    und_flagged_subtitle: set[int] = set()

    und_mode = settings.get("fix_undefined_language", "always_leave")
    # Backward compat: a database that still holds the raw boolean from
    # before this setting became three-state (True/False rather than
    # always_fix/always_ask/always_leave) — maps to the equivalent new
    # value so an existing install's behavior doesn't silently change on
    # upgrade just because the stored value hasn't been touched since.
    if und_mode is True:
        und_mode = "always_fix"
    elif und_mode is False:
        und_mode = "always_leave"

    if und_mode in ("always_fix", "always_ask"):
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
                    # A track with an already-pending override (the user
                    # already resolved this via Apply, possibly before a
                    # reprocess attempt that failed and hasn't updated the
                    # stored language yet) is left alone here entirely —
                    # that specific, deliberate choice takes precedence
                    # over the bulk/automatic pass either way.
                    overrides_for_type = (
                        audio_language_overrides if track_type == "audio"
                        else subtitle_language_overrides
                    )
                    if action.stream_index in overrides_for_type:
                        continue
                    if und_mode == "always_fix":
                        actions[i] = dc_replace(action, target_language=lang_value)
                        has_language_fix = True
                        und_fixed_indices.add(action.stream_index)
                    else:   # always_ask — flag for review, don't touch the track
                        if track_type == "audio":
                            und_flagged_audio.add(action.stream_index)
                        else:
                            und_flagged_subtitle.add(action.stream_index)

    # ── Audio language mismatch detection (for Audio Language Review) ───────
    # Distinct from is_manual_review above — this never blocks processing,
    # it's purely informational. Surfaced separately so a human can
    # optionally relabel a wrong-but-defined language tag (e.g. an English
    # show mistagged "dut") or confirm it's already correct (e.g. anime
    # that's genuinely, correctly Japanese) without holding up the file.
    #
    # Deliberately runs here, after the language fix pass above rather than
    # earlier alongside the audio keep/drop loop — und_flagged_audio only
    # gets its final value from that pass, so detection has to wait for it.
    #
    # Iterates every surviving track rather than assuming exactly one,
    # since a file could have "und" tracks alongside the one non-"und"
    # survivor — "und" is deliberately skipped in THIS half, handled
    # separately below via und_flagged_audio instead (which only exists
    # under always_ask). Also skips a track that already has a pending
    # override, since the override pass below will correct it — nothing
    # left to flag.
    audio_language_mismatch = None
    if not has_preferred_audio:
        for t in kept_audio:
            lang = t["language"] or "und"
            if lang != "und" and t["stream_index"] not in audio_language_overrides:
                audio_language_mismatch = {
                    "stream_index": t["stream_index"],
                    "language":     lang,
                }
                break
    # Undefined tracks flagged by always_ask above — deliberately NOT
    # gated by has_preferred_audio the way the defined-but-wrong case is:
    # a track needing a language decision is worth surfacing regardless of
    # whether some OTHER track in the same file already happens to match a
    # preferred language.
    if audio_language_mismatch is None and und_flagged_audio:
        si = next(iter(und_flagged_audio))
        audio_language_mismatch = {"stream_index": si, "language": "und"}

    # ── Subtitle language mismatch detection (for Subtitle Language Review) ──
    # Subtitle counterpart to the und-flagging half of the block above —
    # see subtitle_language_mismatch's own docstring on ProcessingDecision
    # for why there's no defined-but-wrong-language case here the way
    # there is for audio.
    subtitle_language_mismatch = None
    if und_flagged_subtitle:
        si = next(iter(und_flagged_subtitle))
        subtitle_language_mismatch = {"stream_index": si, "language": "und"}

    # A persisted override (audio_language_overrides / subtitle_language_
    # overrides) never expires or gets cleared once applied — it's meant
    # to survive future re-scans, same as any other override in this
    # file. That's correct and intentional right up until the correction
    # has actually succeeded, at which point the track's REAL, current
    # language (as read from the source file, via `tracks` — not
    # anything derived from the override itself) genuinely already
    # matches what the override says it should be. Both passes below
    # need to check that before reapplying anything, or they can never
    # tell the difference between "still needs correcting" and "already
    # corrected, successfully, a while ago" — confirmed directly: a
    # file that's already been through Audio Language Review keeps
    # showing up as "Correct language tag on 1 track" on every future
    # full scan, forever, even though the actual file is already right.
    current_language_by_stream = {
        t["stream_index"]: (t.get("language") or "und").strip().lower()
        for t in tracks
    }

    # ── Audio & subtitle language override passes ────────────────────────────
    # Per-track, user-directed corrections from the Audio/Subtitle Language
    # Review sections. Shared implementation in
    # _apply_language_override_pass (below analyze_file) — was two
    # separate, structurally identical blocks; see that function's
    # docstring for the full rationale, including why comparing against
    # each track's current, real language matters here.
    override_fixed_indices.update(_apply_language_override_pass(
        actions, "audio", audio_language_overrides, current_language_by_stream,
    ))
    override_fixed_indices.update(_apply_language_override_pass(
        actions, "subtitle", subtitle_language_overrides, current_language_by_stream,
    ))
    if override_fixed_indices:
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
            audio_language_mismatch=audio_language_mismatch,
            subtitle_language_mismatch=subtitle_language_mismatch,
            source_already_faststart=has_faststart is True,
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
    # NOTE: no reason-string branch for has_transcode here — analyze_file
    # itself never produces a transcode_track action (confirmed directly:
    # the only actual creator is worker._make_audio_transcode_decision,
    # which operates on a COPY of the decision at retry time, after
    # analyze_file has already returned and this reason string has
    # already been built). has_transcode is always False whenever this
    # code actually runs, so a "Transcode AAC 5.1 → AC3 5.1" branch here
    # was unreachable dead code describing a feature that no longer
    # exists (the AAC 5.1 -> AC3 setting was removed earlier this
    # project). Caught by independent review. has_transcode itself is
    # kept below for the "anything to do at all" check — only the
    # reason-string branch was actually dead.
    if has_container:
        parts.append(f"Convert {current_container.upper()} → MP4")
    if has_extract:
        n = sum(1 for a in actions if a.action_type == "extract_subtitle")
        parts.append(f"Extract {n} subtitle{'s' if n > 1 else ''} to external SRT")
    if has_faststart_a:
        parts.append("Add fast start (web-optimised streaming)")
    if und_fixed_indices:
        n = len(und_fixed_indices)
        parts.append(f"Fix undefined language tag{'s' if n > 1 else ''} on {n} track{'s' if n > 1 else ''}")
    if override_fixed_indices:
        n = len(override_fixed_indices)
        parts.append(f"Correct language tag{'s' if n > 1 else ''} on {n} track{'s' if n > 1 else ''}")

    return ProcessingDecision(
        should_process=True,
        reason="; ".join(parts),
        actions=actions,
        target_container=target_container,
        audio_language_mismatch=audio_language_mismatch,
        subtitle_language_mismatch=subtitle_language_mismatch,
        source_already_faststart=has_faststart is True,
    )


# ── Internal helpers ───────────────────────────────────────────────────────────

def _apply_language_override_pass(
    actions: list[Action],
    track_type: str,
    overrides: dict[int, str] | None,
    current_language_by_stream: dict[int, str],
) -> set[int]:
    """
    Shared implementation for the audio and subtitle language override
    passes in analyze_file — previously two separate, structurally
    identical blocks (one built by directly mirroring the other),
    differing only in which track_type/overrides dict to consult.

    Per-track, user-directed corrections from the Audio/Subtitle
    Language Review sections — distinct from the bulk fix_undefined_
    language pass, which only ever touches "und" tracks. Audio
    additionally handles the DEFINED-but-WRONG case (e.g. an English
    show whose only audio track is mistagged "dut"); subtitle only ever
    resolves an undefined tag — see subtitle_language_mismatch's own
    docstring on ProcessingDecision for why there's no defined-but-wrong
    subtitle case the way there is for audio.

    Only ever applies to a track that's still present as copy/transcode
    at this point — if the override's target track ended up dropped for
    some other reason, there's nothing to relabel and this is a no-op
    for it, which is the correct, safe outcome rather than an error.
    For subtitles specifically, the action_type check also naturally
    excludes a track that got extracted to an external .srt (that's an
    extract_subtitle action, not copy_track/transcode_track) — no
    separate check needed for that case the way dropped_si is handled
    explicitly.

    Compares the override against each track's real, current language
    (current_language_by_stream, derived from the source file via
    `tracks` — never from the override itself) and skips reapplying it
    once they already match. An override persists indefinitely once
    set, by design, meant to survive future re-scans — but without this
    check it would keep reapplying itself, and keep counting toward
    has_language_fix, forever, even long after the correction had
    already succeeded. Confirmed as a real, reported bug: a file
    already corrected through Audio Language Review kept showing
    "Correct language tag on 1 track" and getting reprocessed on every
    subsequent full scan.

    Mutates `actions` in place, matching every other pass in
    analyze_file. Returns the set of stream_indices actually relabeled,
    for the caller to fold into has_language_fix / override_fixed_indices.
    """
    fixed_indices: set[int] = set()
    if not overrides:
        return fixed_indices

    dropped_si = {a.stream_index for a in actions if a.action_type == "drop_track"}
    for i, action in enumerate(actions):
        override_lang = (overrides.get(action.stream_index) or "").strip().lower()
        if (
            action.track_type == track_type
            and action.stream_index in overrides
            and action.stream_index not in dropped_si
            and action.action_type in ("copy_track", "transcode_track")
            and current_language_by_stream.get(action.stream_index) != override_lang
        ):
            actions[i] = dc_replace(action, target_language=override_lang)
            fixed_indices.add(action.stream_index)

    return fixed_indices


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
        # Plain membership check — no transcoding happens here. AC3 is
        # simply already MP4-compatible in its own right; nothing about
        # this check involves converting one codec into another.
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
