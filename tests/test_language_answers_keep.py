"""
A Language Review answer feeds the keep decision, and an answered track is
kept (decision.py, "Language Review answers").

An answer is written into the file as the track's tag. The keep rules used
to read the tag before it, and the next scan read the tag the job had
written, so the two disagreed: a track answered in a language the keep list
does not hold was kept, tagged, and removed by the next scan. And a track
the last-resort fallback kept could be removed at once by answering what it
was, the untagged track it had been standing in for taking its place.

Each test decides a file, applies the plan as a job would (answers carried
to their tracks' new positions, as scanner.rekey_track_answers carries
them), and decides again: the second decision must have nothing to do.
"""

from app.core.decision import analyze_file
from tests.conftest import make_file_info, make_track

_INFO = make_file_info(path="/m/Film.mkv", container="mkv", video_codec="h264")


def _settings(settings, **over):
    return {**settings, "prefer_mp4_container": False, "extract_text_subtitles_to_srt": False,
            "fix_undefined_language": "always_ask", "fix_undefined_language_audio": "always_ask",
            "undefined_language_value": "eng", **over}


def _decide(tracks, cfg, audio=None, subs=None, subtitle_overrides=None):
    return analyze_file(_INFO, tracks, cfg, audio_language_overrides=audio or {},
                        subtitle_language_overrides=subs or {},
                        subtitle_overrides=subtitle_overrides or {})


def _apply(tracks, decision, audio, subs):
    """The tracks a job leaves, and its answers moved with them."""
    acts = {a.stream_index: a for a in decision.actions if a.stream_index is not None}
    out, moved = [], {}
    for t in tracks:
        a = acts.get(t["stream_index"])
        if a is not None and a.action_type in ("drop_track", "extract_subtitle"):
            continue
        t = dict(t)
        if a is not None and a.target_language:
            t["language"] = a.target_language
        moved[t["stream_index"]] = len(out)
        out.append(t)
    for i, t in enumerate(out):
        t["stream_index"] = i
    rekey = lambda answers: {moved[si]: v for si, v in answers.items() if si in moved}
    return out, rekey(audio), rekey(subs)


def _settled(tracks, cfg, decision, audio=None, subs=None):
    """What the job leaves, as (type, language) pairs, after checking a
    rescan of it, with its answers, has nothing to do."""
    after, audio_after, subs_after = _apply(tracks, decision, audio or {}, subs or {})
    again = _decide(after, cfg, audio_after, subs_after)
    assert not again.should_process, again.reason
    return [(t["track_type"], t["language"]) for t in after if t["track_type"] != "video"]


def _actions(decision, track_type):
    return {a.stream_index: (a.action_type, a.target_language)
            for a in decision.actions if a.track_type == track_type}


def _webdl():
    """The testlib's file 03: English E-AC3 (default), German DTS, and an
    untagged director's commentary."""
    return [
        make_track(stream_index=0, track_type="video", codec="h264"),
        make_track(stream_index=1, codec="eac3", language="eng", channels=6,
                   channel_layout="5.1", is_default=True),
        make_track(stream_index=2, codec="dts", language="ger", channels=6,
                   channel_layout="5.1"),
        make_track(stream_index=3, codec="aac", language="und",
                   title="Director's Commentary"),
    ]


def test_an_audio_track_answered_outside_the_keep_list_is_kept(settings):
    """
    Keep Audio Languages is French. The commentary, answered Japanese, used
    to be kept as untagged, tagged Japanese, and removed by the next scan.
    """
    tracks = _webdl()
    cfg = _settings(settings, keep_audio_languages=["fre"], keep_default_audio=True)
    d = _decide(tracks, cfg, audio={3: "jpn"})
    assert _actions(d, "audio") == {1: ("copy_track", None), 2: ("drop_track", None),
                                    3: ("copy_track", "jpn")}
    assert _settled(tracks, cfg, d, audio={3: "jpn"}) == [("audio", "eng"), ("audio", "jpn")]


