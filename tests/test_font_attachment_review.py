"""
Embedded fonts: keep the file as MKV, convert it anyway, or ask.

A font is only in a media file because its styled subtitles reference it by
name, and only Matroska can hold one. Converting such a file to MP4 loses
both halves at once — the fonts go, and the ASS tracks are flattened to SRT,
so text that was positioned over a sign in the picture becomes a line at the
bottom of the screen with the sign still visible behind it. A "Signs and
Songs" track ends up worse off than if it had been dropped.

Nothing about that fails. The job succeeds, the output size looks right, and
the symptom arrives whenever someone next watches the episode. The reported
case was 24 episodes of one show, each carrying 17 fonts and two ASS tracks,
all converted before anyone noticed.

WHY always_keep IS ONE LINE OF POLICY
-------------------------------------
It injects "keep" overrides for the styled tracks and nothing else. That is
enough because of machinery that already existed:

  * a "keep" override emits copy_track and skips extraction, so the styling
    survives
  * the track then sits in kept_subs
  * ass and ssa are in MP4_INCOMPATIBLE_SUBS, so kept_subs makes
    subs_block_mp4 true
  * which makes the container gate decline to convert, and the file stays
    MKV, and the fonts survive

So there is no second assignment to target_container. The fast-start block
carries a comment asserting the container gate is its only assignment site,
and that assertion stays true. The audio rules live in a different section
and run untouched, which is the other half of the requirement: keeping the
styling must not mean keeping four audio tracks nobody wants.

Fourteen mutants, all confirmed surviving the full suite before this file
existed:

  the trigger   the font count ignored                          killed
                fired on any subtitle codec, not just styled    killed
                fired with no styled tracks present             killed
                already-resolved tracks asked about again       killed
  the policy    always_keep and always_remove swapped           killed
                always_keep not injecting the overrides         killed
                always_remove taking the review branch          killed
                the default resolving to something else         killed
  the outcome   the styled track extracted under always_keep    killed
                the container converted under always_keep       killed
                audio rules suppressed along with the rest      killed
  the review    should_process left true                        killed
                the flagged payload omitting the tracks         killed
                the reason not naming the font count            killed

Three more for the keep rule, added when the gate was found asking about
every styled track in the file, run against the full suite before their
tests existed. The rule inverted was already killed by eleven tests here and
elsewhere, every one of whose tracks is English. The other two survived:

  the keep rule the gate ignoring it                            killed
                a bare language check instead of the shared rule killed

Run from the project root:
    pytest tests/test_font_attachment_review.py -v
"""
import pytest

from app.core.decision import analyze_file
from tests.conftest import make_file_info, make_track


def _anime_tracks():
    """
    The reported file's shape: HEVC, English and Japanese stereo AAC, and
    two ASS tracks — one signs-and-songs, one full dialogue.
    """
    return [
        make_track(0, "video", codec="hevc", language="und"),
        make_track(1, "audio", codec="aac", language="eng", is_default=True),
        make_track(2, "audio", codec="aac", language="jpn"),
        make_track(3, "subtitle", codec="ass", language="eng",
                   is_default=True, title="Signs and Songs [Saiki]"),
        make_track(4, "subtitle", codec="ass", language="eng",
                   title="Full Subtitles [Saiki]"),
    ]


def _mkv(fonts=17):
    return make_file_info(
        path="/media/tv/Saiki K/Season 1/Saiki K. - S01E08.mkv",
        container="mkv", video_codec="hevc", font_attachments=fonts,
    )


def _actions(decision, action_type):
    return [a for a in decision.actions if a.action_type == action_type]


# ── always_ask, the default ───────────────────────────────────────────────────

def test_a_file_with_fonts_and_styled_subtitles_goes_to_review(settings):
    """
    Closes: the font count ignored, and should_process left true.

    The default is to ask, because the answer is a judgement about the
    content rather than something the pipeline can infer — a dialogue-only
    ASS track flattens to SRT perfectly well, and a signs track does not.
    """
    decision = analyze_file(_mkv(), _anime_tracks(), settings)

    assert decision.is_manual_review is True
    assert decision.should_process is False
    assert _actions(decision, "flag_manual_review")


def test_the_review_reason_names_the_font_count_and_the_tracks(settings):
    """
    Closes: the reason not naming the count, and the flagged payload
    omitting the tracks.

    17 fonts and two named tracks is the difference between a prompt
    someone can answer and one they dismiss. The titles are the whole
    signal here: "Signs and Songs" is the track that must not be
    flattened, and it says so itself.
    """
    decision = analyze_file(_mkv(fonts=17), _anime_tracks(), settings)

    assert "17 embedded fonts" in decision.reason
    assert "Signs and Songs [Saiki]" in decision.reason
    assert [f["stream_index"] for f in decision.flagged_subtitles] == [3, 4]


