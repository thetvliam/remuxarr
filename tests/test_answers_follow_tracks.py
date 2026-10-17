"""
Stored answers follow their tracks when Remuxarr rewrites a file.

The three answer columns (subtitle_overrides, audio_language_overrides,
subtitle_language_overrides) are keyed by a descriptor: what a track is,
plus which one it is among identical ones (scanner.descriptors_by_stream).
The ordinal is the part a rewrite can change while the track stays the
same. A job that dropped the first of two identical tracks renumbered the
second from 2 to 1, and the answer given for the dropped one then matched
the survivor:

  • Remove English, Keep Spanish on two untitled PGS tracks became Remove
    on the Spanish track at the next full scan, and it was queued for
    deletion as "removed via manual review".
  • Two undefined AC3 5.1 tracks answered French and English: once a later
    job dropped the French one, the English track inherited "fre", was
    retagged French, and was then kept only by the no-silent-output guard.

Both were reproduced against the real functions before this file existed.
Now a job carries each answer to the track it was given for
(job_stream_mapping + rekey_track_answers, from worker._finish_job) and a
revert carries it back through the revert point's manifest
(revert_restore._apply). An answer for a track that is gone is dropped, so
its question comes back rather than landing on a neighbour. When the
rewritten file cannot be probed the answers are cleared for the same
reason. The worker side of that wiring is pinned in
test_job_finalisation.py, and a real revert round trip in
test_revert_multiple_jobs.py.

The pairing is per track type because that is how build_ffmpeg_command
writes its output: video, then kept audio, then kept subtitles, each in
source order, so a source with a subtitle before its audio comes out
regrouped.

Verified by mutation, 18 applied, 18 killed. Each was first run against the
whole suite as it stood before these tests (1937 tests) with the new code in
place, and all 18 survived it. Killed now by the tests in this file unless
another module is named:

  • _finish_job never re-keys                → test_job_finalisation.py
  • Answers resolved against the new tracks,
    which is the reported bug                → the two reported cases
  • A removed track's answer kept at its
    old index                                → not inherited
  • The job's removals ignored when pairing  → the reported cases
  • An extracted subtitle counted as kept    → test_job_finalisation.py
  • Tracks paired across types               → paired within their type
  • The new key read from the source track   → reencoded, and the
                                               reported cases
  • The count guard removed                  → count does not add up
  • Each of the three columns left out of
    the re-key, one mutant each              → test_job_finalisation.py
                                               (the subtitle and audio
                                               ones also by the revert
                                               probe failure here)
  • A stale key kept                         → matched nothing
  • The type check removed                   → another type
  • A failed post-job probe keeps answers    → test_job_finalisation.py
  • Revert never re-keys                     → carries answers back, and
                                               test_revert_multiple_jobs.py
  • Revert resolves against the restored
    tracks                                   → the same two
  • Revert assumes nothing moved             → the same two
  • A failed post-revert probe keeps answers → forgets the answers

One more was proposed and is not counted: routing a dry run through the
real-success branch, where the re-key lives. The pre-existing
test_dry_run_leaves_the_file_queued_for_a_real_pass already kills it.
"""
import pytest

from app.core.decision import analyze_file
from app.core.scanner import (descriptors_by_stream, job_stream_mapping,
                              rekey_track_answers, resolve_track_answers)
from tests.conftest import BASE_SETTINGS


def t(si, track_type, codec, language, *, channels=None, layout="", title=None):
    return dict(stream_index=si, track_type=track_type, codec=codec,
                language=language, channels=channels, channel_layout=layout,
                is_default=False, is_forced=False, is_hearing_impaired=False,
                is_dub=False, title=title)


def key(tracks, si):
    return descriptors_by_stream(tracks)[si]


def carry(stored, before, after, removed):
    """What a job does: pair the kept tracks, then carry the answers."""
    return rekey_track_answers(
        stored, before, after,
        job_stream_mapping(before, after, removed, "/m/Movie.mkv"))


# ── The two reported cases ───────────────────────────────────────────────────

PGS_SETTINGS = BASE_SETTINGS | {
    "keep_subtitle_languages": ["eng", "spa"],
    "image_subtitle_handling": "always_ask",
}

PGS_SOURCE = [t(0, "video", "h264", "und"),
              t(1, "audio", "ac3", "eng", channels=6, layout="5.1(side)"),
              t(2, "subtitle", "hdmv_pgs_subtitle", "eng"),
              t(3, "subtitle", "hdmv_pgs_subtitle", "spa")]

# The job removed English, so Spanish is stream 2 and ordinal 1 now.
PGS_OUTPUT = [t(0, "video", "h264", "und"),
              t(1, "audio", "ac3", "eng", channels=6, layout="5.1(side)"),
              t(2, "subtitle", "hdmv_pgs_subtitle", "spa")]


