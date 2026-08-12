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
