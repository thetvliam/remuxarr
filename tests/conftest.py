"""
Shared fixtures for the Remuxarr test suite.

BASE_SETTINGS provides sensible, realistic defaults for every setting
analyze_file() reads — individual tests only need to override the specific
setting they care about, via BASE_SETTINGS | {"some_setting": value}.

make_track() builds a single track dict without repeating every key every
time — pass only the fields that matter for a given test.
"""
import os

# Must be set before ANY test module first imports app.database.session
# (directly or transitively, e.g. via app.core.worker): that module's
# import mkdir()s DATABASE_PATH's parent, and the production default
# (/config) isn't writable on dev machines or CI runners. conftest.py is
# imported by pytest before every test module, making this the one place
# that reliably runs first. setdefault, not assignment — an explicitly
# configured environment still wins.
os.environ.setdefault("REMUXARR_DATABASE_PATH", "/tmp/remuxarr-test/remuxarr.db")

import pytest

# Every settings key analyze_file() actually reads, confirmed directly
# against app/core/decision.py rather than assumed — see the grep this was
# built from if these ever need re-verifying:
#   grep -oP 'settings\.get\("[^"]+"' app/core/decision.py
#
# THESE MUST MATCH app/database/session.py's DEFAULT_APP_SETTINGS.
#
# Two of them did not, and it mattered. extract_text_subtitles_to_srt was
# False here while production ships True — so the suite's largest and most
# trusted module ran every test with subtitle extraction OFF, i.e. against a
# configuration almost no install uses. That flag is what routes a kept text
# subtitle down the extract_subtitle path instead of copy_track, and the
# language-override pass only ever rewrote copy/transcode actions. The result
# was a whole data path that no default-configured test could reach.
#
# Flipping it changed no test outcome, because every subtitle-sensitive test
# in test_decision.py already sets the flag explicitly. That is precisely why
# the gap survived: the default never mattered to the tests that cared, and no
# test combined extraction with always_ask at all.
#
# fix_undefined_language was False (the boolean back-compat spelling) where
# production writes one of three strings. The back-compat branch is worth
# covering — but from a test that opts into it, not from the default every
# other test inherits.
#
# A test needing a non-production value should set it explicitly on the
# `settings` fixture, as the extraction tests already do.
BASE_SETTINGS = {
    "keep_audio_languages":         ["eng"],
    "keep_default_audio":           True,
    "keep_subtitle_languages":      ["eng"],
    "keep_forced_subtitles":        True,
    "und_audio_threshold":          2,
    "fix_undefined_language":       "always_leave",
    "undefined_language_value":     "eng",
    "undefined_language_mode":      "all_undefined_per_type",
    "prefer_mp4_container":         True,
    "extract_text_subtitles_to_srt": True,
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
