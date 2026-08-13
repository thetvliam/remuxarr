"""
Seams the suite reached but did not close.

Three pieces of behaviour that were unprotected, and which have in common that
they are one function call past where an existing test stopped, or in a module
with near-zero coverage. Each can be broken without failing anything else.

FASTSTART
  test_decision.py already builds the exact decision that matters and asserts
  `decision.source_already_faststart is True`. Its docstring then says
  "build_ffmpeg_command must re-assert +faststart on ANY remux of a source
  that already had it" — and never calls build_ffmpeg_command. Deleting
  `or decision.source_already_faststart` from ffmpeg.py leaves the whole suite
  green, while every remux of an already-optimised MP4 silently moves the moov
  atom back to the end of the file.

  The flag's own comment records that this was found by checking real FFmpeg
  output: a plain stream-copy rebuilds the container without it. So the
  regression is invisible in the decision, invisible in the logs, and only
  observable by inspecting the produced file.

PATH TRANSLATION AND SCHEDULE WINDOWS
  translate_path_to_plex (plex.py, 11% covered) and _within_window
  (scheduler.py, 14% covered) are pure functions with no I/O and no fixtures
  required. Both decide whether an external side effect happens at all — a
  wrong mapping silently notifies nothing, a wrong window silently scans at
  the wrong time — so both fail quietly by construction.
"""
import pytest

from app.core.decision import analyze_file
from app.core.ffmpeg import build_ffmpeg_command
from tests.conftest import make_file_info, make_track


# ── +faststart in the actual command ─────────────────────────────────────────

def _mp4_tracks():
    return [
        make_track(stream_index=0, track_type="video", codec="h264"),
        make_track(stream_index=1, track_type="audio", codec="aac",
                   language="jpn", is_default=True),
    ]


def test_language_only_remux_preserves_faststart(settings):
    """
    THE regression. A language-tag correction has nothing else to do
    and correctly generates no add_faststart action — so the only thing
    keeping +faststart on the command is source_already_faststart.

    Without it the job succeeds, reports success, and hands back an MP4 whose
    moov atom has moved to the end. A later scan then "discovers" faststart is
    missing and queues another remux to re-add it.
    """
    decision = analyze_file(
        make_file_info(path="/media/Movie.mp4", container="mp4"),
        _mp4_tracks(), settings,
        audio_language_overrides={1: "eng"},
        has_faststart=True,
    )
    assert decision.source_already_faststart is True
    assert not any(a.action_type == "add_faststart" for a in decision.actions)

    cmd = build_ffmpeg_command("/media/Movie.mp4", "/tmp/out.mp4",
                               decision, _mp4_tracks())

    assert "+faststart" in cmd, (
        "the command drops +faststart on a remux of an already-optimised "
        "source, so this job silently de-optimises the file"
    )
    # And it is passed as a movflags value, not merely present somewhere.
    assert cmd[cmd.index("+faststart") - 1] == "-movflags"


def test_container_conversion_to_mp4_gets_faststart(settings):
    """Case 1 of the three the comment documents: a new MP4 should be
    web-optimised."""
    tracks = [
        make_track(stream_index=0, track_type="video", codec="h264"),
        make_track(stream_index=1, track_type="audio", codec="aac",
                   language="eng", is_default=True),
    ]
    decision = analyze_file(
        make_file_info(path="/media/Movie.mkv", container="mkv"),
        tracks, settings,
    )
    if not any(a.action_type == "change_container" for a in decision.actions):
        pytest.skip("settings did not produce a container conversion")

    cmd = build_ffmpeg_command("/media/Movie.mkv", "/tmp/out.mp4", decision, tracks)
    assert "+faststart" in cmd


