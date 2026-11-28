"""
What a Matroska written as an MP4 keeps of its tags and chapter names.

FFmpeg reads a Matroska's tags without their levels: of a show's title
(TITLE at 70), a season (PART_NUMBER at 60) and an episode (PART_NUMBER and
TITLE at 50) it showed one TITLE and one PART_NUMBER, and wrote the season's
number as the MP4's track number while the episode's and the show's title
were lost; tags that name their level (COLLECTION, SEASON, EPISODE, as
mkvmerge's templates write them) it wrote nothing of, not even the title. A
DATE_RELEASED it dropped. So a Matroska with any of these was left as it
was by default (Tags an MP4 Can't Hold), or lost them. A job now writes them
under the MP4's own names, show, season_number, episode_sort, title and
date, by the levels MKVToolNix reads (mkvtoolnix.mp4_tags), and the checks
that refuse a lost tag count them kept there (probe.lost_file_tags).

A chapter named in several languages FFmpeg gives its last name (Partie
un, not Part one); a job gives it the name in the first language the user
keeps (mkvtoolnix.chapter_titles).

Verified by mutation, 33 applied, 33 killed. Run first against the suite as
it stood before this file, the show not mapped and the language preference
ignored survived. Killed now: the show, the season, the episode, the date
not mapped, or the track not the episode (one mutant each); the track kept
for a season with no episode; every file taken as a series; numbers left as
written, or words taken as numbers; tags in a language, or aimed at a
chapter or a track, taken; a title written over the file's own, or where
FFmpeg writes one; the level's name, or the track name for PART_NUMBER, not
how ffprobe shows a tag (one mutant each); the preference ignored; a name
with no language not English; a UID past 2^63 not found; a nested chapter
not found; a name written where FFmpeg's is already it; a tag held under
the MP4's name not counted kept, or counted kept at another value; the
scan's prediction without the mapping, or not writing it; the staging check
without it; the scan, the probe made ahead or the job's pickup not giving
the path (one mutant each; the probe made ahead first surviving, now tested
on its own); the tags or the names not written into the command; the run
that also extracts a subtitle not given them; a Matroska FFmpeg writes not
given the names; an MP4 not given the tags.
"""
import subprocess

import pytest

from app.core import mkvtoolnix as mkv
from app.core.mkvtoolnix import ChapterAtom, Edition, MatroskaInfo, Tag
from app.core.probe import lost_file_tags, mp4_lost_tags, probe_file
from tests import test_kept_file_tags as _kt

rig = _kt.rig


def _tag(level, name, value, type_=None, aims=frozenset(), nested=False, language=None):
    return Tag(level=level, aims=frozenset(aims), name=name, nested=nested,
               type=type_, value=value, language=language)


def _info(*tags, title=None, editions=()):
    return MatroskaInfo(tracks=(), editions=tuple(editions), tags=tuple(tags), title=title)


SERIES = [("70", "TITLE", "The Show", "COLLECTION"), ("60", "PART_NUMBER", "2", "SEASON"),
          ("50", "TITLE", "The Episode", "EPISODE"), ("50", "PART_NUMBER", "5", "EPISODE"),
          ("50", "DATE_RELEASED", "2024-10-24", "EPISODE")]


# ── The mapping ──────────────────────────────────────────────────────────────

def test_a_series_whose_tags_name_their_levels():
    tags = mkv.mp4_tags(_info(*(_tag(lv, n, v, t) for lv, n, v, t in SERIES)))
    assert tags.metadata == {"title": "The Episode", "show": "The Show", "season_number": "2",
                             "date": "2024-10-24", "episode_sort": "5", "track": "5"}
    assert tags.kept_as == {
        ("episode/title", "The Episode"): ("title", "The Episode"),
        ("collection/title", "The Show"): ("show", "The Show"),
        ("season/part_number", "2"): ("season_number", "2"),
        ("episode/date_released", "2024-10-24"): ("date", "2024-10-24"),
        ("episode/part_number", "5"): ("episode_sort", "5")}


def test_a_series_whose_tags_give_levels_only():
    """FFmpeg writes the title itself here, and shows the part numbers as
    track: the season's no longer becomes the MP4's track number."""
    tags = mkv.mp4_tags(_info(*(_tag(lv, n, v) for lv, n, v, _ in SERIES)))
    assert tags.metadata == {"show": "The Show", "season_number": "2", "date": "2024-10-24",
                             "episode_sort": "5", "track": "5"}
    assert tags.kept_as[("track", "2")] == ("season_number", "2")
    assert tags.kept_as[("title", "The Show")] == ("show", "The Show")
    assert tags.kept_as[("track", "5")] == ("episode_sort", "5")


