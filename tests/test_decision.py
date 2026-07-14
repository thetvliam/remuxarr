"""
Regression tests for app/core/decision.py's analyze_file().

Every test here is tied to something real: either a bug that actually
shipped and was found in production, or a specific behavior that took
real effort to get right and would be easy to silently break in a future
edit. The docstring on each test names the incident it guards against —
if a test here starts failing, that's the specific real-world scenario
that's broken, not just an abstract assertion.

Run from the project root:
    pip install -r tests/requirements-test.txt
    pytest tests/ -v
"""
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest
from app.core.decision import analyze_file, MP4_COMPATIBLE_VIDEO
from conftest import make_track, make_file_info, BASE_SETTINGS


# ═══════════════════════════════════════════════════════════════════════════
# Audio language safety net — the silent-audio bug and its fallback tiers
# ═══════════════════════════════════════════════════════════════════════════

def test_single_nondefault_track_survives_via_absolute_fallback(settings):
    """
    The most important test in this file.

    Real incident: a video file's only audio track was mistagged "dan"
    (Danish) by the source release and never flagged default. Before the
    fix, every check failed — language not in the keep list, not "und",
    not default-flagged — and the track was dropped, leaving the output
    file with literally zero audio tracks.

    This is the exact scenario: single non-preferred, non-default track.
    It must survive via the absolute-fallback tier, and the file should
    be flagged for Audio Language Review so a human can see it.
    """
    tracks = [
        make_track(stream_index=0, track_type="video", codec="hevc"),
        make_track(stream_index=1, track_type="audio", codec="aac",
                   language="dan", is_default=False),
    ]
    decision = analyze_file(make_file_info(), tracks, settings)

    dropped_audio = [
        a for a in decision.actions
        if a.track_type == "audio" and a.action_type == "drop_track"
    ]
    assert not dropped_audio, (
        "The only audio track was dropped — this is the exact bug that "
        "produced a silent output file in production."
    )
    assert decision.audio_language_mismatch is not None
    assert decision.audio_language_mismatch["language"] == "dan"


def test_single_default_flagged_track_survives_via_tier2_fallback(settings):
    """
    Real incident: a whole batch of video files had their single audio
    track mistagged "dut" (Dutch), but — unlike the non-default case
    above — the track WAS flagged default in the source file. This is
    tier 2 of the safety net (the default-flag fallback), not tier 3 (the
    absolute fallback) — both need to keep working independently.
    """
    tracks = [
        make_track(stream_index=0, track_type="video", codec="h264"),
        make_track(stream_index=1, track_type="audio", codec="aac",
                   language="dut", is_default=True),
    ]
    decision = analyze_file(make_file_info(), tracks, settings)

    dropped_audio = [
        a for a in decision.actions
        if a.track_type == "audio" and a.action_type == "drop_track"
    ]
    assert not dropped_audio
    assert decision.audio_language_mismatch is not None
    assert decision.audio_language_mismatch["language"] == "dut"


def test_normal_multi_language_file_unaffected(settings):
    """
    Sanity check: the safety net must never activate when a preferred
    track genuinely exists. English survives, French drops, no mismatch
    flag — the ordinary, expected case that must stay ordinary.
    """
    tracks = [
        make_track(stream_index=0, track_type="video", codec="h264"),
        make_track(stream_index=1, track_type="audio", codec="aac",
                   language="eng", is_default=True),
        make_track(stream_index=2, track_type="audio", codec="aac",
                   language="fre", is_default=False),
    ]
    decision = analyze_file(make_file_info(), tracks, settings)

    dropped = {a.stream_index for a in decision.actions if a.action_type == "drop_track"}
    kept    = {a.stream_index for a in decision.actions
               if a.track_type == "audio" and a.action_type == "copy_track"}
    assert 2 in dropped   # French correctly dropped
    assert 1 in kept      # English correctly kept
    assert decision.audio_language_mismatch is None


