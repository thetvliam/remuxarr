"""
ffprobe wrapper.

probe_file()          → raw dict from ffprobe JSON output
extract_tracks()      → normalised list of track dicts
extract_format_info() → container / duration / font_attachments
is_faststart_mp4()    → True/False/None (box-level MP4 atom check)
"""
import json
import logging
import re
import subprocess
from datetime import datetime
from pathlib import Path
from typing import NamedTuple

from app.config import settings as app_settings

logger = logging.getLogger(__name__)

MEDIA_EXTENSIONS = {
    ".mkv", ".mp4", ".avi", ".mov", ".m4v",
    ".ts", ".m2ts", ".wmv", ".flv", ".webm",
}

# Matches "SDH" or "CC" (Closed Captions — US equivalent of SDH) as a whole
# word, case-insensitive.  Both indicate accessibility subtitles and map to
# the .sdh.srt Plex suffix.
# Examples: "SDH", "English (SDH)", "English SDH", "CC", "[CC]", "English [CC]"
_SDH_RE = re.compile(r"\b(SDH|CC)\b", re.IGNORECASE)

# Matches "Forced" as a whole word in a track name tag.
# Many MP4 encoders (e.g. Lavf) store the forced flag as a track name rather
# than setting disposition.forced, so we detect both.
# Examples: "Forced", "English (Forced)", "Forced Subtitles"
_FORCED_RE = re.compile(r"\bForced\b", re.IGNORECASE)

# Matches "Dubtitle" or "Dub" as a whole word (case-insensitive).
# Dubtitles transcribe the dubbed dialogue rather than translating the original
# — common in anime rips.
# Examples: "Dubtitle", "English Dubtitle", "English (Dubtitle)", "Dub"
_DUB_RE = re.compile(r"\b(Dubtitle|Dub)\b", re.IGNORECASE)


class ProbeError(Exception):
    pass


# ── Public API ─────────────────────────────────────────────────────────────────

def probe_file(path: str, ffprobe_bin: str | None = None) -> dict:
    """
    Run ffprobe and return the raw parsed JSON.
    Raises ProbeError on any failure.

    With no binary given it runs the one REMUXARR_FFPROBE_PATH names, read
    at each call. The default used to be "ffprobe" from PATH, and the calls
    that left the binary out, the tags check and revert capture and
    matching among them, ran that one instead of the configured one.

    Chapters are asked for so an MP4's chapter track can be told apart
    (chapter_track_indices). Before that, nothing asked, so every revert
    manifest recorded 0 chapters whatever the file had.
    """
    if ffprobe_bin is None:
        ffprobe_bin = app_settings.FFPROBE_PATH
    cmd = [
        ffprobe_bin,
        "-v", "quiet",
        "-print_format", "json",
        "-show_format",
        "-show_streams",
        "-show_chapters",
        path,
    ]
    try:
        result = subprocess.run(
            cmd, capture_output=True, text=True,
            errors="replace", timeout=120,
        )
    except subprocess.TimeoutExpired:
        raise ProbeError(f"ffprobe timed out: {path}")
    except FileNotFoundError:
        raise ProbeError(f"ffprobe binary not found: {ffprobe_bin}")

    if result.returncode != 0:
        raise ProbeError(f"ffprobe failed for {path}: {result.stderr.strip()}")

    try:
        return json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise ProbeError(f"Cannot parse ffprobe output for {path}: {exc}") from exc


