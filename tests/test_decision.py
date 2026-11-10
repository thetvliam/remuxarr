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
import logging


import pytest
from app.core.decision import analyze_file, MP4_COMPATIBLE_VIDEO
from conftest import make_track, make_file_info


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


def test_und_only_file_does_not_trigger_absolute_fallback(settings, caplog):
    """
    The absolute fallback's guard used to test "no preferred-language track
    and no default-flagged track", which is not what should_keep actually
    does: should_keep ALWAYS retains "und" audio. So a file whose only
    audio track was undefined tripped the fallback and logged a warning
    saying its language tag was "likely wrong".

    The tag was not wrong, it was absent — an ordinary condition with its
    own handling (the Fix Undefined Language settings, Audio Language
    Review). Undefined-language audio is by far the most common
    reason to reach this point, so the false alarm fired constantly, which
    is precisely what teaches people to ignore a warning that does matter
    on the rare occasions it fires for real.

    The track must still survive — just via the "und" rule, silently.
    """
    tracks = [
        make_track(stream_index=0, track_type="video", codec="hevc"),
        make_track(stream_index=1, track_type="audio", codec="aac",
                   language="und", is_default=False),
    ]
    with caplog.at_level(logging.WARNING, logger="app.core.decision"):
        decision = analyze_file(make_file_info(), tracks, settings)

    dropped_audio = [
        a for a in decision.actions
        if a.track_type == "audio" and a.action_type == "drop_track"
    ]
    assert not dropped_audio, "The und track should be kept by the und rule."
    assert not any("No usable audio track" in r.getMessage() for r in caplog.records), (
        "The absolute fallback warned about an undefined-language file. "
        "That tier is for tracks with a DEFINED but non-preferred language; "
        "und audio is kept by should_keep's own rule and needs no rescue."
    )


def test_default_flagged_track_survives_when_keep_default_audio_disabled(settings):
    """
    The mirror image of the und bug, from the same drifted guard, and the
    more serious of the two: a silent output file.

    The guard tested "not has_default_audio" without asking whether
    keep_default_audio was actually enabled. With that setting off, a lone
    non-preferred track carrying the default flag satisfied the guard — so
    the absolute fallback declined to fire — while also failing
    should_keep's keep_default_audio term, so it was dropped. Nothing kept
    it, and the output had zero audio tracks.

    That is the exact outcome this whole safety net exists to prevent, and
    turning OFF a setting called "keep default audio" should never be able
    to cause it: the setting scopes which track is preferred, not whether
    the file is allowed to end up silent.
    """
    settings["keep_default_audio"] = False
    tracks = [
        make_track(stream_index=0, track_type="video", codec="hevc"),
        make_track(stream_index=1, track_type="audio", codec="aac",
                   language="dan", is_default=True),
    ]
    decision = analyze_file(make_file_info(), tracks, settings)

    dropped_audio = [
        a for a in decision.actions
        if a.track_type == "audio" and a.action_type == "drop_track"
    ]
    assert not dropped_audio, (
        "The only audio track was dropped with keep_default_audio off — "
        "a silent output file."
    )


def test_absolute_fallback_warning_names_the_file_and_the_tag(settings, caplog):
    """
    The warning used to identify the file only by stream index, which is
    the one detail that is useless on its own — every file has a stream 1.
    Anyone hitting this in a log had no way to tell which of thousands of
    files it referred to.

    It must name the path and the offending language tag, since those are
    what the reader has to act on, and point at Audio Language Review,
    where the file is already queued and can be fixed in bulk.
    """
    tracks = [
        make_track(stream_index=0, track_type="video", codec="hevc"),
        make_track(stream_index=1, track_type="audio", codec="aac",
                   language="dan", is_default=False),
    ]
    info = make_file_info()
    with caplog.at_level(logging.WARNING, logger="app.core.decision"):
        analyze_file(info, tracks, settings)

    warnings = [r.getMessage() for r in caplog.records
                if "No usable audio track" in r.getMessage()]
    assert warnings, "The absolute fallback fired but logged nothing."
    msg = warnings[0]
    assert info["path"] in msg, "Warning does not name the file."
    assert "'dan'" in msg, "Warning does not name the offending language tag."
    assert "Audio Language Review" in msg, (
        "Warning does not point at the workflow that already exists for this."
    )


def test_a_track_with_a_language_waiting_is_not_flagged_again(settings):
    """
    Set language re-evaluates the file straight away, before the job writes
    the new tag, so the track still reads "und". Without the fix pass's skip
    for a track with a language waiting, the track just answered is flagged
    again at once under Ask, and the answer looks like it did nothing.

    It was written for a skip of the same kind in the retired threshold,
    where dropping it survived the whole suite.
    """
    settings["fix_undefined_language_audio"] = "always_ask"
    tracks = [
        make_track(stream_index=0, track_type="video", codec="h264"),
        make_track(stream_index=1, track_type="audio", codec="aac", language="und"),
        make_track(stream_index=2, track_type="audio", codec="aac", language="und"),
    ]

    decision = analyze_file(make_file_info(), tracks, settings,
                            audio_language_overrides={1: "eng"})

    assert [f["stream_index"] for f in decision.undefined_audio_flags] == [2]


@pytest.mark.parametrize("acknowledged", [False, True], ids=["asked", "confirmed"])
def test_always_fix_does_not_guess_between_two_untagged_tracks(settings, acknowledged):
    """
    With several undefined tracks, tagging them all with the primary
    language is as likely to be wrong as right, so Fix tags only a type's
    single untagged track and leaves these alone.

    Confirming the tracks does not release the guess either. Confirm
    correct says the tags are right as they are, and tagging them
    afterwards would contradict the answer.

    This was first written against the retired Undefined Audio Track
    Threshold, where dropping its skip survived the whole suite: both
    tracks came back tagged "eng", whichever way the file was answered.
    """
    settings["fix_undefined_language_audio"] = "always_fix"
    tracks = [
        make_track(stream_index=0, track_type="video", codec="h264"),
        make_track(stream_index=1, track_type="audio", codec="aac", language="und"),
        make_track(stream_index=2, track_type="audio", codec="aac", language="und"),
    ]
    # An MKV, so the container conversion gives the file something to do:
    # a plan with nothing in it is discarded whole, and the audio actions
    # this test reads would go with it.
    file_info = make_file_info(container="mkv", video_codec="h264")
    file_info["und_audio_threshold_acknowledged"] = acknowledged

    decision = analyze_file(file_info, tracks, settings)

    audio_actions = [a for a in decision.actions if a.track_type == "audio"]
    assert len(audio_actions) == 2, "fixture: both audio tracks should be in the plan"
    assert [a.target_language for a in audio_actions] == [None, None]


# The reason string the threshold used to hold files with is gone, and with
# it the two tests that pinned its grammar and its naming of the setting.
# The threshold itself has since been retired.
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


def test_already_faststart_source_preserves_flag_on_unrelated_remux(settings):
    """
    Reproduces a real, reported bug: after correcting a language tag
    through Audio Language Review, a subsequent full scan flagged the
    SAME file as needing "Add fast start" — even though it was already
    faststart-optimised before the correction ran.

    Root cause wasn't in decision-making at all: a plain FFmpeg remux
    that doesn't explicitly include -movflags +faststart silently
    rebuilds the container with the moov atom at the end, regardless of
    where it was in the source — confirmed directly, empirically,
    against real FFmpeg output, even for a pure lossless stream-copy
    with nothing re-encoded. So a language-only correction (which
    legitimately has nothing else to do, and correctly never generates
    its own add_faststart action) would silently undo an
    already-correct file's optimisation as a side effect — only for a
    LATER scan to genuinely, correctly detect faststart is now missing
    and re-add it. Not a decision-logic bug on its own; a real
    regression in the file produced by an earlier, unrelated job.

    ProcessingDecision.source_already_faststart is what closes this —
    build_ffmpeg_command must re-assert +faststart on ANY remux of a
    source that already had it, not just ones that specifically needed
    to add it or convert containers.
    """
    tracks = [
        make_track(stream_index=0, track_type="video", codec="h264"),
        make_track(stream_index=1, track_type="audio", codec="aac",
                   language="jpn", is_default=True),
    ]
    decision = analyze_file(
        make_file_info(), tracks, settings,
        audio_language_overrides={1: "eng"},
        has_faststart=True,  # the source already has it
    )
    assert decision.should_process is True
    assert "Correct language tag" in decision.reason
    assert not any(a.action_type == "add_faststart" for a in decision.actions), (
        "This should need nothing NEW for faststart — the point is "
        "preserving what's already there, not re-adding it as its own action."
    )
    assert decision.source_already_faststart is True


