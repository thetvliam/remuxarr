"""
The clear-database endpoint.

It had no test coverage at all, which is why the same omission has now
been made three times. Its own docstring records the first two:
PlexAnalyzeBacklog and AudioLanguageFlag were both left behind, both for
the same reason, and both found later. RevertPoint was the third — and the
first where the consequence is data corruption rather than a stale flag.

Why the omission keeps happening: SQLite's foreign keys are not enforced
(no PRAGMA foreign_keys=ON), so `ondelete="CASCADE"` on the model does
nothing and a forgotten table simply survives. Nothing fails. The rows sit
there with a file_id no media file answers to, and because SQLite reuses
rowids once a table is emptied, the next scanned file inherits that id and
silently adopts them.

For a language flag that is a wrong badge. For a revert point it is worse:
capture will extend the stale point, build a sidecar from another file's
manifest, and re-establish the sentinel against the wrong content — so
every later check passes and a revert writes a mux of two unrelated files.

The first test below is therefore derived from the schema rather than
written as a list, so the fourth table to reference media_files is covered
the day it is added.
"""
import json

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def client(tmp_path, monkeypatch):
    from sqlalchemy.orm import sessionmaker

    from app.config import settings as app_settings
    from app.database.models import Base
    import app.database.session as session_mod
    from tests.conftest import memory_engine

    recycle = tmp_path / "recycle"
    recycle.mkdir()
    monkeypatch.setattr(app_settings, "RECYCLE_DIR", str(recycle), raising=False)

    engine = memory_engine()
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine)
    monkeypatch.setattr(session_mod, "SessionLocal", factory)

    from app.main import app

    app.dependency_overrides[session_mod.get_db] = lambda: factory()
    try:
        yield TestClient(app), factory(), recycle
    finally:
        app.dependency_overrides.clear()


def _populate(db, recycle):
    """One media file with a row in every table that references it."""
    from app.database.models import (
        Ac3ForgeJob, AudioLanguageFlag, MediaFile, PlannedAction,
        PlexAnalyzeBacklog, QueueItem, RevertPoint, SubtitleLanguageFlag, Track,
    )

    media = MediaFile(path="/m/Show.mkv", filename="Show.mkv", directory="/m",
                      size=100, mtime=1.0, container="mkv")
    db.add(media)
    db.commit()

    item = QueueItem(file_id=media.id, status="completed")
    db.add(item)
    db.commit()

    sidecar = recycle / f"{media.id}_1.remuxarr_revert"
    sidecar.write_bytes(b"dropped tracks")

    db.add_all([
        PlannedAction(queue_item_id=item.id, action_type="drop",
                      description="drop track 2"),
        Track(file_id=media.id, stream_index=0, track_type="video"),
        Ac3ForgeJob(file_id=media.id, status="pending"),
        PlexAnalyzeBacklog(file_id=media.id, expected_language="eng"),
        AudioLanguageFlag(file_id=media.id, stream_index=1,
                          detected_language="dut"),
        SubtitleLanguageFlag(file_id=media.id, stream_index=2,
                             detected_language="und"),
        RevertPoint(file_id=media.id, sidecar_path=str(sidecar), sidecar_size=14,
                    manifest=json.dumps({"version": 2, "streams": []}),
                    original_path=media.path,
                    processed_size=100, processed_mtime=1.0),
    ])
    db.commit()
    return media, sidecar


def test_every_table_referencing_media_files_is_wiped(client):
    """
    Derived from the schema, not from a list, so the next table to
    reference media_files is covered the day it is added rather than the
    day someone notices it survived.

    Unlike the scanner's equivalent there is no exemption here: detaching
    exists because a rename is indistinguishable from a deletion, and this
    endpoint carries no such ambiguity.
    """
    from sqlalchemy import text

    from app.database.models import Base, MediaFile

    api, db, recycle = client
    _populate(db, recycle)

    referencing = {
        table.name
        for table in Base.metadata.tables.values()
        for fk in table.foreign_keys
        if fk.column.table.name == "media_files"
    }
    assert referencing, "metadata introspection found nothing — test is broken"

    assert api.post("/api/settings/clear-database").status_code == 200

    leftovers = {
        name: db.execute(text(f"SELECT COUNT(*) FROM {name}")).scalar()
        for name in referencing
    }
    orphaned = {n: c for n, c in leftovers.items() if c}
    assert not orphaned, (
        f"rows left behind referencing a wiped media file: {orphaned} — "
        f"clear_database needs a delete for each"
    )
    assert db.query(MediaFile).count() == 0


def test_wiping_removes_the_sidecars_too(client):
    """
    The rows are the only record of where the sidecars are. Deleting them
    without unlinking leaks the recycle volume until the retention sweep's
    orphan pass ages the files out an hour later.
    """
    api, db, recycle = client
    _media, sidecar = _populate(db, recycle)

    api.post("/api/settings/clear-database")

    assert not sidecar.exists(), "sidecar left on the recycle volume"


def test_a_wiped_revert_point_cannot_be_adopted_by_a_reused_id(client, tmp_path):
    """
    The consequence that makes this table different from the others.

    SQLite reuses rowids once a table is emptied, so the next scanned file
    gets the id the stale point still holds. If the point survived, capture
    would find it, judge it usable, and extend it — building a sidecar from
    a different file's manifest and re-establishing the sentinel against
    the wrong content, so every check afterwards passes.
    """
    from app.core.revert_capture import _load_existing_point
    from app.database.models import MediaFile

    api, db, recycle = client
    _populate(db, recycle)
    api.post("/api/settings/clear-database")

    replacement = tmp_path / "Unrelated.mkv"
    replacement.write_bytes(b"a completely different film")
    stat = replacement.stat()
    media = MediaFile(path=str(replacement), filename="Unrelated.mkv",
                      directory=str(tmp_path), size=stat.st_size,
                      mtime=stat.st_mtime, container="mkv")
    db.add(media)
    db.commit()

    assert media.id == 1, "expected SQLite to reuse the id — test premise gone"
    assert _load_existing_point(media.id, str(replacement)) is None


def test_settings_survive_the_wipe(client):
    """
    The endpoint's whole contract: scanned-file data goes, configuration
    stays. Deleting a table too many is as bad as too few.
    """
    from app.database.models import AppSetting

    api, db, recycle = client
    db.add(AppSetting(key="keep_audio_languages", value=json.dumps(["eng"])))
    db.commit()

    api.post("/api/settings/clear-database")

    assert db.query(AppSetting).count() == 1
