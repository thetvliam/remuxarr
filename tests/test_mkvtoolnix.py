"""
app/core/mkvtoolnix.py: whether a Matroska job would keep more through
mkvmerge than through FFmpeg, what would stop it, the command, and the date.

These pin what it decides; tests/test_mkvmerge_jobs.py runs it in a job.
A region counts only on a track the job keeps with its own language, a
chapter's language alone counts for nothing, an old language code mkvmerge
would write back differently keeps the file with FFmpeg, and the date goes
back exactly, to the nanosecond, where mkvpropedit drops the fraction.

The real-binary tests build their files with FFmpeg and mkvmerge and skip
without them; CI installs both (the release the image ships).

Verified by mutation, 23 applied, 23 killed. Nothing imported the module
before this file, so every mutant survived the suite by construction; one
was run to confirm it, as the baseline. Each is killed by the test named
for its behaviour:

  • a region on a dropped track counted, or on a retagged one
  • chapter editions, nesting, names in several languages, edition and
    chapter flags, or tag levels no longer counted (one mutant each)
  • a chapter's language alone counted
  • the language, numbering, WebM or cover-art blocker removed (one each)
  • a dropped or an extracted track kept by the command (one each)
  • the language fix or the track order left out of the command
  • --no-date always, or never
  • the date written to the second, or not written at all
  • progress never read
  • available() satisfied by mkvmerge alone
  • unreadable chapters read as none
"""
import json
import shutil
import struct
import subprocess

import pytest

from app.config import settings as app_settings
from app.core import mkvtoolnix as mkv
from app.core.decision import Action, ProcessingDecision
from app.core.mkvtoolnix import ChapterAtom, Edition, MatroskaInfo, MkvTrack, Tag

needs_binaries = pytest.mark.skipif(
    not (shutil.which("ffmpeg") and shutil.which("ffprobe")
         and shutil.which("mkvmerge") and shutil.which("mkvextract")),
    reason="needs FFmpeg and MKVToolNix")


def _decision(*actions):
    return ProcessingDecision(should_process=True, reason="test", actions=list(actions),
                              target_container="mkv", source_container="mkv")


def _drop(index, kind="audio"):
    return Action(action_type="drop_track", description="drop", track_type=kind,
                  stream_index=index)


def _extract(index):
    return Action(action_type="extract_subtitle", description="extract",
                  track_type="subtitle", stream_index=index)


def _fix(index, language, kind="audio"):
    return Action(action_type="copy_track", description="fix", track_type=kind,
                  stream_index=index, target_language=language)


def _track(index, kind):
    return {"stream_index": index, "track_type": kind}


def _atom(*names, children=(), hidden=False, enabled=True, linked=False):
    return ChapterAtom(names=tuple(names), hidden=hidden, enabled=enabled, linked=linked,
                       children=tuple(children))


def _info(tracks=(), editions=(), tags=()):
    return MatroskaInfo(tracks=tuple(tracks), editions=tuple(editions), tags=tuple(tags))


ONE_EDITION = Edition(ordered=False, hidden=False,
                      atoms=(_atom(("Intro", "eng", "en")), _atom(("Main", "eng", "en"))))


# ── Which tracks a job keeps ─────────────────────────────────────────────────

def test_kept_tracks_come_in_ffmpegs_order_without_what_the_job_removes():
    tracks = [_track(0, "audio"), _track(1, "video"), _track(2, "audio"),
              _track(3, "subtitle"), _track(4, "subtitle"), _track(5, "subtitle")]
    decision = _decision(_drop(2), _extract(4))
    assert mkv.kept_track_ids(decision, tracks) == [1, 0, 3, 5]


def test_a_video_track_is_always_kept():
    assert mkv.kept_track_ids(_decision(_drop(0, "video")), [_track(0, "video")]) == [0]


def test_retagged_languages_are_the_fix_targets():
    decision = _decision(_fix(1, "eng"), _drop(2), _fix(3, "jpn", "subtitle"))
    assert mkv.retagged_languages(decision) == {1: "eng", 3: "jpn"}


# ── What mkvmerge would keep that FFmpeg drops ───────────────────────────────