def test_a_season_without_an_episode_number_writes_no_track_number():
    tags = mkv.mp4_tags(_info(_tag("70", "TITLE", "The Show"), _tag("60", "PART_NUMBER", "2")))
    assert tags.metadata["track"] == ""


def test_a_film_keeps_its_part_number_as_ffmpeg_writes_it():
    """No show and no season: a part number at 50 is not an episode."""
    tags = mkv.mp4_tags(_info(_tag("50", "PART_NUMBER", "2"),
                              _tag("50", "DATE_RELEASED", "1999-03-31")))
    assert tags.metadata == {"date": "1999-03-31"}


def test_numbers_are_written_as_numbers_and_others_left_out():
    tags = mkv.mp4_tags(_info(_tag("70", "TITLE", "S"), _tag("60", "PART_NUMBER", "Two"),
                              _tag("50", "PART_NUMBER", "05")))
    assert "season_number" not in tags.metadata
    assert tags.metadata["episode_sort"] == "5"
    assert tags.kept_as[("track", "05")] == ("episode_sort", "5")


def test_only_the_files_own_tags_in_no_language_count():
    """A tag aimed at a chapter or a track, a nested one, or one in a
    language (a translation, shown as TITLE-eng) is not the show's."""
    tags = mkv.mp4_tags(_info(
        _tag("70", "TITLE", "Translated", language="eng"),
        _tag("70", "TITLE", "Chapter's", aims={"chapter"}),
        _tag("70", "TITLE", "Nested", nested=True),
        _tag("70", "TITLE", "The Show", language="und")))
    assert tags.metadata["show"] == "The Show"


def test_a_title_is_left_to_ffmpeg_where_it_writes_one():
    """A file with a title of its own (the segment's) keeps it."""
    tags = mkv.mp4_tags(_info(_tag("50", "TITLE", "The Episode", "EPISODE"), title="Own"))
    assert "title" not in tags.metadata


def test_nothing_without_mkvtoolnix():
    assert mkv.mp4_tags(None) == mkv.Mp4Tags({}, {})


# ── Kept under another name ──────────────────────────────────────────────────

def _probe_tags(**tags):
    return {"format": {"tags": tags}}


def test_a_tag_the_mp4_holds_under_its_own_name_is_kept():
    kept_as = {("date_released", "2024-10-24"): ("date", "2024-10-24")}
    source = _probe_tags(DATE_RELEASED="2024-10-24")
    assert lost_file_tags(source, _probe_tags(date="2024-10-24"), kept_as) == []
    assert lost_file_tags(source, _probe_tags(date="2024-10-25"), kept_as) == ["DATE_RELEASED"]
    assert lost_file_tags(source, _probe_tags(), kept_as) == ["DATE_RELEASED"]
    assert lost_file_tags(source, _probe_tags(date="2024-10-24")) == ["DATE_RELEASED"]


# ── Chapter names ────────────────────────────────────────────────────────────

def _atom(uid, *names, children=()):
    return ChapterAtom(names=tuple(names), hidden=False, enabled=True, linked=False,
                       children=tuple(children), uid=uid)


EN_FR = (("Part one", "eng", "en"), ("Partie un", "fre", "fr"))


def _chapters(*titles):
    return [{"id": 401 + n, "tags": {"title": t}} for n, t in enumerate(titles)]


def test_a_chapter_gets_its_name_in_the_language_kept():
    info = _info(editions=[Edition(ordered=False, hidden=False, atoms=(
        _atom("401", *EN_FR), _atom("402", ("Two", "eng", "en"))))])
    chapters = _chapters("Partie un", "Two")
    assert mkv.chapter_titles(info, chapters, ("eng",)) == {0: "Part one"}
    assert mkv.chapter_titles(info, chapters, ("fre", "eng")) == {}
    assert mkv.chapter_titles(info, chapters, ("ger", "fre")) == {}


def test_with_none_of_its_languages_kept_a_chapter_gets_its_first_name():
    info = _info(editions=[Edition(ordered=False, hidden=False, atoms=(_atom("401", *EN_FR),))])
    assert mkv.chapter_titles(info, _chapters("Partie un"), ("jpn",)) == {0: "Part one"}