# ═══════════════════════════════════════════════════════════════════════════
# Several untagged audio tracks
#
# The Undefined Audio Track Threshold, retired, flagged these whatever the
# mode, and its zero-value trap (a cleared input saved 0, and ">= 0" held
# every file) went with it; SettingInput still clamps every integer setting
# to its minimum, which the frontend tests pin.
#
# Verified by mutation when the threshold went, each mutant surviving the
# suite as it stood (2268 tests, with the 26 that pinned the threshold
# deselected) first:
#
#   • Leave flagging untagged audio, as the
#     threshold did                         → the Leave test below
#   • the flags raised as "mismatch", as
#     Always Ask's were                     → the flagged-not-held test
#   • the acknowledgement ignored           → test_ask_again's Confirm
#                                             correct test, and the
#                                             acknowledged test further down
#   • the acknowledgement also stopping
#     Fix's tag on a lone track             → the asking-not-tagging test
#                                             further down
#
# The forge copy counted as an untagged track was already killed, by the
# forge-copy test in the Fix-rule section, and is not claimed here.
# ═══════════════════════════════════════════════════════════════════════════

@pytest.mark.parametrize("mode", ["always_fix", "always_ask"])
def test_multiple_undefined_audio_is_flagged_rather_than_held(settings, mode):
    """
    Two untagged tracks: Fix will not guess between them, and Ask never
    tags, so each is flagged for Audio Language Review and the file goes on
    being processed. Undefined audio is kept either way, so holding the
    file would change nothing about what the job does.

    Putting the hold back, and flagging only the lowest track, each
    survived the whole suite before this test existed.
    """
    settings["fix_undefined_language_audio"] = mode
    tracks = [
        make_track(stream_index=0, track_type="video", codec="h264"),
        make_track(stream_index=1, track_type="audio", codec="aac", language="und"),
        make_track(stream_index=2, track_type="audio", codec="aac", language="und"),
    ]
    decision = analyze_file(make_file_info(), tracks, settings)

    assert decision.is_manual_review is False
    assert decision.undefined_audio_flags == [
        {"stream_index": 1, "origin": "undefined"},
        {"stream_index": 2, "origin": "undefined"},
    ]


def test_leave_asks_nothing_about_several_untagged_tracks(settings):
    """
    Leave means the tags are left as they are and nothing is asked. The
    threshold used to flag these under Leave as well, which is the one
    behaviour removing it changes for an existing install.
    """
    settings["fix_undefined_language_audio"] = "always_leave"
    tracks = [
        make_track(stream_index=0, track_type="video", codec="h264"),
        make_track(stream_index=1, track_type="audio", codec="aac", language="und"),
        make_track(stream_index=2, track_type="audio", codec="aac", language="und"),
        make_track(stream_index=3, track_type="audio", codec="aac", language="und"),
    ]
    decision = analyze_file(make_file_info(), tracks, settings)

    assert decision.undefined_audio_flags == []
    assert decision.is_manual_review is False
    assert [a.stream_index for a in decision.actions
            if a.track_type == "audio" and a.action_type == "drop_track"] == []


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
    # No output_extension assertion — the field was removed (it was
    # derivable from target_container + the change_container action, and
    # carrying both is what caused real silent renames; see
    # determine_output_path). Path derivation is covered in
    # test_ffmpeg_command.py.
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
# A container Remuxarr cannot write
# ═══════════════════════════════════════════════════════════════════════════
#
# An FLV kept as FLV (Prefer MP4 Container off) was queued for a language fix,
# the job failed in the command builder, which refuses a container it has no
# muxer for, and every full scan queued it again. FLV stores no language
# tag, so a muxer would only have turned that into a job that never sticks.
# The decision now leaves such a file alone with a reason, and asks nothing.
#
# Verified by mutation, 3 applied, 3 killed, each run first against the
# whole suite as it stood, which all three survived:
#
#   • the check removed                      → left alone with Prefer MP4
#                                              off, tracks MP4 cannot hold,
#                                              asks nothing
#   • the two reasons swapped                → the same first two
#   • its Language Review questions kept     → asks nothing

def _flv(*audio, video="h264"):
    """An FLV with the given (codec, language) audio tracks."""
    tracks = [make_track(stream_index=0, track_type="video", codec=video)]
    for i, (codec, language) in enumerate(audio, start=1):
        tracks.append(make_track(stream_index=i, track_type="audio", codec=codec,
                                 language=language, is_default=(i == 1)))
    file_info = make_file_info(path="/media/x/clip.flv", container="flv",
                               video_codec=video)
    return file_info, tracks


def test_a_file_remuxarr_cannot_write_is_left_alone(settings):
    """An FLV that needs an untagged track tagged, with Prefer MP4 Container
    off: no job, and the reason says what would change that."""
    settings.update(prefer_mp4_container=False, fix_undefined_language_audio="always_fix")
    file_info, tracks = _flv(("aac", "und"))

    decision = analyze_file(file_info, tracks, settings)

    assert decision.should_process is False
    assert decision.actions == []
    assert decision.reason == ("Remuxarr cannot write FLV files, so this one is left as "
                               "it is. Turn on Prefer MP4 Container to convert it.")


def test_one_whose_tracks_mp4_cannot_hold_is_left_alone_too(settings):
    """Prefer MP4 Container on, but the video cannot go into MP4: the reason
    says so instead of pointing at the setting."""
    settings.update(prefer_mp4_container=True)
    file_info, tracks = _flv(("mp3", "eng"), ("mp3", "fre"), video="flv1")

    decision = analyze_file(file_info, tracks, settings)

    assert decision.should_process is False
    assert decision.actions == []
    assert decision.reason == ("Remuxarr cannot write FLV files, and this one's tracks "
                               "cannot go into MP4, so it is left as it is.")


def test_one_that_can_go_into_mp4_is_still_converted(settings):
    settings.update(prefer_mp4_container=True)
    file_info, tracks = _flv(("aac", "eng"))

    decision = analyze_file(file_info, tracks, settings)

    assert decision.should_process is True
    assert decision.target_container == "mp4"
    assert any(a.action_type == "change_container" for a in decision.actions)


def test_one_that_needs_nothing_says_so(settings):
    """Nothing to do is still the reason when there is nothing to do."""
    settings.update(prefer_mp4_container=False)
    file_info, tracks = _flv(("aac", "eng"))

    decision = analyze_file(file_info, tracks, settings)

    assert decision.should_process is False
    assert decision.reason.startswith("File already meets all configured criteria")


def test_a_file_left_alone_asks_nothing(settings):
    """
    Under Always Ask an untagged track is a Language Review question, and a
    language outside the keep list another. Either answer could only come
    back to this decision, so neither is asked.
    """
    settings.update(prefer_mp4_container=False, fix_undefined_language_audio="always_ask",
                    keep_audio_languages=["eng"])
    for audio in (("aac", "und"), ("aac", "jpn")):
        file_info, tracks = _flv(audio)

        decision = analyze_file(file_info, tracks, settings)

        assert decision.should_process is False
        assert decision.undefined_audio_flags == [], audio
        assert decision.audio_language_mismatch is None, audio


# ═══════════════════════════════════════════════════════════════════════════
# Undefined language fix (the bulk pass, distinct from the override pass)
# ═══════════════════════════════════════════════════════════════════════════