def test_a_keep_answer_stays_on_the_track_it_was_given_for():
    stored = {key(PGS_SOURCE, 2): "remove", key(PGS_SOURCE, 3): "keep"}

    carried = carry(stored, PGS_SOURCE, PGS_OUTPUT, removed={2})

    assert resolve_track_answers(carried, PGS_OUTPUT) == {2: "keep"}
    decision = analyze_file({"path": "/m/Movie.mkv", "container": "mkv"},
                            PGS_OUTPUT, PGS_SETTINGS,
                            subtitle_overrides=resolve_track_answers(carried, PGS_OUTPUT))
    assert not any(a.action_type == "drop_track" for a in decision.actions), (
        "the Spanish subtitle the user kept is planned for deletion")


def test_the_answer_for_a_removed_track_is_not_inherited():
    """
    Only the removed track was answered. Nothing may land on the survivor,
    whose own question has not been asked yet.
    """
    stored = {key(PGS_SOURCE, 2): "remove"}

    carried = carry(stored, PGS_SOURCE, PGS_OUTPUT, removed={2})

    assert carried == {}
    decision = analyze_file({"path": "/m/Movie.mkv", "container": "mkv"},
                            PGS_OUTPUT, PGS_SETTINGS,
                            subtitle_overrides=resolve_track_answers(carried, PGS_OUTPUT))
    assert decision.is_manual_review, "the Spanish track should be asked about"


def test_a_language_answer_stays_on_the_track_it_was_given_for():
    """
    The audio case, at the second job: the first wrote fre and eng into the
    two tracks, the second drops the French one.
    """
    surround = dict(channels=6, layout="5.1(side)")
    source = [t(0, "video", "h264", "und"),
              t(1, "audio", "ac3", "und", **surround),
              t(2, "audio", "ac3", "und", **surround)]
    stored = {key(source, 1): "fre", key(source, 2): "eng"}
    tagged = [t(0, "video", "h264", "und"),
              t(1, "audio", "ac3", "fre", **surround),
              t(2, "audio", "ac3", "eng", **surround)]
    output = [t(0, "video", "h264", "und"),
              t(1, "audio", "ac3", "eng", **surround)]

    carried = carry(stored, tagged, output, removed={1})

    assert resolve_track_answers(carried, output) == {1: "eng"}
    decision = analyze_file({"path": "/m/Show.mp4", "container": "mp4"},
                            output, BASE_SETTINGS,
                            audio_language_overrides=resolve_track_answers(carried, output))
    assert not any(getattr(a, "target_language", None) == "fre"
                   for a in decision.actions), "the English track is retagged French"


# ── How tracks are paired ────────────────────────────────────────────────────

def test_tracks_are_paired_within_their_type():
    """
    A subtitle stored before the audio in the source comes out after it. Paired
    across types, the subtitle's answer would be put on the audio track and
    the audio's on the subtitle.
    """
    source = [t(0, "video", "h264", "und"),
              t(1, "subtitle", "ass", "spa"),
              t(2, "audio", "aac", "und", channels=2, layout="stereo"),
              t(3, "subtitle", "subrip", "eng")]
    stored = {key(source, 1): "keep", key(source, 2): "jpn"}
    output = [t(0, "video", "h264", "und"),
              t(1, "audio", "aac", "und", channels=2, layout="stereo"),
              t(2, "subtitle", "ass", "spa")]

    carried = carry(stored, source, output, removed={3})

    assert resolve_track_answers(carried, output) == {1: "jpn", 2: "keep"}


def test_a_reencoded_track_keeps_its_answer():
    """
    The corrupt-audio retry re-encodes to AAC, which changes the codec in the
    descriptor. The new key has to come from the output, not the source.
    """
    source = [t(0, "video", "h264", "und"),
              t(1, "audio", "eac3", "und", channels=6, layout="5.1(side)")]
    output = [t(0, "video", "h264", "und"),
              t(1, "audio", "aac", "und", channels=6, layout="5.1(side)")]

    carried = carry({key(source, 1): "jpn"}, source, output, removed=set())

    assert resolve_track_answers(carried, output) == {1: "jpn"}


def test_a_type_whose_count_does_not_add_up_loses_its_answers(caplog):
    """
    The job kept two audio tracks and the output has one: something this
    code does not know about happened, and which survivor is which cannot be
    told. Pairing the first with the first would guess. The subtitles, whose
    count does add up, are still carried.
    """
    source = [t(0, "video", "h264", "und"),
              t(1, "audio", "ac3", "und", channels=6, layout="5.1(side)"),
              t(2, "audio", "aac", "und", channels=2, layout="stereo"),
              t(3, "subtitle", "ass", "und")]
    stored = {key(source, 1): "jpn", key(source, 3): "keep"}
    output = [t(0, "video", "h264", "und"),
              t(1, "audio", "aac", "und", channels=2, layout="stereo"),
              t(2, "subtitle", "ass", "und")]

    carried = carry(stored, source, output, removed=set())

    assert resolve_track_answers(carried, output) == {2: "keep"}
    assert "audio tracks of /m/Movie.mkv dropped" in caplog.text