def test_the_default_is_to_ask_when_the_setting_is_absent(settings):
    """
    Closes: the default resolving to always_keep or always_remove.

    An install that has never seen this setting must not silently convert
    files it cannot convert losslessly, nor silently stop converting
    anything with a stray font in it.
    """
    settings.pop("font_attachment_handling", None)

    decision = analyze_file(_mkv(), _anime_tracks(), settings)

    assert decision.is_manual_review is True


# ── always_keep ───────────────────────────────────────────────────────────────

def test_always_keep_leaves_the_styled_tracks_embedded(settings):
    """
    Closes: always_keep not injecting the overrides, and the styled track
    extracted anyway.

    Extraction is what destroys the styling — an ASS track written out as
    SRT keeps the words and loses every position, colour and font
    reference. Keeping means keeping it in the mux, not converting it to a
    sidecar.
    """
    settings["font_attachment_handling"] = "always_keep"

    decision = analyze_file(_mkv(), _anime_tracks(), settings)

    assert decision.is_manual_review is False
    copied = {a.stream_index for a in _actions(decision, "copy_track")
              if a.track_type == "subtitle"}
    assert copied == {3, 4}
    assert _actions(decision, "extract_subtitle") == []


def test_always_keep_keeps_the_file_as_mkv(settings):
    """
    Closes: the container converted under always_keep.

    This is what actually saves the fonts — MP4 has no attachment stream,
    so any conversion loses them however the subtitles are handled. It
    follows from the kept ASS tracks rather than from a rule of its own:
    ass is in MP4_INCOMPATIBLE_SUBS, so a kept one blocks the conversion.
    """
    settings["font_attachment_handling"] = "always_keep"

    decision = analyze_file(_mkv(), _anime_tracks(), settings)

    assert decision.target_container == "mkv"
    assert _actions(decision, "change_container") == []


def test_always_keep_still_removes_unwanted_audio(settings):
    """
    Closes: the audio rules suppressed along with the container change.

    The requirement is a file that can still show its styled subtitles,
    not a file left entirely alone. The Japanese track still goes, and if
    it did not, always_keep would quietly become "ignore this file" for
    everyone whose anime is dual-audio.
    """
    settings["font_attachment_handling"] = "always_keep"

    decision = analyze_file(_mkv(), _anime_tracks(), settings)

    dropped = {a.stream_index for a in _actions(decision, "drop_track")
               if a.track_type == "audio"}
    assert dropped == {2}, "the Japanese track survived, or English went too"
    assert decision.should_process is True


# ── always_remove ─────────────────────────────────────────────────────────────

def test_always_remove_converts_exactly_as_before(settings):
    """
    Closes: always_keep and always_remove swapped, and always_remove
    taking the review branch.

    This is the behaviour every existing install has today, and someone
    who sets it has said they would rather have MP4 everywhere than keep
    the typesetting. Silently declining to convert would be as wrong as
    silently converting.
    """
    settings["font_attachment_handling"] = "always_remove"

    decision = analyze_file(_mkv(), _anime_tracks(), settings)

    assert decision.is_manual_review is False
    assert decision.target_container == "mp4"
    assert _actions(decision, "change_container")
    assert len(_actions(decision, "extract_subtitle")) == 2


# ── When the question does not arise ──────────────────────────────────────────

def test_a_file_whose_fonts_serve_nothing_is_not_flagged(settings):
    """
    Closes: the gate firing with no styled tracks present, and firing on
    any subtitle codec rather than the styled ones.

    SRT cannot reference a font, so fonts alongside only SRT subtitles
    serve nothing and there is no decision to make. Asking anyway trains
    people to click through the prompt.
    """
    tracks = [
        make_track(0, "video", codec="hevc"),
        make_track(1, "audio", codec="aac", language="eng", is_default=True),
        make_track(2, "subtitle", codec="subrip", language="eng"),
    ]

    decision = analyze_file(_mkv(), tracks, settings)

    assert decision.is_manual_review is False


def test_a_file_with_no_fonts_is_untouched_by_this(settings):
    """The overwhelming majority of files. Nothing here may apply to them."""
    decision = analyze_file(_mkv(fonts=0), _anime_tracks(), settings)

    assert decision.is_manual_review is False
    assert decision.target_container == "mp4"


def test_a_track_already_resolved_in_review_is_not_asked_about_again(settings):
    """
    Closes: already-resolved tracks re-entering the gate.

    Answering a review writes the choice into MediaFile.subtitle_overrides,
    and the next evaluation must honour it rather than ask the same
    question. Without the exclusion the file cannot leave review at all:
    every pass re-flags it.
    """
    decision = analyze_file(
        _mkv(), _anime_tracks(), settings,
        subtitle_overrides={3: "keep", 4: "remove"},
    )

    assert decision.is_manual_review is False


def test_one_resolved_track_still_leaves_the_other_to_ask_about(settings):
    """
    A partial resolution is still unresolved. Treating "some tracks
    answered" as "done" would convert the file with the unanswered track
    flattened, which is the outcome the review exists to prevent.
    """
    decision = analyze_file(
        _mkv(), _anime_tracks(), settings, subtitle_overrides={3: "keep"},
    )

    assert decision.is_manual_review is True
    assert [f["stream_index"] for f in decision.flagged_subtitles] == [4]


