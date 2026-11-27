"""
Keep Audio and Subtitle Languages entries with a region or a script
(langcodes.keep_entry, decision._keep_list, decision._regional_match).

A plain code (spa) matches a track's old code, as before, and so keeps every
Spanish track. An entry with a region or a script (es-419) matches the
track's BCP 47 language, which only a Matroska file carries and which
Track.language_ietf stores, so es-419 keeps Latin American Spanish and drops
Spain's, which a real web release carries side by side. A track whose BCP 47
language is unknown matches no regional entry, and nor does one the job gives
a language of its own (a Language Review answer, the Fix pass).

Before this, an entry such as pt-BR was saved as pt-br, matched no track, and
every Portuguese track was dropped without a word: an entry that is not a
code is now refused with a message.

Verified by mutation, 19 applied, 19 killed. Run first against the suite as
it stood before this file, two (regional entries read as plain codes, an
entry that is not a code accepted) survived all 3107 tests. Killed now,
each by the test for its behaviour:

  • regional entries read as plain codes; a region matched on its language
    alone; a prefix that is not a whole subtag matched; a narrower tag not
    matched; a plain code no longer keeping regional tracks
  • a subtitle or an audio track the job retags kept by its old region
    (one mutant each)
  • the preferred-audio test, the last-resort test, the keep test and the
    Original Only reason ignoring regional entries (one mutant each);
    Original Only keeping them
  • an entry that is not a code accepted; one saved without normalising
  • an unknown two-letter code accepted; the language not shortened to two
    letters; the script or the region left in the case typed (one mutant
    each); extra subtags accepted
"""
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.api.routes.settings import setting_problem
from app.core.decision import analyze_file
from app.core.langcodes import keep_entry, regional
from app.database.models import Base
from app.database.session import get_app_settings, update_app_setting
from conftest import make_file_info, make_track
from tests.engine_disposal import dispose_after_test


# ── The entry as saved ───────────────────────────────────────────────────────

@pytest.mark.parametrize("typed, saved", [
    ("eng", "eng"), ("en", "eng"), ("deu", "ger"), ("und", "und"), ("fil", "fil"),
    ("es-419", "es-419"), ("ES-419", "es-419"), ("spa-419", "es-419"), ("pt-br", "pt-BR"),
    ("zh-hans", "zh-Hans"), ("zh-Hant-tw", "zh-Hant-TW"), ("nob-NO", "nb-NO"), ("fil-PH", "fil-PH"),
    (" pt-BR ", "pt-BR"),
])
def test_an_entry_is_saved_in_one_form(typed, saved):
    assert keep_entry(typed) == saved


@pytest.mark.parametrize("typed", ["english", "e", "eng,ger", "xx-YY", "xx", "en-",
                                   "-US", "en-US-x-twain", "en-Latn-GB-oxendict", "en-1234"])
def test_what_is_not_a_language_code_is_none(typed):
    assert keep_entry(typed) is None


def test_only_an_entry_with_a_subtag_is_regional():
    assert regional("es-419") and regional("zh-Hans")
    assert not regional("spa") and not regional("eng")


@pytest.mark.parametrize("key", ["keep_audio_languages", "keep_subtitle_languages"])
def test_an_entry_that_is_not_a_code_is_refused(key):
    problem = setting_problem(key, ["eng", "english", "pt-BR", "xx-YY"])
    assert problem is not None
    assert "'english'" in problem and "'xx-YY'" in problem and "'pt-BR'" not in problem


@pytest.mark.parametrize("key", ["keep_audio_languages", "keep_subtitle_languages"])
def test_codes_with_and_without_regions_are_accepted(key):
    assert setting_problem(key, ["eng", "es-419", "zh-Hant", "fil"]) is None


def test_other_lists_are_not_held_to_language_codes():
    assert setting_problem("scan_paths", ["/media/tv"]) is None


@pytest.fixture
def db():
    engine = dispose_after_test(create_engine("sqlite://"))
    Base.metadata.create_all(engine)
    with sessionmaker(bind=engine)() as session:
        yield session


def test_regional_entries_are_saved_in_bcp_47s_form(db):
    update_app_setting(db, "keep_subtitle_languages", ["pt-br", "EN", "zh-hans", "eng"])
    assert get_app_settings(db)["keep_subtitle_languages"] == ["pt-BR", "eng", "zh-Hans"]


# ── Subtitles ────────────────────────────────────────────────────────────────

def _mkv():
    return make_file_info(path="/media/Film/Film.mkv", container="mkv")


def _sub(index, language, tag, **kw):
    track = make_track(stream_index=index, track_type="subtitle", codec="subrip",
                       language=language, channels=None, channel_layout="", **kw)
    track["language_ietf"] = tag
    return track


def _audio(index, language, tag, **kw):
    track = make_track(stream_index=index, track_type="audio", language=language, **kw)
    track["language_ietf"] = tag
    return track


def _video():
    track = make_track(stream_index=0, track_type="video", codec="h264", channels=None,
                       channel_layout="")
    track["language_ietf"] = ""
    return track


def _settings(settings, **changes):
    return {**settings, "prefer_mp4_container": False, "extract_text_subtitles_to_srt": False,
            "keep_audio_languages": ["eng"], **changes}


