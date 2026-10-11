"""
Which files Remuxarr leaves alone.

Three settings decide it. skip_extras (on by default) covers trailers and
other extras named the way Plex or Jellyfin expect, and Plex's optimised
versions. exclude_patterns is the user's own list. A file either one matches
is not probed or queued by a scan, a webhook or the single-file scan
endpoint, and a record already held for it is removed at the end of the next
completed scan (scanner.remove_excluded_files). include_patterns (Always
Include) overrides both: a file it matches is never excluded, so one file
the extras rule caught can be brought back without renaming it or turning
that rule off for the whole library.

The extras conventions
----------------------
Taken from Plex's "Local Files for Trailers and Extras" and Jellyfin's
movies page, folded together and matched without regard to case:

  • A name ending, before the extension, in one of EXTRAS_SUFFIXES, or a
    name that is exactly "trailer" or "sample".
  • A file directly inside a folder named in EXTRAS_FOLDERS, when that
    folder really is an extras folder (_is_extras_folder).
  • Anything below a folder named "Plex Versions", where Plex's Optimize
    writes its copies beside the original.

Jellyfin also accepts " trailer" and " sample" with a space. Those are left
out on purpose: an episode named "... - The Trailer" or "... - Free Sample"
ends the same way, and skipping a real episode leaves no sign anywhere,
which is worse than processing one trailer.

Why a folder's name is not enough
---------------------------------
Folder names like Extras, Shorts and Other are also titles. Sonarr's
default series folder has no year, so the sitcom Extras lives at
/tv/Extras/Season 01/, and a flat collection of short films can live at
/movies/Shorts/. Both would be skipped whole by a name test. A folder only
counts when it holds no subfolder with a video directly in it (a show
folder holds seasons) and the folder above it holds a video file or a
season folder (it is inside a movie or a show). A collection folder sits
in the library root, beside other folders, so the second test fails for it.

Patterns
--------
Without a "/", a pattern is tested against each folder and file name below
the library path: "Plex Versions", "Anime", "*-sample.mkv". With a "/", it
is a path from the library path, and excludes that file or that folder and
everything in it: "Movies/4K", "/Anime". Only * (anything, including "/")
and ? (one character) are wildcards; brackets are literal, since names like
"Show [tvdbid-1234]" are common and would otherwise be read as a character
class. Matching ignores case.

Always Include entries are written the same way, with one difference: an
entry ending in a media extension also matches that name with any other
media extension (see _compile).

The library path's own folders are never tested, or "Movies" would exclude
a whole library mounted at /media/Movies. A path under no library path (a
webhook on an install with none configured) is tested on all its folders
by name patterns and the extras rule; path patterns cannot apply to it.
"""
import os
import re

from app.core.probe import MEDIA_EXTENSIONS, is_media_file

EXTRAS_SUFFIXES = (
    # Plex
    "-behindthescenes", "-deleted", "-featurette", "-interview",
    "-scene", "-short", "-trailer", "-other",
    # Jellyfin, beyond Plex's
    "-deletedscene", "-clip", "-extra", "-sample",
    ".trailer", "_trailer", ".sample", "_sample",
)
EXTRAS_NAMES = {"trailer", "sample"}
EXTRAS_FOLDERS = {
    # Plex
    "behind the scenes", "deleted scenes", "featurettes", "interviews",
    "scenes", "shorts", "trailers", "other",
    # Jellyfin, beyond Plex's
    "clips", "samples", "extras", "theme-music", "backdrops",
}
ANYWHERE_FOLDERS = {"plex versions"}

_SEASON_FOLDER = re.compile(r"^(season[ ._-]*\d+|specials|s\d+)$", re.IGNORECASE)


