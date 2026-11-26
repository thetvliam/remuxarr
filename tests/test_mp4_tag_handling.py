"""
Tags an MP4 Can't Hold (mp4_lost_tag_handling): what happens to a file
Prefer MP4 Container would convert when an MP4 cannot hold some of its own
tags (issue #67).

Before the setting, such a file's job ran in full and the staging check
refused its output (worker._lost_tags_error): a Matroska with an IMDB tag
was refused every conversion, and every scan after held it back, so its
unwanted tracks were never removed either. The tags it would lose are now
known before any job runs (MediaFile.mp4_lost_tags, test_mp4_tag_loss),
and the decision engine does what the setting says:

  • Leave the file as it is (the default)  → nothing is done to it, and
                                              History names the tags
  • Convert to MP4 and drop them            → the job lets exactly those
                                              go, and any other loss is
                                              refused as before
  • Keep its container, still remove tracks → held out of MP4, as Dolby
                                              Vision is

A file already MP4 is left as it is whatever the setting says, as is one
that would lose the date it says it was made: no setting lets a job drop
either (decision.analyze_file). A file whose tags could not be found out
beforehand is decided as before, and the staging check still stops a loss
(test_kept_file_tags).

Verified by mutation, 13 applied, 13 killed. Run first against the suite
as it stood before this file, with the setting in place and the tests it
changed (test_kept_file_tags, test_mp4_tag_loss, test_creation_date and
fixtures the new key reached), eleven survived. Not marking the setting as
applying at the next rescan was already killed by
test_settings_schema's rescan-note test, and the stored answer not read
from the row by test_creation_date's date test as it was changed here;
both are recorded as such. Killed now:

  • Keep its container ignored                → the three decision tests
                                                and the real file that keep
                                                the container
  • Convert and drop treated as leave         → the decision test, and the
                                                three real conversions that
                                                drop them
  • Leave not applied                         → the default, the decision
                                                test and the real file left
                                                as it is
  • A file already MP4 not protected          → an MP4 under drop and under
                                                keep, and the real MP4 set
                                                to drop them
  • The date droppable                        → test_the_date_it_was_made_
                                                is_never_dropped
  • The job allowed to lose any tag once one
    may go                                    → test_converted_no_other_
                                                tag_may_go
  • The tags the job may drop not handed to
    the check                                 → the three real conversions
  • The check not leaving them out            → the three real conversions
  • The decision not carrying them            → the decision test and the
                                                three real conversions
  • Drop as the default                       → the default, and the real
                                                file left as it is
  • Not marked as applying at the next
    rescan                                    → already killed (above); now
                                                also the default's test
  • The stored answer not read from the row   → already killed (above); now
                                                also the row's own test and
                                                every real file here
  • Questions raised for an MP4 left as it is → test_an_mp4_left_as_it_is_
                                                raises_no_language_question,
                                                which first held a Matroska,
                                                where the file leaves through
                                                a decision that carries no
                                                question anyway: the mutant
                                                survived it

No equivalent mutants. The four proposed (the modes swapped, keep its
container ignored, every tag allowed, the allowance never applied) are
among them: the modes swapped as convert-and-drop treated as leave, and
the allowance never applied at its two sites, the hand-over and the check.
"""
import asyncio

import pytest

from tests import test_kept_file_tags as _kt
from tests.test_language_answer_during_job import needs_ffmpeg

rig = _kt.rig

LEFT = ("FFmpeg cannot write this file's own tags IMDB into an MP4, so it is left as "
        "it is. Tags an MP4 Can't Hold can convert it without them or keep its "
        "container instead.")
LEFT_MP4 = ("Rewriting this MP4 would lose its own tags IMDB, which FFmpeg cannot write "
            "into an MP4, so it is left as it is.")


