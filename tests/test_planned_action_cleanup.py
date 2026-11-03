"""
A queue item's plan holds its own actions and no one else's.

Reported from a real server: after Re-check Scanned Files, a file's new
pending item showed "Copy audio [fra]" in its plan, a description no current
version writes, since every tag is normalised to its ISO 639-2/B code first.
It was left over from an earlier item for the same file.

The scan's _clear_stale_status_records deleted the file's skipped and review
rows with a bulk delete, which bypasses the ORM cascade, and SQLite's
foreign_keys PRAGMA is off, so the schema's ondelete=CASCADE did nothing
either. Those rows can carry actions: the worker's pickup moves a pending
job to skipped or review without removing its plan. SQLite reuses the id of
a deleted last row, so the item the scan created next took that id, and with
it the actions. Databases already holding such actions are cleared at
startup (session._remove_orphaned_planned_actions).

The leftover action is added to the skipped row by hand below, as the
pickup leaves it, rather than by running a job through the worker.

Verified by mutation, 5 applied 5 killed, all five surviving the 2234-test
suite before this file:

  the scan      no actions deleted with the rows      → re-check, from a
                                                        skipped and a
                                                        review row
                every action of the file deleted      → finished job kept
  the startup   the cleanup not run by init_db        → both startup tests
                the cleanup deleting nothing          → both startup tests
                the cleanup deleting every action     → attached kept

Run from the project root:
    pytest tests/test_planned_action_cleanup.py -v
"""
import importlib
import json
import sqlite3

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

import app.core.scanner as scanner
from app.core.scanner import scan_library
from app.database.models import AppSetting, Base, MediaFile, PlannedAction, QueueItem
from tests.conftest import memory_engine
from tests.test_schema_migration import _point_the_engine_at


def _probe(*languages):
    streams = [{"index": 0, "codec_type": "video", "codec_name": "h264"}]
    streams += [{"index": i + 1, "codec_type": "audio", "codec_name": "aac",
                 "channels": 2, "tags": {"language": lang},
                 "disposition": {"default": 1 if i == 0 else 0}}
                for i, lang in enumerate(languages)]
    return {"format": {"format_name": "matroska,webm", "duration": "1.0"},
            "streams": streams}


@pytest.fixture
def lib(tmp_path, monkeypatch):
    """
    One file, English and French, scanned once with both kept, so it is
    skipped. Narrowing Keep Audio Languages to English and re-checking then
    queues it, from its stored tracks.
    """
    engine = memory_engine()
    Base.metadata.create_all(engine)
    db = sessionmaker(bind=engine)()
    folder = tmp_path / "tv" / "Show"
    folder.mkdir(parents=True)
    path = folder / "Ep1.mkv"
    path.write_bytes(b"\0" * 64)
    monkeypatch.setattr(scanner, "probe_file", lambda *_a, **_kw: _probe("eng", "fre"))

    def setting(key, value):
        db.merge(AppSetting(key=key, value=json.dumps(value)))
        db.commit()

    def scan(**kwargs):
        scan_library(db, [str(tmp_path / "tv")], **kwargs)

    def leave_an_action(item_id, description="Copy audio [fra] aac 5.1 (stream 1)"):
        db.add(PlannedAction(queue_item_id=item_id, order=0, action_type="copy_track",
                             description=description, track_type="audio",
                             stream_index=1))
        db.commit()
        # The scan works in a session that never loaded these rows; holding
        # them here only makes SQLAlchemy warn when the id comes back.
        db.expunge_all()

    setting("dry_run_mode", False)
    setting("prefer_mp4_container", False)
    setting("keep_audio_languages", ["eng", "fre"])
    scan()
    yield type("Lib", (), dict(db=db, setting=staticmethod(setting),
                               scan=staticmethod(scan),
                               leave_an_action=staticmethod(leave_an_action)))
    db.close()


def _pending_plan(db):
    item = db.query(QueueItem).filter(QueueItem.status == "pending").one()
    db.expire_all()
    return item.id, [a.description for a in db.get(QueueItem, item.id).planned_actions]


@pytest.mark.parametrize("status", ["skipped", "manual_review"])
def test_a_recheck_queues_the_file_with_only_its_own_plan(lib, status):
    """
    The reported case, and the same for a review row, which the queue path
    clears alongside a skipped one. The new item takes the deleted row's id,
    as on the server; asserted so that the test fails loudly, rather than
    passing for the wrong reason, if SQLite stops reusing it.
    """
    old = lib.db.query(QueueItem).one()
    old.status = status
    old_id = old.id
    lib.leave_an_action(old_id)

    lib.setting("keep_audio_languages", ["eng"])
    lib.scan(recheck=True)

    item_id, plan = _pending_plan(lib.db)
    assert item_id == old_id
    assert not any("[fra]" in d for d in plan)
    assert any(d.startswith("Drop audio [fre]") for d in plan)


def test_a_finished_jobs_plan_is_kept(lib):
    """
    History shows what a finished job did from its actions, so only the
    deleted rows' actions go, not every action the file has.
    """
    skipped = lib.db.query(QueueItem).one()
    done = QueueItem(file_id=skipped.file_id, status="success", reason="earlier job")
    lib.db.add(done)
    lib.db.commit()
    done_id = done.id
    lib.leave_an_action(done_id, "Drop audio [ger] aac stereo (stream 2) — not in keep list")

    lib.setting("keep_audio_languages", ["eng"])
    lib.scan(recheck=True)

    lib.db.expire_all()
    assert [a.description for a in lib.db.get(QueueItem, done_id).planned_actions] == [
        "Drop audio [ger] aac stereo (stream 2) — not in keep list"]


# ── Startup ──────────────────────────────────────────────────────────────────

@pytest.fixture
def started(tmp_path, monkeypatch):
    """
    A database holding one item with an action, and one action whose item
    was deleted, as the old scan left them; then a real startup.
    """
    path = tmp_path / "remuxarr.db"
    engine = create_engine(f"sqlite:///{path}")
    Base.metadata.create_all(engine)
    with sessionmaker(bind=engine)() as db:
        media = MediaFile(path="/tv/Show/Ep1.mkv", filename="Ep1.mkv",
                          directory="/tv/Show", size=64, mtime=1.0)
        db.add(media)
        db.flush()
        item = QueueItem(file_id=media.id, status="success")
        db.add(item)
        db.flush()
        db.add_all([
            PlannedAction(queue_item_id=item.id, order=0, action_type="copy_track",
                          description="attached", track_type="audio", stream_index=1),
            PlannedAction(queue_item_id=item.id + 1, order=0, action_type="copy_track",
                          description="left behind", track_type="audio", stream_index=1),
        ])
        db.commit()
    engine.dispose()

    session_mod = _point_the_engine_at(path, monkeypatch)
    session_mod.init_db()
    conn = sqlite3.connect(path)
    yield [d for (d,) in conn.execute("SELECT description FROM planned_actions")]
    conn.close()
    # DATABASE_PATH goes back BEFORE the reload. Reloaded while it still
    # named this database, the session module kept an engine on it, and a
    # module importing SessionLocal later in the run, as the worker does,
    # took that engine: test_scan_and_cancellation then seeded one database
    # and aborted a job in another.
    monkeypatch.undo()
    importlib.reload(session_mod)


def test_startup_removes_actions_left_behind(started):
    assert "left behind" not in started


def test_startup_keeps_actions_that_have_their_item(started):
    assert started == ["attached"]
