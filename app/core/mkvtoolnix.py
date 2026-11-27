"""
MKVToolNix for the Matroska jobs FFmpeg would strip.

A job that keeps a file Matroska is a stream copy through FFmpeg, and
FFmpeg's Matroska muxer writes back less than the file held. Measured with
FFmpeg n8.1.3, a remux that removed one track lost:

  • every track's BCP 47 language: pt-BR, es-419, zh-Hans and en-GB came
    back as por, spa, chi and eng, so two subtitles told apart only by their
    region (es-419 and es-ES, a real web release's) became indistinguishable;
  • every chapter edition but the first, every nested chapter, every name of
    a chapter but one (the last, or the TITLE of a tag aimed at the chapter,
    which it shows as the chapter's name), and every chapter's language;
  • every tag at a level other than the film's (a collection's TITLE at 70)
    and every tag aimed at a chapter.

mkvmerge 104.0 kept all of it. It is not used for every Matroska job,
because it also changes what FFmpeg leaves alone, measured over the
generated test library and files built for these cases:

  • it writes back its own reading of a track's old language code: en as
    eng, fra as fre, deu as ger, undefined as und;
  • it replaces bytes that are not UTF-8 in a text subtitle with U+FFFD,
    destroying a Latin-1 subtitle's accented letters, and exits 1 with a
    warning when it does;
  • it stamps the time it ran as the file's date unless told otherwise, and
    mkvpropedit, the obvious way to put the date back, drops its fraction;
  • it copies an HEVC stream's parameter sets into its keyframes (the
    decoded frames are bit for bit the same), adds a BCP 47 tag beside every
    old code, and gives chapters new UIDs.

So a file goes to mkvmerge only when it holds something FFmpeg would drop
that the job keeps (benefits) and none of the measured changes can reach it
(blockers). A chapter's language alone does not count: it matters only
where a chapter has names in several languages to choose between, which
benefits counts on its own.

This module decides and builds the command; ffmpeg.plan_mkvmerge and
_run_mkvmerge put it in a job's path, with FFmpeg behind it.
"""
from __future__ import annotations

import json
import logging
import os
import re
import shutil
import struct
import subprocess
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from typing import Sequence

from app.config import settings as app_settings

logger = logging.getLogger(__name__)

# mkvmerge prints nothing for --help under a POSIX locale (measured with
# 104.0); muxing worked there, but its messages are what a failed job
# shows, so every run gets a UTF-8 locale.
_ENV = {**os.environ, "LC_ALL": "C.UTF-8"}

# Reading a file's header, chapters and tags reads no packets; a minute is
# far beyond any real file and only stops a hung binary holding a job.
_READ_TIMEOUT = 60


@dataclass(frozen=True)
class MkvTrack:
    id: int
    type: str                 # "video", "audio" or "subtitles", as mkvmerge names them
    language: str | None      # the old code, as mkvmerge would write it back
    language_ietf: str | None


@dataclass(frozen=True)
class ChapterAtom:
    names: tuple[tuple[str, str | None, str | None], ...]   # (name, old code, BCP 47)
    hidden: bool
    enabled: bool
    linked: bool              # plays a part of another file (ChapterSegmentUID)
    children: tuple["ChapterAtom", ...]


@dataclass(frozen=True)
class Edition:
    ordered: bool
    hidden: bool
    atoms: tuple[ChapterAtom, ...]


@dataclass(frozen=True)
class Tag:
    level: str                # TargetTypeValue, "50" when the tag names none
    aims: frozenset[str]      # what its targets name: "track", "chapter", "edition", "attachment"
    name: str
    nested: bool


@dataclass(frozen=True)
class MatroskaInfo:
    tracks: tuple[MkvTrack, ...]
    editions: tuple[Edition, ...]
    tags: tuple[Tag, ...]


def available() -> bool:
    """Whether both binaries this needs can be run."""
    return bool(shutil.which(app_settings.MKVMERGE_PATH)
                and shutil.which(app_settings.MKVEXTRACT_PATH))


def _run(cmd: list[str]) -> subprocess.CompletedProcess | None:
    try:
        return subprocess.run(cmd, capture_output=True, text=True, env=_ENV,
                              timeout=_READ_TIMEOUT)
    except (OSError, subprocess.TimeoutExpired) as exc:
        logger.warning("Could not run %s: %s", cmd[0], exc)
        return None