def test_non_mp4_output_never_gets_faststart(settings):
    """
    +faststart is an MP4/MOV muxer option. Emitting it for a Matroska output
    is at best ignored and at worst rejected, so the target_container guard
    matters as much as the three cases inside it.
    """
    cfg = dict(settings)
    cfg["prefer_mp4_container"] = False
    tracks = [
        make_track(stream_index=0, track_type="video", codec="h264"),
        make_track(stream_index=1, track_type="audio", codec="aac",
                   language="jpn", is_default=True),
    ]
    decision = analyze_file(
        make_file_info(path="/media/Movie.mkv", container="mkv"),
        tracks, cfg,
        audio_language_overrides={1: "eng"},
        has_faststart=True,
    )
    cmd = build_ffmpeg_command("/media/Movie.mkv", "/tmp/out.mkv", decision, tracks)
    if decision.target_container == "mp4":
        pytest.skip("this configuration still targets mp4")
    assert "+faststart" not in cmd


# ── translate_path_to_plex ───────────────────────────────────────────────────
#
# Mappings are "local=plex" STRINGS, not dicts — the settings UI collects them
# as a tag list. Malformed entries are expected input, not an edge case.

def _translate(path, mappings):
    from app.core.plex import translate_path_to_plex

    return translate_path_to_plex(path, mappings)


def test_path_translation_applies_a_matching_mapping():
    assert _translate(
        "/mnt/user/media/Movies/Film.mkv", ["/mnt/user/media=/data"],
    ) == "/data/Movies/Film.mkv"


def test_path_translation_prefers_the_longest_matching_prefix():
    """
    Overlapping mappings are normal in Unraid setups — a broad share plus a
    specific subfolder on different storage. Matching the shorter prefix first
    sends Plex a path that does not exist, and the notify silently does
    nothing.
    """
    mappings = ["/mnt/user=/data", "/mnt/user/media/4k=/data4k"]
    assert _translate("/mnt/user/media/4k/Film.mkv", mappings) == "/data4k/Film.mkv"


def test_path_translation_returns_none_when_nothing_matches():
    """
    Must be distinguishable from a successful translation: the caller uses it
    to decide whether to notify at all, and returning the untranslated path
    would send Plex a local path it cannot resolve.
    """
    assert _translate("/some/other/place/Film.mkv", ["/mnt/user/media=/data"]) is None


def test_path_translation_with_no_mappings():
    assert _translate("/mnt/user/media/Film.mkv", []) is None


def test_path_translation_ignores_malformed_entries():
    """A half-filled row in the settings tag list must not break every notify."""
    mappings = ["no-equals-sign", "=/data", "/mnt/user/media=", "/mnt/user/media=/data"]
    assert _translate("/mnt/user/media/Film.mkv", mappings) == "/data/Film.mkv"


def test_path_translation_tolerates_trailing_slashes():
    assert _translate("/mnt/user/media/Film.mkv", ["/mnt/user/media/=/data/"]) \
        == "/data/Film.mkv"


def test_path_translation_does_not_match_a_partial_directory_name():
    """
    "/mnt/user/media" must not match "/mnt/user/media2/..." — a prefix match
    on the raw string would, and would send Plex a mangled path.
    """
    assert _translate("/mnt/user/media2/Film.mkv", ["/mnt/user/media=/data"]) is None


# ── _within_window ───────────────────────────────────────────────────────────
#
# The function reads datetime.now() itself, so the clock is stubbed rather than
# passed in. strptime is delegated to the real class so parsing still behaves.

def _win(now_hhmm, start, end, monkeypatch):
    import datetime as _dt

    import app.core.scheduler as scheduler

    h, m = (int(x) for x in now_hhmm.split(":"))

    class FrozenDatetime(_dt.datetime):
        @classmethod
        def now(cls, tz=None):
            return _dt.datetime(2026, 6, 15, h, m)

    monkeypatch.setattr(scheduler, "datetime", FrozenDatetime)
    return scheduler._within_window(start, end)


def test_window_same_day_range(monkeypatch):
    assert _win("14:00", "09:00", "17:00", monkeypatch) is True
    assert _win("08:59", "09:00", "17:00", monkeypatch) is False
    assert _win("17:30", "09:00", "17:00", monkeypatch) is False


def test_window_wrapping_midnight(monkeypatch):
    """
    The branch worth having, and the default configuration: the shipped window
    is 02:00-06:00, which does not wrap — but 22:00-06:00 is the obvious thing
    a user sets, and a naive start <= now <= end is false for every minute of
    it. Getting this wrong means the Plex backlog never drains, with nothing
    logged: the scheduler simply decides it is not time.
    """
    assert _win("23:30", "22:00", "06:00", monkeypatch) is True
    assert _win("02:00", "22:00", "06:00", monkeypatch) is True
    assert _win("12:00", "22:00", "06:00", monkeypatch) is False