def extract_tracks(probe_data: dict) -> list[dict]:
    """
    Parse ffprobe stream objects into normalised track dicts.
    Only video, audio, and subtitle streams are returned.
    """
    tracks = []
    for stream in probe_data.get("streams", []):
        codec_type = stream.get("codec_type", "")
        if codec_type not in ("video", "audio", "subtitle"):
            continue

        disposition = stream.get("disposition", {})

        # Embedded cover art / thumbnail images are reported by ffprobe as
        # codec_type="video" with the "attached_pic" disposition flag. A
        # cover is not a track, so it is left out here, and with it out of
        # every track list the decision engine judges and build_ffmpeg_command
        # maps as a track. Covers go back on their own: into a Matroska
        # output attached again (ffmpeg.find_matroska_covers,
        # cover_attach_args), into an MP4 mapped after the tracks
        # (ffmpeg.find_mp4_covers). An older comment here said mapping one
        # into MP4 failed with "dimensions not set"; with FFmpeg 8.1 a JPEG,
        # PNG or BMP cover copies into MP4 and keeps attached_pic.
        if codec_type == "video" and disposition.get("attached_pic", 0) == 1:
            continue

        tags = stream.get("tags", {})
        language = (
            tags.get("language")
            or tags.get("LANGUAGE")
            or "und"
        ).lower().strip()
        if language in ("", "none", "null", "undefined"):
            language = "und"

        # Track title / name — both MKV ("title") and MP4 ("name") conventions.
        # FFmpeg 8.1+ exposes the MP4 ©nam atom as "name" in the JSON tags,
        # so no secondary probe or manual atom parsing is needed.
        title = (
            tags.get("title") or tags.get("TITLE")
            or tags.get("name") or tags.get("NAME")
        )

        # SDH / CC detection
        any_tag_is_sdh = any(
            isinstance(v, str) and _SDH_RE.search(v)
            for v in tags.values()
        )

        # Forced detection — check disposition flag AND name/title tag.
        # Many MP4 files set the track name to "Forced" or "English (Forced)"
        # without setting disposition.forced; we treat both equally.
        any_tag_is_forced = any(
            isinstance(v, str) and _FORCED_RE.search(v)
            for v in tags.values()
        )

        # Dubtitle detection
        any_tag_is_dub = any(
            isinstance(v, str) and _DUB_RE.search(v)
            for v in tags.values()
        )

        tracks.append({
            "stream_index":   stream.get("index", 0),
            "track_type":     codec_type,
            "codec":          stream.get("codec_name", "unknown"),
            "language":       language,
            "channels":       stream.get("channels"),
            "channel_layout": stream.get("channel_layout", ""),
            "is_default":     disposition.get("default", 0) == 1,
            "is_forced": (
                disposition.get("forced", 0) == 1
                or bool(title and _FORCED_RE.search(title))
                or any_tag_is_forced
            ),
            "is_hearing_impaired": (
                disposition.get("hearing_impaired", 0) == 1
                or bool(title and _SDH_RE.search(title))
                or any_tag_is_sdh
            ),
            "is_dub": (
                disposition.get("dub", 0) == 1
                or bool(title and _DUB_RE.search(title))
                or any_tag_is_dub
            ),
            "title":          title,
            # Read for the AC3 forge's marker (forge.FORGE_AC3_HANDLER_NAME).
            # FFmpeg writes it as the MP4 track's handler name, and into
            # Matroska as a HANDLER_NAME tag, so both spellings are looked
            # for. It is the one stream tag that survived every job the
            # forge marker has to outlive: titles are lost when a job
            # rewrites an MP4, and a tag of our own when one turns Matroska
            # into MP4 (both measured with the FFmpeg 8.1 the image ships).
            "handler_name":   tags.get("handler_name") or tags.get("HANDLER_NAME"),
        })

    return tracks


# Fonts embedded in a Matroska file. Modern mkvmerge writes a "font/*"
# mimetype, but older versions and other muxers write one of these, and
# some write application/octet-stream and leave only the filename to go
# on — so all three signals are checked.
_LEGACY_FONT_MIMETYPES = frozenset({
    "application/x-truetype-font",
    "application/x-font-ttf",
    "application/x-font-otf",
    "application/vnd.ms-opentype",
    "application/font-sfnt",
})
_FONT_SUFFIXES = frozenset({".ttf", ".otf", ".ttc", ".eot", ".woff", ".woff2"})