def test_an_undefined_subtitle_is_kept_when_undefined_means_a_kept_language(settings):
    """
    Reported from a real library. fix_undefined_language = always_fix with
    undefined_language_value = eng says "an untagged track is English", and
    keep_subtitle_languages is ["eng"] — so the track should survive.

    It did not. Keep/drop ran first and dropped every und subtitle for not
    being in the keep list; the fix pass, which only ever looks at
    surviving tracks, then found nothing left to retag. The same file came
    out with its und AUDIO retagged to English and its und SUBTITLE
    deleted, which no setting asked for.
    """
    settings["fix_undefined_language"] = "always_fix"
    settings["undefined_language_value"] = "eng"
    settings["keep_subtitle_languages"] = ["eng"]
    settings["extract_text_subtitles_to_srt"] = False

    tracks = [
        make_track(stream_index=0, track_type="video", codec="h264"),
        make_track(stream_index=1, track_type="audio", codec="aac",
                   language="eng", is_default=True),
        make_track(stream_index=2, track_type="subtitle", codec="subrip",
                   language=None),
    ]
    decision = analyze_file(
        make_file_info(path="/m/Show.mkv", container="mkv", video_codec="h264"),
        tracks, settings)

    subtitle_actions = [a for a in decision.actions if a.track_type == "subtitle"]
    assert [a.action_type for a in subtitle_actions] == ["copy_track"]
    assert subtitle_actions[0].target_language == "eng", (
        "kept, but never retagged — the tag it was kept for is still missing"
    )


# Untagged subtitles are kept in every mode (decision._sub_is_kept), and
# Keep Undefined Subtitles, which used to switch that on, is retired. Three
# tests here pinned the old drop and were rewritten to the new outcome: this
# one, the mixed set under the per-type rule, and two untagged under
# single_per_type (the last two rewritten again since, for the Fix rule). The
# tests that set the retired switch lost that line.
#
# Verified by mutation, 4 applied 4 killed, each run against the full suite
# with the three rewritten tests and the sample-library snapshot deselected,
# as all four pinned the old drop. Untagged not kept (the old default) and
# kept only under Ask were already killed by the tests that used to turn the
# switch on, here and in test_ask_again, and are now killed by the rewritten
# ones and the snapshot as well. Keeping by the raw tag rather than the
# language after Fix was already killed by
# test_an_undefined_subtitle_is_not_kept_when_it_would_still_be_unwanted. The
# stored value left at startup survived, and is killed in
# test_schema_migration.


def test_an_undefined_subtitle_left_alone_stays_as_it_is(settings):
    """
    always_leave means the user has not said what und means, and the track
    is left as it is, so here the file needs nothing at all. It used to be
    dropped for matching nothing in the keep list, which made "leave"
    remove the subtitle a file most often has.
    """
    settings["fix_undefined_language"] = "always_leave"
    settings["keep_subtitle_languages"] = ["eng"]
    settings["extract_text_subtitles_to_srt"] = False

    tracks = [
        make_track(stream_index=0, track_type="video", codec="h264"),
        make_track(stream_index=1, track_type="audio", codec="aac",
                   language="eng", is_default=True),
        make_track(stream_index=2, track_type="subtitle", codec="subrip",
                   language=None),
    ]
    decision = analyze_file(
        make_file_info(path="/m/Show.mkv", container="mkv", video_codec="h264"),
        tracks, settings)

    assert not decision.should_process, decision.reason
    assert [a for a in decision.actions if a.track_type == "subtitle"] == []


def test_an_undefined_subtitle_is_not_kept_when_it_would_still_be_unwanted(settings):
    """
    Resolving und is not the same as keeping it. If undefined means German
    and German is not wanted, the track is dropped — on its resolved tag
    rather than on the absence of one.
    """
    settings["fix_undefined_language"] = "always_fix"
    settings["undefined_language_value"] = "ger"
    settings["keep_subtitle_languages"] = ["eng"]
    settings["keep_forced_subtitles"] = False
    settings["extract_text_subtitles_to_srt"] = False

    tracks = [
        make_track(stream_index=0, track_type="video", codec="h264"),
        make_track(stream_index=1, track_type="audio", codec="aac",
                   language="eng", is_default=True),
        make_track(stream_index=2, track_type="subtitle", codec="subrip",
                   language=None),
    ]
    decision = analyze_file(
        make_file_info(path="/m/Show.mkv", container="mkv", video_codec="h264"),
        tracks, settings)

    subtitle_actions = [a for a in decision.actions if a.track_type == "subtitle"]
    assert [a.action_type for a in subtitle_actions] == ["drop_track"]


def test_fix_tags_a_lone_untagged_subtitle_beside_a_tagged_one(settings):
    """
    Always Fix counts the untagged subtitles alone, so one untagged track
    beside a tagged one is the file's only one, and is tagged. The rule
    this replaces ("only when they are ALL undefined") also counted the
    tagged track, and a job that dropped it changed the answer on the next
    re-check.
    """
    settings["fix_undefined_language"] = "always_fix"
    settings["undefined_language_value"] = "eng"
    settings["keep_subtitle_languages"] = ["eng"]
    settings["extract_text_subtitles_to_srt"] = False

    tracks = [
        make_track(stream_index=0, track_type="video", codec="h264"),
        make_track(stream_index=1, track_type="audio", codec="aac",
                   language="eng", is_default=True),
        make_track(stream_index=2, track_type="subtitle", codec="subrip",
                   language="eng"),
        make_track(stream_index=3, track_type="subtitle", codec="subrip",
                   language=None),
    ]
    decision = analyze_file(
        make_file_info(path="/m/Show.mkv", container="mkv", video_codec="h264"),
        tracks, settings)

    tagged = {a.stream_index: a.target_language for a in decision.actions
              if a.track_type == "subtitle" and a.target_language}
    assert tagged == {3: "eng"}
    assert decision.subtitle_language_mismatches == []

def test_fix_sends_two_untagged_subtitles_to_review(settings):
    """
    Two untagged subtitles are genuinely ambiguous: tagging both with one
    language would be a guess. Neither is tagged, and both are sent to
    Subtitle Language Review. Always Fix used to leave such tracks as they
    were and say nothing; before that, they were dropped.
    """
    settings["fix_undefined_language"] = "always_fix"
    settings["undefined_language_value"] = "eng"
    settings["keep_subtitle_languages"] = ["eng"]
    settings["keep_forced_subtitles"] = False
    settings["extract_text_subtitles_to_srt"] = False

    tracks = [
        make_track(stream_index=0, track_type="video", codec="h264"),
        make_track(stream_index=1, track_type="audio", codec="aac",
                   language="eng", is_default=True),
        make_track(stream_index=2, track_type="subtitle", codec="subrip",
                   language=None),
        make_track(stream_index=3, track_type="subtitle", codec="subrip",
                   language=None),
    ]
    decision = analyze_file(
        make_file_info(path="/m/Show.mkv", container="mkv", video_codec="h264"),
        tracks, settings)

    assert not decision.should_process, decision.reason
    assert [a for a in decision.actions if a.track_type == "subtitle"] == []
    assert [m["stream_index"] for m in decision.subtitle_language_mismatches] == [2, 3]

def test_fix_tags_a_files_only_untagged_subtitle(settings):
    """The other half — one untagged subtitle is exactly the case it is for."""
    settings["fix_undefined_language"] = "always_fix"
    settings["undefined_language_value"] = "eng"
    settings["keep_subtitle_languages"] = ["eng"]
    settings["extract_text_subtitles_to_srt"] = False

    tracks = [
        make_track(stream_index=0, track_type="video", codec="h264"),
        make_track(stream_index=1, track_type="audio", codec="aac",
                   language="eng", is_default=True),
        make_track(stream_index=2, track_type="subtitle", codec="subrip",
                   language=None),
    ]
    decision = analyze_file(
        make_file_info(path="/m/Show.mkv", container="mkv", video_codec="h264"),
        tracks, settings)

    subtitle_actions = [a for a in decision.actions if a.track_type == "subtitle"]
    assert [a.action_type for a in subtitle_actions] == ["copy_track"]


