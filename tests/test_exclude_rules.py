"""
What the exclude settings match: app/core/exclude.py.

A user asked for a way to keep Remuxarr away from trailers and backdrops.
Until this existed the scanner skipped only hidden folders and non-video
extensions, so a trailer beside a movie was probed and decided on like the
movie itself: queued, converted, or sent to language review.

skip_extras (on by default) covers what Plex and Jellyfin document as
extras, plus Plex's optimised versions. exclude_patterns is the user's own
list, and include_patterns (Always Include) overrides both. These run the
rules against a real folder tree, since whether a folder counts as an
extras folder depends on what is around it on disk.

The conventions are written out here rather than imported from the module.
A name dropped from the module's lists then fails a test, where reading the
same constant back would agree with any edit.

The tests in the "not an extras folder" section are the reason the folder
rule is more than a name check: a sitcom called Extras, a flat folder of
short films, Season 00 and Specials. Each is a library someone has.

Confirmed unprotected before this file was written: see
test_exclude_scan.py, which records the mutation run for the feature as a
whole.
"""
from types import SimpleNamespace

import pytest

from app.core.exclude import ExcludeRules


@pytest.fixture
def lib(tmp_path):
    root = tmp_path / "library"
    root.mkdir()

    def put(*relpaths):
        for rel in relpaths:
            path = root / rel
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(b"\0")
        return str(root / relpaths[-1])

    def rules(skip_extras=True, patterns=(), roots=None, includes=()):
        return ExcludeRules(skip_extras, list(patterns),
                            [str(root)] if roots is None else roots,
                            includes=list(includes))

    return SimpleNamespace(root=root, put=put, rules=rules)


MOVIE = "Avatar (2009)/Avatar (2009).mkv"


# ── Names ────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("ending", [
    # Plex
    "-behindthescenes", "-deleted", "-featurette", "-interview",
    "-scene", "-short", "-trailer", "-other",
    # Jellyfin
    "-deletedscene", "-clip", "-extra", "-sample",
    ".trailer", "_trailer", ".sample", "_sample",
])
def test_a_name_ending_like_an_extra_is_excluded(lib, ending):
    path = lib.put(MOVIE, f"Avatar (2009)/Making Of{ending}.mkv")
    assert lib.rules().excludes(path)


def test_the_ending_ignores_case(lib):
    path = lib.put(MOVIE, "Avatar (2009)/Teaser-TRAILER.mp4")
    assert lib.rules().excludes(path)


@pytest.mark.parametrize("name", ["trailer.mp4", "Sample.mkv"])
def test_a_file_named_only_trailer_or_sample_is_excluded(lib, name):
    path = lib.put(MOVIE, f"Avatar (2009)/{name}")
    assert lib.rules().excludes(path)


def test_the_main_file_is_not_excluded(lib):
    assert not lib.rules().excludes(lib.put(MOVIE))


@pytest.mark.parametrize("name", [
    "Show - S02E05 - The Trailer.mkv",
    "Free Sample (2020)/Free Sample.mkv",
])
def test_an_ending_after_a_space_is_not_taken_for_an_extra(lib, name):
    """
    Jellyfin accepts " trailer" and " sample". An episode title ends the
    same way, and skipping a real episode leaves no sign anywhere.
    """
    assert not lib.rules().excludes(lib.put(name))


# ── Extras folders ───────────────────────────────────────────────────────────

@pytest.mark.parametrize("folder", [
    # Plex
    "Behind The Scenes", "Deleted Scenes", "Featurettes", "Interviews",
    "Scenes", "Shorts", "Trailers", "Other",
    # Jellyfin
    "clips", "samples", "extras", "theme-music", "backdrops",
])
def test_a_file_in_a_movies_extras_folder_is_excluded(lib, folder):
    path = lib.put(MOVIE, f"Avatar (2009)/{folder}/Clip One.mkv")
    assert lib.rules().excludes(path)


