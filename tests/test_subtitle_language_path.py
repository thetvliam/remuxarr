"""
The subtitle-language data path: flagging, override application, and the
sidecar filename.

WHY THIS FILE EXISTS
--------------------
This whole path was 100% uncovered, and three separate defects lived in it.
The reason it was uncovered is itself the finding: conftest.BASE_SETTINGS had
extract_text_subtitles_to_srt=False while production ships True, so every
default-configured test ran against the minority configuration — the one where
kept text subtitles become copy_track actions instead of extract_subtitle, and
the language override pass therefore works.

What was broken, on a stock install:

  * Subtitle Language Review produced NO rows at all. The und pass excluded
    extracted streams from its candidate set, and with extraction on that is
    every SRT-convertible subtitle. A feature with its own table, migration,
    index, router and UI section was dormant.

  * If a row did somehow exist and the user resolved it, Apply did nothing.
    _apply_language_override_pass only rewrites copy/transcode actions, so the
    override was skipped, the sidecar kept its .und. name permanently, and
    _upsert_language_flags then DELETED the flag row because the mismatch had
    cleared. Success toast, vanished row, nothing changed.

  * The flagged stream index came from next(iter(set)), which is hash-slot
    order, not ascending — next(iter({18, 2, 10})) is 18. That index is the
    track Apply writes to.

Every test below runs at production defaults unless it says otherwise.
"""

from app.core.decision import analyze_file
from app.database.session import DEFAULT_APP_SETTINGS


def _prod(**overrides):
    """Production settings, so these tests cannot drift from what users run."""
    cfg = dict(DEFAULT_APP_SETTINGS)
    cfg.update(overrides)
    return cfg


def _fmt(path="/media/Movie.mkv"):
    return {"path": path, "container": "mkv", "format_name": "matroska,webm",
            "duration": 100.0, "size": 1000, "bit_rate": 1000}


VIDEO = {"track_type": "video", "stream_index": 0, "codec": "h264",
         "language": None, "channels": None, "is_default": True,
         "is_forced": False, "title": None}


def _audio(si=1, lang="eng"):
    return {"track_type": "audio", "stream_index": si, "codec": "aac",
            "language": lang, "channels": 2, "is_default": True,
            "is_forced": False, "title": None}


def _sub(si, lang="und", codec="subrip", forced=False):
    return {"track_type": "subtitle", "stream_index": si, "codec": codec,
            "language": lang, "channels": None, "is_default": False,
            "is_forced": forced, "title": None}


def _action_for(decision, si):
    return next(a for a in decision.actions if a.stream_index == si)


# ── Reachability at default settings ─────────────────────────────────────────

def test_subtitle_language_review_reachable_with_extraction_on():
    """
    THE dormancy bug. extract_text_subtitles_to_srt defaults to True, and the
    und pass used to exclude extracted streams from its candidate set — so
    und_kept was empty for every text subtitle and subtitle_language_mismatch
    was never populated on a stock install.
    """
    cfg = _prod(fix_undefined_language="always_ask")
    assert cfg["extract_text_subtitles_to_srt"] is True, "not testing the default config"

    d = analyze_file(_fmt(), [VIDEO, _audio(), _sub(2, forced=True)], cfg)

    assert d.subtitle_language_mismatch is not None, (
        "no subtitle language flag raised under default (extraction-on) "
        "settings — Subtitle Language Review is dormant"
    )
    assert d.subtitle_language_mismatch == {"stream_index": 2, "language": "und"}


def test_extraction_off_still_flags():
    """The pre-existing behaviour must survive the change."""
    cfg = _prod(fix_undefined_language="always_ask",
                extract_text_subtitles_to_srt=False)
    d = analyze_file(_fmt(), [VIDEO, _audio(), _sub(2, forced=True)], cfg)
    assert d.subtitle_language_mismatch == {"stream_index": 2, "language": "und"}


def test_dropped_subtitle_is_not_flagged():
    """
    A dropped track genuinely has nothing to review — that is the distinction
    the exclusion was reaching for, and it must still hold. A non-forced
    Spanish subtitle is dropped under the default keep list.
    """
    cfg = _prod(fix_undefined_language="always_ask")
    d = analyze_file(_fmt(), [VIDEO, _audio(), _sub(2, lang="spa")], cfg)
    assert _action_for(d, 2).action_type == "drop_track"
    assert d.subtitle_language_mismatch is None


