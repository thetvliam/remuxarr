"""
A revert gives back what FFmpeg changed or dropped of a Matroska original.

A revert writes the file with FFmpeg, which gave back one edition of
chapters, flattened, with one name each and no language, its own tags in
place of the original's, a new UID for every track and attachment, and a
film-level TITLE tag turned into the segment's title. The revert point
records the original's chapters and tags as mkvextract writes them, each
track's and attachment's UID and the segment's title and UIDs
(mkvtoolnix.matroska_record), and the revert writes them all back into the
restored file before it is swapped in (mkvtoolnix.write_matroska_record).
Measured first on every Matroska file in the test library: all came back
exact.

The round trips compare what mkvextract and mkvmerge read of the original
and of the restored file: the chapters and tags byte for byte, the track
UIDs, the attachments (UID, name, size, type) and the segment's title and
UIDs.

Verified by mutation, 23 applied, 23 killed. Run first against the suite as
it stood before this file, the chapters not written survived; the track
UIDs not set was already killed there, by a Forge round trip whose tracks
lost the tags aimed at them. Six tests of test_revert_point_matroska failed
on the change without a mutant, written for write_languages and the record
before the UIDs, so they counted as no kill; they are rewritten. Killed now:
the track UIDs, the languages, the attachment UIDs or the segment's
properties not set (one mutant each); an attachment's MIME type not kept; a
title set deleted, or one the original lacked kept; the chapters or the tags
not written (one mutant each); both written when already as recorded; the
chapters written with --disable-language-ietf never, or always; the tags
written without it; tags aimed at a track written from a point without
UIDs; the tags pass failing ignored; the XML files left; tracks numbered
differently given UIDs; the track UIDs, the segment's properties or the
attachments not recorded (one mutant each); a stream's UID, or the record's
attachments and segment properties, not stored in the manifest; the
restore passing no UIDs.
"""
import json
import os
import subprocess

import pytest

from app.config import settings as app_settings
from app.core import mkvtoolnix as mkv
from tests import test_revert_multiple_jobs as _rm


def _run(cmd):
    return subprocess.run(cmd, capture_output=True, text=True, check=True).stdout


def _read(path):
    """What the round trips compare, as mkvextract and mkvmerge read it."""
    j = json.loads(_run(["mkvmerge", "-J", str(path)]))
    c = j["container"]["properties"]
    return {
        "chapters": _run(["mkvextract", str(path), "chapters", "-"]),
        "tags": _run(["mkvextract", str(path), "tags", "-"]),
        "track_uids": [t["properties"]["uid"] for t in j["tracks"]],
        "languages": [t["properties"].get("language_ietf") for t in j["tracks"]],
        "attachments": sorted((a["properties"]["uid"], a["file_name"], a["size"], a["content_type"])
                              for a in j.get("attachments") or []),
        "info": {k: c.get(k) for k in ("title", "segment_uid", "previous_segment_uid",
                                       "next_segment_uid")},
    }


_CHAPTERS = """<?xml version="1.0"?>
<Chapters>
 <EditionEntry><EditionFlagDefault>1</EditionFlagDefault>
  <ChapterAtom><ChapterUID>401</ChapterUID><ChapterTimeStart>00:00:00.000</ChapterTimeStart><ChapterTimeEnd>00:00:00.500</ChapterTimeEnd>
   <ChapterDisplay><ChapterString>Part one</ChapterString><ChapLanguageIETF>en</ChapLanguageIETF></ChapterDisplay>
   <ChapterDisplay><ChapterString>Partie un</ChapterString><ChapLanguageIETF>fr</ChapLanguageIETF></ChapterDisplay>
   <ChapterAtom><ChapterUID>402</ChapterUID><ChapterTimeStart>00:00:00.100</ChapterTimeStart><ChapterTimeEnd>00:00:00.300</ChapterTimeEnd>
    <ChapterDisplay><ChapterString>Nested</ChapterString><ChapLanguageIETF>en</ChapLanguageIETF></ChapterDisplay></ChapterAtom>
  </ChapterAtom>
  <ChapterAtom><ChapterUID>403</ChapterUID><ChapterTimeStart>00:00:00.500</ChapterTimeStart><ChapterTimeEnd>00:00:01.000</ChapterTimeEnd>
   <ChapterDisplay><ChapterString>Part two</ChapterString><ChapLanguageIETF>en</ChapLanguageIETF></ChapterDisplay></ChapterAtom>
 </EditionEntry>
 <EditionEntry>
  <ChapterAtom><ChapterUID>404</ChapterUID><ChapterTimeStart>00:00:00.000</ChapterTimeStart><ChapterTimeEnd>00:00:01.000</ChapterTimeEnd>
   <ChapterDisplay><ChapterString>Director's cut</ChapterString><ChapLanguageIETF>en</ChapLanguageIETF></ChapterDisplay></ChapterAtom>
 </EditionEntry>
</Chapters>
"""