def test_the_folder_name_ignores_case(lib):
    path = lib.put(MOVIE, "Avatar (2009)/TRAILERS/Teaser.mkv")
    assert lib.rules().excludes(path)


def test_a_shows_extras_folder_beside_its_seasons_is_excluded(lib):
    path = lib.put("Show/Season 01/Show - S01E01.mkv", "Show/Featurettes/Cast.mkv")
    assert lib.rules().excludes(path)


def test_a_seasons_extras_folder_is_excluded(lib):
    path = lib.put("Show/Season 01/Show - S01E01.mkv",
                   "Show/Season 01/Deleted Scenes/Cut.mkv")
    assert lib.rules().excludes(path)


def test_a_metadata_folder_inside_an_extras_folder_does_not_stop_it(lib):
    """Only a subfolder with a video in it makes it something else."""
    path = lib.put(MOVIE, "Avatar (2009)/Trailers/@eaDir/thumb.jpg",
                   "Avatar (2009)/Trailers/Teaser.mkv")
    assert lib.rules().excludes(path)


def test_plex_versions_are_excluded_however_deep(lib):
    path = lib.put("Dune (2021)/Dune (2021).mkv",
                   "Dune (2021)/Plex Versions/Optimized for TV/Dune (2021).mp4")
    assert lib.rules().excludes(path)


# ── Not an extras folder ─────────────────────────────────────────────────────

def test_a_show_called_extras_is_not_excluded(lib):
    """Sonarr's default series folder has no year: /tv/Extras/Season 01/."""
    path = lib.put("Extras/Season 01/Extras - S01E01.mkv")
    assert not lib.rules().excludes(path)


def test_a_folder_holding_videos_in_subfolders_is_not_an_extras_folder(lib):
    """
    Even inside a movie folder. A folder with seasons of its own is a show,
    whatever it is called, and a video directly in it is part of that show.
    """
    path = lib.put("Kids (2020)/Kids (2020).mkv",
                   "Kids (2020)/Shorts/Season 01/Shorts - S01E01.mkv",
                   "Kids (2020)/Shorts/Shorts - Pilot.mkv")
    assert not lib.rules().excludes(path)


def test_a_flat_collection_of_short_films_is_not_excluded(lib):
    """The folder above it is the library, not a movie or a show."""
    path = lib.put("Avatar (2009)/Avatar (2009).mkv", "Shorts/Some Short (2019).mkv")
    assert not lib.rules().excludes(path)


@pytest.mark.parametrize("season", ["Season 00", "Specials"])
def test_specials_are_not_extras(lib, season):
    path = lib.put("Show/Season 01/Show - S01E01.mkv",
                   f"Show/{season}/Show - S00E01.mkv")
    assert not lib.rules().excludes(path)


def test_the_switch_off_leaves_extras_to_be_processed(lib):
    trailer = lib.put(MOVIE, "Avatar (2009)/Trailers/Teaser.mkv")
    named = lib.put("Avatar (2009)/Teaser-trailer.mkv")
    rules = lib.rules(skip_extras=False)
    assert not rules.excludes(trailer)
    assert not rules.excludes(named)


# ── Patterns ─────────────────────────────────────────────────────────────────

def test_a_name_pattern_matches_a_folder_anywhere(lib):
    rules = lib.rules(patterns=["Anime"])
    assert rules.excludes(lib.put("TV/Anime/Show/Season 01/Show - S01E01.mkv"))
    assert not rules.excludes(lib.put("TV/Anime Classics/Show/Show - S01E01.mkv"))


def test_a_name_pattern_with_a_wildcard_matches_a_file_name(lib):
    rules = lib.rules(patterns=["*remux*"])
    assert rules.excludes(lib.put("Dune (2021)/Dune (2021) Remux-2160p.mkv"))
    assert not rules.excludes(lib.put("Dune (2021)/Dune (2021) Bluray-1080p.mkv"))