def _is_font_attachment(stream: dict) -> bool:
    """Whether an attachment stream is a font, by mimetype or by filename."""
    tags = stream.get("tags") or {}
    mimetype = (tags.get("mimetype") or "").strip().lower()
    if mimetype.startswith("font/") or mimetype in _LEGACY_FONT_MIMETYPES:
        return True
    filename = (tags.get("filename") or "").strip().lower()
    return any(filename.endswith(suffix) for suffix in _FONT_SUFFIXES)


def count_font_attachments(probe_data: dict) -> int:
    """
    How many embedded font attachments the file carries.

    Not derivable from extract_tracks(), which keeps only video, audio and
    subtitle streams — an attachment has never appeared in the track list
    and so cannot reach the decision engine through it.

    It has to reach the decision engine because fonts only exist in a file
    because its styled subtitles reference them by name, and only Matroska
    can hold them. Converting such a file to MP4 drops every font and
    flattens the ASS tracks to SRT: the typesetting that placed text over
    signs in the frame becomes lines at the bottom of the screen, over a
    sign still visible underneath. Nothing fails, and the only symptom is
    subtitles rendering in a fallback typeface some time later.

    Cover art is not counted and never could be: ffprobe reports it as
    codec_type="video" with the attached_pic disposition, not as an
    attachment, and extract_tracks drops it separately.
    """
    return sum(
        1 for stream in probe_data.get("streams", [])
        if stream.get("codec_type") == "attachment"
        and _is_font_attachment(stream)
    )


# The major brand QuickTime writes. ffprobe names a QuickTime file and an
# MP4 alike ("mov,mp4,m4a,3gp,3g2,mj2"), and both normalise to "mp4", so the
# brand is what tells them apart.
QUICKTIME_BRAND = "qt  "


def is_quicktime(probe_data: dict | None) -> bool:
    """
    Whether the probed file is a QuickTime movie rather than an MP4, by its
    major brand. A revert or an AC3 Forge undo writes one back with FFmpeg's
    QuickTime muxer, so it stays one (ffmpeg.output_muxer); no job or add
    starts on one (recording_signs).
    """
    tags = ((probe_data or {}).get("format") or {}).get("tags") or {}
    return tags.get("major_brand") == QUICKTIME_BRAND


# A file's own tags a muxer writes for itself, whatever it is given: the
# MP4 and QuickTime muxers their brand fields, every muxer but AVI's the
# program that wrote the file ("encoder"), the AVI muxer that program as
# "software" (its ISFT chunk). A rewrite changes them however carefully it
# copies the rest, and none is the file's own: lost_file_tags passes them
# over. Each was seen changed by an ordinary job on the test library.
REWRITTEN_TAGS = frozenset({"major_brand", "minor_version", "compatible_brands",
                            "encoder", "software"})


def lost_file_tags(source: dict | None, output: dict | None) -> list[str]:
    """
    The source's own tags, as ffprobe shows them on the file, that the
    output does not have with the same value, by the source's names. Names
    are matched ignoring case, as a Matroska stores them in capitals and an
    MP4 in lower case. Tags the output has and the source does not are no
    concern, nor are REWRITTEN_TAGS.

    A job and an AC3 Forge run refuse to replace a file that would lose any
    (worker._lost_tags_error). FFmpeg's MP4 muxer writes a fixed list of
    tags and no others (mov_write_ilst_box, libavformat/movenc.c, FFmpeg
    8.1.3): an MP4 rewritten as an MP4 lost an iTunes-style file's content
    rating ("iTunEXTC"), cast ("iTunMOVI"), purchase date, rating and sort
    name, measured with a real job. No revert could give them back, as a
    revert writes an MP4 the same way.

    creation_time counts as kept when the output has the same date and time
    to the second (_same_second). An MP4 holds the date in whole seconds,
    and FFmpeg's MP4 muxer drops the fraction rather than rounding it:
    measured, .460353, .5 and .999999 all came back as .000000 in the same
    second. A Matroska or WebM date carries microseconds, as mkvmerge
    writes it, so every conversion of such a file to MP4 was refused as
    losing its date (issue #61). A Matroska output keeps the fraction and
    matches as written.
    """
    def tags(probe_data):
        return ((probe_data or {}).get("format") or {}).get("tags") or {}
    kept = {key.lower(): value for key, value in tags(output).items()}

    def lost(key, value):
        name = key.lower()
        if name in REWRITTEN_TAGS or kept.get(name) == value:
            return False
        return not (name == "creation_time" and _same_second(value, kept.get(name)))
    return [key for key, value in tags(source).items() if lost(key, value)]