def _decide(container="mkv", lost=("IMDB",), audio=("eng", "ger"), **settings):
    """
    The decision for a file with H.264 video and AAC audio in these
    languages, the first default, whose stored MediaFile.mp4_lost_tags is
    `lost` (None for never looked at), under the suite's base settings
    with `settings` over them.
    """
    from app.core.decision import analyze_file
    from tests.conftest import BASE_SETTINGS, make_file_info, make_track

    tracks = [make_track(stream_index=0, track_type="video", codec="h264")]
    tracks += [make_track(stream_index=i, track_type="audio", codec="aac", language=language,
                          is_default=(i == 1))
               for i, language in enumerate(audio, start=1)]
    file_info = make_file_info(path=f"/media/Film/Film.{container}", container=container,
                               video_codec="h264")
    file_info["mp4_lost_tags"] = None if lost is None else list(lost)
    return analyze_file(file_info, tracks, {**BASE_SETTINGS, **settings})


def _types(decision):
    return [a.action_type for a in decision.actions]


# ── The setting ───────────────────────────────────────────────────────────────

def test_it_is_offered_with_three_choices_and_leave_by_default():
    """
    Leave is what such a file got before the setting, without the job that
    failed first. The engine's own default is the app's: BASE_SETTINGS has
    no value for the setting. A change applies at the next rescan, as for
    Prefer MP4 Container.
    """
    from app.api.routes.settings import SETTINGS_SCHEMA
    from app.database.session import DEFAULT_APP_SETTINGS

    entry = next(e for e in SETTINGS_SCHEMA if e["key"] == "mp4_lost_tag_handling")
    assert entry["label"] == "Tags an MP4 Can't Hold"
    assert [o["value"] for o in entry["options"]] == ["leave_file", "drop_tags", "keep_container"]
    assert entry.get("rescan") is True
    assert DEFAULT_APP_SETTINGS["mp4_lost_tag_handling"] == "leave_file"
    assert _decide().reason == LEFT


def test_the_stored_answer_reaches_the_decision(tmp_path):
    """Read from the row as the scan stored it, null as null."""
    from types import SimpleNamespace

    from app.core.scanner import _file_info_for

    def row(stored):
        return SimpleNamespace(path=str(tmp_path / "Film.mkv"), container="mkv",
                               video_codec="h264", font_attachments=0, recording_signs="",
                               dolby_vision="", mp4_lost_tags=stored,
                               und_audio_threshold_acknowledged=False, original_language=None)

    assert _file_info_for(row('["IMDB", "TMDB"]'))["mp4_lost_tags"] == ["IMDB", "TMDB"]
    assert _file_info_for(row("[]"))["mp4_lost_tags"] == []
    assert _file_info_for(row(None))["mp4_lost_tags"] is None


# ── A Matroska Prefer MP4 would convert ──────────────────────────────────────

def test_left_as_it_is_nothing_is_done_to_it():
    decision = _decide(mp4_lost_tag_handling="leave_file")

    assert not decision.should_process
    assert decision.actions == []
    assert decision.reason == LEFT


def test_converted_the_job_may_drop_exactly_those():
    decision = _decide(mp4_lost_tag_handling="drop_tags", lost=("IMDB", "TMDB"))

    assert decision.should_process
    assert decision.target_container == "mp4"
    assert decision.dropped_tags == ["IMDB", "TMDB"]
    assert decision.reason == ("Remove 1 audio track; Convert MKV → MP4, dropping tags an "
                               "MP4 cannot hold (IMDB, TMDB)")
    container = next(a for a in decision.actions if a.action_type == "change_container")
    assert container.description == ("Convert container: MKV → MP4 — drops tags an MP4 "
                                     "cannot hold: IMDB, TMDB")


def test_kept_in_its_container_the_rest_still_happens():
    decision = _decide(mp4_lost_tag_handling="keep_container")

    assert decision.should_process
    assert decision.target_container == "mkv"
    assert "change_container" not in _types(decision)
    assert decision.reason == "Remove 1 audio track"
    assert decision.dropped_tags == []
    assert decision.mp4_blocked_beyond_subtitles


def test_kept_in_its_container_with_nothing_else_to_do_history_says_why():
    decision = _decide(mp4_lost_tag_handling="keep_container", audio=("eng",))

    assert not decision.should_process
    assert decision.reason == ("FFmpeg cannot write its own tags IMDB into an MP4, so "
                               "it stays MKV, as Tags an MP4 Can't Hold says.")


