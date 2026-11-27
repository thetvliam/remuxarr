"""
Each track's BCP 47 language, stored as Track.language_ietf
(mkvtoolnix.bcp47_languages, mkvtoolnix.with_bcp47).

ffprobe reports a Matroska track's old three-letter code and nothing of its
BCP 47 one, so two subtitles told apart only by region (es-419 and es-ES, as
a real web release has them) were stored as two identical Spanish tracks.
mkvmerge -J reads it. Stored wherever tracks are: by a scan (and the probe
made ahead of one for a caller holding a write), after a job, after a
revert, after AC3 Forge rewrites a file; and refreshed when a job starts,
since a row stored before the column existed holds none and a normal scan
skips an unchanged file. "" is a track with none, every track of a file
that is not Matroska included; NULL is not known.

Like the tags an MP4 would lose, it is read beside the probe, before
anything is written: reading it runs mkvmerge, and SQLite lets one writer
in at a time.

Verified by mutation, 12 applied, 12 killed. Run first against the suite as
it stood before this file, two (a scan not storing them, a job not reading
them when it starts) survived all 3087 tests, as the column was new.
Killed now, each by the test for its behaviour:

  • a scan not storing them; a probe made ahead not bringing them; a scan
    reading them again after one did
  • a job, a revert or AC3 Forge not storing them (one mutant each)
  • a job not reading them when it starts
  • the rows not written; the decision not given them
  • a file that is not Matroska read with mkvmerge
  • tracks numbered differently stored anyway
  • a failed read failing the scan
"""
import asyncio
import json
import shutil
import subprocess

import pytest

from app.config import settings as app_settings
from app.core import mkvtoolnix as mkv
from tests import test_creation_date as _cd
from tests import test_kept_file_tags as _kt
from tests import test_language_answer_during_job as answers

pytestmark = pytest.mark.skipif(
    not (shutil.which("ffmpeg") and shutil.which("ffprobe") and shutil.which("mkvmerge")
         and shutil.which("mkvpropedit")),
    reason="needs FFmpeg and MKVToolNix")

# Fixtures of other modules, bound rather than imported by name.
rig = _cd.rig
database = answers.database
lib = answers.lib


def _regional(path, audio=("eng", "ger"), regions=("en-GB", "de-AT")):
    """A Matroska with one audio track per language, each given a region
    in place (mkvpropedit), and its video with none."""
    _kt._file(path, "matroska", audio=audio)
    edits = []
    for n, region in enumerate(regions):
        edits += ["--edit", f"track:{n + 2}", "--set", f"language-ietf={region}"]
    subprocess.run(["mkvpropedit", "-q", str(path), *edits], check=True)
    return path


def _probe(path):
    out = subprocess.run(["ffprobe", "-v", "error", "-show_streams", "-show_format",
                          "-of", "json", str(path)], capture_output=True, text=True, check=True)
    return json.loads(out.stdout)


def _rows(Session, name):
    """(track_type, language, language_ietf) of the file called name."""
    from app.database.models import MediaFile, Track

    with Session() as db:
        media = db.query(MediaFile).filter(MediaFile.filename == name).one()
        return [(t.track_type, t.language, t.language_ietf) for t in
                db.query(Track).filter(Track.file_id == media.id).order_by(Track.stream_index)]


# ── Reading it ───────────────────────────────────────────────────────────────

def test_a_matroska_tracks_regions_are_read_by_ffprobes_index(tmp_path):
    path = _regional(tmp_path / "Film.mkv")
    assert mkv.bcp47_languages(str(path), _probe(path)) == {0: "", 1: "en-GB", 2: "de-AT"}


def test_a_file_that_is_not_matroska_has_none_and_runs_nothing(tmp_path, monkeypatch):
    path = _kt._file(tmp_path / "Film.mp4", "mp4")
    probe = _probe(path)
    monkeypatch.setattr(app_settings, "MKVMERGE_PATH", str(tmp_path / "no-mkvmerge-here"))
    assert mkv.bcp47_languages(str(path), probe) == {}


def test_without_mkvmerge_they_are_not_known(tmp_path, monkeypatch):
    path = _regional(tmp_path / "Film.mkv")
    probe = _probe(path)
    monkeypatch.setattr(app_settings, "MKVMERGE_PATH", str(tmp_path / "no-mkvmerge-here"))
    assert mkv.bcp47_languages(str(path), probe) is None


def test_a_failed_read_leaves_them_unknown_and_the_scan_goes_on(database, monkeypatch):
    """An mkvmerge that runs and says nothing readable: the tracks are
    stored with their regions unknown, and the file is scanned as before."""
    from app.core import scanner

    _regional(database.library / "Film.mkv")
    monkeypatch.setattr(app_settings, "MKVMERGE_PATH", shutil.which("false"))

    with database.Session() as db:
        stats = scanner.scan_library(db, [str(database.library)], force_probe=True)

    assert stats.errors == 0
    assert _rows(database.Session, "Film.mkv") == [
        ("video", "und", None), ("audio", "eng", None), ("audio", "ger", None)]


def test_tracks_numbered_differently_are_not_known(tmp_path):
    """A region stored against the wrong track would be worse than none."""
    path = _regional(tmp_path / "Film.mkv")
    probe = _probe(path)
    probe["streams"][1]["index"], probe["streams"][2]["index"] = 2, 1
    assert mkv.bcp47_languages(str(path), probe) is None


def test_with_bcp47_marks_each_track():
    tracks = [{"stream_index": 0}, {"stream_index": 1}, {"stream_index": 2}]
    assert [t["language_ietf"] for t in mkv.with_bcp47(tracks, {1: "es-419"})] == [
        "", "es-419", ""]
    assert [t["language_ietf"] for t in mkv.with_bcp47(tracks, None)] == [None, None, None]