def _same_second(made: str, kept: str | None) -> bool:
    """
    Whether two creation dates, as ffprobe shows them
    (2026-11-20T08:00:15.460353Z), are the same date and time to the second.
    False when either is not such a date: one FFmpeg could not read is then
    compared as written, as any other tag is.
    """
    try:
        a, b = datetime.fromisoformat(made), datetime.fromisoformat(kept)
    except (TypeError, ValueError):
        return False
    return a.replace(microsecond=0) == b.replace(microsecond=0)


def creation_time(probe_data: dict | None) -> str | None:
    """
    The date the file says it was made, as ffprobe shows it in the file's
    own tags, or None when it gives none. For a phone's or a camera's
    recording this is when it was shot. A job writes it back
    (ffmpeg.build_ffmpeg_command), as FFmpeg does not carry it across.
    """
    tags = ((probe_data or {}).get("format") or {}).get("tags") or {}
    return tags.get("creation_time") or None


# How ffprobe names a Dolby Vision stream's configuration record among its
# side data: an MP4's dvcC, dvvC or dvwC box, a Matroska's
# BlockAdditionMapping. The RPUs that carry the Dolby Vision metadata ride
# in the packets, which every job copies; the record is the container's
# word that they are there, beside an MP4's sample entry, and FFmpeg's MP4
# muxer drops it unless asked (ffmpeg.build_ffmpeg_command, issue #62).
DOVI_RECORD = "DOVI configuration record"


# The HEVC sample entries Dolby Vision defines for an MP4: dvh1, like hvc1,
# with the parameter sets in the sample entry, and dvhe, like hev1, where
# they may be in the stream instead.
DOVI_SAMPLE_ENTRIES = frozenset({"dvh1", "dvhe"})


class DolbyVision(NamedTuple):
    """A video stream's Dolby Vision profile, from its record (None when it
    has none), and the codec tag it has where it is now, as ffprobe shows
    it ("dvh1", "hev1", "[0][0][0][0]" for a Matroska's)."""
    profile: int | None
    codec_tag: str | None


def dolby_vision(probe_data: dict | None) -> dict[int, DolbyVision]:
    """
    Each video stream the file says is Dolby Vision, by its stream index:
    with a configuration record, or a sample entry that says so without
    one. The tag can outlast the record: a copy into an MP4 that does not
    ask for the record keeps one and drops the other, as AC3 Forge did
    before it asked, and FFmpeg will not copy such a stream back into
    Matroska as it is (ffmpeg.build_restore_command).
    """
    found = {}
    for stream in (probe_data or {}).get("streams") or []:
        if stream.get("codec_type") != "video":
            continue
        record = next((d for d in stream.get("side_data_list") or []
                       if d.get("side_data_type") == DOVI_RECORD), None)
        tag = stream.get("codec_tag_string")
        if record is not None or tag in DOVI_SAMPLE_ENTRIES:
            found[stream["index"]] = DolbyVision(
                record.get("dv_profile") if record is not None else None, tag)
    return found


# The sample entries an MP4 or QuickTime chapter track carries: QuickTime
# text, which FFmpeg's muxer writes, and 3GPP timed text.
CHAPTER_TRACK_TAGS = frozenset({"text", "tx3g"})