@pytest.mark.parametrize("handling", ["leave_file", "drop_tags", "keep_container"])
@pytest.mark.parametrize("lost", [(), None], ids=["none lost", "never looked at"])
def test_a_file_with_nothing_known_to_lose_is_converted_as_before(handling, lost):
    """Never looked at, the worker looks at pickup and decides again, and
    the staging check still stops a loss."""
    decision = _decide(mp4_lost_tag_handling=handling, lost=lost)

    assert decision.target_container == "mp4"
    assert decision.dropped_tags == []
    assert decision.reason == "Remove 1 audio track; Convert MKV → MP4"


@pytest.mark.parametrize("handling", ["leave_file", "drop_tags", "keep_container"])
def test_with_prefer_mp4_off_the_setting_changes_nothing(handling):
    decision = _decide(mp4_lost_tag_handling=handling, prefer_mp4_container=False)

    assert decision.target_container == "mkv"
    assert decision.reason == "Remove 1 audio track"
    assert decision.dropped_tags == []


def test_the_date_it_was_made_is_never_dropped():
    """A job writes it back; FFmpeg fails to only when it cannot read it."""
    lost = ("IMDB", "creation_time")
    left = _decide(mp4_lost_tag_handling="drop_tags", lost=lost)
    kept = _decide(mp4_lost_tag_handling="keep_container", lost=lost)

    assert not left.should_process
    assert left.reason == ("FFmpeg cannot write this file's own tags IMDB, creation_time "
                           "into an MP4, so it is left as it is. Tags an MP4 Can't Hold "
                           "can keep its container instead.")
    assert kept.target_container == "mkv" and kept.reason == "Remove 1 audio track"


def test_an_mp4_left_as_it_is_raises_no_language_question():
    """
    An MP4 with an untagged audio track and nothing else to do: under
    Always Ask the track is a question, and its answer would make a job
    that rewrites the file, which would be left as it is all the same. An
    answer could only come back to that decision, as for the other files
    left as they are, so with a tag to lose it is not asked.
    """
    from app.core.decision import analyze_file
    from tests.conftest import BASE_SETTINGS, make_file_info, make_track

    tracks = [make_track(stream_index=0, track_type="video", codec="h264"),
              make_track(stream_index=1, track_type="audio", codec="aac", language="und",
                         is_default=True)]
    file_info = make_file_info(path="/media/Film/Film.mp4", container="mp4", video_codec="h264")
    settings = {**BASE_SETTINGS, "fix_undefined_language_audio": "always_ask"}

    asked = analyze_file({**file_info, "mp4_lost_tags": []}, tracks, settings,
                         has_faststart=True)
    left = analyze_file({**file_info, "mp4_lost_tags": ["IMDB"]}, tracks, settings,
                        has_faststart=True)

    assert asked.undefined_audio_flags == [{"stream_index": 1, "origin": "undefined"}], \
        "the case is not what it says"
    assert left.undefined_audio_flags == []


# ── A file already MP4 ───────────────────────────────────────────────────────

@pytest.mark.parametrize("handling", ["leave_file", "drop_tags", "keep_container"])
def test_an_mp4_is_left_as_it_is_whatever_the_setting(handling):
    """Its iTunes tags cannot be written back by a revert either."""
    decision = _decide(container="mp4", mp4_lost_tag_handling=handling)

    assert not decision.should_process
    assert decision.reason == LEFT_MP4


def test_an_mp4_with_nothing_to_lose_is_processed():
    decision = _decide(container="mp4", lost=())

    assert decision.should_process
    assert decision.reason == "Remove 1 audio track"


# ── Real files ───────────────────────────────────────────────────────────────

def _jobs(rig):
    from app.database.models import QueueItem

    with rig.Session() as db:
        return [(q.status, q.reason, q.error_message)
                for q in db.query(QueueItem).order_by(QueueItem.id)]


@needs_ffmpeg
def test_left_as_it_is_the_file_is_untouched_and_history_says_why(rig):
    _kt._set(rig, prefer_mp4_container=True)
    source = _kt._file(rig.library / "Film.mkv", "matroska", tag=("IMDB", "tt0"))
    before = _kt._digest(source)

    _kt._scan_and_run(rig)

    assert _jobs(rig) == [("skipped", LEFT, None)]
    assert _kt._digest(source) == before