REGIONAL = [MkvTrack(0, "video", "und", "und"), MkvTrack(1, "audio", "eng", "en-GB"),
            MkvTrack(2, "subtitles", "spa", "es-419"), MkvTrack(3, "subtitles", "spa", "es-ES")]


def test_a_kept_regional_language_is_a_benefit():
    reasons = mkv.benefits(_info(REGIONAL), kept=[0, 1, 2], retagged={})
    assert reasons == ["regional languages en-GB (track 1), es-419 (track 2)"]


def test_a_regional_language_on_a_dropped_track_is_not():
    assert mkv.benefits(_info(REGIONAL), kept=[0], retagged={}) == []


def test_a_regional_language_on_a_retagged_track_is_not():
    """The job writes its own language there, which replaces the region."""
    assert mkv.benefits(_info(REGIONAL[:2]), kept=[0, 1], retagged={1: "jpn"}) == []


def test_a_language_with_no_region_is_not():
    tracks = [MkvTrack(0, "video", "und", "und"), MkvTrack(1, "audio", "eng", "en")]
    assert mkv.benefits(_info(tracks), kept=[0, 1], retagged={}) == []


def test_a_chapters_language_alone_is_not():
    """
    FFmpeg writes every chapter's language as und. A player chooses a
    chapter's name by language only among several names, which counts on
    its own below, so one name's language alone is no reason to change the
    tool: 12 of the user's 28 real files had only this.
    """
    assert mkv.benefits(_info(editions=[ONE_EDITION]), kept=[], retagged={}) == []


def test_a_second_chapter_edition_is_a_benefit():
    second = Edition(ordered=False, hidden=False, atoms=(_atom(("Cut", "eng", "en")),))
    assert mkv.benefits(_info(editions=[ONE_EDITION, second]), kept=[], retagged={}) \
        == ["2 chapter editions"]


def test_a_nested_chapter_is_a_benefit():
    nested = Edition(ordered=False, hidden=False,
                     atoms=(_atom(("Part", "und", None), children=[_atom(("Scene", "und", None))]),))
    assert mkv.benefits(_info(editions=[nested]), kept=[], retagged={}) == ["nested chapters"]


def test_a_chapter_named_in_two_languages_is_a_benefit():
    named = Edition(ordered=False, hidden=False,
                    atoms=(_atom(("Part one", "eng", "en"), ("Partie un", "fre", "fr")),))
    assert mkv.benefits(_info(editions=[named]), kept=[], retagged={}) \
        == ["chapters named in several languages"]


@pytest.mark.parametrize("edition", [
    Edition(ordered=True, hidden=False, atoms=(_atom(("A", "und", None)),)),
    Edition(ordered=False, hidden=True, atoms=(_atom(("A", "und", None)),)),
    Edition(ordered=False, hidden=False, atoms=(_atom(("A", "und", None), hidden=True),)),
    Edition(ordered=False, hidden=False, atoms=(_atom(("A", "und", None), enabled=False),)),
    Edition(ordered=False, hidden=False, atoms=(_atom(("A", "und", None), linked=True),)),
], ids=["ordered edition", "hidden edition", "hidden chapter", "disabled chapter",
        "linked chapter"])
def test_ordered_hidden_or_linked_chapters_are_a_benefit(edition):
    assert mkv.benefits(_info(editions=[edition]), kept=[], retagged={}) \
        == ["ordered, hidden or linked chapters"]


@pytest.mark.parametrize("tag", [
    Tag(level="70", aims=frozenset(), name="TITLE", nested=False),
    Tag(level="30", aims=frozenset({"chapter"}), name="TITLE", nested=False),
    Tag(level="50", aims=frozenset({"edition"}), name="TITLE", nested=False),
    Tag(level="50", aims=frozenset({"attachment"}), name="TITLE", nested=False),
    Tag(level="50", aims=frozenset(), name="ACTOR", nested=True),
    Tag(level="30", aims=frozenset({"track"}), name="TITLE", nested=False),
], ids=["collection level", "aimed at a chapter", "aimed at an edition",
        "aimed at an attachment", "nested", "a track's at another level"])
def test_tags_ffmpeg_cannot_carry_are_a_benefit(tag):
    assert mkv.benefits(_info(tags=[tag]), kept=[], retagged={}) \
        == ["tags at other levels or aimed at chapters"]