def test_genuinely_foreign_language_still_flagged(settings):
    """
    Not a bug — a documented, deliberate limitation. The detection can't
    distinguish "wrong tag" from "correctly foreign content" on its own
    (e.g. anime that's genuinely, correctly Japanese) — that's exactly
    what the Ignore action in Audio Language Review exists for. This test
    exists to make that limitation explicit and intentional, so a future
    change doesn't "fix" it by accident and silently stop flagging
    legitimately-foreign content for review.
    """
    tracks = [
        make_track(stream_index=0, track_type="video", codec="h264"),
        make_track(stream_index=1, track_type="audio", codec="aac",
                   language="jpn", is_default=True),
    ]
    decision = analyze_file(make_file_info(), tracks, settings)
    assert decision.audio_language_mismatch is not None
    assert decision.audio_language_mismatch["language"] == "jpn"


def test_audio_language_override_resolves_mismatch(settings):
    """
    Once a user applies a correction via Audio Language Review, the
    override must both (a) actually get written as the track's new
    target_language, and (b) stop the file being flagged again — a
    previously-applied override shouldn't keep nagging.
    """
    tracks = [
        make_track(stream_index=0, track_type="video", codec="h264"),
        make_track(stream_index=1, track_type="audio", codec="aac",
                   language="dut", is_default=True),
    ]
    decision = analyze_file(
        make_file_info(), tracks, settings,
        audio_language_overrides={1: "eng"},
    )
    audio_action = next(a for a in decision.actions if a.track_type == "audio")
    assert audio_action.target_language == "eng"
    assert decision.audio_language_mismatch is None


def test_applied_override_does_not_reapply_once_already_correct(settings):
    """
    The test above already covers the override actually taking effect
    the first time. This covers the OTHER half of its own docstring's
    claim ("shouldn't keep nagging") that it never actually verified —
    audio_language_mismatch being None only confirms the file stops
    showing up in Audio Language Review, a different concern from
    whether the file stops being queued for reprocessing at all.

    Reproduces a real, reported bug: a file already corrected through
    Audio Language Review kept showing "Correct language tag on 1
    track" and getting reprocessed on every subsequent full scan,
    forever — because the override pass never checked whether the
    track's actual, current language already matched the override
    before reapplying it. Confirmed directly from production logs
    showing the exact same five files requeuing identically across two
    separate full scans, with the actual FFmpeg command re-running an
    already-successful language correction each time.
    """
    tracks = [
        make_track(stream_index=0, track_type="video", codec="h264"),
        # Track's language is ALREADY "eng" — simulating the file AFTER
        # a previous, successful correction, not before one.
        make_track(stream_index=1, track_type="audio", codec="aac",
                   language="eng", is_default=True),
    ]
    decision = analyze_file(
        make_file_info(), tracks, settings,
        audio_language_overrides={1: "eng"},
    )
    assert decision.should_process is False, (
        "Override kept reapplying even though the track is already "
        "correct — this is the exact reported 'requeues forever' bug."
    )


# ═══════════════════════════════════════════════════════════════════════════
# und_audio_threshold — the zero-value trap
# ═══════════════════════════════════════════════════════════════════════════

def test_und_threshold_zero_does_not_flag_clean_files(settings):
    """
    Real incident: clearing the threshold input field (even briefly)
    silently saved 0. Since "count of undefined tracks >= 0" is true for
    EVERY file including ones with zero undefined tracks, this put the
    entire pipeline into manual review. The fix clamps to a minimum of 1
    — this test uses a file with ZERO undefined tracks specifically,
    since that's the only input that actually distinguishes "clamped to
    1" from "still 0": with a genuine 0-track file, >=0 and >=1 disagree,
    while a 1-track file would pass either way and wouldn't catch a
    regression.
    """
    settings["und_audio_threshold"] = 0
    tracks = [
        make_track(stream_index=0, track_type="video", codec="h264"),
        make_track(stream_index=1, track_type="audio", codec="aac",
                   language="eng", is_default=True),
    ]
    decision = analyze_file(make_file_info(), tracks, settings)
    assert decision.is_manual_review is False, (
        "A file with zero undefined tracks was flagged for manual review "
        "— the threshold=0 trap has regressed."
    )