def _xml(path: str, what: str) -> ET.Element | None | bool:
    """mkvextract's chapters or tags as XML: None when the file has none,
    False when they could not be read."""
    r = _run([app_settings.MKVEXTRACT_PATH, path, what, "-"])
    if r is None or r.returncode != 0:
        return False
    if not r.stdout.strip():
        return None
    try:
        return ET.fromstring(r.stdout)
    except ET.ParseError:
        return False


def _atom(element: ET.Element) -> ChapterAtom:
    return ChapterAtom(
        names=tuple((d.findtext("ChapterString") or "", d.findtext("ChapterLanguage"),
                     d.findtext("ChapLanguageIETF"))
                    for d in element.findall("ChapterDisplay")),
        hidden=element.findtext("ChapterFlagHidden") == "1",
        enabled=element.findtext("ChapterFlagEnabled") != "0",
        linked=element.find("ChapterSegmentUID") is not None,
        children=tuple(_atom(a) for a in element.findall("ChapterAtom")),
    )


# Tags every muxer writes for itself.
_STATISTICS = frozenset({"BPS", "DURATION", "NUMBER_OF_FRAMES", "NUMBER_OF_BYTES", "ENCODER"})


def read_matroska(path: str) -> MatroskaInfo | None:
    """
    What mkvmerge and mkvextract read of a Matroska file. None for a file
    that is not Matroska, or whose tracks, chapters or tags could not be
    read: such a file stays with FFmpeg.
    """
    r = _run([app_settings.MKVMERGE_PATH, "-J", path])
    if r is None:
        return None
    try:
        identified = json.loads(r.stdout)
    except ValueError:
        return None
    container = identified.get("container") or {}
    if not container.get("recognized") or container.get("type") != "Matroska":
        return None
    tracks = tuple(
        MkvTrack(id=t["id"], type=t["type"],
                 language=(t.get("properties") or {}).get("language"),
                 language_ietf=(t.get("properties") or {}).get("language_ietf"))
        for t in identified.get("tracks", []))

    chapters, tags = _xml(path, "chapters"), _xml(path, "tags")
    if chapters is False or tags is False:
        return None
    editions = () if chapters is None else tuple(
        Edition(ordered=e.findtext("EditionFlagOrdered") == "1",
                hidden=e.findtext("EditionFlagHidden") == "1",
                atoms=tuple(_atom(a) for a in e.findall("ChapterAtom")))
        for e in chapters.findall("EditionEntry"))
    found = []
    for tag in [] if tags is None else tags.findall("Tag"):
        targets = tag.find("Targets")
        level = (targets.findtext("TargetTypeValue") if targets is not None else None) or "50"
        aims = frozenset(child.tag[:-3].lower() for child in (targets if targets is not None else [])
                         if child.tag.endswith("UID"))
        for simple in tag.findall("Simple"):
            name = simple.findtext("Name") or ""
            if name in _STATISTICS or name.startswith("_STATISTICS"):
                continue
            found.append(Tag(level=level, aims=aims, name=name,
                             nested=simple.find("Simple") is not None))
    return MatroskaInfo(tracks=tracks, editions=editions, tags=tuple(found))


# ── Which tracks a job keeps ─────────────────────────────────────────────────

def kept_track_ids(decision, all_tracks: Sequence[dict]) -> list[int]:
    """
    The input tracks a job's output holds, in the order build_ffmpeg_command
    maps them: every video track, then the audio and the subtitles it
    neither drops nor extracts to a sidecar.
    """
    gone = {a.stream_index for a in decision.actions
            if a.action_type in ("drop_track", "extract_subtitle")}
    order = []
    for kind in ("video", "audio", "subtitle"):
        order += [t["stream_index"] for t in all_tracks
                  if t["track_type"] == kind and (kind == "video" or t["stream_index"] not in gone)]
    return order


def retagged_languages(decision) -> dict[int, str]:
    """The tracks a job gives a language (the Fix pass's target), by index."""
    return {a.stream_index: a.target_language for a in decision.actions
            if getattr(a, "target_language", None)}


# ── Whether mkvmerge would keep more ─────────────────────────────────────────

def _atoms(atoms: Sequence[ChapterAtom], depth: int = 1):
    for a in atoms:
        yield a, depth
        yield from _atoms(a.children, depth + 1)


