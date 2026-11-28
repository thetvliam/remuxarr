"""
What a revert point records of a Matroska original that ffprobe cannot see
(mkvtoolnix.matroska_record, revert.build_manifest's matroska): each
stream's BCP 47 language, and the chapters and tags as mkvextract writes
them, verbatim.

A revert writes the file with FFmpeg, which keeps none of them, then puts
each track's BCP 47 language back with mkvpropedit
(mkvtoolnix.write_matroska_record): without it an es-419 subtitle came back plain
Spanish, a Keep Subtitle Languages entry of es-419 matched nothing, and the
next job dropped the track the first one kept. The chapters and the tags
are not restored yet (the root README's revert limitations): the restore
that does will need them, and only the first job ever sees the original, so
a point records them now or never. They come from the original, not from
what the job wrote: a dropped track's region is only there.

Verified by mutation, 8 applied, 8 killed. Run first against the suite as it
stood before this file, two (capture recording nothing, the streams'
languages not recorded) survived all 3101 tests. Killed now, each by the
test for its behaviour: capture recording nothing, or recording what the job
wrote instead of the original; the streams' languages, the chapters or the
tags not recorded (one mutant each); a file that is not Matroska recorded;
a record made without MKVToolNix; a manifest without a record given one.

The languages written back, verified by mutation, 9 applied, 9 killed. Run
first against the suite as it stood before them, the write not made at all
survived all 3107 tests. A second, tracks numbered differently written
anyway, failed one: test_track_regions' revert test, which said a revert
stores no region and failed on the fix without a mutant, so it was no kill;
it now says the regions come back. Killed now, each by the test for its
behaviour: the write not made; a failed write ignored, or its message lost;
tracks numbered differently written; a track that already has its language
written again; mkvpropedit's warning taken as a failure; no mkvpropedit
failing the revert; a track given none written as und; the language written
to the wrong track.
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
    assert [s.get("uid") for s in manifest["streams"]] == [
        record["track_uids"][i] for i in range(3)]
    assert manifest["matroska"] == {"chapters": record["chapters"], "tags": record["tags"],
                                    "attachments": [], "info": record["info"]}
    assert record["info"]["title"] == "A Film" and record["info"]["segment_uid"]


def test_a_manifest_without_a_record_has_none_of_it(tmp_path):
    """As every point recorded before it, and every original that is not
    Matroska."""
    path = _original(tmp_path / "Film.mkv")
    manifest = build_manifest(_probe(path), original_path=str(path), original_container="mkv")
    assert "matroska" not in manifest
    assert not any("language_ietf" in s or "uid" in s for s in manifest["streams"])


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


# ── Written back by a revert ─────────────────────────────────────────────────

def _job_and_point(rig):
    """The job drops the German (de-AT) track and keeps the English
    (en-GB) one: (the original's path, the point's id)."""
    from app.database.models import RevertPoint
    source = _original(rig.library / "Film.mkv")
    _cd._run_job(rig, source)
    with rig.Session() as db:
        return source, db.query(RevertPoint).one().id


def _revert(point_id):
    import asyncio
    from app.core.revert_restore import restore_revert_point
    return asyncio.run(restore_revert_point(point_id))


def _languages(path):
    return mkv.bcp47_languages(str(path), _probe(path))


def _write_languages(path, probe, languages):
    """write_matroska_record with languages only, as a point recorded
    before the UIDs were would give it."""
    return mkv.write_matroska_record(path, probe, mkv.RecordToWrite(
        languages=languages, track_uids=None, attachments=None, info=None,
        chapters=None, tags=None))


def _failing(tmp_path, code):
    """A stand-in mkvpropedit that says why and exits with code."""
    script = tmp_path / f"mkvpropedit-{code}"
    script.write_text(f"#!/bin/sh\necho 'Error: the file could not be opened' >&2\nexit {code}\n")
    script.chmod(0o755)
    return str(script)


def test_a_revert_writes_each_tracks_regional_language_back(rig):
    """The dropped track's region as well as the kept one's, and the old
    language codes FFmpeg wrote are left as they were."""
    source, point_id = _job_and_point(rig)
    outcome = _revert(point_id)
    assert outcome.success, outcome.error
    assert _languages(source) == {0: "", 1: "en-GB", 2: "de-AT"}
    assert [s.get("tags", {}).get("language") for s in _probe(source)["streams"]] == [
        None, "eng", "ger"]


def test_without_mkvpropedit_a_revert_goes_on_without_them(rig, tmp_path, monkeypatch):
    source, point_id = _job_and_point(rig)
    monkeypatch.setattr(app_settings, "MKVPROPEDIT_PATH", str(tmp_path / "no-mkvpropedit-here"))
    outcome = _revert(point_id)
    assert outcome.success, outcome.error
    assert _languages(source) == {0: "", 1: "", 2: ""}


def test_a_failed_write_stops_the_revert_and_changes_nothing(rig, tmp_path, monkeypatch):
    """mkvpropedit edits the file in place: one that fails may have left it
    half written, so it is not swapped in, and the point is kept."""
    from app.database.models import RevertPoint
    source, point_id = _job_and_point(rig)
    processed = source.read_bytes()
    monkeypatch.setattr(app_settings, "MKVPROPEDIT_PATH", _failing(tmp_path, 2))
    outcome = _revert(point_id)
    assert not outcome.success
    assert "mkvpropedit could not write the original's Matroska properties back" in outcome.error
    assert "could not be opened" in outcome.error
    assert outcome.error.endswith("Nothing was changed.")
    assert source.read_bytes() == processed
    with rig.Session() as db:
        assert db.query(RevertPoint).count() == 1


def test_a_warning_from_mkvpropedit_is_not_a_failure(tmp_path, monkeypatch):
    """mkvpropedit exits 1 when it made the change but has a warning."""
    path = _kt._file(tmp_path / "Film.mkv", "matroska", audio=("eng",))
    monkeypatch.setattr(app_settings, "MKVPROPEDIT_PATH", _failing(tmp_path, 1))
    assert _write_languages(str(path), _probe(path), {1: "en-GB"}) is None


def test_a_track_that_already_has_its_language_is_left_alone(tmp_path, monkeypatch):
    """Nothing to change runs nothing, so not even a failing mkvpropedit
    stops it."""
    path = _original(tmp_path / "Film.mkv")
    monkeypatch.setattr(app_settings, "MKVPROPEDIT_PATH", _failing(tmp_path, 2))
    assert _write_languages(str(path), _probe(path), {1: "en-GB", 2: "de-AT"}) is None


def test_tracks_numbered_differently_are_given_none(tmp_path):
    """A language written to the wrong track would be worse than none."""
    path = _kt._file(tmp_path / "Film.mkv", "matroska", audio=("eng", "ger"))
    probe = _probe(path)
    probe["streams"][1]["index"], probe["streams"][2]["index"] = 2, 1
    assert _write_languages(str(path), probe, {1: "en-GB", 2: "de-AT"}) is None
    assert _languages(path) == {0: "", 1: "", 2: ""}


def test_only_the_tracks_given_one_are_changed(tmp_path):
    path = _kt._file(tmp_path / "Film.mkv", "matroska", audio=("eng", "ger"))
    assert _write_languages(str(path), _probe(path), {2: "de-CH"}) is None
    assert _languages(path) == {0: "", 1: "", 2: "de-CH"}