# ── Only the tracks the file keeps ────────────────────────────────────────────
#
# The gate once collected every styled track in the file, including languages
# the keep list was about to delete. The reported file carried sixteen ASS
# tracks in thirteen languages with a keep list of English: the review asked
# about all sixteen, and Always Keep kept all sixteen, because its synthetic
# "keep" outranks the language rule. It now asks only about tracks that
# _sub_is_kept keeps, which is the image gate's rule.

def _multilingual_tracks():
    """
    The shape of the SPY x FAMILY report: a forced English signs track, a
    full English track, and styled tracks in languages the keep list drops.
    """
    return [
        make_track(0, "video", codec="h264", language="und"),
        make_track(1, "audio", codec="aac", language="eng", is_default=True),
        make_track(3, "subtitle", codec="ass", language="eng", is_forced=True,
                   title="Forced"),
        make_track(4, "subtitle", codec="ass", language="eng"),
        make_track(6, "subtitle", codec="ass", language="ara",
                   title="Saudi Arabia"),
        make_track(7, "subtitle", codec="ass", language="ger"),
    ]


def test_only_styled_tracks_the_file_keeps_are_asked_about(settings):
    """
    Closes: the gate ignoring the keep rule.

    The Arabic and German tracks are deleted whatever the answer, so a
    question about them has no consequence. In the reported file fourteen
    of them buried the two that mattered.
    """
    decision = analyze_file(_mkv(), _multilingual_tracks(), settings)

    assert decision.is_manual_review is True
    assert [f["stream_index"] for f in decision.flagged_subtitles] == [3, 4]


def test_always_keep_keeps_only_the_kept_languages(settings):
    """
    Closes: Always Keep overriding the keep list.

    Handed every styled track, its synthetic "keep" kept every language. The
    English tracks stay embedded and hold the file in MKV; the others go,
    as they would in a file with no fonts.
    """
    settings["font_attachment_handling"] = "always_keep"

    decision = analyze_file(_mkv(), _multilingual_tracks(), settings)

    assert {3, 4} <= {a.stream_index for a in _actions(decision, "copy_track")}
    assert {6, 7} <= {a.stream_index for a in _actions(decision, "drop_track")}
    assert decision.target_container == "mkv"


def test_no_review_when_every_styled_track_is_being_dropped(settings):
    """
    The consequence of asking only about kept tracks, agreed rather than
    incidental. English survives here only as SRT, and the one styled track
    is in a dropped language. The fonts serve nothing that stays, so the
    file converts as any other would.
    """
    tracks = [
        make_track(0, "video", codec="h264", language="und"),
        make_track(1, "audio", codec="aac", language="eng", is_default=True),
        make_track(2, "subtitle", codec="subrip", language="eng"),
        make_track(3, "subtitle", codec="ass", language="ara"),
    ]

    decision = analyze_file(_mkv(), tracks, settings)

    assert decision.is_manual_review is False
    assert decision.target_container == "mp4"
    assert 3 in {a.stream_index for a in _actions(decision, "drop_track")}


def test_a_forced_styled_track_in_another_language_is_still_asked_about(settings):
    """
    Closes: a bare language check standing in for the shared rule.

    keep_forced_subtitles keeps a forced track whatever its language, so a
    forced Spanish signs track survives the language filter and its styling
    is at stake. A check against the keep list alone would leave out the
    one styled track this file keeps.
    """
    tracks = [
        make_track(0, "video", codec="h264", language="und"),
        make_track(1, "audio", codec="aac", language="eng", is_default=True),
        make_track(2, "subtitle", codec="ass", language="spa", is_forced=True),
        make_track(3, "subtitle", codec="ass", language="ger"),
    ]

    decision = analyze_file(_mkv(), tracks, settings)

    assert [f["stream_index"] for f in decision.flagged_subtitles] == [2]


@pytest.mark.parametrize("answers, container", [
    ({3: "keep", 4: "remove"}, "mkv"),
    ({3: "remove", 4: "remove"}, "mp4"),
], ids=["keeping-a-styled-track-stays-mkv", "removing-them-all-converts"])
def test_the_review_answers_decide_the_container(settings, answers, container):
    """
    Keeping any styled track in the review holds the file in MKV, and
    removing them all lets it convert. The dropped languages play no part
    in either, since they are never in the question.

    Nothing pinned this before: the tests above pin that an answered track
    is not asked about again, not what the answer then does to the file.
    """
    decision = analyze_file(_mkv(), _multilingual_tracks(), settings,
                            subtitle_overrides=answers)

    assert decision.is_manual_review is False
    assert decision.target_container == container
    assert {6, 7} <= {a.stream_index for a in _actions(decision, "drop_track")}
