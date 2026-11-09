"""
Always Fix's audio tag is resolved before the keep decision
(decision._resolve_undefined_audio_language), as the subtitle one is.

An untagged audio track used to be judged as untagged, and so always kept;
the job then tagged it Primary Language, and when that language was not in
Keep Audio Languages the next scan removed it: two jobs for one decision.
Kept that way, it also stopped the last-resort fallback from firing, so a
file whose tagged tracks were all outside the keep list kept only the
untagged track and lost its main audio.

Each test runs the decision, applies its plan to the track list as a job
would, and decides again: the second decision must have nothing to do.
"""

from app.core.decision import analyze_file
from tests.conftest import make_file_info, make_track


def _webdl(original_language=None):
    """The testlib's file 03, where it showed: an English E-AC3 (default), a
    German DTS, and an untagged director's commentary."""
    info = make_file_info(path="/m/Film.mkv", container="mkv", video_codec="h264",
                          original_language=original_language)
    tracks = [
        make_track(stream_index=0, track_type="video", codec="h264"),
        make_track(stream_index=1, codec="eac3", language="eng", channels=6,
                   channel_layout="5.1", is_default=True),
        make_track(stream_index=2, codec="dts", language="ger", channels=6,
                   channel_layout="5.1"),
        make_track(stream_index=3, codec="aac", language="und",
                   title="Director's Commentary"),
    ]
    return info, tracks


def _settings(settings, **over):
    return {**settings, "fix_undefined_language_audio": "always_fix",
            "undefined_language_value": "eng", "prefer_mp4_container": False,
            "keep_audio_languages": ["fre"], "keep_default_audio": True, **over}


def _apply(tracks, decision):
    """The tracks a job leaves: dropped ones gone, tags written, renumbered."""
    acts = {a.stream_index: a for a in decision.actions if a.stream_index is not None}
    out = []
    for t in tracks:
        a = acts.get(t["stream_index"])
        if a is not None and a.action_type == "drop_track":
            continue
        t = dict(t)
        if a is not None and a.target_language:
            t["language"] = a.target_language
        out.append(t)
    for i, t in enumerate(out):
        t["stream_index"] = i
    return out


def _audio(decision):
    return {a.stream_index: (a.action_type, a.target_language)
            for a in decision.actions if a.track_type == "audio"}


def _settled(info, tracks, cfg, decision, **kwargs):
    """What the job leaves, as (codec, language) pairs, after checking a
    rescan of it has nothing to do."""
    after = _apply(tracks, decision)
    again = analyze_file(info, after, cfg, **kwargs)
    assert not again.should_process, again.reason
    return [(t["codec"], t["language"]) for t in after if t["track_type"] == "audio"]


def test_taken_as_primary_language_and_dropped_in_one_job(settings):
    """
    Item 9: Keep Default Audio keeps the default English track; the
    untagged commentary is taken as English too, so it goes now rather
    than on the next scan.
    """
    info, tracks = _webdl()
    cfg = _settings(settings)
    d = analyze_file(info, tracks, cfg)
    assert _audio(d) == {1: ("copy_track", None), 2: ("drop_track", None),
                         3: ("drop_track", None)}
    drop = next(a for a in d.actions if a.stream_index == 3)
    assert drop.description.startswith("Drop audio [und] aac")
    assert "untagged, taken as eng (Primary Language): not in keep list" in drop.description
    assert _settled(info, tracks, cfg, d) == [("eac3", "eng")]


def test_the_fallback_keeps_the_main_track_rather_than_the_untagged_one(settings):
    """
    Keep Default Audio off: nothing survives on its language, so the
    fallback keeps the first track. The untagged commentary used to survive
    as untagged, stopping the fallback, and every tagged track was dropped.
    """
    info, tracks = _webdl()
    cfg = _settings(settings, keep_default_audio=False)
    d = analyze_file(info, tracks, cfg)
    assert _audio(d) == {1: ("copy_track", None), 2: ("drop_track", None),
                         3: ("drop_track", None)}
    assert _settled(info, tracks, cfg, d) == [("eac3", "eng")]