def benefits(info: MatroskaInfo, kept: Sequence[int], retagged: dict[int, str]) -> list[str]:
    """
    What this job's output would hold through mkvmerge and lose through
    FFmpeg, as reasons for the job log; empty when FFmpeg loses nothing that
    matters. A track's region counts only on a track the job keeps with its
    own language: a dropped one leaves, and a retagged one gets the job's.
    """
    reasons = []
    regional = [f"{t.language_ietf} (track {t.id})" for t in info.tracks
                if t.id in kept and t.id not in retagged
                and t.language_ietf and "-" in t.language_ietf]
    if regional:
        reasons.append("regional languages " + ", ".join(regional))
    if len(info.editions) > 1:
        reasons.append(f"{len(info.editions)} chapter editions")
    every = [(a, d) for e in info.editions for a, d in _atoms(e.atoms)]
    if any(d > 1 for _, d in every):
        reasons.append("nested chapters")
    if any(len(a.names) > 1 for a, _ in every):
        reasons.append("chapters named in several languages")
    if (any(e.ordered or e.hidden for e in info.editions)
            or any(a.hidden or not a.enabled or a.linked for a, _ in every)):
        reasons.append("ordered, hidden or linked chapters")
    if any(t.level != "50" or t.aims - {"track"} or t.nested for t in info.tags):
        reasons.append("tags at other levels or aimed at chapters")
    return reasons


# ── What would stop it ───────────────────────────────────────────────────────

def blockers(info: MatroskaInfo, probe_data: dict, kept: Sequence[int],
             retagged: dict[int, str], input_path: str) -> list[str]:
    """
    Why this job must stay with FFmpeg even though mkvmerge would keep
    more: an output it would write differently in a way the job did not ask
    for. Empty when nothing stops it.

    probe_data is ffprobe's own output for the file, not probe.extract_tracks':
    that normalises a track's language, and what mkvmerge changes is the tag
    as written.
    """
    reasons = []
    # A WebM written as Matroska. FFmpeg writes it as Matroska too (its
    # container normalises to mkv, ffmpeg._CONTAINER_FORMAT), so mkvmerge
    # would change nothing there, but its WebM mode refuses what a WebM
    # cannot hold, and no file here has been measured through it.
    if input_path.lower().endswith(".webm"):
        reasons.append("a WebM file")
    # The job names tracks by ffprobe's index and mkvmerge by its own id.
    # They matched for every Matroska file measured; a file where they do
    # not would have the wrong tracks kept.
    streams = [(s.get("index"), s.get("codec_type")) for s in probe_data.get("streams", [])
               if s.get("codec_type") in ("video", "audio", "subtitle")
               and not (s.get("disposition") or {}).get("attached_pic")]
    names = {"video": "video", "audio": "audio", "subtitles": "subtitle"}
    if [(t.id, names.get(t.type)) for t in info.tracks] != streams:
        # Nothing below can pair a track with its ffprobe stream.
        return reasons + ["mkvmerge numbers its tracks differently from ffprobe"]
    tags_by_index = {s.get("index"): (s.get("tags") or {}) for s in probe_data.get("streams", [])}
    rewritten = []
    for t in info.tracks:
        if t.id not in kept or t.id in retagged:
            continue
        written = tags_by_index.get(t.id, {}).get("language") or "und"
        if written != (t.language or "und"):
            rewritten.append(f"{written} (track {t.id}) as {t.language or 'und'}")
    if rewritten:
        reasons.append("mkvmerge would write back " + ", ".join(rewritten))
    return reasons


# ── The command ──────────────────────────────────────────────────────────────

def build_mkvmerge_command(input_path: str, output_path: str, decision,
                           all_tracks: Sequence[dict], keep_date: bool) -> list[str]:
    """
    The mkvmerge argv for a job's Matroska output, keeping what
    build_ffmpeg_command keeps: every video track, the audio and subtitles
    the job neither drops nor extracts, every attachment (fonts and cover
    art alike, which mkvmerge copies unasked), and the file's chapters and
    tags. Tracks come out in build_ffmpeg_command's order, so a revert
    point's capture finds them where an FFmpeg run would have put them.

    keep_date says the source has a date of its own. mkvmerge then writes
    the time it ran, and write_date_utc puts the source's back; without one
    it writes none, as FFmpeg writes none.

    --gui-mode prints progress as "#GUI#progress 45%" (parse_gui_progress).
    """
    kept = kept_track_ids(decision, all_tracks)
    kind_of = {t["stream_index"]: t["track_type"] for t in all_tracks}
    by_type = {kind: [i for i in kept if kind_of[i] == kind] for kind in ("audio", "subtitle")}
    cmd = [app_settings.MKVMERGE_PATH, "--gui-mode", "--output", output_path]
    cmd += (["--audio-tracks", ",".join(map(str, by_type["audio"]))] if by_type["audio"]
            else ["--no-audio"])
    cmd += (["--subtitle-tracks", ",".join(map(str, by_type["subtitle"]))] if by_type["subtitle"]
            else ["--no-subtitles"])
    for index, language in sorted(retagged_languages(decision).items()):
        if index in kept:
            cmd += ["--language", f"{index}:{language}"]
    if kept:
        cmd += ["--track-order", ",".join(f"0:{i}" for i in kept)]
    if not keep_date:
        cmd.append("--no-date")
    cmd.append(input_path)
    return cmd