def test_multiple_undefined_audio_triggers_manual_review(settings):
    """The normal, working case the threshold exists for in the first place."""
    settings["und_audio_threshold"] = 2
    tracks = [
        make_track(stream_index=0, track_type="video", codec="h264"),
        make_track(stream_index=1, track_type="audio", codec="aac", language="und"),
        make_track(stream_index=2, track_type="audio", codec="aac", language="und"),
    ]
    decision = analyze_file(make_file_info(), tracks, settings)
    assert decision.is_manual_review is True


# ═══════════════════════════════════════════════════════════════════════════
# Container conversion
# ═══════════════════════════════════════════════════════════════════════════

def test_container_conversion_mkv_to_mp4(settings):
    """The straightforward, working conversion case."""
    tracks = [
        make_track(stream_index=0, track_type="video", codec="h264"),
        make_track(stream_index=1, track_type="audio", codec="aac", language="eng"),
    ]
    file_info = make_file_info(
        path="/media/movies/Test.mkv", container="mkv", video_codec="h264",
    )
    decision = analyze_file(file_info, tracks, settings)
    assert decision.target_container == "mp4"
    assert decision.output_extension == ".mp4"
    assert any(a.action_type == "change_container" for a in decision.actions)


def test_no_conversion_when_video_codec_incompatible(settings):
    """
    A video codec MP4 can't hold (confirmed directly against
    MP4_COMPATIBLE_VIDEO, not assumed) must block the conversion —
    converting anyway would mean transcoding video, which this feature
    has never done and shouldn't start doing silently.
    """
    assert "vp9" not in MP4_COMPATIBLE_VIDEO  # guards the test itself against drift
    tracks = [
        make_track(stream_index=0, track_type="video", codec="vp9"),
        make_track(stream_index=1, track_type="audio", codec="aac", language="eng"),
    ]
    file_info = make_file_info(
        path="/media/movies/Test.mkv", container="mkv", video_codec="vp9",
    )
    decision = analyze_file(file_info, tracks, settings)
    assert not any(a.action_type == "change_container" for a in decision.actions), (
        "A container conversion was attempted despite an MP4-incompatible "
        "video codec — this would mean transcoding video, which this "
        "feature has never done."
    )


def test_unknown_container_raises_rather_than_guessing(settings):
    """
    Real incident: current_container = (file_info.get("container") or
    "mkv") silently treated "I don't know the container" identically to
    "I'm sure it's MKV". A video file that was genuinely MP4 came back
    written as Matroska after this fired. Now raises instead of
    guessing — this test locks that in.
    """
    tracks = [make_track(stream_index=0, track_type="video", codec="h264")]
    file_info = make_file_info(container=None)
    with pytest.raises(ValueError):
        analyze_file(file_info, tracks, settings)


# ═══════════════════════════════════════════════════════════════════════════
# Undefined language fix (the bulk pass, distinct from the override pass)
# ═══════════════════════════════════════════════════════════════════════════

def test_fix_undefined_language_writes_target_language(settings):
    """The core, original feature this whole area of the code exists for."""
    settings["fix_undefined_language"] = True
    settings["undefined_language_value"] = "eng"
    tracks = [
        make_track(stream_index=0, track_type="video", codec="h264"),
        make_track(stream_index=1, track_type="audio", codec="aac", language="und"),
    ]
    decision = analyze_file(make_file_info(), tracks, settings)
    audio_action = next(a for a in decision.actions if a.track_type == "audio")
    assert audio_action.target_language == "eng"


def test_reason_text_distinguishes_undefined_from_wrong(settings):
    """
    Confirmed-fixed cosmetic bug, kept as a regression guard: the reason
    string must say something different for "fixed a blank tag" versus
    "corrected a wrong tag" — they were sharing one counter and one
    message at one point in this project's history.
    """
    settings["fix_undefined_language"] = True
    tracks = [
        make_track(stream_index=0, track_type="video", codec="h264"),
        make_track(stream_index=1, track_type="audio", codec="aac",
                   language="dut", is_default=True),
    ]
    decision = analyze_file(
        make_file_info(), tracks, settings,
        audio_language_overrides={1: "eng"},
    )
    assert "undefined" not in decision.reason.lower(), (
        "An override-driven correction is describing itself as fixing an "
        "'undefined' tag — the track was 'dut', not blank."
    )


