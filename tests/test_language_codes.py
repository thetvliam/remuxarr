"""
Language codes in any of their three spellings: en, eng and, for twenty
languages, a second three-letter form (deu beside ger, fra beside fre).

Every comparison was an exact string match. Reproduced against a real server
with real files first:

  • Keep Audio Languages eng and ger: a German track tagged deu was removed,
    and on another file an English track tagged en was removed while the
    Japanese default track was kept.
  • Keep Audio Languages en: an English track tagged eng was removed.
  • An MKV whose English track was tagged en, converted to MP4: MP4 cannot
    hold a two-letter code and FFmpeg writes none for it, without an error,
    so the track came out undefined.
  • Set language en in Audio Language Review on a file converted to MP4: the
    same loss, and every Force Full Rescan queued the correction again.

app/core/langcodes.py turns any spelling into ISO 639-2/B, and the decision
engine, saved settings, Language Review and the Plex check compare that.

Mutation, 19 applied, each run against the whole 2121-test suite first.
Fifteen survived it and are killed here: the audio keep list, the subtitle
keep list, the tracks' tags, the audio answers, the subtitle answers and
Primary Language each compared as stored; a two-letter tag never written in
full, written in full for a file with nothing else to do, written over a
language answer, and the other three-letter form rewritten too; the
Norwegian .srt name dropped; settings saved as typed, a keep list saved with
duplicates, Primary Language saved as typed; and the Plex check comparing
as reported. Four were already killed: the .srt name built from the stored
code (by the extraction tests in test_decision.py), and a Set language
answer stored as typed and either table ignored (by the cases in
test_language_review_isolation.py this change updated). All four are
killed here too.
"""
import json

import pytest

import app.core.plex as plex_mod
from app.core.decision import analyze_file
from app.core.langcodes import (TERMINOLOGIC, TWO_LETTER, canonical,
                                two_letter)
from app.database.models import AppSetting
from app.database.session import update_app_setting
from conftest import make_file_info, make_track

MKV = make_file_info(path="/tv/Show/Ep1.mkv", container="mkv")


def _video():
    return make_track(0, "video", "h264", language="und")


def _audio(si, language, default=False):
    return make_track(si, "audio", "aac", language=language, is_default=default)


def _sub(si, language, codec="subrip"):
    return make_track(si, "subtitle", codec, language=language, channels=None,
                      channel_layout=None)


def _actions(decision, kind=None):
    return {a.stream_index: a for a in decision.actions
            if a.stream_index is not None
            and (kind is None or a.action_type == kind)}


# ── The tables ─────────────────────────────────────────────────────────────

def test_the_tables_hold_the_whole_registry():
    """184 two-letter codes; twenty languages with a second three-letter code."""
    assert len(TWO_LETTER) == 184
    assert len(TERMINOLOGIC) == 20
    assert all(len(k) == 2 and len(v) == 3 for k, v in TWO_LETTER.items())
    # Two-letter codes lead to the bibliographic form, never the other one.
    assert not set(TWO_LETTER.values()) & set(TERMINOLOGIC)
    assert set(TERMINOLOGIC.values()) <= set(TWO_LETTER.values())


@pytest.mark.parametrize("code, expected", [
    ("eng", "eng"), ("en", "eng"), (" EN ", "eng"),
    ("ger", "ger"), ("deu", "ger"), ("de", "ger"),
    ("chi", "chi"), ("zho", "chi"), ("zh", "chi"),
    ("wel", "wel"), ("cym", "wel"), ("cy", "wel"),
    ("und", "und"), ("fil", "fil"), ("xyz", "xyz"),
    (None, None), ("", ""),
])
def test_every_spelling_becomes_the_bibliographic_code(code, expected):
    assert canonical(code) == expected


@pytest.mark.parametrize("code, expected", [
    ("eng", "en"), ("en", "en"), ("ger", "de"), ("deu", "de"),
    ("fil", "fil"), ("und", "und"),
    # Named "no" before this change, and kept: a file processed again would
    # otherwise get a second sidecar beside the first.
    ("nob", "no"), ("nno", "no"),
])
def test_srt_names_keep_the_two_letter_code(code, expected):
    assert two_letter(code) == expected


# ── The decision engine ────────────────────────────────────────────────────

def test_a_track_tagged_with_the_other_three_letter_code_is_kept(settings):
    settings |= {"keep_audio_languages": ["eng", "ger"], "prefer_mp4_container": False}
    tracks = [_video(), _audio(1, "eng", default=True), _audio(2, "deu")]

    decision = analyze_file(MKV, tracks, settings)

    assert decision.should_process is False


def test_a_track_tagged_with_a_two_letter_code_is_kept(settings):
    """The real server removed this English track and kept the Japanese one."""
    settings |= {"prefer_mp4_container": False}
    tracks = [_video(), _audio(1, "jpn", default=True), _audio(2, "en")]

    decision = analyze_file(MKV, tracks, settings)

    assert _actions(decision, "drop_track").keys() == {1}
    assert _actions(decision, "copy_track").keys() >= {2}


def test_a_keep_list_saved_with_two_letter_codes_still_matches(settings):
    """Saved before settings were stored canonical."""
    settings |= {"keep_audio_languages": ["en"], "prefer_mp4_container": False}
    tracks = [_video(), _audio(1, "jpn", default=True), _audio(2, "eng")]

    decision = analyze_file(MKV, tracks, settings)

    assert _actions(decision, "drop_track").keys() == {1}