_GUI_PROGRESS = re.compile(r"^#GUI#progress (\d+(?:\.\d+)?)%$")


def parse_gui_progress(line: str) -> float | None:
    """A percentage from one line of mkvmerge --gui-mode output, or None."""
    m = _GUI_PROGRESS.match(line.strip())
    return float(m.group(1)) if m else None


# ── The date the file says it was made ───────────────────────────────────────
#
# Matroska keeps it as DateUTC in the segment's Info: nanoseconds since
# 2001-01-01, an 8-byte signed integer. ffprobe shows it as creation_time to
# the microsecond, so it is copied from file to file as stored rather than
# through that text. mkvpropedit --set date drops the fraction (measured
# with 104.0: .460353 came back .000000), so it is written here, in place
# of the value mkvmerge wrote, which is the same size.

_EBML_HEADER, _SEGMENT, _INFO, _DATE_UTC = 0x1A45DFA3, 0x18538067, 0x1549A966, 0x4461
# Info follows the SeekHead and any padding at the start of the segment,
# well inside this for every muxer measured.
_HEADER_READ = 1 << 20


class DateUnreadable(Exception):
    """The file's Info, or a DateUTC in it, could not be found or read."""


def _vint(data: bytes, pos: int, keep_marker: bool) -> tuple[int, int, bool]:
    """(value, position after it, whether it is EBML's "unknown size")."""
    if pos >= len(data):
        raise DateUnreadable("the header ends early")
    first = data[pos]
    length = 1
    while length <= 8 and not first & (0x80 >> (length - 1)):
        length += 1
    if length > 8 or pos + length > len(data):
        raise DateUnreadable("not an EBML number")
    value = first if keep_marker else first & (0xFF >> length)
    for b in data[pos + 1:pos + length]:
        value = (value << 8) | b
    unknown = not keep_marker and value == (1 << (7 * length)) - 1
    return value, pos + length, unknown


def _element(data: bytes, pos: int) -> tuple[int, int, int, bool]:
    """(id, start of its data, size, unknown size) of the element at pos."""
    eid, pos, _ = _vint(data, pos, keep_marker=True)
    size, pos, unknown = _vint(data, pos, keep_marker=False)
    return eid, pos, size, unknown


def _date_position(data: bytes) -> int | None:
    """Where DateUTC's eight bytes start, or None for an Info without one."""
    eid, start, size, unknown = _element(data, 0)
    if eid != _EBML_HEADER or unknown:
        raise DateUnreadable("not an EBML file")
    eid, pos, _, _ = _element(data, start + size)
    if eid != _SEGMENT:
        raise DateUnreadable("no segment after the EBML header")
    while pos < len(data):
        eid, start, size, unknown = _element(data, pos)
        if eid == _INFO:
            if unknown:
                raise DateUnreadable("the Info has no size")
            end, at = start + size, start
            while at < end:
                child, cstart, csize, cunknown = _element(data, at)
                if cunknown:
                    raise DateUnreadable("an element of the Info has no size")
                if child == _DATE_UTC:
                    if csize != 8:
                        raise DateUnreadable(f"a DateUTC of {csize} bytes")
                    return cstart
                at = cstart + csize
            return None
        if unknown:
            raise DateUnreadable("an element before the Info has no size")
        pos = start + size
    raise DateUnreadable("no Info in the first megabyte")


def read_date_utc(path: str) -> int | None:
    """The file's DateUTC as stored, or None when it has none."""
    with open(path, "rb") as f:
        data = f.read(_HEADER_READ)
    at = _date_position(data)
    return None if at is None else struct.unpack(">q", data[at:at + 8])[0]


def write_date_utc(path: str, value: int) -> None:
    """
    Overwrite the file's DateUTC with value, in place. The file must
    already have one (mkvmerge writes one unless told --no-date); the
    value is read back before this returns.
    """
    with open(path, "r+b") as f:
        data = f.read(_HEADER_READ)
        at = _date_position(data)
        if at is None:
            raise DateUnreadable("the file has no DateUTC to overwrite")
        f.seek(at)
        f.write(struct.pack(">q", value))
    if read_date_utc(path) != value:
        raise DateUnreadable("the date did not read back")