class ExcludeRules:
    def __init__(self, skip_extras: bool, patterns, roots, fallback_roots=(),
                 includes=()):
        self.skip_extras = bool(skip_extras)
        self.patterns = [_compile(p) for p in (patterns or []) if p and p.strip()]
        self.includes = [_compile(p, any_media_extension=True)
                         for p in (includes or []) if p and p.strip()]
        self.roots = [os.path.normpath(r) for r in roots if r]
        # Paths a scan was asked for, used only for a file under none of the
        # configured library paths: a pattern is a path from the configured
        # library path even when a subfolder of it is what is being scanned.
        self.fallback_roots = [os.path.normpath(r) for r in fallback_roots if r]

    @classmethod
    def from_settings(cls, cfg: dict, scanned_paths=()):
        return cls(cfg.get("skip_extras", True), cfg.get("exclude_patterns"),
                   cfg.get("scan_paths") or [], scanned_paths,
                   includes=cfg.get("include_patterns"))

    def reason(self, path: str) -> str | None:
        """Why path is excluded, or None."""
        path = os.path.normpath(path)
        rel = _relative(path, self.roots)
        if rel is None:
            rel = _relative(path, self.fallback_roots)
        if rel is not None:
            parts = rel.split(os.sep)
        else:
            parts = [p for p in path.split(os.sep) if p]

        if any(_matches(pattern, parts, rel) for pattern in self.includes):
            return None

        if self.skip_extras:
            why = _extras_reason(path, parts)
            if why:
                return why

        for pattern in self.patterns:
            if _matches(pattern, parts, rel):
                return f"exclude pattern {pattern[0]!r}"
        return None

    def excludes(self, path: str) -> bool:
        return self.reason(path) is not None


def _relative(path: str, roots) -> str | None:
    # Longest first, so a path under two library paths is placed under the
    # deeper one.
    for root in sorted(roots, key=len, reverse=True):
        if path.startswith(root.rstrip(os.sep) + os.sep):
            return path[len(root.rstrip(os.sep)) + 1:]
    return None


def _compile(pattern: str, any_media_extension: bool = False):
    raw = pattern.strip()
    text = raw.lower()
    kind = "path" if "/" in text else "name"
    if kind == "path":
        text = text.strip("/")
    ending = r"\Z"
    if any_media_extension and is_media_file(text):
        # An Always Include entry naming a file has to survive Remuxarr
        # changing that file's container, or the converted file is excluded
        # again at the next scan and its record removed. So "Film.mkv"
        # stands for that name with any media extension. Only the extension
        # is open: "Film.trailer.mkv" and "Film 2.mkv" are other names.
        text = os.path.splitext(text)[0]
        ending = "(?:" + "|".join(re.escape(e) for e in sorted(MEDIA_EXTENSIONS)) + r")\Z"
    regex = "".join(
        ".*" if ch == "*" else "." if ch == "?" else re.escape(ch)
        for ch in text
    )
    return raw, kind, re.compile(regex + ending, re.DOTALL)


def _matches(pattern, parts: list[str], rel: str | None) -> bool:
    """Whether a compiled pattern matches the file whose parts are given."""
    _raw, kind, regex = pattern
    if kind == "path":
        if rel is None:
            return False
        target = "/".join(parts).lower()
        return bool(regex.match(target)) or any(
            regex.match("/".join(parts[:n]).lower())
            for n in range(1, len(parts)))
    return any(regex.match(part.lower()) for part in parts)


def _extras_reason(path: str, parts: list[str]) -> str | None:
    stem = os.path.splitext(parts[-1])[0].lower()
    if stem in EXTRAS_NAMES or stem.endswith(EXTRAS_SUFFIXES):
        return "named as a trailer or extra"
    folders = [p.lower() for p in parts[:-1]]
    if ANYWHERE_FOLDERS.intersection(folders):
        return "inside a Plex Versions folder"
    if folders and folders[-1] in EXTRAS_FOLDERS and _is_extras_folder(
            os.path.dirname(path)):
        return f"inside an extras folder ({parts[-2]})"
    return None


def _is_extras_folder(folder: str) -> bool:
    """See "Why a folder's name is not enough" above."""
    # Each scandir in a with block: one left behind by an early return stays
    # open until it is garbage collected, a directory handle per file checked.
    try:
        with os.scandir(folder) as entries:
            for entry in entries:
                if entry.is_dir() and _holds_video(entry.path):
                    return False
        with os.scandir(os.path.dirname(folder)) as entries:
            for entry in entries:
                if entry.is_dir() and _SEASON_FOLDER.match(entry.name):
                    return True
                if entry.is_file() and is_media_file(entry.name):
                    return True
    except OSError:
        pass
    return False


def _holds_video(folder: str) -> bool:
    try:
        with os.scandir(folder) as entries:
            return any(e.is_file() and is_media_file(e.name) for e in entries)
    except OSError:
        return False