def chapter_track_indices(probe_data: dict | None) -> list[int]:
    """
    The streams that are an MP4's or QuickTime file's chapter track.

    The chapters themselves are read from that track, and FFmpeg shows the
    track as well, as a data stream of codec bin_data (mov_read_chapters
    in libavformat/mov.c, FFmpeg 8.1.3, which makes any text chapter track
    one). A command copying every stream copied it, as bin_data under the
    only tag the muxer has for that codec, "gpmd" (GoPro metadata), while
    the muxer wrote the chapters as a fresh chapter track of its own
    (mov_create_chapter_track in movenc.c). So each AC3 Forge run and each
    revert on an MP4 with chapters added one junk data track. Commands
    writing an MP4 or a QuickTime file leave these streams out; chapters
    are written from the chapter list, which -map_chapters copies by
    default.

    Only in a file with chapters, and only a bin_data stream with a text
    sample entry. A real GoPro track is bin_data too, tagged "gpmd", and so
    are the junk tracks those runs left; neither is a chapter track, and
    both are copied as they are.
    """
    if not (probe_data or {}).get("chapters"):
        return []
    return [stream["index"] for stream in probe_data.get("streams", [])
            if stream.get("codec_type") == "data"
            and stream.get("codec_name") == "bin_data"
            and stream.get("codec_tag_string") in CHAPTER_TRACK_TAGS]


def timecode_track_indices(probe_data: dict | None) -> list[int]:
    """
    The streams that are a timecode track (tmcd) the muxer writes again
    itself, so a command writing an MP4 leaves them out with the chapter
    track (rebuilt_track_indices).

    FFmpeg's MP4 muxer cannot copy a tmcd stream at all: it takes only the
    codecs in its MP4 tag table, which has none for one, and fails at
    header write ("Could not find tag for codec none"; mov_init in
    libavformat/movenc.c, FFmpeg 8.1.3). So every revert of an MP4 with a
    timecode track failed, as did every AC3 Forge add and undo on one, and
    many cameras write one (GoPro, for one). A job never maps it and still
    keeps it: the MP4 and QuickTime muxers write one tmcd track for each
    video stream with a timecode (mov_init again), and FFmpeg's demuxer
    gives the video the timecode of the tmcd track that belongs to it, or
    the file the timecode of one no video claims (mov_read_header,
    export_orphan_timecode, libavformat/mov.c). The track written again
    holds the same sample (checked at 24 and at 29.97 frames a second).

    It goes after every other track, as the muxer adds it last: in a file
    where it was not last, a GoPro file's for one, it moves. So not for a
    QuickTime file, which is written back as one (is_quicktime): FFmpeg's
    QuickTime muxer copies a tmcd track where it stands. And only with a
    video stream to give it the timecode: in a file without one it is not
    written again, so it is copied, which the MP4 muxer refuses, as before.
    """
    streams = (probe_data or {}).get("streams", [])
    if is_quicktime(probe_data):
        return []
    if not any(s.get("codec_type") == "video" for s in streams):
        return []
    return [s["index"] for s in streams
            if s.get("codec_type") == "data" and s.get("codec_tag_string") == "tmcd"]


# The data tracks FFmpeg's MP4 muxer writes properly, by sample entry. It
# takes bin_data as "gpmd" (codec_mp4_tags), but writes a data track's
# sample description only for "tmcd", "gpmd" and its own RTP hint tracks
# (mov_write_stsd_tag, libavformat/movenc.c n8.1.3); any other, an
# iPhone's "mebx" for one, comes out a track whose description announces
# an entry and holds none. It refuses a copied "tmcd" stream, and writes
# its own (timecode_track_indices). A job keeps these
# (ffmpeg.find_data_streams); a file holding any other is taken for a
# phone's or camera's own recording and left alone (recording_signs), as
# is every QuickTime movie.
MP4_DATA_ENTRIES = frozenset({"gpmd"})


