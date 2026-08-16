"""
recycle.py — the storage layer behind "revert to original".

Three things are being protected here, and only the first is ordinary
unit-test material:

  1. delete_sidecar never raises. Every caller is part-way through a
     database cleanup when it calls this, and a file that cannot be
     removed must not be able to abort that cleanup.

  2. A missing recycle directory is reported, never created. Docker
     creates a bind mount's target inside the container, so the
     directory's existence is the only signal available that the volume
     was actually mounted. Creating it on a miss produces the worst
     possible failure: sidecars written to the container's writable
     layer, surviving restarts, convincing the user their retention
     window works, then vanishing on the next image pull.
     test_missing_root_is_not_created is the test for that, and it
     asserts the absence, not just the exception.

  3. _delete_media_file_and_related unlinks the sidecar, not just the
     row. RevertPoint is the first table referencing media_files whose
     rows own bytes on disk. Nothing else would ever collect an orphaned
     sidecar: it is not under TEMP_DIR or scan_paths (so the startup
     sweep misses it) and its suffix is outside MEDIA_EXTENSIONS (so
     every scan misses it).

Two mutants survive individually and are recorded rather than papered
over, because the pair is the interesting result: removing the explicit
`rp.file_id = None`, and adding cascade="all, delete-orphan" to the
backref. Either alone passes everything here — SQLAlchemy nullifies a
loaded child's FK on parent delete anyway, and delete-orphan does not
fire when the FK is cleared directly rather than through the collection.
Applied TOGETHER the rows are deleted and these tests fail.

That is the justification for a line that looks redundant: the explicit
assignment is what makes detachment a property of the function rather
than of the relationship's configuration, and it is the reason a future
cascade change cannot quietly turn detaching back into deleting.
Verified by applying all three combinations.

Verified by mutation, 11 applied to recycle.py and the deletion path:

  • Returning True instead of False from delete_sidecar's FileNotFoundError
    branch                                          → killed
  • Removing the OSError branch entirely            → killed (raises)
  • Removing the `if not path` guard               → killed (raises on None)
  • os.path.exists → os.path.isdir in the status check, and the reverse
                                                    → killed (file-not-dir case)
  • Dropping the os.access check                    → killed
  • ensure_recycle_subdir calling makedirs before the readiness check
                                                    → killed by
                                                      test_missing_root_is_not_created
  • Dropping the delete_sidecar loop from
    _delete_media_file_and_related                  → killed
  • Deleting the RevertPoint row query from the same function
                                                    → killed, by name, by
                                                      test_media_file_deletion's
                                                      metadata-derived test

One equivalent mutant, recorded rather than papered over:
`exist_ok=True` on ensure_recycle_subdir's makedirs. Flipping it to False
changes nothing observable through this module's own surface, because the
only caller reaching that line has already passed the readiness check and
every call site creates the same per-install subdirectory name — the
second call is the one that would raise, and there is no code path today
that makes a second call with different state. It becomes a real mutant
the moment a caller creates per-job subdirectories; noted here so that
change is not made silently.

The suffix-invariant tests below are deliberately weak on their own —
they restate a constant. The load-bearing versions are behavioural and
live in test_startup_recovery.py, where a real sidecar is put in front of
the real sweep.
"""
import os

import pytest


# ── Harness ──────────────────────────────────────────────────────────────────

@pytest.fixture
def recycle(tmp_path, monkeypatch):
    """Point RECYCLE_DIR at a real, mounted-looking directory."""
    from app.config import settings as app_settings

    root = tmp_path / "recycle"
    root.mkdir()
    monkeypatch.setattr(app_settings, "RECYCLE_DIR", str(root), raising=False)
    return root


@pytest.fixture
def unmounted(tmp_path, monkeypatch):
    """Point RECYCLE_DIR at a path that does not exist — the unmounted case."""
    from app.config import settings as app_settings

    root = tmp_path / "not-mounted"
    monkeypatch.setattr(app_settings, "RECYCLE_DIR", str(root), raising=False)
    return root


@pytest.fixture
def db():
    from sqlalchemy.orm import sessionmaker

    from tests.conftest import memory_engine

    from app.database.models import Base

    engine = memory_engine()
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)()


# ── recycle_dir_status ───────────────────────────────────────────────────────

def test_mounted_writable_directory_is_ready(recycle):
    from app.core.recycle import recycle_dir_status

    ready, reason = recycle_dir_status()
    assert ready is True
    assert reason == ""


