"""
Which of a file's own tags a job writing it as an MP4 would lose, found
before any job runs (probe.mp4_lost_tags) and kept on
MediaFile.mp4_lost_tags, from which the decision engine does what Tags an
MP4 Can't Hold says (test_mp4_tag_handling).

A job refuses to replace a file whose output would lose any of its own
tags (test_kept_file_tags): issue #67's Matroska, with an IMDB tag, was
refused every conversion to MP4, after the whole job had run. Knowing
beforehand is what lets a file be decided differently. It is found by
writing the tags alone into a tiny MP4 and reading back what was kept,
through the muxer a job uses, so the answer is FFmpeg's own: the first
tests here hold it against what real jobs refuse.

It runs FFmpeg, so it runs where the probe does, before anything is
written: SQLite lets one writer in at a time, and every other write in the
app waits while a write is held (test_retry_probes_first). Where a caller
already holds one, the probe made ahead for it finds the tags as well
(scanner.probe_ahead).

Verified by mutation, 12 applied, 12 killed. Run first against the suite
as it stood before this file, all twelve survived it. Killed now:

  • Nothing ever found (always [])            → every agreement case of a
                                                lost tag, and every test
                                                below them that expects a
                                                tag or no answer at all
  • Found through the QuickTime muxer         → the agreement cases of
                                                synopsis and description,
                                                both sources, which that
                                                muxer drops
  • The rewritten tags not left out           → test_a_file_with_only_
                                                rewritten_tags_runs_nothing
                                                (an encoder, every rewritten
                                                tag)
  • Not found again when a job starts         → both runs of test_a_job_
                                                finds_them_again_when_it_
                                                starts
  • An FFmpeg that cannot start taken as
    "none lost"                               → test_an_ffmpeg_that_cannot_
                                                start_tells_nothing and
                                                test_a_scan_that_cannot_tell_
                                                leaves_the_file_unknown
  • An FFmpeg that fails taken as "none
    lost"                                     → test_an_ffmpeg_that_fails_
                                                tells_nothing
  • Unknown stored as "none lost"             → test_a_scan_that_cannot_tell_
                                                leaves_the_file_unknown
  • A probe made ahead not bringing them      → test_a_probe_made_ahead_
                                                brings_them_with_it, and both
                                                retry tests, which then see
                                                no look at all
  • A scan of a changed file not storing
    them                                      → test_a_scan_of_a_changed_
                                                file_finds_them_again, and,
                                                since the setting, the
                                                agreement cases of a lost
                                                tag, whose second scan keeps
                                                the answer it should replace
  • A new file's row not storing them         → every agreement case, and
                                                the two tests that read a
                                                new row
  • A scan looking after its first write      → test_a_scan_looks_with_no_
                                                write_open
  • _process_file looking again for a caller
    that probed ahead, inside its write       → test_a_probe_made_ahead_
                                                brings_them_with_it and both
                                                retry tests

No equivalent mutants. The four proposed (nothing found, the wrong muxer,
the rewritten tags not left out, not found again when a job starts) are
among them; the rest were added for each place the answer is stored, for
the two ways the check can fail, and for where it runs.
"""
import asyncio
import json
import shutil
import sqlite3

import pytest

from tests import test_kept_file_tags as _kt
from tests import test_language_answer_during_job as answers
from tests.test_retry_probes_first import _fail, _retry

pytestmark = answers.needs_ffmpeg

# Fixtures of other modules, bound rather than imported by name, so pytest
# finds them and pyflakes does not report the names as unused.
rig = _kt.rig
database = answers.database
lib = answers.lib


def _stored(Session, name):
    """MediaFile.mp4_lost_tags for the file called name, read back."""
    from app.database.models import MediaFile

    with Session() as db:
        value = db.query(MediaFile).filter(MediaFile.filename == name).one().mp4_lost_tags
    return None if value is None else json.loads(value)


def _scan(Session, library):
    from app.core import scanner

    with Session() as db:
        scanner.scan_library(db, [str(library)], force_probe=False)


def _run_jobs(rig):
    """Run every job waiting; their (status, error) in order."""
    from app.database.models import QueueItem

    async def go():
        while (job_id := rig.worker._claim_next()) is not None:
            await rig.worker._run_and_broadcast(job_id, _kt._WS(), asyncio.get_running_loop())
    asyncio.run(go())
    with rig.Session() as db:
        return [(q.status, q.error_message) for q in db.query(QueueItem).order_by(QueueItem.id)]


