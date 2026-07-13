"""
Shared fixtures for the Remuxarr test suite.

BASE_SETTINGS provides sensible, realistic defaults for every setting
analyze_file() reads — individual tests only need to override the specific
setting they care about, via BASE_SETTINGS | {"some_setting": value}.

make_track() builds a single track dict without repeating every key every
time — pass only the fields that matter for a given test.
"""
import pytest

# Every settings key analyze_file() actually reads, confirmed directly
# against app/core/decision.py rather than assumed — see the grep this was
# built from if these ever need re-verifying:
#   grep -oP 'settings\.get\("[^"]+"' app/core/decision.py
BASE_SETTINGS = {
    "keep_audio_languages":         ["eng"],
    "keep_default_audio":           True,
    "keep_subtitle_languages":      ["eng"],
    "keep_forced_subtitles":        True,
    "und_audio_threshold":          2,
    "fix_undefined_language":       False,
    "undefined_language_value":     "eng",
    "undefined_language_mode":      "all_undefined_per_type",
    "prefer_mp4_container":         True,
    "extract_text_subtitles_to_srt": False,
    "add_faststart_to_mp4":         True,
}


def make_track(
    stream_index=0,
    track_type="audio",
    codec="aac",
    language="und",
    channels=2,
    channel_layout="stereo",
    is_default=False,
    is_forced=False,
    is_hearing_impaired=False,
    is_dub=False,
    title=None,
):
    """Build one track dict matching probe.extract_tracks()'s output shape."""
    return {
        "stream_index":        stream_index,
        "track_type":          track_type,
        "codec":               codec,
        "language":            language,
        "channels":            channels,
        "channel_layout":      channel_layout,
        "is_default":          is_default,
        "is_forced":           is_forced,
        "is_hearing_impaired": is_hearing_impaired,
        "is_dub":              is_dub,
        "title":               title,
    }


def make_file_info(path="/media/movies/Test Movie (2020)/Test Movie (2020).mp4",
                    container="mp4", video_codec="h264"):
    return {"path": path, "container": container, "video_codec": video_codec}


@pytest.fixture
def settings():
    """A fresh copy of BASE_SETTINGS per test — mutate freely, no cross-test leakage."""
    return dict(BASE_SETTINGS)