def test_a_name_with_no_language_is_english():
    info = _info(editions=[Edition(ordered=False, hidden=False, atoms=(
        _atom("401", ("Partie un", "fre", None), ("Part one", None, None)),))])
    assert mkv.chapter_titles(info, _chapters("Partie un"), ("eng",)) == {0: "Part one"}


def test_a_nested_chapter_is_found_by_its_uid():
    info = _info(editions=[Edition(ordered=False, hidden=False, atoms=(
        _atom("400", ("Top", "eng", "en"), children=[_atom("401", *EN_FR)]),))])
    assert mkv.chapter_titles(info, [{"id": 401, "tags": {"title": "Partie un"}}],
                              ("eng",)) == {0: "Part one"}


def test_a_uid_past_two_to_the_63_is_found_as_ffmpeg_shows_it():
    """FFmpeg holds a chapter's ID signed, so it shows such a UID negative."""
    uid = 2 ** 64 - 5
    info = _info(editions=[Edition(ordered=False, hidden=False, atoms=(_atom(str(uid), *EN_FR),))])
    assert mkv.chapter_titles(info, [{"id": uid - 2 ** 64, "tags": {"title": "Partie un"}}],
                              ("eng",)) == {0: "Part one"}


# ── Jobs ─────────────────────────────────────────────────────────────────────

_TV_TAGS = """<?xml version="1.0"?>
<Tags>
 <Tag><Targets><TargetTypeValue>70</TargetTypeValue>{t70}</Targets><Simple><Name>TITLE</Name><String>The Show</String></Simple></Tag>
 <Tag><Targets><TargetTypeValue>60</TargetTypeValue>{t60}</Targets><Simple><Name>PART_NUMBER</Name><String>2</String></Simple></Tag>
 <Tag><Targets><TargetTypeValue>50</TargetTypeValue>{t50}</Targets>
  <Simple><Name>TITLE</Name><String>The Episode</String></Simple>
  <Simple><Name>PART_NUMBER</Name><String>5</String></Simple>
  <Simple><Name>DATE_RELEASED</Name><String>2024-10-24</String></Simple>
 </Tag>
</Tags>
"""

_CHAPTERS = """<?xml version="1.0"?>
<Chapters><EditionEntry>
 <ChapterAtom><ChapterUID>401</ChapterUID><ChapterTimeStart>00:00:00.000</ChapterTimeStart>
  <ChapterDisplay><ChapterString>Part one</ChapterString><ChapLanguageIETF>en</ChapLanguageIETF></ChapterDisplay>
  <ChapterDisplay><ChapterString>Partie un</ChapterString><ChapLanguageIETF>fr</ChapLanguageIETF></ChapterDisplay></ChapterAtom>
 <ChapterAtom><ChapterUID>402</ChapterUID><ChapterTimeStart>00:00:00.500</ChapterTimeStart>
  <ChapterDisplay><ChapterString>Part two</ChapterString><ChapLanguageIETF>en</ChapLanguageIETF></ChapterDisplay></ChapterAtom>
</EditionEntry></Chapters>
"""


def _episode(rig, typed=True, subtitle=False):
    """English and German audio, the series tags, and chapters named in
    English and French: as mkvmerge writes them, without the title "A
    Film" FFmpeg gave the file. subtitle adds an English SubRip."""
    source = _kt._file(rig.library / "made.mkv", "matroska")
    tags = rig.library.parent / "tags.xml"
    tags.write_text(_TV_TAGS.format(
        t70="<TargetType>COLLECTION</TargetType>" if typed else "",
        t60="<TargetType>SEASON</TargetType>" if typed else "",
        t50="<TargetType>EPISODE</TargetType>" if typed else ""))
    chapters = rig.library.parent / "chapters.xml"
    chapters.write_text(_CHAPTERS)
    path = rig.library / "Show S02E05.mkv"
    extra = []
    if subtitle:
        srt = rig.library.parent / "eng.srt"
        srt.write_text("1\n00:00:00,100 --> 00:00:00,900\nHello\n")
        extra = ["--language", "0:eng", str(srt)]
    subprocess.run(["mkvmerge", "-q", "-o", str(path), "--title", "", "--no-global-tags",
                    "--global-tags", str(tags), "--chapters", str(chapters), str(source),
                    *extra], check=True)
    source.unlink()
    return path


def _mp4(rig):
    out = rig.library / "Show S02E05.mp4"
    probed = probe_file(str(out))
    return ({k.lower(): v for k, v in (probed["format"].get("tags") or {}).items()},
            [(c.get("tags") or {}).get("title") for c in probed.get("chapters") or []])