_TAGS = """<?xml version="1.0"?>
<Tags>
 <Tag><Targets><TargetTypeValue>70</TargetTypeValue></Targets><Simple><Name>TITLE</Name><String>The Collection</String></Simple></Tag>
 <Tag><Targets><TargetTypeValue>50</TargetTypeValue></Targets><Simple><Name>TITLE</Name><String>The Film</String></Simple></Tag>
 <Tag><Targets><TargetTypeValue>30</TargetTypeValue><ChapterUID>403</ChapterUID></Targets><Simple><Name>TITLE</Name><String>The Second Part</String></Simple></Tag>
</Tags>
"""


def _remake(rig, *mkvmerge_args, tags=_TAGS, chapters=_CHAPTERS, statistics=True):
    """Rewrite the rig's file with mkvmerge: English (en-GB) and German
    (de-AT) audio, and whatever else the arguments give it."""
    tmp = rig["tmp"]
    args = ["mkvmerge", "-q", "--no-date", "-o", str(tmp / "remade.mkv")]
    if chapters is not None:
        (tmp / "chapters.xml").write_text(chapters)
        args += ["--chapters", str(tmp / "chapters.xml")]
    if tags is not None:
        (tmp / "tags.xml").write_text(tags)
        args += ["--global-tags", str(tmp / "tags.xml")]
    else:
        # Nor the tags FFmpeg wrote into the file it remakes.
        args += ["--no-global-tags"]
    if not statistics:
        # No tag aimed at a track: neither mkvmerge's statistics nor the
        # track tags FFmpeg wrote into the file it remakes.
        args += ["--disable-track-statistics-tags", "--no-track-tags"]
    args += [*mkvmerge_args, "--language", "1:en-GB", "--language", "2:de-AT", str(rig["path"])]
    subprocess.run(args, check=True)
    os.replace(tmp / "remade.mkv", rig["path"])
    stat = rig["path"].stat()
    rig["media"].size, rig["media"].mtime = stat.st_size, stat.st_mtime
    rig["db"].commit()


@pytest.fixture
def rig(tmp_path, monkeypatch):
    return _rm._library(tmp_path, monkeypatch, audio=[(440, "eng"), (880, "ger")])


def _cover_and_font(rig):
    """A cover before a font, the font with the legacy MIME type FFmpeg and
    older tools write."""
    cover = rig["tmp"] / "cover.jpg"
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "color=c=red:s=64x48",
                    "-frames:v", "1", str(cover)], check=True)
    font = rig["tmp"] / "Font.ttf"
    font.write_bytes(b"FONTDATA" * 32)
    return ["--attachment-mime-type", "image/jpeg", "--attach-file", str(cover),
            "--attachment-mime-type", "application/x-truetype-font", "--attach-file", str(font)]


def _drop_german(rig):
    _rm._run_job(rig, ["-map", "0:0", "-map", "0:1", "-map", "0:t?", "-c", "copy"], job_id=1)


def _round_trip(rig):
    original = _read(rig["path"])
    _drop_german(rig)
    outcome = _rm._revert(rig)
    assert outcome.success, outcome.error
    return original, _read(rig["path"])


# ── Round trips ──────────────────────────────────────────────────────────────