# ═══════════════════════════════════════════════════════════════════════════
# image_subtitle_handling — always_keep / always_remove / always_ask
# ═══════════════════════════════════════════════════════════════════════════

def _image_sub_tracks():
    return [
        make_track(stream_index=0, track_type="video", codec="h264"),
        # Deliberately "und" with fix_undefined_language on — gives the file
        # something else that genuinely needs processing, so it doesn't
        # take the early "nothing to do at all" exit path (which discards
        # the actions list entirely) before the subtitle action can be
        # inspected below.
        make_track(stream_index=1, track_type="audio", codec="aac", language="und"),
        make_track(stream_index=2, track_type="subtitle", codec="hdmv_pgs_subtitle",
                   language="eng"),
    ]


def test_image_subtitle_always_ask_is_the_default(settings):
    """
    The setting must default to the exact existing behavior for any
    install that hasn't touched it — a KEPT image-based subtitle with
    extraction enabled still halts for manual review, unchanged.
    """
    settings["extract_text_subtitles_to_srt"] = True
    # Deliberately NOT setting image_subtitle_handling — confirms the
    # settings.get(..., "always_ask") fallback itself, not just the
    # explicit value.
    decision = analyze_file(make_file_info(), _image_sub_tracks(), settings)
    assert decision.is_manual_review is True
    assert decision.flagged_subtitles is not None
    assert decision.flagged_subtitles[0]["stream_index"] == 2


def test_image_subtitle_always_keep_resolves_automatically(settings):
    """
    The actual feature request this exists for: 205 items sitting in
    manual review, and a way to set a policy once instead of clicking
    through each one. always_keep must resolve without ever touching
    is_manual_review, and the track must survive as a copy, not a drop.
    """
    settings["extract_text_subtitles_to_srt"] = True
    settings["image_subtitle_handling"] = "always_keep"
    settings["fix_undefined_language"] = True  # gives the file something
                                                # else to actually do
    decision = analyze_file(make_file_info(), _image_sub_tracks(), settings)

    assert decision.is_manual_review is False
    assert decision.flagged_subtitles is None
    sub_action = next(a for a in decision.actions if a.stream_index == 2)
    assert sub_action.action_type == "copy_track"


def test_image_subtitle_always_remove_resolves_automatically(settings):
    """Same as above, but for the remove policy — the track must be dropped."""
    settings["extract_text_subtitles_to_srt"] = True
    settings["image_subtitle_handling"] = "always_remove"
    settings["fix_undefined_language"] = True
    decision = analyze_file(make_file_info(), _image_sub_tracks(), settings)

    assert decision.is_manual_review is False
    assert decision.flagged_subtitles is None
    sub_action = next(a for a in decision.actions if a.stream_index == 2)
    assert sub_action.action_type == "drop_track"


def test_image_subtitle_setting_does_not_affect_text_based_subs(settings):
    """
    Sanity check: this setting only governs image-based codecs specifically
    — a normal, SRT-convertible text subtitle must be completely unaffected
    regardless of this setting's value, since the gate this setting
    controls never applies to it in the first place.
    """
    settings["extract_text_subtitles_to_srt"] = True
    settings["image_subtitle_handling"] = "always_remove"
    tracks = [
        make_track(stream_index=0, track_type="video", codec="h264"),
        make_track(stream_index=1, track_type="audio", codec="aac", language="eng"),
        make_track(stream_index=2, track_type="subtitle", codec="subrip", language="eng"),
    ]
    decision = analyze_file(make_file_info(), tracks, settings)
    assert decision.is_manual_review is False
    sub_action = next(a for a in decision.actions if a.stream_index == 2)
    assert sub_action.action_type == "extract_subtitle", (
        "always_remove for image-based subs incorrectly affected a "
        "text-based, SRT-convertible track — it should have been "
        "extracted normally, completely untouched by this setting."
    )