def test_window_boundaries_are_inclusive(monkeypatch):
    assert _win("09:00", "09:00", "17:00", monkeypatch) is True
    assert _win("17:00", "09:00", "17:00", monkeypatch) is True


def test_window_default_configuration(monkeypatch):
    """The shipped default, 02:00-06:00."""
    assert _win("03:30", "02:00", "06:00", monkeypatch) is True
    assert _win("09:00", "02:00", "06:00", monkeypatch) is False


def test_malformed_window_is_closed_not_open(monkeypatch):
    """
    Unparseable bounds return False — the drain is skipped rather than run at
    an arbitrary hour. That is the safe direction: Analyze is the expensive
    call this window exists to confine, and the drain is separately gated on
    plex_analyze_backlog_enabled, so a closed window cannot disable a feature
    the user had working.
    """
    assert _win("03:00", None, None, monkeypatch) is False
    assert _win("03:00", "", "", monkeypatch) is False
    assert _win("03:00", "not a time", "06:00", monkeypatch) is False


def test_an_existing_mp4_missing_faststart_gets_it_added(settings):
    """
    Case 2 of the three the comment documents, and the only one that had no
    test: rewriting an EXISTING MP4 that was never faststart-optimised.

    The distinguishing setup is a source that is ALREADY mp4 (so no
    change_container action) and was NOT already optimised (so
    source_already_faststart is False) — leaving the add_faststart action as
    the only term that can put +faststart on the command.

    Found by an independent mutation audit (Phase 1): dropping the
    has_faststart_action term survived the entire 662-test suite, because the
    two sibling terms were each covered and this one was reachable only
    through a combination no test built.
    """
    tracks = _mp4_tracks()
    decision = analyze_file(
        make_file_info(path="/media/Movie.mp4", container="mp4"),
        tracks, settings,
        audio_language_overrides={1: "eng"},
        has_faststart=False,
    )

    assert decision.target_container == "mp4"
    assert decision.source_already_faststart is False, (
        "source is already optimised — this test cannot isolate the "
        "add_faststart term"
    )
    assert not any(a.action_type == "change_container" for a in decision.actions), (
        "a container conversion is present — case 1 would supply +faststart "
        "regardless and this test would prove nothing"
    )
    assert any(a.action_type == "add_faststart" for a in decision.actions), (
        "no add_faststart action was generated, so there is nothing to test"
    )

    cmd = build_ffmpeg_command("/media/Movie.mp4", "/tmp/out.mp4",
                               decision, tracks)

    assert "+faststart" in cmd, (
        "an add_faststart action was planned but the command omits "
        "+faststart — the job reports success and the file stays "
        "un-optimised, so the next scan queues the identical work again"
    )
    assert cmd[cmd.index("+faststart") - 1] == "-movflags"


# ═══════════════════════════════════════════════════════════════════════════
# webhooks.py pure helpers
#
# Found by an independent mutation audit (Phase 2): 7 of 8 mutations of
# webhooks.py survived the whole suite — the weakest module either phase
# audited. All five helpers below are pure: no HTTP client, no DB, no async.
#
# _translate_path is the direct analogue of plex.translate_path_to_plex, whose
# equivalent mutations were all killed in Phase 1. The same class of function
# was well tested in one module and untested in the other, and this one's own
# docstring says a wrong result causes "silent queue failures".
# ═══════════════════════════════════════════════════════════════════════════

from app.api.routes.webhooks import (  # noqa: E402
    _radarr_movie_id, _radarr_paths, _sonarr_paths, _sonarr_series_id,
    _translate_path,
)


# ── _translate_path ──────────────────────────────────────────────────────────

def test_a_matching_prefix_is_translated():
    """
    The case the function exists for: Sonarr and Remuxarr mount the same
    physical directory at different container paths.
    """
    assert _translate_path("/media/Show/ep.mkv", "/media", "/media/tv") == \
        "/media/tv/Show/ep.mkv"