# What marks an MP4 or QuickTime file as a phone's or camera's own
# recording (recording_signs). Each is from a real recording's probe
# (tests/real_recordings/probes.json): the brands from iPhones' QuickTime
# movies, a Sony camera ("XAVC"), a file of Sony's "MSNV" brand, a Nokia
# phone and a 3GPP video; the tags from iPhones, Android phones and GoPro
# cameras, but for "make" and "model", which no sample has: they are the
# names FFmpeg gives the maker's atoms, as "firmware" is its name for
# GoPro's and "location" for the GPS position, which it also gives per
# language, as "location-eng" (mov_read_udta_string, mov_metadata_loci,
# libavformat/mov.c, FFmpeg 8.1.3).
RECORDING_BRANDS = frozenset({QUICKTIME_BRAND, "XAVC", "MSNV", "nvr1"})
RECORDING_BRAND_PREFIX = "3g"
DEVICE_TAGS = frozenset({"make", "model", "firmware"})
DEVICE_TAG_PREFIXES = ("com.apple.", "com.android.")
LOCATION_TAGS = frozenset({"location", "com.apple.quicktime.location.ISO6709"})
LOCATION_TAG_PREFIX = "location-"
UNMARKED_DATA_TRACKS = frozenset({"tmcd", "gpmd"})


def recording_signs(probe_data: dict | None) -> list[str]:
    """
    What marks an MP4 or QuickTime file as a phone's or camera's own
    recording, in words for the user, in this order: "data tracks", "GPS
    location", "device tags", "camera brand". Empty for a file with none,
    and for any other container.

    Remuxarr is for downloaded media, the user's call (PARKED 45), and a
    file with any of these is left as it is whatever the settings say
    (decision.analyze_file; AC3 Forge too, forge.load_forge_job_data). A job
    would cost a recording what a download does not have: FFmpeg's MP4
    muxer writes back neither an iPhone's metadata tracks nor a phone's
    device tags (a fast-start rewrite of an Android phone's video lost its
    "com.android.version"), and the brand is rewritten. No revert point
    could hold a data track either, as a Matroska sidecar has none.

      - A data track that is not the chapter track, a timecode track
        ("tmcd") or a "gpmd" track: an iPhone's "mebx", a GoPro's "fdsc",
        a Sony camera's "rtmd". The three left out are no sign of a
        camera. The muxer writes any MP4's chapters as a track, and FFmpeg
        writes a timecode track for any file it is given a timecode for.
        And "gpmd", GoPro's telemetry, is also what released versions of
        AC3 Forge and of revert left behind in a download: each run on an
        MP4 with chapters copied the chapter track in as one more "gpmd"
        track (chapter_track_indices). Counting it would leave those
        downloads alone for good, a Forge undo included. A GoPro file is
        known by its "fdsc" track and "firmware" tag, which all five real
        ones have, and a job keeps a "gpmd" track where it does run
        (MP4_DATA_ENTRIES).
      - A GPS tag (LOCATION_TAGS).
      - A device tag (DEVICE_TAGS, DEVICE_TAG_PREFIXES). An iTunes-style
        download's own tags are not among them: FFmpeg shows
        "com.apple.iTunes" tags by their short names, "iTunEXTC" for one
        (checked on a file tagged that way).
      - A camera's brand (RECORDING_BRANDS), which is all that marked the
        "MSNV" file. A QuickTime movie counts, an editor's export among
        them: every one is left alone, the user's call.

    Not every recording has one. An editor's MP4 export and a video a
    messaging app has rewritten carry none of this, look like any
    download, and are processed like one.
    """
    fmt = (probe_data or {}).get("format") or {}
    if "mov" not in fmt.get("format_name", "").split(","):
        return []
    tags = fmt.get("tags") or {}
    chapter_tracks = set(chapter_track_indices(probe_data))
    brand = tags.get("major_brand") or ""
    found = {
        "data tracks": any(
            s.get("codec_type") == "data" and s["index"] not in chapter_tracks
            and s.get("codec_tag_string") not in UNMARKED_DATA_TRACKS
            for s in probe_data.get("streams", [])),
        "GPS location": any(
            key in LOCATION_TAGS or key.startswith(LOCATION_TAG_PREFIX) for key in tags),
        "device tags": any(
            key in DEVICE_TAGS or key.startswith(DEVICE_TAG_PREFIXES) for key in tags),
        "camera brand": brand in RECORDING_BRANDS or brand.startswith(RECORDING_BRAND_PREFIX),
    }
    return [sign for sign, present in found.items() if present]