@pytest.mark.parametrize("tag", [
    Tag(level="50", aims=frozenset(), name="IMDB", nested=False),
    Tag(level="50", aims=frozenset({"track"}), name="TITLE", nested=False),
], ids=["the film's own", "a track's own"])
def test_tags_ffmpeg_carries_are_not(tag):
    """The film's own tags and a track's at level 50 come through FFmpeg
    (checks 26 and 30 of the explorer hold that)."""
    assert mkv.benefits(_info(tags=[tag]), kept=[], retagged={}) == []


# ── What keeps a file with FFmpeg ────────────────────────────────────────────

def _probe(*streams):
    return {"streams": [dict(s) for s in streams]}


PROBE_REGIONAL = _probe(
    {"index": 0, "codec_type": "video"},
    {"index": 1, "codec_type": "audio", "tags": {"language": "eng"}},
    {"index": 2, "codec_type": "subtitle", "tags": {"language": "spa"}},
    {"index": 3, "codec_type": "subtitle", "tags": {"language": "spa"}})


def test_nothing_stops_a_file_mkvmerge_reads_as_ffprobe_does():
    assert mkv.blockers(_info(REGIONAL), PROBE_REGIONAL, kept=[0, 1, 2, 3], retagged={},
                        input_path="/m/Film.mkv") == []


def test_a_webm_stays_with_ffmpeg():
    assert mkv.blockers(_info(REGIONAL), PROBE_REGIONAL, kept=[0, 1, 2, 3], retagged={},
                        input_path="/m/Clip.WebM") == ["a WebM file"]


def test_tracks_numbered_differently_stay_with_ffmpeg():
    shifted = _probe({"index": 1, "codec_type": "video"},
                     {"index": 0, "codec_type": "audio", "tags": {"language": "eng"}},
                     {"index": 2, "codec_type": "subtitle", "tags": {"language": "spa"}},
                     {"index": 3, "codec_type": "subtitle", "tags": {"language": "spa"}})
    assert mkv.blockers(_info(REGIONAL), shifted, kept=[0, 1, 2, 3], retagged={},
                        input_path="/m/Film.mkv") \
        == ["mkvmerge numbers its tracks differently from ffprobe"]


def test_cover_art_is_not_counted_as_a_track():
    """ffprobe shows a Matroska cover as an attached_pic video stream after
    the tracks; mkvmerge, as an attachment."""
    with_cover = _probe(*PROBE_REGIONAL["streams"],
                        {"index": 4, "codec_type": "video", "disposition": {"attached_pic": 1}})
    assert mkv.blockers(_info(REGIONAL), with_cover, kept=[0, 1, 2, 3], retagged={},
                        input_path="/m/Film.mkv") == []


@pytest.mark.parametrize("written, read", [("en", "eng"), ("fra", "fre"), ("undefined", "und")])
def test_a_kept_tracks_language_mkvmerge_would_write_back_differently_stays_with_ffmpeg(
        written, read):
    tracks = [MkvTrack(0, "video", "und", None), MkvTrack(1, "audio", read, None)]
    probe = _probe({"index": 0, "codec_type": "video"},
                   {"index": 1, "codec_type": "audio", "tags": {"language": written}})
    assert mkv.blockers(_info(tracks), probe, kept=[0, 1], retagged={}, input_path="/m/F.mkv") \
        == [f"mkvmerge would write back {written} (track 1) as {read}"]


def test_a_dropped_or_retagged_track_is_not_held_to_its_language():
    tracks = [MkvTrack(0, "video", "und", None), MkvTrack(1, "audio", "eng", None),
              MkvTrack(2, "audio", "fre", None)]
    probe = _probe({"index": 0, "codec_type": "video"},
                   {"index": 1, "codec_type": "audio", "tags": {"language": "en"}},
                   {"index": 2, "codec_type": "audio", "tags": {"language": "fra"}})
    assert mkv.blockers(_info(tracks), probe, kept=[0, 1], retagged={1: "eng"},
                        input_path="/m/F.mkv") == []


def test_an_untagged_track_is_und_on_both_sides():
    tracks = [MkvTrack(0, "video", "und", None), MkvTrack(1, "audio", None, None)]
    probe = _probe({"index": 0, "codec_type": "video"}, {"index": 1, "codec_type": "audio"})
    assert mkv.blockers(_info(tracks), probe, kept=[0, 1], retagged={}, input_path="/m/F.mkv") == []