def test_an_exact_prefix_match_translates_to_the_bare_local_prefix():
    assert _translate_path("/media", "/media", "/data") == "/data"


def test_a_path_outside_the_remote_prefix_is_untouched():
    assert _translate_path("/other/ep.mkv", "/media", "/data") == "/other/ep.mkv"


def test_a_sibling_directory_sharing_a_prefix_is_not_translated():
    """
    The separator boundary. "/media" must not match "/media2/..." — a loose
    `remote in path` test would rewrite an unrelated library's paths into a
    directory that does not exist, and the queue attempt fails silently.

    Audit ref: WHK-01.
    """
    assert _translate_path("/media2/ep.mkv", "/media", "/data") == "/media2/ep.mkv"


def test_a_prefix_appearing_mid_path_is_not_translated():
    """
    The other half of WHK-01: a loose substring match would also fire on a
    remote prefix buried in the middle of an unrelated path.
    """
    assert _translate_path("/mnt/media/ep.mkv", "/media", "/data") == \
        "/mnt/media/ep.mkv"


@pytest.mark.parametrize("remote,local", [
    ("", "/data"),
    ("/media", ""),
    ("", ""),
    ("/", "/data"),      # rstrips to empty
    ("/media", "/"),
])
def test_a_blank_prefix_pair_passes_the_path_through(remote, local):
    """
    BOTH prefixes are required. Unconfigured setups — where the two containers
    already agree on the path — must work out of the box, and a half-filled
    settings pair must not produce a half-translated path.

    Audit ref: WHK-02 (guard removed) and WHK-03 (or → and, so one blank
    prefix still translates: "/media/ep.mkv" with local "" becomes
    "/ep.mkv", pointing at nothing).
    """
    assert _translate_path("/media/ep.mkv", remote, local) == "/media/ep.mkv"


def test_trailing_slashes_on_either_prefix_do_not_double_up():
    assert _translate_path("/media/ep.mkv", "/media/", "/data/") == "/data/ep.mkv"


# ── _sonarr_paths ────────────────────────────────────────────────────────────

def test_a_sonarr_download_event_yields_its_episode_file():
    assert _sonarr_paths({"episodeFile": {"path": "/media/ep.mkv"}}) == \
        ["/media/ep.mkv"]


def test_a_sonarr_rename_event_yields_every_renamed_file():
    payload = {"renamedEpisodeFiles": [{"path": "/media/a.mkv"},
                                       {"path": "/media/b.mkv"}]}
    assert _sonarr_paths(payload) == ["/media/a.mkv", "/media/b.mkv"]


def test_a_sonarr_import_event_yields_every_episode_file():
    """
    The third payload shape — the episodeFiles array used by import/upgrade.
    It was silently dropped with nothing failing: the rename array was covered
    and this one was not, so one of Sonarr's three event shapes queued nothing.

    Audit ref: WHK-05.
    """
    payload = {"episodeFiles": [{"path": "/media/a.mkv"},
                                {"path": "/media/b.mkv"}]}
    assert _sonarr_paths(payload) == ["/media/a.mkv", "/media/b.mkv"]


def test_sonarr_paths_are_deduplicated_in_order():
    """
    A payload can name the same file in more than one array. Without the
    dedupe the file is queued once per mention, so a single import produces
    duplicate jobs racing each other over one file.

    Audit ref: WHK-06.
    """
    payload = {
        "episodeFile":         {"path": "/media/a.mkv"},
        "renamedEpisodeFiles": [{"path": "/media/a.mkv"}],
        "episodeFiles":        [{"path": "/media/a.mkv"}, {"path": "/media/b.mkv"}],
    }
    assert _sonarr_paths(payload) == ["/media/a.mkv", "/media/b.mkv"]


def test_sonarr_entries_without_a_path_are_skipped():
    payload = {"episodeFile": {}, "renamedEpisodeFiles": [{"path": None}, {}],
               "episodeFiles": [{"path": "/media/real.mkv"}]}
    assert _sonarr_paths(payload) == ["/media/real.mkv"]


def test_an_empty_sonarr_payload_yields_no_paths():
    assert _sonarr_paths({}) == []