# ── Flagged index ────────────────────────────────────────────────────────────

def test_und_flag_targets_lowest_stream_index():
    """
    The flagged index is the track Apply writes the corrected language to, so
    it must be predictable. next(iter(set)) follows hash-table slot order:
    next(iter({18, 2, 10})) is 18, not 2.
    """
    cfg = _prod(fix_undefined_language="always_ask",
                undefined_language_mode="all_undefined")
    tracks = [VIDEO, _audio(),
              _sub(18, forced=True), _sub(2, forced=True), _sub(10, forced=True)]

    d = analyze_file(_fmt(), tracks, cfg)

    assert d.subtitle_language_mismatch["stream_index"] == 2, (
        f"flagged stream {d.subtitle_language_mismatch['stream_index']} rather "
        "than the lowest (2) — the index came from set iteration order"
    )


def test_und_audio_flag_targets_lowest_stream_index():
    """
    Same rule on the audio side, where the flag drives AudioLanguageFlag.

    und_audio_threshold is raised so the manual-review gate does not fire
    first — at the default of 2, three und audio tracks return early as
    manual_review and never reach the flagging pass at all.
    """
    cfg = _prod(fix_undefined_language="always_ask",
                undefined_language_mode="all_undefined",
                und_audio_threshold=99)
    tracks = [VIDEO, _audio(si=18, lang="und"), _audio(si=3, lang="und"),
              _audio(si=11, lang="und")]

    d = analyze_file(_fmt(), tracks, cfg)
    assert d.audio_language_mismatch["stream_index"] == 3


# ── The override reaching the sidecar ────────────────────────────────────────

def test_subtitle_override_reaches_the_sidecar_filename():
    """
    THE silent-failure bug. The sidecar carries the language in its filename —
    that is how Plex identifies it — so an override that does not reach
    _build_srt_path accomplishes nothing while reporting success.
    """
    cfg = _prod(fix_undefined_language="always_ask")
    tracks = [VIDEO, _audio(), _sub(2, forced=True)]

    d = analyze_file(_fmt(), tracks, cfg, subtitle_language_overrides={2: "eng"})

    action = _action_for(d, 2)
    assert action.action_type == "extract_subtitle"
    assert action.external_path.endswith("Movie.en.forced.srt"), (
        f"sidecar written as {action.external_path!r} — the language override "
        "never reached the filename, so the correction is invisible to Plex "
        "and permanent on disk"
    )
    assert action.language == "eng"


def test_resolved_override_clears_the_flag():
    """
    Once the override IS applied the mismatch must clear, because there is
    genuinely nothing left to review. Previously it also cleared — but with
    the work not done, which is what deleted the row from the Review page
    while leaving the file wrong.
    """
    cfg = _prod(fix_undefined_language="always_ask")
    tracks = [VIDEO, _audio(), _sub(2, forced=True)]

    before = analyze_file(_fmt(), tracks, cfg)
    after = analyze_file(_fmt(), tracks, cfg, subtitle_language_overrides={2: "eng"})

    assert before.subtitle_language_mismatch is not None
    assert after.subtitle_language_mismatch is None
    assert _action_for(after, 2).external_path.endswith(".en.forced.srt")


def test_override_does_not_change_keep_or_drop():
    """
    Scope guard. Applying a language correction must not silently add or
    remove a subtitle track — the Review page offers to relabel, not to change
    what ends up in the library. A Spanish track dropped by the keep list stays
    dropped even with an override naming it English.
    """
    cfg = _prod(fix_undefined_language="always_ask")
    d = analyze_file(_fmt(), [VIDEO, _audio(), _sub(2, lang="spa")], cfg,
                     subtitle_language_overrides={2: "eng"})
    assert _action_for(d, 2).action_type == "drop_track"


def test_override_is_ignored_when_extraction_is_off():
    """With extraction off the track stays embedded and the existing
    copy_track override pass handles it — the older path must still work."""
    cfg = _prod(fix_undefined_language="always_ask",
                extract_text_subtitles_to_srt=False)
    d = analyze_file(_fmt(), [VIDEO, _audio(), _sub(2, forced=True)], cfg,
                     subtitle_language_overrides={2: "eng"})
    action = _action_for(d, 2)
    assert action.action_type == "copy_track"
    assert action.target_language == "eng"