# ── The command ──────────────────────────────────────────────────────────────

TRACKS = [_track(0, "video"), _track(1, "audio"), _track(2, "audio"),
          _track(3, "subtitle"), _track(4, "subtitle")]


def test_the_command_keeps_what_the_job_keeps_in_ffmpegs_order():
    decision = _decision(_drop(2), _extract(4), _fix(1, "eng"))
    cmd = mkv.build_mkvmerge_command("/m/in.mkv", "/t/out", decision, TRACKS, keep_date=True)
    assert cmd == [app_settings.MKVMERGE_PATH, "--gui-mode", "--output", "/t/out",
                   "--audio-tracks", "1", "--subtitle-tracks", "3",
                   "--language", "1:eng", "--track-order", "0:0,0:1,0:3", "/m/in.mkv"]


def test_a_job_that_keeps_no_audio_or_subtitles_says_so():
    decision = _decision(_drop(1), _drop(2), _drop(3, "subtitle"), _drop(4, "subtitle"))
    cmd = mkv.build_mkvmerge_command("/m/in.mkv", "/t/out", decision, TRACKS, keep_date=True)
    assert "--no-audio" in cmd and "--no-subtitles" in cmd
    assert "--audio-tracks" not in cmd and "--subtitle-tracks" not in cmd


def test_a_language_for_a_track_the_job_removes_is_not_passed():
    decision = _decision(_drop(2), Action(action_type="drop_track", description="d",
                                          track_type="audio", stream_index=2,
                                          target_language="jpn"))
    cmd = mkv.build_mkvmerge_command("/m/in.mkv", "/t/out", decision, TRACKS, keep_date=True)
    assert "--language" not in cmd


def test_a_source_with_no_date_gets_none():
    with_date = mkv.build_mkvmerge_command("/m/in.mkv", "/t/out", _decision(), TRACKS,
                                           keep_date=True)
    without = mkv.build_mkvmerge_command("/m/in.mkv", "/t/out", _decision(), TRACKS,
                                         keep_date=False)
    assert "--no-date" not in with_date
    assert without[-2:] == ["--no-date", "/m/in.mkv"]


@pytest.mark.parametrize("line, value", [
    ("#GUI#progress 45%", 45.0), ("#GUI#progress 100%\n", 100.0), ("#GUI#progress 7.5%", 7.5),
    ("#GUI#warning something", None), ("progress=end", None), ("", None)])
def test_gui_progress(line, value):
    assert mkv.parse_gui_progress(line) == value


# ── Real files ───────────────────────────────────────────────────────────────

CHAPTERS = """<?xml version="1.0"?>
<Chapters>
 <EditionEntry><EditionFlagDefault>1</EditionFlagDefault>
  <ChapterAtom><ChapterUID>1</ChapterUID><ChapterTimeStart>00:00:00.000</ChapterTimeStart>
   <ChapterDisplay><ChapterString>Part one</ChapterString><ChapLanguageIETF>en</ChapLanguageIETF></ChapterDisplay>
   <ChapterDisplay><ChapterString>Partie un</ChapterString><ChapLanguageIETF>fr</ChapLanguageIETF></ChapterDisplay>
   <ChapterAtom><ChapterUID>2</ChapterUID><ChapterTimeStart>00:00:00.500</ChapterTimeStart>
    <ChapterDisplay><ChapterString>Nested</ChapterString><ChapLanguageIETF>en</ChapLanguageIETF></ChapterDisplay></ChapterAtom>
  </ChapterAtom>
 </EditionEntry>
 <EditionEntry>
  <ChapterAtom><ChapterUID>3</ChapterUID><ChapterTimeStart>00:00:00.000</ChapterTimeStart>
   <ChapterDisplay><ChapterString>Cut</ChapterString><ChapLanguageIETF>en</ChapLanguageIETF></ChapterDisplay></ChapterAtom>
 </EditionEntry>
</Chapters>
"""

TAGS = """<?xml version="1.0"?>
<Tags>
 <Tag><Targets><TargetTypeValue>70</TargetTypeValue></Targets><Simple><Name>TITLE</Name><String>Collection</String></Simple></Tag>
</Tags>
"""