# ── _radarr_paths ────────────────────────────────────────────────────────────

def test_a_radarr_download_event_yields_its_movie_file():
    assert _radarr_paths({"movieFile": {"path": "/media/m.mkv"}}) == \
        ["/media/m.mkv"]


def test_a_radarr_rename_event_reads_the_plural_array():
    """
    renamedMovieFiles is a LIST. The previous code read "renamedMovieFile", a
    field Radarr never emits, so Rename events matched nothing — Download kept
    working, which is what hid it.
    """
    payload = {"renamedMovieFiles": [{"path": "/media/a.mkv"},
                                     {"path": "/media/b.mkv"}]}
    assert _radarr_paths(payload) == ["/media/a.mkv", "/media/b.mkv"]


def test_radarr_paths_are_deduplicated_in_order():
    payload = {"movieFile": {"path": "/media/a.mkv"},
               "renamedMovieFiles": [{"path": "/media/a.mkv"},
                                     {"path": "/media/b.mkv"}]}
    assert _radarr_paths(payload) == ["/media/a.mkv", "/media/b.mkv"]


def test_an_empty_radarr_payload_yields_no_paths():
    assert _radarr_paths({}) == []


# ── ID extraction ────────────────────────────────────────────────────────────

def test_a_sonarr_series_id_is_read_as_an_integer():
    assert _sonarr_series_id({"series": {"id": "42"}}) == 42


def test_a_radarr_movie_id_is_read_as_an_integer():
    assert _radarr_movie_id({"movie": {"id": "17"}}) == 17


@pytest.mark.parametrize("payload", [
    {},
    {"series": {}},
    {"series": None},
    {"series": {"id": None}},
    {"series": {"id": "not a number"}},
    {"series": "not a dict"},
])
def test_a_malformed_series_id_degrades_to_none(payload):
    """
    None specifically, not a sentinel. _resolve_translated_path_sync branches
    on `if series_id:` — a truthy sentinel like -1 selects the SONARR path
    prefixes for a payload whose series could not be identified, translating
    the path with the wrong mapping. The id is also stored on the queue item
    and later drives a RescanSeries call, so a bogus value addresses a series
    that does not exist.

    Audit ref: WHK-07.
    """
    assert _sonarr_series_id(payload) is None


@pytest.mark.parametrize("payload", [
    {},
    {"movie": {}},
    {"movie": None},
    {"movie": {"id": None}},
    {"movie": {"id": "not a number"}},
    {"movie": "not a dict"},
])
def test_a_malformed_movie_id_degrades_to_none(payload):
    """
    Same contract, opposite failure shape: 0 is falsy, so a 0 sentinel would
    silently skip Radarr path translation entirely rather than misapply it —
    equally wrong, and equally invisible.

    Audit ref: WHK-08.
    """
    assert _radarr_movie_id(payload) is None


def test_the_two_id_extractors_read_their_own_payload_shapes():
    """
    Near-identical functions reading different keys — the copy-paste shape.
    A Sonarr payload must not yield a movie id, or vice versa.
    """
    assert _sonarr_series_id({"movie": {"id": 5}}) is None
    assert _radarr_movie_id({"series": {"id": 5}}) is None


def test_a_sonarr_rename_takes_the_new_path_not_the_previous_one():
    """
    Rename payloads carry previousPath alongside path. Only the new path is
    usable: after a rename the file no longer exists at the old location, so
    queuing it would probe-fail on a missing file. The stale row is the
    scanner's problem, not the webhook's.

    Merged from the Phase 4 audit, which covered this and the tests above did
    not.
    """
    payload = {"renamedEpisodeFiles": [
        {"path": "/media/Show/new.mkv", "previousPath": "/media/Show/old.mkv"},
    ]}
    assert _sonarr_paths(payload) == ["/media/Show/new.mkv"]


def test_a_radarr_rename_takes_the_new_path_not_the_previous_one():
    payload = {"renamedMovieFiles": [
        {"path": "/media/Movie/new.mkv", "previousPath": "/media/Movie/old.mkv"},
    ]}
    assert _radarr_paths(payload) == ["/media/Movie/new.mkv"]