def test_missing_directory_is_not_ready_and_names_the_path(unmounted):
    """
    The reason string is user-facing — it is what tells someone their
    volume is not mounted, so it has to identify which path is missing.
    """
    from app.core.recycle import recycle_dir_status

    ready, reason = recycle_dir_status()
    assert ready is False
    assert str(unmounted) in reason


def test_path_that_is_a_file_is_not_ready(tmp_path, monkeypatch):
    """
    Distinct from "missing": os.path.exists is true here. A status check
    written with exists() alone reports this as ready and every sidecar
    write then fails at the point of use instead.
    """
    from app.config import settings as app_settings
    from app.core.recycle import recycle_dir_status

    f = tmp_path / "recycle"
    f.write_text("not a directory")
    monkeypatch.setattr(app_settings, "RECYCLE_DIR", str(f), raising=False)

    ready, reason = recycle_dir_status()
    assert ready is False
    assert "not a directory" in reason


def test_unwritable_directory_is_not_ready(recycle, monkeypatch):
    """
    Patched rather than chmod'd on purpose: the suite runs as root in some
    environments (including the container image), and root ignores the mode
    bits, so a chmod-based version of this test passes for the wrong reason
    in one environment and the right reason in another.
    """
    import app.core.recycle as r

    monkeypatch.setattr(r.os, "access", lambda *_a, **_k: False)

    ready, reason = r.recycle_dir_status()
    assert ready is False
    assert "not writable" in reason


def test_empty_configured_path_is_not_ready(monkeypatch):
    from app.config import settings as app_settings
    from app.core.recycle import recycle_dir_status

    monkeypatch.setattr(app_settings, "RECYCLE_DIR", "", raising=False)

    ready, reason = recycle_dir_status()
    assert ready is False
    assert reason


# ── ensure_recycle_subdir ────────────────────────────────────────────────────

def test_subdirectory_is_created_under_a_mounted_root(recycle):
    from app.core.recycle import ensure_recycle_subdir

    path = ensure_recycle_subdir("sidecars")

    assert path == str(recycle / "sidecars")
    assert os.path.isdir(path)


def test_missing_root_is_not_created(unmounted):
    """
    The trap this whole module exists to avoid.

    Asserting the RuntimeError alone would still pass an implementation
    that created the directory and then raised, or one that created it on
    the way to succeeding. The assertion that matters is that nothing
    appeared on disk: an unmounted volume must stay unmounted.
    """
    from app.core.recycle import ensure_recycle_subdir

    with pytest.raises(RuntimeError):
        ensure_recycle_subdir("sidecars")

    assert not unmounted.exists(), (
        "the recycle root was created despite not being mounted — sidecars "
        "would be written into the container's writable layer and lost on "
        "the next image pull"
    )


# ── delete_sidecar ───────────────────────────────────────────────────────────

def test_existing_sidecar_is_removed(recycle):
    from app.core.recycle import delete_sidecar

    f = recycle / "7.remuxarr_revert"
    f.write_bytes(b"payload")

    assert delete_sidecar(str(f)) is True
    assert not f.exists()


def test_missing_sidecar_is_not_an_error(recycle):
    """
    Already-gone is the state the caller wanted. Retention sweeps, a
    hand-emptied volume and a failed write all reach here.
    """
    from app.core.recycle import delete_sidecar

    assert delete_sidecar(str(recycle / "never-existed")) is False


def test_none_path_is_not_an_error():
    from app.core.recycle import delete_sidecar

    assert delete_sidecar(None) is False


def test_unremovable_sidecar_does_not_raise(recycle, monkeypatch):
    """
    The caller is mid-cleanup with rows already deleted from the session.
    An exception here would abort a database cleanup over a file that is,
    at worst, still taking up space — leaving a revert point that can
    never be deleted at all.
    """
    import app.core.recycle as r

    f = recycle / "8.remuxarr_revert"
    f.write_bytes(b"payload")

    def _boom(*_a, **_k):
        raise OSError("device or resource busy")

    monkeypatch.setattr(r.os, "remove", _boom)

    assert r.delete_sidecar(str(f)) is False


# ── Suffix invariants ────────────────────────────────────────────────────────