def test_an_answer_that_matched_nothing_is_not_kept():
    """
    A key that resolves to no track before the job is a stale answer, and
    left in the column it could start matching some later track by chance.
    """
    stale = key([t(5, "subtitle", "dvd_subtitle", "und")], 5)

    carried = carry({stale: "keep"}, PGS_SOURCE, PGS_OUTPUT, removed={2})

    assert carried == {}


def test_an_answer_is_never_carried_onto_a_track_of_another_type():
    """
    A revert pairs tracks through the manifest rather than by type. Track
    rows that no longer describe the file (it was replaced while the revert
    ran) could pair a subtitle's index with an audio track's.
    """
    before = [t(0, "video", "h264", "und"), t(1, "subtitle", "ass", "und")]
    after = [t(0, "video", "h264", "und"), t(1, "audio", "aac", "und")]

    carried = rekey_track_answers({key(before, 1): "keep"}, before, after, {0: 0, 1: 1})

    assert carried == {}


# ── Revert ───────────────────────────────────────────────────────────────────
#
# _apply driven directly, with the probe of the restored file stubbed. The
# same path with a real FFmpeg restore is in test_revert_multiple_jobs.py.

ORIGINAL = [t(0, "video", "h264", "und"),
            t(1, "audio", "ac3", "eng", channels=6, layout="5.1(side)"),
            t(2, "audio", "ac3", "und", channels=6, layout="5.1(side)"),
            t(3, "subtitle", "ass", "und")]
# The job dropped the English audio.
PROCESSED = [t(0, "video", "h264", "und"),
             t(1, "audio", "ac3", "und", channels=6, layout="5.1(side)"),
             t(2, "subtitle", "ass", "und")]
MANIFEST = {"streams": [
    {"index": 0, "processed_index": 0},
    {"index": 1, "processed_index": None, "sidecar_index": 0},
    {"index": 2, "processed_index": 1},
    {"index": 3, "processed_index": 2},
]}


@pytest.fixture
def reverting(tmp_path, monkeypatch):
    """A processed file's rows and answers, and an _apply that restores it."""
    import json

    from sqlalchemy.orm import sessionmaker

    import app.core.revert_restore as rr
    from app.database.models import Base, MediaFile, Track
    from tests.conftest import memory_engine

    engine = memory_engine()
    Base.metadata.create_all(engine)
    db = sessionmaker(bind=engine)()
    path = str(tmp_path / "Show.mkv")
    media = MediaFile(path=path, filename="Show.mkv", directory=str(tmp_path),
                      size=1, mtime=1.0, container="mkv",
                      audio_language_overrides=json.dumps({key(PROCESSED, 1): "jpn"}),
                      subtitle_overrides=json.dumps({key(PROCESSED, 2): "keep"}))
    db.add(media)
    db.flush()
    for td in PROCESSED:
        db.add(Track(file_id=media.id, **td))
    db.commit()

    def restore(*, probe_fails=False):
        def fake_probe(path, ffprobe_path):
            if probe_fails:
                raise rr.ProbeError("unreadable")
            return {}
        monkeypatch.setattr(rr, "probe_file", fake_probe)
        monkeypatch.setattr(rr, "extract_format_info", lambda _d: {"container": "mkv"})
        monkeypatch.setattr(rr, "extract_tracks", lambda _d: [dict(x) for x in ORIGINAL])
        plan = rr._Plan(point_id=1, file_id=media.id, current_path=path,
                        sidecar_path="", manifest=MANIFEST, original_path=path,
                        destination=path)
        rr._apply(db, plan, path)
        db.commit()
        db.refresh(media)
        return media

    return restore


def _answers(media, column):
    import json
    return resolve_track_answers(json.loads(getattr(media, column) or "{}"), ORIGINAL)


def test_a_revert_carries_answers_back_to_where_the_tracks_were(reverting):
    media = reverting()

    assert _answers(media, "audio_language_overrides") == {2: "jpn"}, (
        "the language answer is not on the undefined audio track it was given for")
    assert _answers(media, "subtitle_overrides") == {3: "keep"}


def test_a_revert_whose_file_cannot_be_probed_forgets_the_answers(reverting):
    media = reverting(probe_fails=True)

    assert media.audio_language_overrides is None
    assert media.subtitle_overrides is None