def rebuilt_track_indices(probe_data: dict | None) -> list[int]:
    """
    The streams a command writing an MP4 or a QuickTime file leaves out
    because the muxer writes them itself: the chapter track, and an MP4's
    timecode track.
    """
    return sorted(chapter_track_indices(probe_data) + timecode_track_indices(probe_data))


def extract_format_info(probe_data: dict) -> dict:
    """Extract file-level format metadata.

    Container, duration, and the embedded font count — confirmed directly,
    at every call site across the codebase, that bit_rate and size were
    never actually read from this dict once computed (previously computed
    anyway, on every scan, for values nothing consumed).

    font_attachments is here rather than in extract_tracks because it is a
    property of the file, not of a track, and because extract_tracks
    cannot carry it — see count_font_attachments. recording_signs too,
    joined by commas, "" for none (recording_signs).
    """
    fmt = probe_data.get("format", {})
    format_names = fmt.get("format_name", "").split(",")
    return {
        "container":        _normalise_container(format_names),
        "duration":         _float_or_none(fmt.get("duration")),
        "font_attachments": count_font_attachments(probe_data),
        "recording_signs":  ",".join(recording_signs(probe_data)),
    }


def is_media_file(path: str) -> bool:
    return Path(path).suffix.lower() in MEDIA_EXTENSIONS


# The signature a DivX 5 or Xvid encoder writes into an MPEG-4 Part 2
# stream's user data, ending in "p" when its B-frames are packed. FFmpeg's
# own test (decode_user_data, libavcodec/mpeg4videodec.c, FFmpeg 8.1.3):
# sscanf of "DivX%dBuild%d%c", then "DivX%db%d%c", packed when the %c read
# is "p". A full digit run followed by "p" here is the same: a "p" cannot
# follow a shorter run, as the next byte would be a digit.
_PACKED_DIVX = re.compile(rb"\x00\x00\x01\xb2DivX\d+(?:Build|b)\d+p")


def has_packed_divx_signature(data: bytes) -> bool:
    """Whether MPEG-4 Part 2 bytes carry a packed B-frame signature, such
    as "DivX503b1393p". "DivX500Build413" is a DivX 5 stream without."""
    return _PACKED_DIVX.search(data) is not None


def is_packed_divx(path: str, ffmpeg_bin: str | None = None) -> bool | None:
    """
    Whether the file's first video stream is MPEG-4 Part 2 with packed
    B-frames, the way many DivX 5 and Xvid encoders stored them in AVI: a
    B-frame carried inside the packet of the frame before it.

    Such a file is not converted to MP4 (decision.analyze_file). Copied
    into one, its frames came out timed in storage order, as generating
    presentation times on input (ffmpeg.build_ffmpeg_command) cannot work
    them out from packed frames; only unpacking it in a pass of its own
    first could, which would change the video's bytes and so lose the
    exact revert.

    Reads the first video packet with the stream's extradata joined to it
    (dump_extra), where the encoder's signature sits. None when it cannot
    be read. With no binary given it runs the one REMUXARR_FFMPEG_PATH
    names, as probe_file does REMUXARR_FFPROBE_PATH.
    """
    if ffmpeg_bin is None:
        ffmpeg_bin = app_settings.FFMPEG_PATH
    cmd = [ffmpeg_bin, "-v", "error", "-i", path, "-map", "0:v:0", "-frames:v", "1",
           "-c", "copy", "-bsf:v", "dump_extra=freq=all", "-f", "data", "-"]
    try:
        result = subprocess.run(cmd, capture_output=True, timeout=60)
    except (subprocess.TimeoutExpired, OSError) as exc:
        logger.warning("Could not read the video of %s for packed B-frames: %s", path, exc)
        return None
    if result.returncode != 0:
        logger.warning("Could not read the video of %s for packed B-frames: %s", path,
                       result.stderr.decode(errors="replace").strip()[:200])
        return None
    return has_packed_divx_signature(result.stdout)