def test_answering_the_track_the_fallback_kept_does_not_remove_it(settings):
    """
    Keep Default Audio off, nothing French: the fallback keeps the English
    track, and the untagged commentary is taken as English under Always
    Fix and dropped. Answering the English track Japanese made it count
    among the file's untagged tracks, so the commentary stayed untagged and
    kept, the fallback stood down, and the answered track was dropped.
    """
    tracks = _webdl()
    cfg = _settings(settings, keep_audio_languages=["fre"], keep_default_audio=False,
                    fix_undefined_language_audio="always_fix")
    d = _decide(tracks, cfg, audio={1: "jpn"})
    assert _actions(d, "audio")[1] == ("copy_track", "jpn")
    assert _settled(tracks, cfg, d, audio={1: "jpn"}) == [("audio", "jpn"), ("audio", "und")]


def test_an_answer_in_a_preferred_language_stops_keep_default_audio(settings):
    """
    A default German track and one tagged Japanese, answered English: there
    is preferred audio, so the German track is not kept as the default. It
    used to be, and the next scan dropped it once English was written.
    """
    tracks = [
        make_track(stream_index=0, track_type="video", codec="h264"),
        make_track(stream_index=1, codec="ac3", language="ger", is_default=True),
        make_track(stream_index=2, codec="aac", language="jpn"),
    ]
    cfg = _settings(settings, keep_audio_languages=["eng"], keep_default_audio=True)
    d = _decide(tracks, cfg, audio={2: "eng"})
    assert _actions(d, "audio") == {1: ("drop_track", None), 2: ("copy_track", "eng")}
    assert _settled(tracks, cfg, d, audio={2: "eng"}) == [("audio", "eng")]


def test_an_answered_track_keeps_the_fallback_from_keeping_another(settings):
    """
    Nothing French, Keep Default Audio off, and the Japanese track answered
    Spanish. The answered track is kept, so the file has audio and the
    fallback has nothing to do: the German track goes rather than being
    kept beside it as the first audio track.
    """
    tracks = [
        make_track(stream_index=0, track_type="video", codec="h264"),
        make_track(stream_index=1, codec="ac3", language="ger"),
        make_track(stream_index=2, codec="aac", language="jpn"),
    ]
    cfg = _settings(settings, keep_audio_languages=["fre"], keep_default_audio=False)
    d = _decide(tracks, cfg, audio={2: "spa"})
    assert _actions(d, "audio") == {1: ("drop_track", None), 2: ("copy_track", "spa")}
    assert _settled(tracks, cfg, d, audio={2: "spa"}) == [("audio", "spa")]


def _two_subtitles():
    """The testlib's file 13: an untagged subtitle and an English one."""
    return [
        make_track(stream_index=0, track_type="video", codec="h264"),
        make_track(stream_index=1, codec="aac", language="jpn"),
        make_track(stream_index=2, track_type="subtitle", codec="subrip", language=None),
        make_track(stream_index=3, track_type="subtitle", codec="subrip", language="eng"),
    ]


def test_a_subtitle_answered_outside_the_keep_list_is_kept(settings):
    """
    Keep Subtitle Languages is Japanese. The untagged subtitle, answered
    English, used to be kept as untagged, tagged English, and removed by the
    next scan. The English one the file already had still goes.
    """
    tracks = _two_subtitles()
    cfg = _settings(settings, keep_audio_languages=["jpn"],
                    keep_subtitle_languages=["jpn"], keep_forced_subtitles=False)
    d = _decide(tracks, cfg, subs={2: "eng"})
    assert _actions(d, "subtitle") == {2: ("copy_track", "eng"), 3: ("drop_track", None)}
    assert _settled(tracks, cfg, d, subs={2: "eng"}) == [("audio", "jpn"), ("subtitle", "eng")]


def test_a_remove_answer_still_removes_an_answered_subtitle(settings):
    """A "remove" answer (Delete, in Manual Review) is a choice to remove,
    and still wins."""
    tracks = _two_subtitles()
    cfg = _settings(settings, keep_audio_languages=["jpn"],
                    keep_subtitle_languages=["jpn"], keep_forced_subtitles=False)
    d = _decide(tracks, cfg, subs={2: "eng"}, subtitle_overrides={2: "remove"})
    assert _actions(d, "subtitle")[2] == ("drop_track", None)