@pytest.mark.parametrize("typed", [True, False], ids=["levels named", "levels only"])
def test_a_series_converted_to_mp4_keeps_show_season_episode_and_date(rig, typed):
    _kt._set(rig, prefer_mp4_container=True)
    _episode(rig, typed)
    jobs, _ = _kt._scan_and_run(rig)
    assert [status for status, _ in jobs] == ["success"], jobs
    tags, _chapters_ = _mp4(rig)
    assert {k: tags.get(k) for k in ("show", "season_number", "episode_sort", "track",
                                     "title", "date")} == {
        "show": "The Show", "season_number": "2", "episode_sort": "5", "track": "5",
        "title": "The Episode", "date": "2024-10-24"}


def test_the_scan_finds_nothing_an_mp4_would_lose(rig):
    """So the file is converted, not left as it was."""
    path = _episode(rig)
    probed = probe_file(str(path))
    assert mp4_lost_tags(probed, path=str(path)) == []
    assert mp4_lost_tags(probed) != [], "without the path, as before"


def test_the_probe_made_ahead_finds_nothing_lost_either(rig):
    """The probe a caller holding a write takes ahead (scanner.probe_ahead)
    finds the tags the same way."""
    from app.core.scanner import probe_ahead
    path = _episode(rig)
    assert probe_ahead(str(path)).mp4_lost_tags == []


def test_a_tag_the_mp4_cannot_hold_is_still_found(tmp_path):
    source = _kt._file(tmp_path / "made.mkv", "matroska", tag=("IMDB", "tt1234567"))
    assert mp4_lost_tags(probe_file(str(source)), path=str(source)) == ["IMDB"]


@pytest.mark.parametrize("keep, name", [(["eng"], "Part one"), (["fre", "eng"], "Partie un")])
def test_a_chapter_named_in_several_languages_takes_the_one_kept(rig, keep, name):
    _kt._set(rig, prefer_mp4_container=True, keep_audio_languages=keep)
    _episode(rig)
    jobs, _ = _kt._scan_and_run(rig)
    assert [status for status, _ in jobs] == ["success"], jobs
    _tags, chapters = _mp4(rig)
    assert chapters == [name, "Part two"]


def test_a_job_that_also_extracts_a_subtitle_writes_them_too(rig):
    """The run writing the MP4 and a sidecar at once builds its command on
    its own path."""
    _kt._set(rig, prefer_mp4_container=True, extract_text_subtitles_to_srt=True)
    _episode(rig, subtitle=True)
    jobs, _ = _kt._scan_and_run(rig)
    assert [status for status, _ in jobs] == ["success"], jobs
    assert (rig.library / "Show S02E05.en.srt").exists()
    tags, chapters = _mp4(rig)
    assert (tags.get("show"), tags.get("episode_sort"), chapters[0]) == ("The Show", "5", "Part one")


def test_a_matroska_ffmpeg_writes_takes_the_name_kept_too(rig, monkeypatch):
    """mkvmerge keeps every name, but a Matroska it cannot write goes to
    FFmpeg, which took the last."""
    from app.core import ffmpeg

    async def no_mkvmerge(*args, **kwargs):
        return None

    monkeypatch.setattr(ffmpeg, "plan_mkvmerge", no_mkvmerge)
    _kt._set(rig, prefer_mp4_container=False)
    _episode(rig)
    jobs, _ = _kt._scan_and_run(rig)
    assert [status for status, _ in jobs] == ["success"], jobs
    probed = probe_file(str(rig.library / "Show S02E05.mkv"))
    assert [(c.get("tags") or {}).get("title") for c in probed["chapters"]] == [
        "Part one", "Part two"]
    assert "Lavf" in (probed["format"]["tags"].get("ENCODER") or "")


def test_the_job_writes_them_into_the_command():
    """Both kinds of FFmpeg run: the one-output path and the one that also
    extracts subtitles take the same tags and names."""
    from app.core import ffmpeg
    from app.core.decision import ProcessingDecision
    decision = ProcessingDecision(should_process=True, reason="", target_container="mp4",
                                  source_container="mkv")
    cmd = ffmpeg.build_ffmpeg_command("in.mkv", "out.mp4", decision, [],
                                      mp4_tags={"show": "S", "track": ""},
                                      chapter_titles={1: "B", 0: "A"})
    joined = " ".join(cmd)
    assert "-metadata show=S -metadata track=" in joined
    assert "-metadata:c:0 title=A -metadata:c:1 title=B" in joined
    assert cmd.index("-metadata:c:1") < cmd.index("out.mp4")