def test_a_revert_gives_back_the_chapters_tags_uids_and_title(rig):
    """Two editions, a nested chapter, one named in two languages, tags at
    the collection, film, chapter and track (statistics) levels, regional
    languages, a title, a cover before a font: all as they were."""
    _remake(rig, "--title", "A Film", *_cover_and_font(rig))
    original, restored = _round_trip(rig)
    assert restored == original


def test_the_attachments_keep_the_mime_types_the_file_held(rig):
    """mkvpropedit writes the type of an attachment it gives a UID in its
    modern form, font/ttf, which FFmpeg reads as no codec at all."""
    _remake(rig, *_cover_and_font(rig))
    before = _rm._streams(rig["path"])
    _round_trip(rig)
    after = _rm._streams(rig["path"])
    held = [(s.get("codec_name"), _rm._tag(s, "mimetype")) for s in before
            if s["codec_type"] == "attachment"]
    assert held == [("ttf", "application/x-truetype-font")]
    assert [(s.get("codec_name"), _rm._tag(s, "mimetype")) for s in after
            if s["codec_type"] == "attachment"] == held


def test_a_title_tag_does_not_become_the_title(rig):
    """FFmpeg made a film-level TITLE tag the segment's title, which the
    original did not have: it is removed, and the tag is back."""
    _remake(rig)
    original, restored = _round_trip(rig)
    assert original["info"]["title"] is None
    assert restored == original


def test_tags_ffmpeg_adds_go_when_the_original_had_none(rig):
    _remake(rig, tags=None, statistics=False)
    original, restored = _round_trip(rig)
    assert original["tags"] == ""
    assert restored == original


def test_chapters_without_bcp_47_codes_come_back_without_them(rig):
    """As an older mkvmerge wrote them: mkvpropedit is told not to add the
    codes, and adds none."""
    _remake(rig, "--disable-language-ietf",
            chapters=_CHAPTERS.replace("ChapLanguageIETF", "ChapterLanguage")
            .replace(">en<", ">eng<").replace(">fr<", ">fre<"))
    original, restored = _round_trip(rig)
    assert "ChapLanguageIETF" not in original["chapters"]
    assert restored["chapters"] == original["chapters"]


def test_an_ffmpeg_made_originals_chapters_are_left_as_ffmpeg_gives_them(rig):
    """FFmpeg's own copy is exact, and mkvpropedit would give the edition a
    UID it never had."""
    chapters = rig["tmp"] / "ffmetadata.txt"
    chapters.write_text(";FFMETADATA1\n[CHAPTER]\nTIMEBASE=1/1000\nSTART=0\nEND=500\n"
                        "title=Opening\n[CHAPTER]\nTIMEBASE=1/1000\nSTART=500\nEND=1000\n"
                        "title=Main\n")
    made = rig["tmp"] / "made.mkv"
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", str(rig["path"]), "-i", str(chapters),
                    "-map", "0", "-map_chapters", "1", "-c", "copy", str(made)], check=True)
    os.replace(made, rig["path"])
    stat = rig["path"].stat()
    rig["media"].size, rig["media"].mtime = stat.st_size, stat.st_mtime
    rig["db"].commit()
    original, restored = _round_trip(rig)
    assert "EditionUID" not in original["chapters"]
    assert restored["chapters"] == original["chapters"]


# ── Points recorded before the UIDs were ─────────────────────────────────────

def _as_recorded_before_uids(rig):
    """The point as a revert point made by 2b to 2d holds it: chapters,
    tags and languages, no UIDs, attachments or segment properties."""
    from app.database.models import RevertPoint
    point = rig["db"].query(RevertPoint).one()
    manifest = json.loads(point.manifest)
    for stream in manifest["streams"]:
        stream.pop("uid", None)
    manifest["matroska"] = {"chapters": manifest["matroska"]["chapters"],
                            "tags": manifest["matroska"]["tags"]}
    point.manifest = json.dumps(manifest)
    rig["db"].commit()