def is_faststart_mp4(path: str, max_boxes: int = 64) -> bool | None:
    """
    Determine whether an MP4/MOV file has the 'moov' atom before 'mdat'
    (i.e. is web-optimised / fast-start ready) by walking its top-level
    box headers — no decoding, no ffprobe, just a few bytes at the start.

    Returns
    -------
    True   moov precedes mdat → fast-start IS present
    False  mdat precedes moov → fast-start is NOT present
    None   could not determine (truncated file, non-MP4 container, I/O
           error, or max_boxes exceeded without finding either atom)
    """
    try:
        with open(path, "rb") as fh:
            for _ in range(max_boxes):
                header = fh.read(8)
                if len(header) < 8:
                    return None

                size   = int.from_bytes(header[:4], "big")
                fourcc = header[4:8]

                if fourcc == b"moov":
                    return True
                if fourcc == b"mdat":
                    return False

                if size == 0:
                    return None
                if size == 1:
                    largesize_bytes = fh.read(8)
                    if len(largesize_bytes) < 8:
                        return None
                    size = int.from_bytes(largesize_bytes, "big")
                    remaining = size - 16
                else:
                    remaining = size - 8

                if remaining < 0:
                    return None
                if remaining > 0:
                    fh.seek(remaining, 1)

        return None
    except OSError:
        return None


# ── Helpers ────────────────────────────────────────────────────────────────────

def _normalise_container(format_names: list[str]) -> str:
    name = ",".join(format_names).lower()
    # ⚠ "matroska" MUST be checked before "webm", and the "webm" branch is
    # deliberately unreachable. ffprobe reports format_name="matroska,webm"
    # for EVERY Matroska-family file — both real .mkv AND real .webm —
    # because it's a property of the shared demuxer, not the individual
    # file. So `name` contains "webm" for ordinary MKVs too, and checking
    # "webm" first mislabels every MKV as a webm container. That routes
    # normal H.264/HEVC/AC3/DTS/PGS MKVs to `-f webm` output, which FFmpeg
    # rejects ("Only VP8/VP9/AV1 video and Vorbis/Opus audio and WebVTT
    # subtitles are supported for WebM"), breaking every remux — a real
    # regression that shipped once. format_name genuinely CANNOT
    # distinguish mkv from webm (only the file extension or the EBML
    # DocType can), so both are treated as "mkv": `-f matroska` muxes
    # both correctly, and a real .webm keeps playing. Do not "fix" the
    # dead webm branch by moving it up.
    if "matroska" in name: return "mkv"
    if "mp4"      in name: return "mp4"
    if "mov"      in name: return "mp4"
    if "avi"      in name: return "avi"
    if "mpegts"   in name: return "ts"
    if "mpeg"     in name: return "ts"
    if "wmv"      in name: return "wmv"
    if "asf"      in name: return "wmv"
    if "webm"     in name: return "webm"   # unreachable (see above); kept for intent
    return format_names[0].strip() if format_names else "unknown"


def _float_or_none(val) -> float | None:
    try:
        return float(val)
    except (TypeError, ValueError):
        return None