def _run(*cmd):
    subprocess.run(cmd, check=True, capture_output=True)


def _matroska(tmp_path, name="film.mkv", date=True):
    """A two-second Matroska with a video, English (en-GB) and German
    audio, two Spanish subtitles told apart by region, two chapter
    editions with a nested chapter and two names, and a level-70 tag."""
    base = tmp_path / "base.mkv"
    _run("ffmpeg", "-v", "error", "-f", "lavfi", "-i", "testsrc=d=2:s=160x90:r=24",
         "-f", "lavfi", "-i", "sine=f=440:d=2", "-f", "lavfi", "-i", "sine=f=550:d=2",
         "-map", "0", "-map", "1", "-map", "2", "-c:v", "libx264", "-preset", "ultrafast",
         "-c:a", "aac", str(base))
    for region in ("419", "ES"):
        (tmp_path / f"{region}.srt").write_text(f"1\n00:00:00,500 --> 00:00:01,000\nes-{region}\n")
    (tmp_path / "chapters.xml").write_text(CHAPTERS)
    (tmp_path / "tags.xml").write_text(TAGS)
    out = tmp_path / name
    _run("mkvmerge", "-q", *([] if date else ["--no-date"]), "-o", str(out),
         "--chapters", str(tmp_path / "chapters.xml"), "--global-tags", str(tmp_path / "tags.xml"),
         "--language", "1:en-GB", "--language", "2:de", str(base),
         "--language", "0:es-419", str(tmp_path / "419.srt"),
         "--language", "0:es-ES", str(tmp_path / "ES.srt"))
    return out


def _probe_file(path):
    r = subprocess.run(["ffprobe", "-v", "error", "-show_streams", "-show_format", "-of", "json",
                        str(path)], check=True, capture_output=True, text=True)
    return json.loads(r.stdout)


@needs_binaries
def test_read_matroska_sees_what_ffprobe_does_not(tmp_path):
    info = mkv.read_matroska(str(_matroska(tmp_path)))
    assert [(t.id, t.type, t.language, t.language_ietf) for t in info.tracks] == [
        (0, "video", "und", "und"), (1, "audio", "eng", "en-GB"), (2, "audio", "ger", "de"),
        (3, "subtitles", "spa", "es-419"), (4, "subtitles", "spa", "es-ES")]
    assert len(info.editions) == 2
    assert [n for n, _, _ in info.editions[0].atoms[0].names] == ["Part one", "Partie un"]
    assert len(info.editions[0].atoms[0].children) == 1
    assert Tag(level="70", aims=frozenset(), name="TITLE", nested=False) in info.tags
    assert mkv.benefits(info, kept=[0, 1, 3, 4], retagged={}) == [
        "regional languages en-GB (track 1), es-419 (track 3), es-ES (track 4)",
        "2 chapter editions", "nested chapters", "chapters named in several languages",
        "tags at other levels or aimed at chapters"]


@needs_binaries
def test_a_real_file_is_numbered_alike_by_both_tools(tmp_path):
    path = _matroska(tmp_path)
    info = mkv.read_matroska(str(path))
    assert mkv.blockers(info, _probe_file(path), kept=[0, 1, 3, 4], retagged={},
                        input_path=str(path)) == []


@needs_binaries
def test_read_matroska_refuses_what_is_not_matroska(tmp_path):
    mp4 = tmp_path / "clip.mp4"
    _run("ffmpeg", "-v", "error", "-f", "lavfi", "-i", "testsrc=d=1:s=160x90:r=24",
         "-c:v", "libx264", "-preset", "ultrafast", str(mp4))
    junk = tmp_path / "junk.mkv"
    junk.write_bytes(b"not a media file" * 64)
    assert mkv.read_matroska(str(mp4)) is None
    assert mkv.read_matroska(str(junk)) is None


@needs_binaries
def test_unreadable_chapters_keep_the_file_with_ffmpeg(tmp_path, monkeypatch):
    """A file whose chapters cannot be read cannot be judged, so it is not
    read as one without chapters."""
    path = _matroska(tmp_path)
    monkeypatch.setattr(app_settings, "MKVEXTRACT_PATH", str(tmp_path / "no-mkvextract-here"))
    assert mkv.read_matroska(str(path)) is None