@needs_ffmpeg
def test_converted_the_tags_go_and_the_rest_is_kept(rig):
    _kt._set(rig, prefer_mp4_container=True, mp4_lost_tag_handling="drop_tags")
    _kt._file(rig.library / "Film.mkv", "matroska", tag=("IMDB", "tt0"))

    _kt._scan_and_run(rig)

    output = rig.library / "Film.mp4"
    assert [(s, e) for s, _, e in _jobs(rig)] == [("success", None)]
    assert _kt._audio(output) == ["eng"]
    tags = _kt._file_tags(output)
    assert "imdb" not in tags
    assert tags["title"] == "A Film"


@needs_ffmpeg
def test_converted_no_other_tag_may_go(rig, monkeypatch):
    """
    The answer found beforehand names IMDB alone, and the file has a TMDB
    tag as well, which the MP4 loses too: the job is refused over that one,
    and the file is left as it was.
    """
    import app.core.scanner as scanner
    import app.core.worker as worker

    _kt._set(rig, prefer_mp4_container=True, mp4_lost_tag_handling="drop_tags")
    source = _kt._file(rig.library / "Film.mkv", "matroska", tag=("IMDB", "tt0"),
                       extra=["-metadata", "TMDB=movie/1"])
    before = _kt._digest(source)
    monkeypatch.setattr(scanner, "mp4_lost_tags", lambda *args, **kwargs: ["IMDB"])
    monkeypatch.setattr(worker, "mp4_lost_tags", lambda *args, **kwargs: ["IMDB"])

    _kt._scan_and_run(rig)

    [(status, _, error)] = _jobs(rig)
    assert status == "failed"
    assert error == f"{worker.LOST_TAGS} It would lose: TMDB."
    assert _kt._digest(source) == before


@needs_ffmpeg
def test_kept_in_its_container_the_track_goes_and_the_tags_stay(rig):
    _kt._set(rig, prefer_mp4_container=True, mp4_lost_tag_handling="keep_container")
    source = _kt._file(rig.library / "Film.mkv", "matroska", tag=("IMDB", "tt0"))

    _kt._scan_and_run(rig)

    assert [(s, e) for s, _, e in _jobs(rig)] == [("success", None)]
    assert sorted(p.name for p in rig.library.iterdir()) == ["Film.mkv"]
    assert _kt._audio(source) == ["eng"]
    assert _kt._file_tags(source)["imdb"] == "tt0"


@needs_ffmpeg
def test_an_mp4_set_to_drop_them_is_left_as_it_was(rig):
    _kt._set(rig, mp4_lost_tag_handling="drop_tags")
    source = _kt._file(rig.library / "Film.mp4", "mp4", tag=("purchase_date", "2020-01-01"))
    before = _kt._digest(source)

    _kt._scan_and_run(rig)

    assert _jobs(rig) == [("skipped", LEFT_MP4.replace("IMDB", "purchase_date"), None)]
    assert _kt._digest(source) == before


@needs_ffmpeg
def test_a_revert_of_the_conversion_gives_the_tags_back(rig):
    """
    The job removes the German track, so it records a revert point, and the
    manifest holds the original's own tags (revert.build_manifest), which a
    restore writes back into the Matroska.
    """
    from app.core.revert_restore import restore_revert_point
    from app.database.models import RevertPoint

    _kt._set(rig, prefer_mp4_container=True, mp4_lost_tag_handling="drop_tags")
    _kt._file(rig.library / "Film.mkv", "matroska", tag=("IMDB", "tt0"))
    _kt._scan_and_run(rig)
    assert "imdb" not in _kt._file_tags(rig.library / "Film.mp4"), "the case is not what it says"
    with rig.Session() as db:
        point_id = db.query(RevertPoint).one().id

    outcome = asyncio.run(restore_revert_point(point_id))

    assert outcome.success, outcome.error
    assert _kt._file_tags(rig.library / "Film.mkv")["imdb"] == "tt0"