def test_an_older_point_gives_back_its_chapters_but_not_tags_aimed_at_tracks(rig):
    """Without the tracks' UIDs, the tags aimed at them would aim at
    nothing: FFmpeg's stay. The chapters come back."""
    _remake(rig)
    original = _read(rig["path"])
    _drop_german(rig)
    _as_recorded_before_uids(rig)
    assert _rm._revert(rig).success
    restored = _read(rig["path"])
    assert restored["chapters"] == original["chapters"]
    assert restored["tags"] != original["tags"]
    assert "<TrackUID>" in original["tags"]


def test_an_older_point_gives_back_tags_aimed_at_no_track(rig):
    _remake(rig, statistics=False)
    original = _read(rig["path"])
    _drop_german(rig)
    _as_recorded_before_uids(rig)
    assert _rm._revert(rig).success
    restored = _read(rig["path"])
    assert "<TrackUID>" not in original["tags"]
    assert (restored["chapters"], restored["tags"]) == (original["chapters"], original["tags"])
    assert restored["info"]["title"] == "The Film", "an older point changes no title"


# ── Failures and leftovers ───────────────────────────────────────────────────

def test_a_failed_tags_write_stops_the_revert(rig, monkeypatch):
    """The tags are written in a pass of their own, after the rest: that
    pass failing stops the revert as the first would."""
    _remake(rig)
    _drop_german(rig)
    processed = rig["path"].read_bytes()
    script = rig["tmp"] / "mkvpropedit"
    script.write_text('#!/bin/sh\nfor a in "$@"; do [ "$a" = "--tags" ] && '
                      '{ echo "Error: no space left" >&2; exit 2; }; done\n'
                      f'exec {_real_mkvpropedit()} "$@"\n')
    script.chmod(0o755)
    monkeypatch.setattr(app_settings, "MKVPROPEDIT_PATH", str(script))
    outcome = _rm._revert(rig)
    assert not outcome.success
    assert "no space left" in outcome.error and outcome.error.endswith("Nothing was changed.")
    assert rig["path"].read_bytes() == processed


def _real_mkvpropedit():
    import shutil
    return shutil.which("mkvpropedit")


def test_no_xml_is_left_beside_the_restored_file(rig, monkeypatch):
    _remake(rig)
    written = []
    real = mkv._run

    def recorded(cmd):
        written.extend(a.split(":", 1)[-1] for a in cmd if a.endswith(".xml"))
        return real(cmd)

    monkeypatch.setattr(mkv, "_run", recorded)
    _round_trip(rig)
    assert len(written) == 2, "both the chapters and the tags were written from files"
    assert not any(os.path.exists(p) for p in written)


# ── What is written, and what is not ─────────────────────────────────────────

def _record(**changes):
    base = dict(languages={}, track_uids=None, attachments=None, info=None,
                chapters=None, tags=None)
    return mkv.RecordToWrite(**{**base, **changes})


def test_nothing_already_as_recorded_is_written(rig, monkeypatch):
    """A file that already holds everything recorded runs no mkvpropedit,
    so not even one that fails stops it."""
    _remake(rig, "--title", "A Film")
    j = json.loads(_run(["mkvmerge", "-J", str(rig["path"])]))
    now = _read(rig["path"])
    record = _record(
        languages={0: "", 1: "en-GB", 2: "de-AT"},
        track_uids={t["id"]: t["properties"]["uid"] for t in j["tracks"]},
        attachments=[], info=now["info"], chapters=now["chapters"], tags=now["tags"])
    monkeypatch.setattr(app_settings, "MKVPROPEDIT_PATH", "false")
    assert mkv.write_matroska_record(str(rig["path"]), _rm._probe(rig["path"]), record) is None


def test_tracks_numbered_differently_are_given_nothing(rig):
    _remake(rig)
    probe = _rm._probe(rig["path"])
    probe["streams"][1]["index"], probe["streams"][2]["index"] = 2, 1
    before = _read(rig["path"])
    record = _record(track_uids={0: 1, 1: 2, 2: 3}, chapters="", tags="")
    assert mkv.write_matroska_record(str(rig["path"]), probe, record) is None
    assert _read(rig["path"]) == before
