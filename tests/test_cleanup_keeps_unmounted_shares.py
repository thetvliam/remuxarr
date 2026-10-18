"""
Deleted-file cleanup and a share that is not mounted.

An unmounted share is still there as an empty folder: the mount point stays,
only what was mounted on it goes. The cleanup at the end of a scan saw every
file under it missing and removed every record: the answers, ignores and
history, and the forge jobs, so a forged file was offered for forging again
once the share came back. Reproduced on real scans before this file existed,
both with the scan path as the share and with a share below it (a scan path
holding tv and movies as two shares), where the walk still found the movies
and skipping an empty scan path would not have helped.

cleanup_deleted_files now asks the nearest folder that still exists on the
way from the missing file up to its scan path. Something in it shows the file
was deleted. Empty, or unreadable, it could be a share, and the record stays.
Run Cleanup Now goes through the same function, so the button follows the
same rule.

Verified by mutation, 7 applied, 7 killed. The baseline was the suite as it
stood (1985 tests) plus three tests that put a missing file alone in an empty
scan path and expected it removed; that is now exactly what an unmounted
share looks like, so each gained a file beside the missing one. Reading the
listing the wrong way round is killed by those. The other six survived the
baseline and are killed here:

  • no check at all                          → the empty mount point, the
                                               share below, the unreadable
                                               folder, the button
  • the file's own folder only, no walk up   → the deleted show
  • only the scan path asked, as skipping an
    empty scan path would                    → the share below, the
                                               unreadable folder
  • an unreadable folder counted as occupied → the unreadable folder, the
                                               button
  • the walk going above the scan path       → the button
  • no warning                               → the empty mount point
"""
import logging
import os

import pytest

from app.core.scanner import cleanup_deleted_files, scan_library


@pytest.fixture
def db():
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    from app.database.models import Base

    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)()


@pytest.fixture
def no_probe(monkeypatch):
    """The scan's per-file work shells out to ffprobe; the cleanup after it
    is what is under test."""
    from app.core import scanner

    monkeypatch.setattr(scanner, "_process_file", lambda *args, **kwargs: None)


def _file(path):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as f:
        f.write(b"not really a video")
    return str(path)


def _record(db, path, *, history=False):
    """A file's record; with history, a finished job and a revert point,
    the rows a rescan cannot rebuild."""
    from app.database.models import MediaFile, QueueItem, RevertPoint

    media = MediaFile(path=str(path), filename=os.path.basename(path),
                      directory=os.path.dirname(path), size=100, mtime=1.0)
    db.add(media)
    db.commit()
    if history:
        db.add_all([
            QueueItem(file_id=media.id, status="success"),
            RevertPoint(file_id=media.id, sidecar_path="/recycle/1.remuxarr_revert",
                        manifest="{}", original_path=media.path),
        ])
        db.commit()
    return media


def _paths(db):
    from app.database.models import MediaFile

    return {m.path for m in db.query(MediaFile).all()}


def test_a_scan_path_that_is_an_empty_mount_point_keeps_its_records(
        db, tmp_path, no_probe, caplog):
    from app.database.models import QueueItem, RevertPoint

    share = tmp_path / "tv"
    share.mkdir()                                     # the mount point, empty
    episode = _record(db, share / "Show" / "E01.mkv", history=True)

    with caplog.at_level(logging.WARNING, logger="app.core.scanner"):
        stats = scan_library(db, [str(share)])

    assert stats.removed == 0
    assert _paths(db) == {episode.path}
    assert db.query(QueueItem).count() == 1, "the file's history went with it"
    assert db.query(RevertPoint).one().file_id == episode.id, "the revert point was detached"
    assert "check that it is mounted" in caplog.text


def test_an_empty_share_below_the_scan_path_keeps_its_records(db, tmp_path, no_probe):
    library = tmp_path / "media"
    (library / "tv").mkdir(parents=True)              # the tv share, not mounted
    film = _file(library / "movies" / "Film" / "Film.mkv")
    _record(db, film)
    episode = _record(db, library / "tv" / "Show" / "E01.mkv")

    stats = scan_library(db, [str(library)])

    assert stats.removed == 0
    assert _paths(db) == {film, episode.path}


def test_a_show_deleted_beside_other_shows_is_removed(db, tmp_path, no_probe):
    """The other half: a deleted folder among others is a deletion, and
    "keep everything missing" must not pass."""
    library = tmp_path / "tv"
    other = _file(library / "Other Show" / "E01.mkv")
    _record(db, other)
    _record(db, library / "Deleted Show" / "E01.mkv")
    _record(db, library / "Deleted Show" / "E02.mkv")

    stats = scan_library(db, [str(library)])

    assert stats.removed == 2
    assert _paths(db) == {other}


def test_a_folder_that_cannot_be_listed_keeps_its_records(db, tmp_path, monkeypatch):
    """A stale network mount answers with an error rather than a listing."""
    from app.core import scanner

    share = tmp_path / "tv"
    _file(share / "Show" / "E02.mkv")
    episode = _record(db, share / "Show" / "E01.mkv")

    real_listdir = os.listdir

    def stale(path):
        if os.path.normpath(path) == str(share / "Show"):
            raise OSError(116, "Stale file handle")
        return real_listdir(path)
    monkeypatch.setattr(scanner.os, "listdir", stale)

    assert cleanup_deleted_files(db, [str(share)]) == 0
    assert _paths(db) == {episode.path}


def test_run_cleanup_now_keeps_the_records_of_a_scan_path_that_is_not_there(db, tmp_path):
    """
    The button hands cleanup every configured path, mounted or not, where a
    scan passes only the ones that exist. Next to it here is a folder with
    something in it, which a walk that went above the scan path would find.
    """
    _file(tmp_path / "tv" / "Show" / "E01.mkv")
    missing = tmp_path / "movies"
    film = _record(db, missing / "Film" / "Film.mkv")

    assert cleanup_deleted_files(db, [str(tmp_path / "tv"), str(missing)]) == 0
    assert _paths(db) == {film.path}