# ── Stored by a scan ─────────────────────────────────────────────────────────

def test_a_scan_stores_them(database):
    _regional(database.library / "Film.mkv")
    _kt._file(database.library / "Clip.mp4", "mp4")

    with database.Session() as db:
        from app.core import scanner
        scanner.scan_library(db, [str(database.library)], force_probe=True)

    assert _rows(database.Session, "Film.mkv") == [
        ("video", "und", ""), ("audio", "eng", "en-GB"), ("audio", "ger", "de-AT")]
    assert [r[2] for r in _rows(database.Session, "Clip.mp4")] == ["", "", ""]


def test_a_probe_made_ahead_brings_them_with_it(database, monkeypatch):
    from app.core import scanner
    from app.database.session import get_app_settings

    path = _regional(database.library / "Film.mkv")
    probed = scanner.probe_ahead(str(path))
    assert probed.bcp47 == {0: "", 1: "en-GB", 2: "de-AT"}

    def not_again(*args, **kwargs):
        raise AssertionError("read them again")
    monkeypatch.setattr(scanner, "bcp47_languages", not_again)
    with database.Session() as db:
        scanner._process_file(db, str(path), get_app_settings(db), force_probe=True,
                              dry_run=False, stats=scanner.ScanStats(), probed=probed)
        db.commit()

    assert [r[2] for r in _rows(database.Session, "Film.mkv")] == ["", "en-GB", "de-AT"]


# ── Never while a write is held ──────────────────────────────────────────────

def _watch(monkeypatch, module, seen, lib_or_db):
    """Before every read through module, try to take SQLite's write lock
    from another connection, without waiting, and note whether it was free."""
    import sqlite3

    real = module.bcp47_languages
    database_path = lib_or_db.Session.kw["bind"].url.database

    def check_then_read(*args, **kwargs):
        other = sqlite3.connect(database_path, timeout=0, isolation_level=None)
        try:
            other.execute("BEGIN IMMEDIATE")
            other.execute("ROLLBACK")
            seen.append("free")
        except sqlite3.OperationalError as exc:
            seen.append(f"held ({exc})")
        finally:
            other.close()
        return real(*args, **kwargs)
    monkeypatch.setattr(module, "bcp47_languages", check_then_read)


def test_a_scan_reads_them_with_no_write_open(database, monkeypatch):
    from app.core import scanner

    _regional(database.library / "Film.mkv")
    seen = []
    _watch(monkeypatch, scanner, seen, database)

    with database.Session() as db:
        scanner.scan_library(db, [str(database.library)], force_probe=False)

    assert seen == ["free"]


def test_a_job_reads_them_with_no_write_open_when_it_starts(lib, monkeypatch):
    from app.core import worker

    seen = []
    _watch(monkeypatch, worker, seen, lib)

    answers._run_next(lib)

    assert seen and seen[0] == "free"


# ── Refreshed when a job starts ──────────────────────────────────────────────

def test_a_job_decides_from_them_read_when_it_starts(rig, monkeypatch):
    """A row stored before the column existed holds none (NULL): the job
    reads them before it decides, so the decision sees the regions."""
    from app.core import scanner
    from app.database.models import Track

    source = _regional(rig.library / "Film.mkv")
    with rig.Session() as db:
        item = scanner.queue_single_file(db, str(source))
        assert item is not None
        db.query(Track).update({Track.language_ietf: None})
        db.commit()
    seen = []
    real = rig.worker.analyze_file

    def watch(file_info, tracks, *args, **kwargs):
        seen.append([t.get("language_ietf") for t in tracks])
        return real(file_info, tracks, *args, **kwargs)
    monkeypatch.setattr(rig.worker, "analyze_file", watch)

    async def go():
        job_id = rig.worker._claim_next()
        await rig.worker._run_and_broadcast(job_id, _cd._WS(), asyncio.get_running_loop())
    asyncio.run(go())

    assert seen[0] == ["", "en-GB", "de-AT"]


# ── Stored after a job, a revert and AC3 Forge ───────────────────────────────

def test_a_job_stores_the_regions_of_what_it_wrote(rig):
    """The job keeps the English track and drops the German one; mkvmerge
    writes the file, as it keeps the region."""
    source = _regional(rig.library / "Film.mkv")

    _cd._run_job(rig, source)

    assert _rows(rig.Session, "Film.mkv") == [("video", "und", "und"),
                                              ("audio", "eng", "en-GB")]


def test_a_revert_stores_the_regions_of_what_it_restored(rig):
    """A revert writes with FFmpeg, which writes no region: every track of
    the restored file is stored with none, which is known, not unknown."""
    from app.core.revert_restore import restore_revert_point
    from app.database.models import RevertPoint

    source = _regional(rig.library / "Film.mkv")
    _cd._run_job(rig, source)
    with rig.Session() as db:
        point_id = db.query(RevertPoint).one().id

    outcome = asyncio.run(restore_revert_point(point_id))

    assert outcome.success, outcome.error
    assert _rows(rig.Session, "Film.mkv") == [("video", "und", ""), ("audio", "eng", ""),
                                              ("audio", "ger", "")]


def test_ac3_forge_stores_the_regions_of_what_it_rewrote(tmp_path):
    from app.core.forge import probe_tracks_after_forge

    path = _regional(tmp_path / "Film.mkv")
    assert [t["language_ietf"] for t in probe_tracks_after_forge(str(path))] == [
        "", "en-GB", "de-AT"]
