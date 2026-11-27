"""
What a revert point records of a Matroska original that ffprobe cannot see
(mkvtoolnix.matroska_record, revert.build_manifest's matroska): each
stream's BCP 47 language, and the chapters and tags as mkvextract writes
them, verbatim.

Nothing restores them yet. A revert writes the file with FFmpeg, which
keeps none of them (the root README's revert limitations): the restore that
does will need them, and only the first job ever sees the original, so a
point records them now or never. They come from the original, not from what
the job wrote: a dropped track's region is only there.

Verified by mutation, 8 applied, 8 killed. Run first against the suite as it
stood before this file, two (capture recording nothing, the streams'
languages not recorded) survived all 3101 tests. Killed now, each by the
test for its behaviour: capture recording nothing, or recording what the job
wrote instead of the original; the streams' languages, the chapters or the
tags not recorded (one mutant each); a file that is not Matroska recorded;
a record made without MKVToolNix; a manifest without a record given one.
"""
import shutil
import subprocess

import pytest

from app.config import settings as app_settings
from app.core import mkvtoolnix as mkv
from app.core.revert import build_manifest
from tests import test_creation_date as _cd
from tests import test_kept_file_tags as _kt

pytestmark = pytest.mark.skipif(
    not (shutil.which("ffmpeg") and shutil.which("ffprobe") and shutil.which("mkvmerge")
         and shutil.which("mkvextract") and shutil.which("mkvpropedit")),
    reason="needs FFmpeg and MKVToolNix")

rig = _cd.rig

CHAPTERS = """<?xml version="1.0"?>
<Chapters>
 <EditionEntry><EditionFlagDefault>1</EditionFlagDefault>
  <ChapterAtom><ChapterTimeStart>00:00:00.000</ChapterTimeStart>
   <ChapterDisplay><ChapterString>Part one</ChapterString><ChapLanguageIETF>en</ChapLanguageIETF></ChapterDisplay>
   <ChapterDisplay><ChapterString>Partie un</ChapterString><ChapLanguageIETF>fr</ChapLanguageIETF></ChapterDisplay></ChapterAtom>
 </EditionEntry>
 <EditionEntry>
  <ChapterAtom><ChapterTimeStart>00:00:00.000</ChapterTimeStart>
   <ChapterDisplay><ChapterString>Cut</ChapterString><ChapLanguageIETF>en</ChapLanguageIETF></ChapterDisplay></ChapterAtom>
 </EditionEntry>
</Chapters>
"""
TAGS = """<?xml version="1.0"?>
<Tags>
 <Tag><Targets><TargetTypeValue>70</TargetTypeValue></Targets><Simple><Name>TITLE</Name><String>The Collection</String></Simple></Tag>
</Tags>
"""


def _original(path):
    """English (en-GB) and German (de-AT) audio, two chapter editions and a
    collection-level tag, all added in place to an FFmpeg-made Matroska."""
    _kt._file(path, "matroska", audio=("eng", "ger"))
    chapters, tags = path.with_suffix(".chapters.xml"), path.with_suffix(".tags.xml")
    chapters.write_text(CHAPTERS)
    tags.write_text(TAGS)
    subprocess.run(["mkvpropedit", "-q", str(path), "--chapters", str(chapters),
                    "--tags", f"global:{tags}",
                    "--edit", "track:2", "--set", "language-ietf=en-GB",
                    "--edit", "track:3", "--set", "language-ietf=de-AT"], check=True)
    chapters.unlink()
    tags.unlink()
    return path


def _probe(path):
    from app.core.probe import probe_file
    return probe_file(str(path))


def test_a_matroska_record_holds_what_ffprobe_cannot_see(tmp_path):
    path = _original(tmp_path / "Film.mkv")
    record = mkv.matroska_record(str(path), _probe(path))
    assert record["languages"] == {0: "", 1: "en-GB", 2: "de-AT"}
    assert record["chapters"].count("<EditionEntry>") == 2 and "Partie un" in record["chapters"]
    assert "<TargetTypeValue>70</TargetTypeValue>" in record["tags"]


def test_a_file_that_is_not_matroska_has_no_record(tmp_path):
    path = _kt._file(tmp_path / "Film.mp4", "mp4")
    assert mkv.matroska_record(str(path), _probe(path)) is None


def test_without_mkvtoolnix_there_is_no_record(tmp_path, monkeypatch):
    path = _original(tmp_path / "Film.mkv")
    probe = _probe(path)
    monkeypatch.setattr(app_settings, "MKVEXTRACT_PATH", str(tmp_path / "no-mkvextract-here"))
    assert mkv.matroska_record(str(path), probe) is None


def test_the_manifest_holds_the_record(tmp_path):
    path = _original(tmp_path / "Film.mkv")
    probe = _probe(path)
    record = mkv.matroska_record(str(path), probe)

    manifest = build_manifest(probe, original_path=str(path), original_container="mkv",
                              matroska=record)

    assert [s.get("language_ietf") for s in manifest["streams"]] == ["", "en-GB", "de-AT"]
    assert manifest["matroska"] == {"chapters": record["chapters"], "tags": record["tags"]}


def test_a_manifest_without_a_record_has_none_of_it(tmp_path):
    """As every point recorded before it, and every original that is not
    Matroska."""
    path = _original(tmp_path / "Film.mkv")
    manifest = build_manifest(_probe(path), original_path=str(path), original_container="mkv")
    assert "matroska" not in manifest
    assert not any("language_ietf" in s for s in manifest["streams"])


def test_a_job_records_the_original_not_what_it_wrote(rig):
    """The job drops the German track: its region is only in the original.
    The point is still restored as before."""
    from app.core.revert_restore import restore_revert_point
    from app.database.models import RevertPoint
    import asyncio
    import json

    source = _original(rig.library / "Film.mkv")
    _cd._run_job(rig, source)

    with rig.Session() as db:
        point = db.query(RevertPoint).one()
        manifest = json.loads(point.manifest) if isinstance(point.manifest, str) else point.manifest
        point_id = point.id
    assert [s.get("language_ietf") for s in manifest["streams"]] == ["", "en-GB", "de-AT"]
    assert manifest["matroska"]["chapters"].count("<EditionEntry>") == 2
    assert "The Collection" in manifest["matroska"]["tags"]

    outcome = asyncio.run(restore_revert_point(point_id))
    assert outcome.success, outcome.error