def test_subtitles_compare_the_same_way(settings):
    """Both sides in a form other than the stored one: list de, track deu."""
    settings |= {"keep_subtitle_languages": ["de"], "prefer_mp4_container": False,
                 "extract_text_subtitles_to_srt": False}
    tracks = [_video(), _audio(1, "eng", default=True), _sub(2, "deu")]

    decision = analyze_file(MKV, tracks, settings)

    assert decision.should_process is False


def test_a_two_letter_tag_is_written_in_full_when_the_file_is_converted(settings):
    """MP4 would otherwise lose it: FFmpeg writes no language for "en"."""
    tracks = [_video(), _audio(1, "en", default=True)]

    decision = analyze_file(MKV, tracks, settings)

    assert decision.should_process is True
    assert _actions(decision, "copy_track")[1].target_language == "eng"
    assert "three-letter language code on 1 track" in decision.reason


def test_a_two_letter_tag_alone_does_not_queue_the_file(settings):
    settings |= {"prefer_mp4_container": False}
    tracks = [_video(), _audio(1, "en", default=True)]

    decision = analyze_file(MKV, tracks, settings)

    assert decision.should_process is False


def test_the_other_three_letter_form_is_left_as_it_is(settings):
    """Both are valid ISO 639-2, and MP4 holds either: no reason to touch it."""
    settings |= {"keep_audio_languages": ["eng", "ger"]}
    tracks = [_video(), _audio(1, "eng", default=True), _audio(2, "deu")]

    decision = analyze_file(MKV, tracks, settings)

    assert decision.should_process is True          # converted to MP4
    assert _actions(decision, "copy_track")[2].target_language is None


def test_a_language_answer_is_not_replaced_by_the_rewrite(settings):
    settings |= {"keep_audio_languages": ["eng", "fre"]}
    tracks = [_video(), _audio(1, "en", default=True)]

    decision = analyze_file(MKV, tracks, settings,
                            audio_language_overrides={1: "fre"})

    assert _actions(decision, "copy_track")[1].target_language == "fre"


def test_an_audio_answer_saved_as_two_letters_is_not_applied_again(settings):
    """
    The loop from the real server: the answer en, the track eng. Without the
    same form on both sides the file was corrected on every scan.
    """
    settings |= {"prefer_mp4_container": False}
    tracks = [_video(), _audio(1, "eng", default=True)]

    decision = analyze_file(MKV, tracks, settings, audio_language_overrides={1: "en"})

    assert decision.should_process is False


def test_a_subtitle_answer_saved_as_two_letters_is_not_applied_again(settings):
    settings |= {"prefer_mp4_container": False, "extract_text_subtitles_to_srt": False}
    tracks = [_video(), _audio(1, "eng", default=True), _sub(2, "eng")]

    decision = analyze_file(MKV, tracks, settings,
                            subtitle_language_overrides={2: "en"})

    assert decision.should_process is False


def test_primary_language_is_written_in_full(settings):
    settings |= {"fix_undefined_language_audio": "always_fix",
                 "undefined_language_value": "en"}
    tracks = [_video(), _audio(1, "und", default=True)]

    decision = analyze_file(MKV, tracks, settings)

    assert _actions(decision, "copy_track")[1].target_language == "eng"


@pytest.mark.parametrize("language, name", [
    ("deu", "Ep1.de.srt"), ("en", "Ep1.en.srt"), ("nob", "Ep1.no.srt"),
])
def test_extracted_subtitles_are_named_with_the_two_letter_code(settings, language, name):
    settings |= {"keep_subtitle_languages": ["eng", "ger", "nob"]}
    tracks = [_video(), _audio(1, "eng", default=True), _sub(2, language)]

    decision = analyze_file(MKV, tracks, settings)

    assert _actions(decision, "extract_subtitle")[2].external_path == f"/tv/Show/{name}"


# ── Settings ───────────────────────────────────────────────────────────────

@pytest.fixture
def db():
    from sqlalchemy.orm import sessionmaker

    from app.database.models import Base
    from tests.conftest import memory_engine
    engine = memory_engine()
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    yield session
    session.close()


def _stored(db, key):
    return json.loads(db.get(AppSetting, key).value)


def test_keep_lists_are_saved_in_one_form_once_each(db):
    update_app_setting(db, "keep_audio_languages", ["en", "EN", "eng", "deu", "jpn"])

    assert _stored(db, "keep_audio_languages") == ["eng", "ger", "jpn"]


def test_primary_language_is_saved_in_full(db):
    update_app_setting(db, "undefined_language_value", "en")

    assert _stored(db, "undefined_language_value") == "eng"


def test_other_settings_are_saved_as_given(db):
    update_app_setting(db, "scan_paths", ["/media/EN"])

    assert _stored(db, "scan_paths") == ["/media/EN"]


# ── Plex ───────────────────────────────────────────────────────────────────

def test_plex_reporting_the_other_form_counts_as_a_match(monkeypatch):
    """The file says deu, Plex says ger: the same language, no Analyze needed."""
    monkeypatch.setattr(plex_mod, "_plex_request", lambda *a, **k: {
        "MediaContainer": {"Metadata": [{"Media": [{"Part": [{"Stream": [
            {"streamType": 2, "languageCode": "ger"}]}]}]}]}})

    assert plex_mod._audio_languages_match("http://plex", "t", 1, ["deu"]) is True