def test_undefined_subtitles_can_be_kept_without_being_resolved(settings):
    """
    A user may want untagged subtitles without knowing what language to
    call them. The keep list cannot express that — it is about languages,
    and this track has not claimed one — so a track still undefined is kept,
    as the forced exemption keeps a forced one. Keep Undefined Subtitles
    used to switch this on; it is how every mode behaves now.
    """
    settings["fix_undefined_language"] = "always_leave"
    settings["keep_subtitle_languages"] = ["eng"]
    settings["extract_text_subtitles_to_srt"] = False

    tracks = [
        make_track(stream_index=0, track_type="video", codec="h264"),
        make_track(stream_index=1, track_type="audio", codec="aac",
                   language="eng", is_default=True),
        make_track(stream_index=2, track_type="subtitle", codec="subrip",
                   language=None),
    ]
    decision = analyze_file(
        make_file_info(path="/m/Show.mkv", container="mkv", video_codec="h264"),
        tracks, settings)

    dropped = [a for a in decision.actions
               if a.track_type == "subtitle" and a.action_type == "drop_track"]
    assert not dropped


def test_keeping_undefined_subtitles_does_not_keep_unwanted_languages(settings):
    """
    Keeping untagged subtitles is for the absence of a tag, not a way round
    the keep list. A subtitle tagged German is still dropped.
    """
    settings["keep_subtitle_languages"] = ["eng"]
    settings["keep_forced_subtitles"] = False
    settings["extract_text_subtitles_to_srt"] = False

    tracks = [
        make_track(stream_index=0, track_type="video", codec="h264"),
        make_track(stream_index=1, track_type="audio", codec="aac",
                   language="eng", is_default=True),
        make_track(stream_index=2, track_type="subtitle", codec="subrip",
                   language="ger"),
    ]
    decision = analyze_file(
        make_file_info(path="/m/Show.mkv", container="mkv", video_codec="h264"),
        tracks, settings)

    dropped = [a for a in decision.actions
               if a.track_type == "subtitle" and a.action_type == "drop_track"]
    assert len(dropped) == 1


def test_a_kept_undefined_subtitle_is_offered_for_review(settings):
    """
    The combination this exists to serve: keep the track, extract it, and
    ask what it is. Previously the track was dropped before anything could
    be asked about it, so always_ask never produced a subtitle review row
    at all — the question was moot because the subject was already gone.
    """
    settings["fix_undefined_language"] = "always_ask"
    settings["keep_subtitle_languages"] = ["eng"]
    settings["extract_text_subtitles_to_srt"] = True

    tracks = [
        make_track(stream_index=0, track_type="video", codec="h264"),
        make_track(stream_index=1, track_type="audio", codec="aac",
                   language="eng", is_default=True),
        make_track(stream_index=2, track_type="subtitle", codec="subrip",
                   language=None),
    ]
    decision = analyze_file(
        make_file_info(path="/m/Show.mkv", container="mkv", video_codec="h264"),
        tracks, settings)

    assert len(decision.subtitle_language_mismatches) == 1
    mismatch = decision.subtitle_language_mismatches[0]
    assert mismatch["stream_index"] == 2
    assert mismatch["language"] == "und"

    extracts = [a for a in decision.actions
                if a.action_type == "extract_subtitle"]
    assert len(extracts) == 1, "kept for review but never extracted"

    # The sidecar path travels with the flag, because by review time it
    # cannot be worked out: extraction takes the track out of the mux, so
    # the forced / SDH / dub suffixes that shaped the filename are no
    # longer readable from the file.
    assert mismatch["extracted_path"] == extracts[0].external_path


def test_an_extracted_undefined_subtitle_is_named_by_its_resolved_language(settings):
    """
    The sidecar carries the language in its filename permanently, and that
    is what Plex reads. Dropping the track meant no sidecar at all; keeping
    it without resolving would have written Show.und.srt.
    """
    settings["fix_undefined_language"] = "always_fix"
    settings["undefined_language_value"] = "eng"
    settings["keep_subtitle_languages"] = ["eng"]
    settings["extract_text_subtitles_to_srt"] = True

    tracks = [
        make_track(stream_index=0, track_type="video", codec="h264"),
        make_track(stream_index=1, track_type="audio", codec="aac",
                   language="eng", is_default=True),
        make_track(stream_index=2, track_type="subtitle", codec="subrip",
                   language=None),
    ]
    decision = analyze_file(
        make_file_info(path="/m/Show.mkv", container="mkv", video_codec="h264"),
        tracks, settings)

    extracts = [a for a in decision.actions
                if a.action_type == "extract_subtitle"]
    assert len(extracts) == 1
    assert ".und." not in extracts[0].external_path
    assert extracts[0].external_path.endswith(".en.srt"), (
        f"sidecar named {extracts[0].external_path!r}"
    )


def test_fix_undefined_language_writes_target_language(settings):
    """
    The core, original feature this whole area of the code exists for.

    Sets the audio key, since the track under test is audio. Still the
    legacy boolean rather than "always_fix" on purpose — that back-compat
    coercion has to keep working for the new key too, and this is the test
    that exercises it.
    """
    settings["fix_undefined_language_audio"] = True
    settings["undefined_language_value"] = "eng"
    tracks = [
        make_track(stream_index=0, track_type="video", codec="h264"),
        make_track(stream_index=1, track_type="audio", codec="aac", language="und"),
    ]
    decision = analyze_file(make_file_info(), tracks, settings)
    audio_action = next(a for a in decision.actions if a.track_type == "audio")
    assert audio_action.target_language == "eng"


# A Language Review answer that corrects a tag is named the same way. 3
# mutations applied, 3 killed, all three surviving the 2242-test suite
# before these: the track's current tag named in place of the answer (by
# the first two), the answer named on audio only (by the second), and the
# answer named on a track whose tag already matches it (by the third).

def test_the_plan_names_the_tag_an_audio_answer_corrects_to(settings):
    tracks = [
        make_track(stream_index=0, track_type="video", codec="h264"),
        make_track(stream_index=1, track_type="audio", codec="aac", language="dut"),
    ]
    settings["keep_audio_languages"] = ["eng", "dut"]

    d = analyze_file(make_file_info(), tracks, settings,
                     audio_language_overrides={1: "eng"})

    assert next(a for a in d.actions if a.stream_index == 1).description == (
        "Copy audio [dut] aac stereo (stream 1) — language tag set to eng")


def test_the_plan_names_the_tag_a_subtitle_answer_corrects_to(settings):
    settings["extract_text_subtitles_to_srt"] = False
    tracks = [
        make_track(stream_index=0, track_type="video", codec="h264"),
        make_track(stream_index=1, track_type="audio", codec="aac", language="eng"),
        make_track(stream_index=2, track_type="subtitle", codec="subrip", language="und"),
    ]

    d = analyze_file(make_file_info(path="/m/Film.mkv", container="mkv"), tracks,
                     settings, subtitle_language_overrides={2: "eng"})

    assert next(a for a in d.actions if a.stream_index == 2).description.endswith(
        " — language tag set to eng")


def test_an_answer_the_tag_already_matches_names_nothing(settings):
    """
    The pass skips it, as the correction already happened. The German track
    gives the file something else to do, so that the line exists.
    """
    settings["keep_audio_languages"] = ["eng"]
    tracks = [
        make_track(stream_index=0, track_type="video", codec="h264"),
        make_track(stream_index=1, track_type="audio", codec="aac", language="eng"),
        make_track(stream_index=2, track_type="audio", codec="aac", language="ger"),
    ]

    d = analyze_file(make_file_info(), tracks, settings,
                     audio_language_overrides={1: "eng"})

    kept = next(a for a in d.actions if a.stream_index == 1)
    assert kept.action_type == "copy_track"
    assert "language tag" not in kept.description


# ── Undefined-language mode, per track type ──────────────────────────────────
#
# fix_undefined_language used to cover audio and subtitles together, so
# "fix audio automatically but ask me about subtitles" was unexpressible.
# It is two settings now: fix_undefined_language_audio, and plain
# fix_undefined_language for subtitles.
#
# The unsuffixed key belongs to subtitles rather than audio deliberately.
# Every install that predates the split has one stored value, and whichever
# type moves to a new key inherits a fresh always_leave. On audio that
# costs a language tag; on subtitles always_leave means the track keeps its
# und tag, matches nothing in keep_subtitle_languages, and is dropped. The
# last test in this group is the one that pins that, and it is the reason
# the naming is asymmetric.