def test_a_path_pattern_covers_everything_in_the_folder(lib):
    rules = lib.rules(patterns=["Movies/4K"])
    assert rules.excludes(lib.put("Movies/4K/Dune (2021)/Dune (2021).mkv"))
    assert not rules.excludes(lib.put("Movies/4K Restorations/Jaws (1975)/Jaws (1975).mkv"))
    assert not rules.excludes(lib.put("TV/Movies/4K/x.mkv"))


def test_a_path_pattern_can_name_a_file(lib):
    rules = lib.rules(patterns=["Movies/Dune (2021)/Dune (2021).mkv"])
    assert rules.excludes(lib.put("Movies/Dune (2021)/Dune (2021).mkv"))


def test_a_wildcard_in_a_path_pattern_crosses_folders(lib):
    rules = lib.rules(patterns=["TV/*/Season 00"])
    assert rules.excludes(lib.put("TV/Show/Season 00/Show - S00E01.mkv"))
    assert not rules.excludes(lib.put("TV/Show/Season 01/Show - S01E01.mkv"))


def test_a_leading_slash_makes_it_a_path_from_the_library_path(lib):
    rules = lib.rules(patterns=["/Anime"])
    assert rules.excludes(lib.put("Anime/Show/Show - S01E01.mkv"))
    assert not rules.excludes(lib.put("TV/Anime/Show/Show - S01E01.mkv"))


def test_patterns_ignore_case(lib):
    rules = lib.rules(patterns=["anime", "movies/4k"])
    assert rules.excludes(lib.put("TV/ANIME/Show/Show - S01E01.mkv"))
    assert rules.excludes(lib.put("Movies/4K/Dune/Dune.mkv"))


def test_brackets_in_a_pattern_are_literal(lib):
    """Sonarr and Jellyfin both put IDs in square brackets in folder names."""
    rules = lib.rules(patterns=["Show [tvdbid-1234]"])
    assert rules.excludes(lib.put("Show [tvdbid-1234]/Season 01/Show - S01E01.mkv"))
    assert not rules.excludes(lib.put("Show 2/Season 01/Show 2 - S01E01.mkv"))


def test_the_library_paths_own_folders_are_not_tested(lib):
    """Otherwise "Movies" would exclude a whole library mounted at /media/Movies."""
    root = lib.root / "Movies"
    path = lib.put("Movies/Dune (2021)/Dune (2021).mkv")
    rules = lib.rules(patterns=["Movies", "library"], roots=[str(root)])
    assert not rules.excludes(path)


def test_a_path_under_no_library_path_is_tested_by_name_only(lib):
    """A webhook on an install with no library paths configured."""
    path = lib.put("TV/Anime/Show/Show - S01E01.mkv")
    assert lib.rules(patterns=["Anime"], roots=[]).excludes(path)
    assert not lib.rules(patterns=["TV/Anime"], roots=[]).excludes(path)
    # Written as an absolute path, a path pattern lines up with the start of
    # the file's own path. That is the case the rule exists for: with no
    # library path to measure from, a path pattern does not apply at all.
    assert not lib.rules(patterns=[str(lib.root / "TV")], roots=[]).excludes(path)


def test_blank_patterns_are_ignored(lib):
    path = lib.put(MOVIE)
    assert not lib.rules(patterns=["", "   "]).excludes(path)


# ── Reasons ──────────────────────────────────────────────────────────────────
#
# A reason is what the excluded-files list shows, so it has to say which
# setting caught the file and what in the file it caught. Each case below
# is a different ending, in a different case, so a reason built from the
# wrong ending, or from a lower-cased copy, does not match.

@pytest.mark.parametrize("name, ending", [
    ("POCAWE_Sample.mkv", "_Sample"),
    ("Making Of-Featurette.mp4", "-Featurette"),
    ("Teaser.TRAILER.mkv", ".TRAILER"),
])
def test_the_reason_quotes_the_ending_from_the_files_own_name(lib, name, ending):
    path = lib.put(MOVIE, f"Avatar (2009)/{name}")
    assert lib.rules().reason(path) == (
        f'Skip Trailers and Extras: name ends in "{ending}"')