def test_sidecar_suffix_is_not_a_media_extension():
    """
    A sidecar that a scan can see is a file the pipeline will try to
    process. The recycle volume is not in scan_paths today; this keeps the
    suffix safe even if that changes.
    """
    from app.core.probe import MEDIA_EXTENSIONS
    from app.core.recycle import SIDECAR_SUFFIX

    assert SIDECAR_SUFFIX not in MEDIA_EXTENSIONS


def test_sidecar_suffix_is_not_swept_as_a_temp_file():
    from app.core.recycle import SIDECAR_SUFFIX

    assert not SIDECAR_SUFFIX.endswith((".part", ".remuxarr_tmp", ".forge_tmp"))


# ── Deletion integration ─────────────────────────────────────────────────────

def _media_with_revert_point(db, recycle, *, sidecar_exists=True):
    from app.database.models import MediaFile, RevertPoint

    media = MediaFile(path="/m/Show.mkv", filename="Show.mkv", directory="/m",
                      size=100, mtime=1.0)
    db.add(media)
    db.commit()

    sidecar = recycle / f"{media.id}.remuxarr_revert"
    if sidecar_exists:
        sidecar.write_bytes(b"dropped tracks")

    db.add(RevertPoint(file_id=media.id, sidecar_path=str(sidecar),
                       manifest="{}", original_path=media.path))
    db.commit()
    return media, sidecar


def test_a_vanished_file_detaches_its_revert_point_and_keeps_the_sidecar(db,
                                                                        recycle):
    """
    The rename case, which this function cannot distinguish from a
    deletion — it only knows the path is gone. Sonarr changing a naming
    scheme moves a whole library in one pass, and every one of those files
    still exists. Deleting their stored tracks would empty the recycle bin
    for a library that was never deleted.
    """
    from app.core.scanner import _delete_media_file_and_related
    from app.database.models import RevertPoint

    media, sidecar = _media_with_revert_point(db, recycle)

    _delete_media_file_and_related(db, media)
    db.commit()

    assert sidecar.exists(), "the stored tracks were destroyed"
    point = db.query(RevertPoint).one()
    assert point.file_id is None, "the point still points at a deleted row"
    assert point.detached_at is not None


def test_a_detached_point_keeps_everything_needed_to_match_it_back(db, recycle):
    """
    Detaching is only useful if what survives identifies the file. The
    original path and the manifest are how a user recognises which point
    belongs to which renamed file.
    """
    from app.core.scanner import _delete_media_file_and_related
    from app.database.models import RevertPoint

    media, _sidecar = _media_with_revert_point(db, recycle)

    _delete_media_file_and_related(db, media)
    db.commit()

    point = db.query(RevertPoint).one()
    assert point.original_path == "/m/Show.mkv"
    assert point.manifest
    assert point.sidecar_path


def test_detaching_completes_when_the_sidecar_is_already_gone(db, recycle):
    """
    Ordinary, not exceptional: retention may have swept the file already,
    or the user emptied the volume by hand.
    """
    from app.core.scanner import _delete_media_file_and_related
    from app.database.models import MediaFile, RevertPoint

    media, _sidecar = _media_with_revert_point(db, recycle, sidecar_exists=False)

    _delete_media_file_and_related(db, media)
    db.commit()

    assert db.query(MediaFile).count() == 0
    assert db.query(RevertPoint).one().file_id is None


def test_another_files_revert_point_is_not_detached(db, recycle):
    """
    The update is filtered by file_id. Dropping that filter detaches the
    entire recycle bin the first time any one file disappears — unattended,
    on every scan.
    """
    from app.core.scanner import _delete_media_file_and_related
    from app.database.models import MediaFile, RevertPoint

    doomed, doomed_sidecar = _media_with_revert_point(db, recycle)

    keeper = MediaFile(path="/m/Other.mkv", filename="Other.mkv",
                       directory="/m", size=200, mtime=2.0)
    db.add(keeper)
    db.commit()
    keeper_sidecar = recycle / f"{keeper.id}.remuxarr_revert"
    keeper_sidecar.write_bytes(b"still wanted")
    db.add(RevertPoint(file_id=keeper.id, sidecar_path=str(keeper_sidecar),
                       manifest="{}", original_path=keeper.path))
    db.commit()

    _delete_media_file_and_related(db, doomed)
    db.commit()

    assert doomed_sidecar.exists()
    assert keeper_sidecar.exists()
    attached = db.query(RevertPoint).filter(
        RevertPoint.file_id.isnot(None)).all()
    assert [p.file_id for p in attached] == [keeper.id]