def _und_of(decision, track_type):
    """The action for the single und track of a type, whatever happened to it."""
    return next(a for a in decision.actions if a.track_type == track_type)


# The three below: 3 mutations applied, 3 killed, all three surviving the
# 2239-test suite before them. The tag named under Always ask (by the last),
# the track's old tag named in place of the new one (by the first two), and
# the tag named on audio only (by the second).

def test_the_plan_names_the_tag_an_undefined_audio_track_is_given(settings):
    """
    Reported from a real library: the reason said "Fix undefined language
    tag on 1 track" and the only line was "Copy audio [und] ...", which says
    what the track is now and not what it becomes. French here, so the
    line cannot pass by naming the default English.
    """
    settings["fix_undefined_language_audio"] = "always_fix"
    settings["undefined_language_value"] = "fre"
    tracks = [
        make_track(stream_index=0, track_type="video", codec="h264"),
        make_track(stream_index=1, track_type="audio", codec="aac", language="und"),
    ]

    d = analyze_file(make_file_info(), tracks, settings)

    assert next(a for a in d.actions if a.track_type == "audio").description == (
        "Copy audio [und] aac stereo (stream 1) — language tag set to fre")


def test_the_plan_names_the_tag_an_undefined_embedded_subtitle_is_given(settings):
    """Subtitles kept in the file go through the same pass as audio."""
    settings["fix_undefined_language"] = "always_fix"
    settings["undefined_language_value"] = "eng"
    settings["keep_subtitle_languages"] = ["eng"]
    settings["extract_text_subtitles_to_srt"] = False
    tracks = [
        make_track(stream_index=0, track_type="video", codec="h264"),
        make_track(stream_index=1, track_type="audio", codec="aac", language="eng"),
        make_track(stream_index=2, track_type="subtitle", codec="subrip", language="und"),
    ]

    d = analyze_file(make_file_info(path="/m/Film.mkv", container="mkv"), tracks, settings)

    assert _und_of(d, "subtitle").description.endswith(" — language tag set to eng")


def test_asking_about_an_undefined_track_names_no_tag(settings):
    """
    Always ask leaves the tag alone, so the plan promises none. The German
    track is there to be dropped: a file asked about and needing nothing
    else has no plan at all, and the line under test would not exist.
    """
    settings["fix_undefined_language_audio"] = "always_ask"
    settings["keep_audio_languages"] = ["eng"]
    tracks = [
        make_track(stream_index=0, track_type="video", codec="h264"),
        make_track(stream_index=1, track_type="audio", codec="aac", language="und"),
        make_track(stream_index=2, track_type="audio", codec="aac", language="ger"),
    ]

    d = analyze_file(make_file_info(), tracks, settings)

    undefined = next(a for a in d.actions if a.stream_index == 1)
    assert undefined.action_type == "copy_track"
    assert "language tag" not in undefined.description


def test_audio_can_be_fixed_while_subtitles_are_asked(settings):
    """
    The case the split exists for, and the one that was impossible before:
    tag undefined audio automatically, but hold undefined subtitles for a
    human. One setting could not say this.
    """
    settings["fix_undefined_language_audio"] = "always_fix"
    settings["fix_undefined_language"]       = "always_ask"
    settings["extract_text_subtitles_to_srt"] = False
    # always_ask does not resolve the tag, so the track reaches keep/drop
    # still reading "und". It used to be dropped there unless Keep Undefined
    # Subtitles was on, and was never asked about; a track still undefined
    # is always kept now.
    tracks = [
        make_track(stream_index=0, track_type="video", codec="h264"),
        make_track(stream_index=1, track_type="audio", codec="aac", language="und"),
        make_track(stream_index=2, track_type="subtitle", codec="subrip",
                   language="und"),
    ]

    d = analyze_file(make_file_info(), tracks, settings)

    # Audio: tagged outright.
    assert _und_of(d, "audio").target_language == "eng"
    # Subtitle: surfaced for review rather than tagged — always_ask
    # answering on the user's behalf would defeat the point of asking.
    assert [m["stream_index"] for m in d.subtitle_language_mismatches] == [2]
    assert not any(a.track_type == "subtitle" and a.target_language
                   for a in d.actions)


def test_subtitles_can_be_fixed_while_audio_is_left_alone(settings):
    """
    The reverse direction. Worth its own test rather than trusting symmetry:
    the two types are read at different points in analyze_file — subtitles
    additionally through the pre-pass that runs before keep/drop — so one
    direction working does not demonstrate the other.
    """
    settings["fix_undefined_language"]       = "always_fix"
    settings["fix_undefined_language_audio"] = "always_leave"
    settings["extract_text_subtitles_to_srt"] = False
    tracks = [
        make_track(stream_index=0, track_type="video", codec="h264"),
        make_track(stream_index=1, track_type="audio", codec="aac", language="und"),
        make_track(stream_index=2, track_type="subtitle", codec="subrip",
                   language="und"),
    ]

    d = analyze_file(make_file_info(), tracks, settings)

    assert _und_of(d, "subtitle").target_language == "eng"
    assert _und_of(d, "audio").target_language is None
    # Left alone means left alone — not quietly routed to review instead.
    assert d.audio_language_mismatch is None


def test_the_fix_rule_counts_each_type_on_its_own(settings):
    """
    One untagged audio track and two untagged subtitles: the audio track is
    its type's only one and is tagged, the subtitles are not and are sent to
    review. A count shared across types could not produce this split.
    """
    settings["fix_undefined_language_audio"]      = "always_fix"
    settings["fix_undefined_language"]            = "always_fix"
    settings["extract_text_subtitles_to_srt"]     = False
    tracks = [
        make_track(stream_index=0, track_type="video", codec="h264"),
        make_track(stream_index=1, track_type="audio", codec="aac", language="und"),
        make_track(stream_index=3, track_type="subtitle", codec="subrip",
                   language="und"),
        make_track(stream_index=4, track_type="subtitle", codec="subrip",
                   language="und"),
    ]

    d = analyze_file(make_file_info(), tracks, settings)

    tagged = {a.stream_index for a in d.actions if a.target_language == "eng"}
    assert tagged == {1}
    assert [m["stream_index"] for m in d.subtitle_language_mismatches] == [3, 4]

def test_an_upgraded_install_does_not_lose_its_subtitles():
    """
    The upgrade guard, and the reason the subtitle key is the unsuffixed one.

    Simulates an install that set the old combined setting to always_fix
    before the split: its stored value lands on the subtitle key, and the
    new audio key sits at its fresh default. The undefined subtitle must
    still survive.

    Built from DEFAULT_APP_SETTINGS rather than the test fixture on
    purpose — the claim is about what production defaults do to a real
    upgraded database, so reading them from the fixture would be testing a
    copy of the assumption instead of the thing itself.

    If the keys are ever renamed so that subtitles take the new suffixed
    key, this fails: the stored always_fix stops reaching the subtitle
    pre-pass, the und subtitle is judged on its raw und tag, matches
    nothing in keep_subtitle_languages, and is dropped from the output.
    """
    from app.database.session import DEFAULT_APP_SETTINGS
    from copy import deepcopy

    cfg = deepcopy(DEFAULT_APP_SETTINGS)
    cfg["fix_undefined_language"] = "always_fix"      # their pre-split choice
    cfg["extract_text_subtitles_to_srt"] = False      # keep it embedded so a
                                                      # drop is visible here
    tracks = [
        make_track(stream_index=0, track_type="video", codec="h264"),
        make_track(stream_index=1, track_type="audio", codec="aac", language="eng"),
        make_track(stream_index=2, track_type="subtitle", codec="subrip",
                   language="und"),
    ]

    d = analyze_file(make_file_info(), tracks, cfg)

    sub = _und_of(d, "subtitle")
    assert sub.action_type == "copy_track"
    assert sub.target_language == "eng"


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
        # Deliberately "und" with fix_undefined_language_audio on — gives
        # the file something else that genuinely needs processing, so it
        # doesn't take the early "nothing to do at all" exit path (which
        # discards the actions list entirely) before the subtitle action
        # can be inspected below.
        #
        # The AUDIO key specifically, since this track is audio. It used to
        # be the combined setting; once that split, the subtitle key left
        # this track untouched and the file had nothing to do.
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
    settings["fix_undefined_language_audio"] = True  # gives the file
                                                # something else to actually do
    decision = analyze_file(make_file_info(), _image_sub_tracks(), settings)

    assert decision.is_manual_review is False
    assert decision.flagged_subtitles is None
    sub_action = next(a for a in decision.actions if a.stream_index == 2)
    assert sub_action.action_type == "copy_track"