def _refused_over(error):
    """The tags a job's error names as lost, [] for no such error."""
    prefix = " It would lose: "
    if not error or prefix not in error:
        return []
    return error.split(prefix, 1)[1].rstrip(".").split(", ")


# ── What it finds, against what a job refuses over ───────────────────────────

# Lost by both kinds of job, and kept by both. synopsis and description are
# kept by the MP4 muxer a job uses and dropped by the QuickTime muxer.
# DATE_RELEASED is lost rewriting an MP4, and kept converting a Matroska,
# whose job writes it as the MP4's own date (mkvtoolnix.mp4_tags).
LOST = ["IMDB", "DATE_RELEASED", "title-eng", "Original Title"]
KEPT = ["synopsis", "description", "comment", "genre"]
KEPT_FROM_MATROSKA = {"DATE_RELEASED"}


@pytest.mark.parametrize("key", LOST + KEPT)
@pytest.mark.parametrize("source", ["Film.mkv", "Film.mp4"],
                         ids=["Matroska converted", "MP4 rewritten"])
def test_it_names_the_tags_a_job_is_refused_over(rig, monkeypatch, source, key):
    """
    Prefer MP4 converts the Matroska, and the MP4 is rewritten to remove
    its German track. The scan stores what the job then finds, and a file
    with nothing to lose is not refused.

    A file known to lose tags is left alone before any job runs
    (test_mp4_tag_handling), so once the scan's answer is read the file is
    scanned again as one whose tags could not be found out beforehand
    (test_kept_file_tags._not_known_beforehand), which queues the job.
    """
    _kt._set(rig, prefer_mp4_container=True)
    muxer = "matroska" if source.endswith(".mkv") else "mp4"
    _kt._file(rig.library / source, muxer, tag=(key, "Some value"))
    _scan(rig.Session, rig.library)
    found = _stored(rig.Session, source)
    _kt._not_known_beforehand(monkeypatch)

    jobs, _ = _kt._scan_and_run(rig)

    assert jobs[-1][0] in ("success", "failed")
    assert found == _refused_over(jobs[-1][1])
    lost = key in LOST and not (muxer == "matroska" and key in KEPT_FROM_MATROSKA)
    assert len(found) == (1 if lost else 0), "the case is not what it says"


# ── What it costs, and when it cannot tell ───────────────────────────────────

@pytest.mark.parametrize("tags", [
    {}, {"encoder": "Lavf62.12.103"},
    {"ENCODER": "libebml", "major_brand": "isom", "minor_version": "512",
     "compatible_brands": "isomiso2mp41", "software": "x"},
], ids=["none", "an encoder", "every rewritten tag"])
def test_a_file_with_only_rewritten_tags_runs_nothing(tmp_path, tags):
    """Every job writes these afresh, so there is nothing to find out, and
    an FFmpeg that cannot run is not needed."""
    from app.core.probe import mp4_lost_tags

    assert mp4_lost_tags({"format": {"tags": tags}},
                         ffmpeg_bin=str(tmp_path / "no-ffmpeg-here")) == []


def test_an_ffmpeg_that_cannot_start_tells_nothing(tmp_path):
    from app.core.probe import mp4_lost_tags

    assert mp4_lost_tags({"format": {"tags": {"IMDB": "tt0"}}},
                         ffmpeg_bin=str(tmp_path / "no-ffmpeg-here")) is None


def test_an_ffmpeg_that_fails_tells_nothing():
    from app.core.probe import mp4_lost_tags

    assert mp4_lost_tags({"format": {"tags": {"IMDB": "tt0"}}},
                         ffmpeg_bin=shutil.which("false")) is None


def test_a_scan_that_cannot_tell_leaves_the_file_unknown(rig, monkeypatch, tmp_path):
    """Null, not an empty list: none lost is an answer the check did not
    give."""
    from app.config import settings as app_settings

    _kt._file(rig.library / "Film.mkv", "matroska", tag=("IMDB", "tt0"))
    monkeypatch.setattr(app_settings, "FFMPEG_PATH", str(tmp_path / "no-ffmpeg-here"))

    _scan(rig.Session, rig.library)

    assert _stored(rig.Session, "Film.mkv") is None