def _dropped(decision):
    return sorted(a.stream_index for a in decision.actions if a.action_type == "drop_track")


def _why(decision, index):
    return next(a.description for a in decision.actions
                if a.action_type == "drop_track" and a.stream_index == index)


SPANISH = [_video(), _audio(1, "eng", "en"), _sub(2, "spa", "es-419"), _sub(3, "spa", "es-ES")]


def test_a_regional_entry_keeps_its_region_and_drops_the_other(settings):
    decision = analyze_file(_mkv(), SPANISH, _settings(settings, keep_subtitle_languages=["es-419"]))
    assert _dropped(decision) == [3]


def test_a_plain_code_still_keeps_every_region(settings):
    decision = analyze_file(_mkv(), SPANISH, _settings(settings, keep_subtitle_languages=["spa"]))
    assert _dropped(decision) == []


def test_a_narrower_tag_matches_the_entry_above_it(settings):
    tracks = [_video(), _audio(1, "eng", "en"), _sub(2, "chi", "zh-Hant-TW"), _sub(3, "chi", "zh-Hans")]
    decision = analyze_file(_mkv(), tracks, _settings(settings, keep_subtitle_languages=["zh-Hant"]))
    assert _dropped(decision) == [3]


def test_a_prefix_that_is_not_a_subtag_does_not_match(settings):
    """zh-Han is not zh-Hant's parent: only a whole subtag counts."""
    tracks = [_video(), _audio(1, "eng", "en"), _sub(2, "chi", "zh-Hant")]
    decision = analyze_file(_mkv(), tracks, _settings(settings, keep_subtitle_languages=["zh-Han"]))
    assert _dropped(decision) == [2]


def test_a_track_whose_region_is_unknown_matches_no_regional_entry(settings):
    tracks = [_video(), _audio(1, "eng", "en"), _sub(2, "spa", None), _sub(3, "spa", "")]
    decision = analyze_file(_mkv(), tracks, _settings(settings, keep_subtitle_languages=["es-419"]))
    assert _dropped(decision) == [2, 3]


def test_a_subtitle_the_fix_pass_retags_is_not_kept_by_its_region(settings):
    """An untagged subtitle that a Matroska nonetheless marks es-419: the
    Fix pass writes English onto it, which replaces the region."""
    tracks = [_video(), _audio(1, "eng", "en"), _sub(2, "und", "es-419")]
    decision = analyze_file(_mkv(), tracks, _settings(
        settings, keep_subtitle_languages=["es-419"], fix_undefined_language="always_fix",
        undefined_language_value="eng"))
    assert _dropped(decision) == [2]


# ── Audio ────────────────────────────────────────────────────────────────────

def test_a_regional_audio_entry_keeps_its_region_and_drops_the_other(settings):
    tracks = [_video(), _audio(1, "eng", "en-GB"), _audio(2, "eng", "en-US")]
    decision = analyze_file(_mkv(), tracks, _settings(
        settings, keep_audio_languages=["en-GB"], keep_default_audio=False))
    assert _dropped(decision) == [2]


def test_a_regional_match_is_a_preferred_track(settings):
    """With a preferred track present, the default track is not kept for
    being the default."""
    tracks = [_video(), _audio(1, "eng", "en-US", is_default=True), _audio(2, "eng", "en-GB")]
    decision = analyze_file(_mkv(), tracks, _settings(
        settings, keep_audio_languages=["en-GB"], keep_default_audio=True))
    assert _dropped(decision) == [1]


def test_a_lone_regional_match_needs_no_last_resort(settings):
    """The only audio track matches by region: kept on its own account,
    not as the absolute fallback that keeps the first track of a file
    whose every track would go."""
    tracks = [_video(), _audio(1, "fre", "fr-FR"), _audio(2, "fre", "fr-CA")]
    decision = analyze_file(_mkv(), tracks, _settings(
        settings, keep_audio_languages=["fr-CA"], keep_default_audio=False))
    assert _dropped(decision) == [1]


def test_an_audio_track_the_fix_pass_retags_is_not_kept_by_its_region(settings):
    tracks = [_video(), _audio(1, "eng", "en-US"), _audio(2, "und", "en-GB")]
    decision = analyze_file(_mkv(), tracks, _settings(
        settings, keep_audio_languages=["en-GB", "eng"], keep_default_audio=False,
        fix_undefined_language_audio="always_fix", undefined_language_value="jpn"))
    assert 2 in _dropped(decision)


def test_original_only_leaves_regional_entries_out_and_says_why(settings):
    """Keep original only keeps just the original language when the file
    has it; a track kept by a regional entry otherwise goes, and the line
    names the setting that dropped it, as it does for a plain code."""
    tracks = [_video(), _audio(1, "jpn", "ja"), _audio(2, "eng", "en-GB")]
    file_info = {**_mkv(), "original_language": "jpn"}
    decision = analyze_file(file_info, tracks, _settings(
        settings, keep_audio_languages=["en-GB"], keep_default_audio=False,
        original_language_audio="original_only"))
    assert _dropped(decision) == [2]
    assert "not the original language" in _why(decision, 2)