def test_image_subtitle_always_remove_resolves_automatically(settings):
    """Same as above, but for the remove policy — the track must be dropped."""
    settings["extract_text_subtitles_to_srt"] = True
    settings["image_subtitle_handling"] = "always_remove"
    settings["fix_undefined_language_audio"] = True
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


# An "extract" answer for a track the extraction branch cannot take. The
# resolve endpoint refuses one; these pin the decision engine's own handling,
# for an answer that reaches it anyway. Both gates treat ANY stored value as
# an answer, so without the filter above the image gate the PGS answer skipped
# the gate, and the extraction branch then took the track as though it were
# text. Removing the filter, moving it below the image gate, and testing it
# against IMAGE_BASED_SUBS instead of SRT_CONVERTIBLE_SUBS all survived the
# full suite before these existed; the first two are killed by the PGS test,
# the first and third by the webvtt one.

def test_an_extract_answer_on_an_image_track_is_asked_again(settings):
    """
    A bitmap has no text to extract. Silently keeping the track instead
    would answer a question the user did not answer, so the image gate asks
    it again.
    """
    decision = analyze_file(
        make_file_info(), _image_sub_tracks(), settings,
        subtitle_overrides={2: "extract"},
    )

    assert decision.is_manual_review is True
    assert decision.review_reason == "image_subtitles"
    assert [f["stream_index"] for f in decision.flagged_subtitles] == [2]


def test_an_extract_answer_on_a_text_codec_extraction_cannot_take_is_ignored(settings):
    """
    webvtt is text, but not in SRT_CONVERTIBLE_SUBS, so the extraction
    branch has never handled it. No gate asks about it either, so the answer
    is dropped and the track follows the normal rules — kept, and embedded.
    The Japanese audio track gives the file something to do, so the plan is
    not discarded as a no-op before it can be read.
    """
    tracks = [
        make_track(stream_index=0, track_type="video", codec="h264"),
        make_track(stream_index=1, track_type="audio", codec="aac", language="eng"),
        make_track(stream_index=2, track_type="audio", codec="aac", language="jpn"),
        make_track(stream_index=3, track_type="subtitle", codec="webvtt", language="eng"),
    ]

    decision = analyze_file(
        make_file_info(container="mkv"), tracks, settings,
        subtitle_overrides={3: "extract"},
    )

    assert not [a for a in decision.actions if a.action_type == "extract_subtitle"]
    sub_action = next(a for a in decision.actions if a.stream_index == 3)
    assert sub_action.action_type == "copy_track"


# ═══════════════════════════════════════════════════════════════════════════
# Title-derived forced/SDH flags — legacy DB rows (F-B4 regression)
# ═══════════════════════════════════════════════════════════════════════════

def test_forced_by_title_legacy_row_is_kept_not_dropped(settings):
    """
    Regression for a real bug: the
    title-regex fallback for Track rows scanned before probe-side forced
    detection existed only upgraded LOCAL variables inside the subtitle
    loop — but the keep/drop decision (_sub_is_kept) reads the track
    dict, and the manual-review gate runs before the loop entirely. So a
    legacy forced-by-title subtitle with a non-kept language was DROPPED
    despite keep_forced_subtitles=True, defeating the fallback's own
    stated purpose. Verified before the fix: this exact setup produced
    [('drop_track', 2)]. Flags are now normalised into upgraded copies
    before any gating code runs.
    """
    settings["extract_text_subtitles_to_srt"] = False
    tracks = [
        make_track(stream_index=0, track_type="video", codec="h264"),
        make_track(stream_index=1, track_type="audio", codec="aac",
                   language="eng", is_default=True),
        make_track(stream_index=2, track_type="subtitle", codec="subrip",
                   language="fre", is_forced=False, title="English (Forced)"),
    ]
    tracks_snapshot = [dict(t) for t in tracks]
    decision = analyze_file(make_file_info(), tracks, settings)

    # Before the fix this exact setup produced should_process=True with a
    # drop_track for stream 2. With the flag correctly upgraded, the
    # subtitle is kept — and since the fixture file is already MP4 with
    # nothing else wrong, the correct outcome is "no changes needed at
    # all". That no-op is the sharpest form of the regression assertion.
    assert not any(a.action_type == "drop_track" for a in decision.actions), (
        "Legacy forced-by-title subtitle was dropped — the title fallback "
        "isn't reaching the keep/drop decision."
    )
    assert decision.should_process is False
    # Purity: analyze_file must upgrade COPIES, never the caller's dicts.
    assert tracks == tracks_snapshot, "analyze_file mutated its caller's track dicts"


def test_forced_by_title_legacy_pgs_reaches_manual_review_gate(settings):
    """
    The manual-review gate calls _sub_is_kept before the subtitle loop
    runs at all, so it had the same blind spot: a legacy forced-by-title
    PGS track never even registered as "kept" and silently bypassed the
    image-subtitle gate. It must now land in manual review with the
    upgraded flag visible in the flagged payload.
    """
    settings["extract_text_subtitles_to_srt"] = True
    settings["image_subtitle_handling"] = "always_ask"
    tracks = [
        make_track(stream_index=0, track_type="video", codec="h264"),
        make_track(stream_index=1, track_type="audio", codec="aac",
                   language="eng", is_default=True),
        make_track(stream_index=2, track_type="subtitle",
                   codec="hdmv_pgs_subtitle", language="fre",
                   is_forced=False, title="Forced"),
    ]
    decision = analyze_file(make_file_info(), tracks, settings)
    assert decision.is_manual_review is True
    assert decision.flagged_subtitles
    assert decision.flagged_subtitles[0]["is_forced"] is True


def test_plain_non_forced_non_kept_subtitle_still_drops(settings):
    """No over-keeping: a genuinely non-forced, non-kept-language subtitle
    with an ordinary title must still be dropped."""
    settings["extract_text_subtitles_to_srt"] = False
    tracks = [
        make_track(stream_index=0, track_type="video", codec="h264"),
        make_track(stream_index=1, track_type="audio", codec="aac",
                   language="eng", is_default=True),
        make_track(stream_index=2, track_type="subtitle", codec="subrip",
                   language="fre", is_forced=False, title="Français"),
    ]
    decision = analyze_file(make_file_info(), tracks, settings)
    sub_actions = [a for a in decision.actions if a.track_type == "subtitle"]
    assert any(a.action_type == "drop_track" for a in sub_actions)


# ═══════════════════════════════════════════════════════════════════════════
# MP4 conversion vs kept TEXT subtitles
# ═══════════════════════════════════════════════════════════════════════════

