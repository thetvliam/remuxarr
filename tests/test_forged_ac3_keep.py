"""
AC3 Forge's track goes where the audio it was encoded from goes: a scan
keeps it whenever a kept audio track has its language (decision.py).

The forged AC3 is not marked default, and carries its source's language.
Two keep terms used to keep the source and not the copy, so the next scan
removed the track Forge had just added: Keep Default Audio, which keeps a
default track when no track has a preferred language, and the last-resort
fallback, which keeps the first audio track when nothing else would be.
"""

from app.core.decision import analyze_file
from tests.conftest import make_file_info, make_track


def _video():
    return make_track(stream_index=0, track_type="video", codec="h264")


def _aac51(si, lang, is_default=False):
    return make_track(stream_index=si, codec="aac", language=lang, channels=6,
                      channel_layout="5.1", is_default=is_default)


def _ac3(si, lang):
    return make_track(stream_index=si, codec="ac3", language=lang, channels=6,
                      channel_layout="5.1")


def _dropped(settings, tracks, forged):
    d = analyze_file(
        make_file_info(path="/m/Film.mkv", container="mkv", video_codec="h264"), tracks,
        {**settings, "keep_audio_languages": ["eng"], "keep_default_audio": True},
        forged_ac3_audio_index=forged,
    )
    return [a.stream_index for a in d.actions
            if a.action_type == "drop_track" and a.track_type == "audio"]


def test_kept_beside_a_source_kept_as_the_default(settings):
    """A jpn AAC kept by Keep Default Audio; the forged jpn AC3 stays with it."""
    tracks = [_video(), _aac51(1, "jpn", is_default=True), _ac3(2, "jpn")]
    assert _dropped(settings, tracks, forged=1) == []


def test_kept_beside_a_source_kept_by_the_fallback(settings):
    """A fre AAC, not default, kept only as the file's first audio track."""
    tracks = [_video(), _aac51(1, "fre"), _ac3(2, "fre")]
    assert _dropped(settings, tracks, forged=1) == []


def test_dropped_with_its_source_when_the_language_is_not_kept(settings):
    """An eng track is there, so the fre AAC goes, and the fre AC3 with it."""
    tracks = [_video(),
              make_track(stream_index=1, codec="aac", language="eng", is_default=True),
              _aac51(2, "fre"), _ac3(3, "fre")]
    assert _dropped(settings, tracks, forged=2) == [2, 3]


def test_an_ac3_the_file_had_itself_is_not_kept_this_way(settings):
    """The same layout without a forge: the AC3 is the file's own, and goes."""
    tracks = [_video(), _aac51(1, "jpn", is_default=True), _ac3(2, "jpn")]
    assert _dropped(settings, tracks, forged=None) == [2]