# ── always_fix on an extracted track ─────────────────────────────────────────

def test_always_fix_renames_the_sidecar():
    """
    always_fix means "tag und tracks automatically". For an extracted subtitle
    that means renaming the artifact — setting target_language would do
    nothing, since the stream is not in the output at all.
    """
    cfg = _prod(fix_undefined_language="always_fix", undefined_language_value="eng")
    d = analyze_file(_fmt(), [VIDEO, _audio(), _sub(2, forced=True)], cfg)

    action = _action_for(d, 2)
    assert action.external_path.endswith("Movie.en.forced.srt"), action.external_path
    assert action.language == "eng"
    assert d.subtitle_language_mismatch is None, "always_fix should not also flag"


def test_always_fix_rename_does_not_collide_with_itself():
    """
    The relabel discharges the old path from used_paths first. Without that the
    rebuilt name collides with the name it is replacing and picks up a spurious
    '.2' suffix.
    """
    cfg = _prod(fix_undefined_language="always_fix", undefined_language_value="eng")
    d = analyze_file(_fmt(), [VIDEO, _audio(), _sub(2, forced=True)], cfg)
    assert ".2.srt" not in _action_for(d, 2).external_path


def test_always_leave_touches_nothing():
    cfg = _prod(fix_undefined_language="always_leave")
    d = analyze_file(_fmt(), [VIDEO, _audio(), _sub(2, forced=True)], cfg)
    assert d.subtitle_language_mismatch is None
    assert _action_for(d, 2).external_path.endswith("Movie.und.forced.srt")


# ── The two previously-unpinned undefined_language_mode values ───────────────

def test_mode_all_undefined_flags_every_und_track():
    cfg = _prod(fix_undefined_language="always_ask",
                undefined_language_mode="all_undefined")
    tracks = [VIDEO, _audio(), _sub(2, forced=True), _sub(3, lang="eng")]
    d = analyze_file(_fmt(), tracks, cfg)
    # stream 3 is defined, so only 2 qualifies — and it is flagged even though
    # a defined sibling exists, which is what distinguishes this mode.
    assert d.subtitle_language_mismatch == {"stream_index": 2, "language": "und"}


def test_mode_single_per_type_requires_exactly_one_und_track():
    cfg = _prod(fix_undefined_language="always_ask",
                undefined_language_mode="single_per_type")

    one = analyze_file(_fmt(), [VIDEO, _audio(), _sub(2, forced=True)], cfg)
    assert one.subtitle_language_mismatch is not None

    two = analyze_file(
        _fmt(), [VIDEO, _audio(), _sub(2, forced=True), _sub(3, forced=True)], cfg
    )
    assert two.subtitle_language_mismatch is None, (
        "single_per_type flagged a file with two und subtitle tracks"
    )


def test_mode_all_undefined_per_type_requires_all_und():
    """The default mode: tag only when EVERY kept track of the type is und."""
    cfg = _prod(fix_undefined_language="always_ask",
                undefined_language_mode="all_undefined_per_type")

    all_und = analyze_file(
        _fmt(), [VIDEO, _audio(), _sub(2, forced=True), _sub(3, forced=True)], cfg
    )
    assert all_und.subtitle_language_mismatch == {"stream_index": 2, "language": "und"}

    mixed = analyze_file(
        _fmt(), [VIDEO, _audio(), _sub(2, forced=True), _sub(3, lang="eng")], cfg
    )
    assert mixed.subtitle_language_mismatch is None


# ── Config alignment guard ───────────────────────────────────────────────────

def test_base_settings_match_production_defaults():
    """
    The reason all of the above was undetectable. BASE_SETTINGS diverging from
    DEFAULT_APP_SETTINGS means the suite's largest module tests a configuration
    almost nobody runs.
    """
    from tests.conftest import BASE_SETTINGS

    mismatched = {
        k: (v, DEFAULT_APP_SETTINGS[k])
        for k, v in BASE_SETTINGS.items()
        if k in DEFAULT_APP_SETTINGS and v != DEFAULT_APP_SETTINGS[k]
    }
    assert not mismatched, (
        f"BASE_SETTINGS diverges from production defaults: {mismatched}. "
        "A test needing a non-production value should set it on the settings "
        "fixture rather than changing the shared default."
    )