def test_kept_subrip_blocks_mp4_conversion_when_extraction_disabled(settings):
    """
    Regression for a real bug, reproduced
    empirically with real FFmpeg: MP4's only native text subtitle format
    is mov_text — `-c:s copy` of a SubRip stream into `-f mp4` fails at
    header-write ("Could not find tag for codec subrip", exit 234,
    zero-byte output). MP4_INCOMPATIBLE_SUBS previously listed only
    image/ASS codecs, so a conversion keeping a SubRip track (reachable
    only when extract_text_subtitles_to_srt is disabled — extraction
    otherwise removes text subs from kept_subs before the check)
    produced a guaranteed-failing command on every attempt.

    Note the existing conversion+subtitle test above this section uses
    an already-MP4 file, which is exactly why this case was never
    exercised — flagged by the same review.
    """
    settings["extract_text_subtitles_to_srt"] = False
    tracks = [
        make_track(stream_index=0, track_type="video", codec="h264"),
        make_track(stream_index=1, track_type="audio", codec="aac",
                   language="eng", is_default=True),
        make_track(stream_index=2, track_type="subtitle", codec="subrip",
                   language="eng"),
    ]
    file_info = make_file_info(path="/media/x/show.mkv", container="mkv",
                                video_codec="h264")
    decision = analyze_file(file_info, tracks, settings)
    assert not any(a.action_type == "change_container" for a in decision.actions), (
        "Conversion generated with a kept SubRip track — this command is "
        "guaranteed to fail at FFmpeg header-write."
    )
    assert decision.target_container != "mp4"


def test_kept_subrip_does_not_block_conversion_when_extraction_enabled(settings):
    """Default config: extraction removes the text sub from kept_subs
    before the compatibility check, so conversion proceeds — this is the
    behavior that must NOT change."""
    settings["extract_text_subtitles_to_srt"] = True
    tracks = [
        make_track(stream_index=0, track_type="video", codec="h264"),
        make_track(stream_index=1, track_type="audio", codec="aac",
                   language="eng", is_default=True),
        make_track(stream_index=2, track_type="subtitle", codec="subrip",
                   language="eng"),
    ]
    file_info = make_file_info(path="/media/x/show.mkv", container="mkv",
                                video_codec="h264")
    decision = analyze_file(file_info, tracks, settings)
    assert any(a.action_type == "change_container" for a in decision.actions)
    assert any(a.action_type == "extract_subtitle" for a in decision.actions)


def test_kept_mov_text_still_allows_conversion(settings):
    """mov_text is MP4's own native text format and stream-copies fine —
    deliberately NOT in the blocklist."""
    settings["extract_text_subtitles_to_srt"] = False
    tracks = [
        make_track(stream_index=0, track_type="video", codec="h264"),
        make_track(stream_index=1, track_type="audio", codec="aac",
                   language="eng", is_default=True),
        make_track(stream_index=2, track_type="subtitle", codec="mov_text",
                   language="eng"),
    ]
    file_info = make_file_info(path="/media/x/show.mkv", container="mkv",
                                video_codec="h264")
    decision = analyze_file(file_info, tracks, settings)
    assert any(a.action_type == "change_container" for a in decision.actions)


# ═══════════════════════════════════════════════════════════════════════════
# Gaps found by an independent mutation audit (Phase 1)
#
# Each of these pins a mutation that survived the entire 662-test suite.
# ═══════════════════════════════════════════════════════════════════════════

def test_acknowledged_undefined_audio_raises_no_flags(settings):
    """
    The other half of the acknowledgement contract: Confirm correct sets
    the column, and this is what setting it is FOR. Without the
    acknowledgement the same tracks are flagged again on the next scan, and
    the answer the user gave is worth nothing.

    Audit ref: DEC-03 — dropping the `and not ...acknowledged` term
    survived, and dropping it from the flags survived again. Written for
    the retired threshold; the acknowledgement now answers the undefined
    flags Ask and Fix raise.
    """
    settings["fix_undefined_language_audio"] = "always_ask"
    tracks = [
        make_track(stream_index=0, track_type="video", codec="h264"),
        make_track(stream_index=1, track_type="audio", codec="aac", language="und"),
        make_track(stream_index=2, track_type="audio", codec="aac", language="und"),
        make_track(stream_index=3, track_type="audio", codec="aac", language="und"),
    ]
    file_info = make_file_info(path="/media/x/show.mkv", container="mkv",
                               video_codec="h264")

    flagged = analyze_file(file_info, tracks, settings)
    assert flagged.undefined_audio_flags, (
        "Ask flagged nothing — this test cannot prove the "
        "acknowledgement works if there is nothing to be exempt from"
    )

    exempt = analyze_file({**file_info, "und_audio_threshold_acknowledged": True},
                          tracks, settings)

    assert exempt.undefined_audio_flags == [], (
        "an acknowledged file was flagged anyway — confirming the tracks in "
        "the UI would be a no-op and they would come back forever"
    )


def test_the_acknowledgement_stops_the_asking_not_the_tagging(settings):
    """
    Confirm correct on a file's untagged audio says the tags are right as
    they are, and stops the questions. Always Fix tagging a lone untagged
    track in a Primary Language film is not a question, so it goes on:
    the answer was about the tracks Remuxarr asked about, and it asked
    about none here.
    """
    settings["fix_undefined_language_audio"] = "always_fix"
    tracks = [
        make_track(stream_index=0, track_type="video", codec="h264"),
        make_track(stream_index=1, track_type="audio", codec="aac", language="und"),
    ]
    file_info = make_file_info(path="/m/Film.mkv", container="mkv",
                               video_codec="h264")
    file_info["und_audio_threshold_acknowledged"] = True

    d = analyze_file(file_info, tracks, settings)

    assert {a.stream_index: a.target_language for a in d.actions
            if a.track_type == "audio"} == {1: "eng"}
    assert d.undefined_audio_flags == []


def test_a_confirmed_mismatch_does_not_answer_the_untagged_tracks(settings):
    """
    audio_language_ignored is the language-mismatch switch. The engine reads
    only the undefined-audio one for these flags, so a file confirmed on a
    mismatch is still asked about its untagged tracks.
    """
    settings["fix_undefined_language_audio"] = "always_ask"
    tracks = [
        make_track(stream_index=0, track_type="video", codec="h264"),
        make_track(stream_index=1, track_type="audio", codec="aac", language="und"),
        make_track(stream_index=2, track_type="audio", codec="aac", language="und"),
    ]
    file_info = make_file_info()
    file_info["audio_language_ignored"] = True

    d = analyze_file(file_info, tracks, settings)

    assert [f["stream_index"] for f in d.undefined_audio_flags] == [1, 2]


def test_a_default_flag_does_not_override_the_language_filter(settings):
    """
    The scenario decision.py's own comment names:

        Stream 1: Italian EAC3 (default)
        Stream 2: English EAC3

    Italian must not be kept merely for being default-flagged when English
    is already being retained. keep_default_audio is a safety net for files
    with no usable track, not an override of the language filter.

    The guard half of this pair was already pinned
    (test_default_flagged_track_survives_when_keep_default_audio_disabled);
    the keep half was not.

    Audit ref: DEC-05 — dropping `and not has_preferred_audio` survived.
    """
    tracks = [
        make_track(stream_index=0, track_type="video", codec="h264"),
        make_track(stream_index=1, track_type="audio", codec="eac3",
                   language="ita", is_default=True),
        make_track(stream_index=2, track_type="audio", codec="eac3",
                   language="eng"),
    ]
    file_info = make_file_info(path="/media/x/show.mkv", container="mkv",
                               video_codec="h264")

    decision = analyze_file(file_info, tracks, settings)

    dropped = {a.stream_index for a in decision.actions
               if a.track_type == "audio" and a.action_type == "drop_track"}

    assert 1 in dropped, (
        "the Italian default-flagged track was kept even though a "
        "preferred-language track was available — keep_default_audio "
        "overrode the language filter"
    )
    assert 2 not in dropped, "the English track was dropped"


def test_the_absolute_fallback_keeps_the_first_audio_track(settings):
    """
    The fallback's stated contract is "force-keeps the FIRST audio track by
    stream index". Every existing fallback test uses a single audio track,
    where first and last are the same track — so the choice was unpinned and
    a user would have got whichever the implementation happened to pick.

    Audit ref: DEC-08 — min() → max() survived.
    """
    tracks = [
        make_track(stream_index=0, track_type="video", codec="h264"),
        make_track(stream_index=1, track_type="audio", codec="aac", language="ita"),
        make_track(stream_index=2, track_type="audio", codec="aac", language="fre"),
        make_track(stream_index=3, track_type="audio", codec="aac", language="spa"),
    ]
    file_info = make_file_info(path="/media/x/show.mkv", container="mkv",
                               video_codec="h264")

    decision = analyze_file(file_info, tracks, settings)

    dropped = {a.stream_index for a in decision.actions
               if a.track_type == "audio" and a.action_type == "drop_track"}

    assert dropped == {2, 3}, (
        f"the fallback kept the wrong track — dropped {sorted(dropped)}, "
        f"expected the lowest stream_index (1) to be the survivor"
    )