def test_the_reason_for_a_file_named_only_sample_says_so(lib):
    path = lib.put(MOVIE, "Avatar (2009)/Sample.mkv")
    assert lib.rules().reason(path) == 'Skip Trailers and Extras: named "Sample"'


def test_the_reason_names_the_extras_folder(lib):
    path = lib.put(MOVIE, "Avatar (2009)/Trailers/Teaser.mkv")
    assert lib.rules().reason(path) == (
        "Skip Trailers and Extras: inside an extras folder (Trailers)")


def test_the_reason_names_a_plex_versions_folder(lib):
    path = lib.put(MOVIE, "Avatar (2009)/Plex Versions/Optimized/Avatar (2009).mp4")
    assert lib.rules().reason(path) == (
        "Skip Trailers and Extras: inside a Plex Versions folder")


def test_the_reason_names_the_pattern(lib):
    path = lib.put("Anime/Akira (1988)/Akira (1988).mkv")
    assert lib.rules(patterns=["Anime"]).reason(path) == "Exclude Patterns: 'Anime'"


def test_each_reason_starts_with_its_settings_label():
    """The prefixes are the labels the settings page shows, so a reason
    points at the control to change. A label renamed in the schema and not
    here would point at nothing."""
    from app.api.routes.settings import SETTINGS_SCHEMA
    from app.core.exclude import EXTRAS_RULE, PATTERNS_RULE

    labels = {f["key"]: f["label"] for f in SETTINGS_SCHEMA}
    assert (EXTRAS_RULE, PATTERNS_RULE) == (
        labels["skip_extras"], labels["exclude_patterns"])


# ── Always Include ───────────────────────────────────────────────────────────
#
# include_patterns overrides both of the above. Each test keeps a neighbour
# that is still excluded, so an entry that covered too much would show.

def test_an_include_brings_back_a_file_the_extras_rule_caught(lib):
    kept = lib.put(MOVIE, "Avatar (2009)/Codec Test_sample.mkv")
    other = lib.put("Avatar (2009)/Teaser-trailer.mkv")

    rules = lib.rules(includes=["Codec Test_sample.mkv"])

    assert not rules.excludes(kept)
    assert rules.reason(other) == 'Skip Trailers and Extras: name ends in "-trailer"'


def test_an_include_overrides_an_exclude_pattern_for_a_whole_folder(lib):
    kept = lib.put("Anime/Akira (1988)/Akira (1988).mkv")
    other = lib.put("Anime/Paprika (2006)/Paprika (2006).mkv")

    rules = lib.rules(patterns=["Anime"], includes=["Anime/Akira (1988)"])

    assert not rules.excludes(kept)
    assert rules.reason(other) == "Exclude Patterns: 'Anime'"


def test_a_file_entry_still_covers_the_file_after_its_container_changes(lib):
    """Written as the .mkv it was, matched against the .mp4 a conversion
    leaves. Otherwise the converted file is excluded again at the next scan
    and its record removed."""
    converted = lib.put(MOVIE, "Avatar (2009)/Codec Test_sample.mp4")

    rules = lib.rules(includes=["Avatar (2009)/Codec Test_sample.mkv"])

    assert not rules.excludes(converted)


@pytest.mark.parametrize("name", ["Teaser.trailer.mkv", "Teaser 2.mkv"])
def test_a_file_entry_covers_no_other_name(lib, name):
    """Only the extension is open. Teaser.trailer.mkv is a different file
    that an open ending after the first dot would take in."""
    lib.put(MOVIE, "Avatar (2009)/Teaser.mkv")
    other = lib.put(f"Avatar (2009)/{name}")

    rules = lib.rules(patterns=["Avatar (2009)"],
                      includes=["Avatar (2009)/Teaser.mkv"])

    assert not rules.excludes(str(lib.root / "Avatar (2009)/Teaser.mkv"))
    assert rules.excludes(other)