@needs_binaries
def test_the_command_keeps_regions_editions_and_tag_levels(tmp_path):
    source = _matroska(tmp_path)
    tracks = [_track(0, "video"), _track(1, "audio"), _track(2, "audio"),
              _track(3, "subtitle"), _track(4, "subtitle")]
    out = tmp_path / "out.mkv"
    cmd = mkv.build_mkvmerge_command(str(source), str(out), _decision(_drop(2)), tracks,
                                     keep_date=True)
    assert subprocess.run(cmd, capture_output=True).returncode == 0
    info = mkv.read_matroska(str(out))
    assert [t.language_ietf for t in info.tracks] == ["und", "en-GB", "es-419", "es-ES"]
    assert len(info.editions) == 2 and len(info.editions[0].atoms[0].names) == 2
    assert Tag(level="70", aims=frozenset(), name="TITLE", nested=False) in info.tags


@needs_binaries
def test_the_date_goes_back_to_the_nanosecond(tmp_path):
    """mkvmerge writes the time it ran; the source's DateUTC is copied as
    stored. 123456789 ns past the second: ffprobe shows the microseconds,
    the reader the rest."""
    source = _matroska(tmp_path, "source.mkv")
    original = mkv.read_date_utc(str(source))
    wanted = original - original % 1_000_000_000 + 123_456_789
    mkv.write_date_utc(str(source), wanted)
    out = tmp_path / "out.mkv"
    _run(*mkv.build_mkvmerge_command(str(source), str(out), _decision(), [], keep_date=True))
    assert mkv.read_date_utc(str(out)) != wanted
    mkv.write_date_utc(str(out), mkv.read_date_utc(str(source)))
    assert mkv.read_date_utc(str(out)) == wanted
    made = _probe_file(out)["format"]["tags"]["creation_time"]
    assert made.endswith(".123456Z"), made


@needs_binaries
def test_a_file_with_no_date_reads_none_and_cannot_be_given_one(tmp_path):
    path = _matroska(tmp_path, date=False)
    assert mkv.read_date_utc(str(path)) is None
    with pytest.raises(mkv.DateUnreadable):
        mkv.write_date_utc(str(path), 0)


def test_a_file_that_is_not_ebml_has_no_readable_date(tmp_path):
    junk = tmp_path / "junk.mkv"
    junk.write_bytes(b"\x00" * 64)
    with pytest.raises(mkv.DateUnreadable):
        mkv.read_date_utc(str(junk))


def test_the_date_is_found_past_a_seek_head_and_padding(tmp_path):
    """Info need not come first in the segment: here a SeekHead and a Void
    sit before it, as mkvmerge writes them."""
    def element(eid: bytes, body: bytes) -> bytes:
        return eid + bytes([0x80 | len(body)]) + body if len(body) < 127 else \
            eid + b"\x40" + bytes([len(body)]) + body
    date = struct.pack(">q", 987_654_321)
    info = element(b"\x15\x49\xA9\x66", element(b"\x2A\xD7\xB1", b"\x0F\x42\x40")
                   + element(b"\x44\x61", date))
    segment_body = element(b"\x11\x4D\x9B\x74", b"\x00" * 10) + element(b"\xEC", b"\x00" * 20) + info
    data = element(b"\x1A\x45\xDF\xA3", element(b"\x42\x82", b"matroska")) \
        + b"\x18\x53\x80\x67" + b"\x01\xFF\xFF\xFF\xFF\xFF\xFF\xFF" + segment_body
    path = tmp_path / "hand.mkv"
    path.write_bytes(data)
    assert mkv.read_date_utc(str(path)) == 987_654_321
    mkv.write_date_utc(str(path), -5)
    assert mkv.read_date_utc(str(path)) == -5


def test_available_needs_both_binaries(monkeypatch, tmp_path):
    monkeypatch.setattr(app_settings, "MKVMERGE_PATH", "sh")
    monkeypatch.setattr(app_settings, "MKVEXTRACT_PATH", str(tmp_path / "missing"))
    assert not mkv.available()
    monkeypatch.setattr(app_settings, "MKVEXTRACT_PATH", "sh")
    assert mkv.available()