# ── What else keeps a file out of MP4 ────────────────────────────────────────
#
# The Review page's outcome line has to tell two sentences apart: keeping
# this subtitle is what holds the file as MKV, and this file stays MKV
# whatever you choose. Deleting a track on a file whose audio MP4 cannot hold
# does nothing the card led anyone to expect.
#
# Three mutations of this survived the full suite before these tests existed:
# reporting nothing as a blocker, ignoring the audio and video check, and
# ignoring the prefer-MP4 setting.

def _mkv_with(settings, *, audio="aac", video="h264"):
    tracks = [
        make_track(stream_index=0, track_type="video", codec=video),
        make_track(stream_index=1, track_type="audio", codec=audio, language="eng"),
        make_track(stream_index=2, track_type="subtitle", codec="ass", language="eng"),
    ]
    return analyze_file(make_file_info(container="mkv", video_codec=video),
                        tracks, settings, subtitle_overrides={2: "remove"})


def test_a_file_whose_only_obstacle_is_its_subtitles_says_so(settings):
    """Nothing else in the way: deleting the flagged track really does convert it."""
    decision = _mkv_with(settings)

    assert decision.target_container == "mp4"
    assert decision.mp4_blocked_beyond_subtitles is False


@pytest.mark.parametrize("kwargs", [
    {"audio": "dts"},
    {"video": "vp9"},
], ids=["audio MP4 cannot hold", "video MP4 cannot hold"])
def test_a_codec_mp4_cannot_hold_blocks_it_whatever_the_answer(settings, kwargs):
    """
    The case the line exists for. Every flagged subtitle is deleted here and
    the file still stays MKV, so a card promising a conversion would be
    promising something the answer cannot deliver.
    """
    decision = _mkv_with(settings, **kwargs)

    assert decision.target_container != "mp4"
    assert decision.mp4_blocked_beyond_subtitles is True


def test_preferring_mkv_blocks_it_too(settings):
    """
    The same for the person who turned conversion off: their files stay MKV
    by choice, and no answer on this card changes that.
    """
    settings["prefer_mp4_container"] = False

    decision = _mkv_with(settings)

    assert decision.mp4_blocked_beyond_subtitles is True


# ── Always Fix: the one untagged track ───────────────────────────────────────
#
# Always Fix tags an untagged track only when it is the file's one untagged
# track of its type (decision._lone_undefined_track), and sends every other
# untagged track to Language Review. For audio, only when the film or series
# was made in Primary Language or its original language is not known:
# Primary Language is the only language Remuxarr writes on its own. The two
# Apply To settings this replaces are retired.
#
# Verified by mutation, 9 applied, 9 killed. Each was run against the full
# suite (2288 tests, with the four tests that pinned Apply To outcomes
# deselected) before these tests existed, and seven survived it:
#
#   • answered tracks not counted           → the answered-and-dropped test,
#                                             the real re-check run in
#                                             test_language_answer_during_job
#   • the Forge copy counted                → the Forge copy test
#   • the Forge copy not tagged along       → the Forge copy test
#   • audio ignoring the original language  → the foreign original test
#   • Fix not flagging what it does not tag → six tests: the two-subtitles,
#                                             per-type, foreign original and
#                                             answered tests here, the real
#                                             re-check run, and the Fix test
#                                             in test_subtitle_language_path
#   • the fix pass counting kept subtitles
#     only, not the pre-pass's count        → the answered-and-dropped test
#   • the retired settings left stored      → test_schema_migration
#
# Two were already killed: an unknown original language treated as foreign
# (five tests that tag a lone untagged audio track with no original
# language), and Ask flagging only a lone track (the stream-order tests in
# test_subtitle_language_path). Neither is claimed here.

def _fix_audio(settings, **file_info):
    settings["fix_undefined_language_audio"] = "always_fix"
    settings["undefined_language_value"] = "eng"
    info = make_file_info(path="/m/Film.mkv", container="mkv", video_codec="h264")
    info.update(file_info)
    return info


def _audio_tags(decision):
    return {a.stream_index: a.target_language for a in decision.actions
            if a.track_type == "audio" and a.target_language}


def test_fix_sends_a_lone_untagged_track_in_a_foreign_film_to_review(settings):
    """
    The film was made in Japanese. Its one untagged audio track is most
    likely Japanese, and Remuxarr writes no language but Primary Language
    on its own, so it asks.
    """
    info = _fix_audio(settings, original_language="jpn")
    tracks = [make_track(stream_index=0, track_type="video", codec="h264"),
              make_track(stream_index=1, track_type="audio", codec="aac", language="und")]

    d = analyze_file(info, tracks, settings)

    assert _audio_tags(d) == {}
    assert [f["stream_index"] for f in d.undefined_audio_flags] == [1]


@pytest.mark.parametrize("original", ["eng", None], ids=["primary", "unknown"])
def test_fix_tags_a_lone_untagged_track_in_a_film_in_primary_language(settings, original):
    info = _fix_audio(settings, original_language=original)
    tracks = [make_track(stream_index=0, track_type="video", codec="h264"),
              make_track(stream_index=1, track_type="audio", codec="aac", language="und")]

    d = analyze_file(info, tracks, settings)

    assert _audio_tags(d) == {1: "eng"}
    assert d.undefined_audio_flags == []


def test_an_answered_track_still_counts_once_its_answer_is_written(settings):
    """
    Two untagged subtitles; the user named the first French in Subtitle
    Language Review and the job wrote it. Keep Subtitle Languages (English)
    keeps it all the same, as an answered track is kept (decision.py,
    "Language Review answers"). The file had two untagged subtitles to ask
    about, and the second is still asked about rather than taken as
    English: the answer is stored against the track, so it is counted with
    it.
    """
    settings["fix_undefined_language"] = "always_fix"
    settings["undefined_language_value"] = "eng"
    settings["keep_subtitle_languages"] = ["eng"]
    settings["keep_forced_subtitles"] = False
    settings["extract_text_subtitles_to_srt"] = False
    tracks = [
        make_track(stream_index=0, track_type="video", codec="h264"),
        make_track(stream_index=1, track_type="audio", codec="aac", language="eng"),
        make_track(stream_index=2, track_type="subtitle", codec="subrip", language="fre"),
        make_track(stream_index=3, track_type="subtitle", codec="subrip", language=None),
    ]

    d = analyze_file(make_file_info(path="/m/Show.mkv", container="mkv", video_codec="h264"),
                     tracks, settings, subtitle_language_overrides={2: "fre"})

    assert ("drop_track", 2) not in [(a.action_type, a.stream_index) for a in d.actions]
    assert not [a for a in d.actions if a.track_type == "subtitle" and a.target_language]
    assert [m["stream_index"] for m in d.subtitle_language_mismatches] == [3]


def test_the_forge_copy_is_not_counted_and_takes_its_sources_tag(settings):
    """
    An untagged AAC 5.1 and the AC3 the forge built from it, which inherited
    its missing tag. The copy says nothing new about the file: the AAC is
    still its one untagged track, and both are tagged so the two copies of
    one piece of audio agree.
    """
    info = _fix_audio(settings)
    tracks = [
        make_track(stream_index=0, track_type="video", codec="h264"),
        make_track(stream_index=1, track_type="audio", codec="aac", language="und",
                   channels=6, channel_layout="5.1"),
        make_track(stream_index=2, track_type="audio", codec="ac3", language="und",
                   channels=6, channel_layout="5.1"),
    ]

    d = analyze_file(info, tracks, settings, forged_ac3_audio_index=1)

    assert _audio_tags(d) == {1: "eng", 2: "eng"}
    assert d.undefined_audio_flags == []