def test_a_track_taken_as_preferred_stops_keep_default_audio(settings):
    """
    Taken as English, the untagged track is a preferred-language track, so
    the default German one is not kept as the default: it used to be, and
    the next scan dropped it once the English tag was written.
    """
    info = make_file_info(path="/m/Film.mkv", container="mkv", video_codec="h264")
    tracks = [
        make_track(stream_index=0, track_type="video", codec="h264"),
        make_track(stream_index=1, codec="ac3", language="ger", is_default=True),
        make_track(stream_index=2, codec="aac", language="und"),
    ]
    cfg = _settings(settings, keep_audio_languages=["eng"])
    d = analyze_file(info, tracks, cfg)
    assert _audio(d) == {1: ("drop_track", None), 2: ("copy_track", "eng")}
    assert _settled(info, tracks, cfg, d) == [("aac", "eng")]


def test_original_only_sees_the_track_taken_as_the_original_language(settings):
    """
    Keep original only narrows the keep list to the original language when
    a track in it exists. The untagged track, taken as English, is one, so
    the French track goes now, not on the scan after the tag is written.
    """
    info = make_file_info(path="/m/Film.mkv", container="mkv", video_codec="h264",
                          original_language="eng")
    tracks = [
        make_track(stream_index=0, track_type="video", codec="h264"),
        make_track(stream_index=1, codec="ac3", language="fre"),
        make_track(stream_index=2, codec="aac", language="und"),
    ]
    cfg = _settings(settings, original_language_audio="original_only")
    d = analyze_file(info, tracks, cfg)
    assert _audio(d) == {1: ("drop_track", None), 2: ("copy_track", "eng")}
    assert _settled(info, tracks, cfg, d) == [("aac", "eng")]


def test_kept_and_tagged_when_primary_language_is_kept(settings):
    info, tracks = _webdl()
    cfg = _settings(settings, keep_audio_languages=["eng"])
    d = analyze_file(info, tracks, cfg)
    assert _audio(d) == {1: ("copy_track", None), 2: ("drop_track", None),
                         3: ("copy_track", "eng")}
    assert _settled(info, tracks, cfg, d) == [("eac3", "eng"), ("aac", "eng")]


def test_a_forged_copy_takes_the_tag_and_stays_with_its_source(settings):
    """An untagged AAC 5.1, its only audio, and the AC3 Forge made from it."""
    info = make_file_info(path="/m/Film.mkv", container="mkv", video_codec="h264")
    tracks = [
        make_track(stream_index=0, track_type="video", codec="h264"),
        make_track(stream_index=1, codec="aac", language="und", channels=6,
                   channel_layout="5.1"),
        make_track(stream_index=2, codec="ac3", language="und", channels=6,
                   channel_layout="5.1"),
    ]
    cfg = _settings(settings)
    d = analyze_file(info, tracks, cfg, forged_ac3_audio_index=1)
    assert _audio(d) == {1: ("copy_track", "eng"), 2: ("copy_track", "eng")}
    assert _settled(info, tracks, cfg, d, forged_ac3_audio_index=1) == \
        [("aac", "eng"), ("ac3", "eng")]


# ── Where Always Fix tags nothing, nothing changes ───────────────────────────

def test_not_taken_as_primary_language_in_a_film_made_in_another(settings):
    """Always Fix does not tag it, so it is untagged, and kept as such."""
    info, tracks = _webdl(original_language="fre")
    d = analyze_file(info, tracks, _settings(settings))
    assert _audio(d)[3] == ("copy_track", None)


def test_not_taken_as_primary_language_once_answered(settings):
    """A Language Review answer is the user's, and is applied as a retag."""
    info, tracks = _webdl()
    d = analyze_file(info, tracks, _settings(settings),
                     audio_language_overrides={3: "jpn"})
    assert _audio(d)[3] == ("copy_track", "jpn")


def test_not_taken_as_primary_language_with_two_untagged_tracks(settings):
    """
    Always Fix tags only a file's one untagged track, so both stay
    untagged and kept: with the English default kept too, there is nothing
    to do.
    """
    info, tracks = _webdl()
    tracks[2] = make_track(stream_index=2, codec="dts", language="und", channels=6,
                           channel_layout="5.1")
    d = analyze_file(info, tracks, _settings(settings))
    assert not d.should_process, d.reason
    assert _audio(d) == {}


def test_always_ask_and_always_leave_keep_it_untagged(settings):
    info, tracks = _webdl()
    for mode in ("always_ask", "always_leave"):
        d = analyze_file(info, tracks,
                         _settings(settings, fix_undefined_language_audio=mode))
        assert _audio(d)[3] == ("copy_track", None), mode
