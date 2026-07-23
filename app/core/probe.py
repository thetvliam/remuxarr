"""
ffprobe wrapper.

probe_file()          → raw dict from ffprobe JSON output
extract_tracks()      → normalised list of track dicts
extract_format_info() → container / duration / size
is_faststart_mp4()    → True/False/None (box-level MP4 atom check)
"""
import json
import logging
import re
import subprocess
from pathlib import Path

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

def probe_file(path: str, ffprobe_bin: str = "ffprobe") -> dict:
    """
    Run ffprobe and return the raw parsed JSON.
    Raises ProbeError on any failure.
    """
    cmd = [
        ffprobe_bin,
        "-v", "quiet",
        "-print_format", "json",
        "-show_format",
        "-show_streams",
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
        # codec_type="video" with the "attached_pic" disposition flag.
        # Mapping them alongside the real video stream into MP4 causes the
        # muxer to fail ("dimensions not set"). Skip them entirely.
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
        })

    return tracks


def extract_format_info(probe_data: dict) -> dict:
    """Extract file-level format metadata.

    Only container and duration — confirmed directly, at every call site
    across the codebase, that bit_rate and size were never actually read
    from this dict once computed (previously computed anyway, on every
    scan, for values nothing consumed).
    """
    fmt = probe_data.get("format", {})
    format_names = fmt.get("format_name", "").split(",")
    return {
        "container": _normalise_container(format_names),
        "duration":  _float_or_none(fmt.get("duration")),
    }


def is_media_file(path: str) -> bool:
    return Path(path).suffix.lower() in MEDIA_EXTENSIONS


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