# ── Where it is kept ─────────────────────────────────────────────────────────

def test_a_scan_of_a_changed_file_finds_them_again(rig):
    _kt._file(rig.library / "Film.mkv", "matroska")
    _scan(rig.Session, rig.library)
    assert _stored(rig.Session, "Film.mkv") == []

    _kt._file(rig.library / "Film.mkv", "matroska", tag=("IMDB", "tt0"),
              extra=["-metadata", "comment=changed"])
    _scan(rig.Session, rig.library)

    assert _stored(rig.Session, "Film.mkv") == ["IMDB"]


@pytest.mark.parametrize("before", [None, "[]"], ids=["never looked at", "out of date"])
def test_a_job_finds_them_again_when_it_starts(rig, before):
    """
    A row from before the column holds null, and a file replaced since its
    scan by one with the same tracks can hold the wrong answer. The job
    finds them from its own probe, as it does the signs of a recording.
    """
    from app.database.models import MediaFile

    _kt._file(rig.library / "Film.mkv", "matroska", tag=("IMDB", "tt0"))
    _scan(rig.Session, rig.library)
    with rig.Session() as db:
        db.query(MediaFile).one().mp4_lost_tags = before
        db.commit()

    _run_jobs(rig)

    assert _stored(rig.Session, "Film.mkv") == ["IMDB"]


def test_a_probe_made_ahead_brings_them_with_it(database, monkeypatch):
    """
    A caller that holds a write open probes first (scanner.probe_ahead),
    and the tags come with that probe: _process_file does not look again.
    """
    from app.core import scanner
    from app.database.session import get_app_settings

    path = _kt._file(database.library / "Film.mkv", "matroska", tag=("IMDB", "tt0"))
    probed = scanner.probe_ahead(str(path))
    assert probed.mp4_lost_tags == ["IMDB"]

    def not_again(*args, **kwargs):
        raise AssertionError("looked for the tags again")
    monkeypatch.setattr(scanner, "mp4_lost_tags", not_again)
    with database.Session() as db:
        scanner._process_file(db, str(path), get_app_settings(db), force_probe=True,
                              dry_run=False, stats=scanner.ScanStats(), probed=probed)

    assert _stored(database.Session, "Film.mkv") == ["IMDB"]


# ── Never while a write is held ──────────────────────────────────────────────

def _watch(monkeypatch, module, seen, database_path):
    """
    Before every look for the tags through module, try to take SQLite's
    write lock from another connection, without waiting, and note whether
    it was free.
    """
    real = module.mp4_lost_tags

    def check_then_look(probe_data, *args, **kwargs):
        other = sqlite3.connect(database_path, timeout=0, isolation_level=None)
        try:
            other.execute("BEGIN IMMEDIATE")
            other.execute("ROLLBACK")
            seen.append("free")
        except sqlite3.OperationalError as exc:
            seen.append(f"held ({exc})")
        finally:
            other.close()
        return real(probe_data, *args, **kwargs)
    monkeypatch.setattr(module, "mp4_lost_tags", check_then_look)


def _database_path(lib):
    return lib.Session.kw["bind"].url.database


def test_a_scan_looks_with_no_write_open(database, monkeypatch):
    from app.core import scanner

    _kt._file(database.library / "Film.mkv", "matroska", tag=("IMDB", "tt0"))
    seen = []
    _watch(monkeypatch, scanner, seen, _database_path(database))

    _scan(database.Session, database.library)

    assert seen == ["free"]


@pytest.mark.parametrize("which", ["Retry", "Retry All"])
def test_a_retry_looks_with_no_write_of_its_own_open(lib, monkeypatch, which):
    from app.core import scanner
    from app.database.models import QueueItem

    with lib.Session() as db:
        failed = db.query(QueueItem).one().id
    _fail(lib, failed)
    seen = []
    _watch(monkeypatch, scanner, seen, _database_path(lib))

    _retry(lib, which, failed)

    assert seen == ["free"]


def test_a_job_looks_with_no_write_open(lib, monkeypatch):
    from app.core import worker

    seen = []
    _watch(monkeypatch, worker, seen, _database_path(lib))

    answers._run_next(lib)

    assert seen == ["free"]
