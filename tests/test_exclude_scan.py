"""
Excluded files through a scan, a webhook and the single-file endpoint.

test_exclude_rules.py pins what the rules match. This file pins what each
way into Remuxarr does with a match: a scan passes over it and counts it, a
record already held for it is removed at the end of a completed scan the
way a file gone from disk is, and neither a webhook nor the single-file
endpoint queues it. These run the real scan_library, queue_single_file and
scan route against a file-backed database, with only the probe stubbed.

The removal runs whether or not auto cleanup is on. That setting exists
for a share that is not mounted, where a missing file proves nothing; an
excluded file is one the user asked to have left alone.

Confirmed unprotected before this file and test_exclude_rules.py were
written: eighteen mutations, each run against the whole 1768-test suite
(the toast one against the frontend suite), all eighteen survived. The
switch ignored; an extras folder's no-video-subfolder test dropped; its
inside-a-movie-or-show test dropped; season folders not recognised; name
endings matched with case; Plex Versions only as the direct folder; the
library path's own folders tested by name patterns; path patterns not
covering a folder's contents; brackets in a pattern read as a character
class; the walk counting an excluded file and processing it anyway; the
progress total including excluded files; the removal gated on auto
cleanup; the removal taking a record whose job is running; the removal on
a cancelled scan; queue_single_file ignoring the rules; the single-file
endpoint not checking them; scan_completed without the count; and the
toast without it. All eighteen are killed.
"""
import asyncio
import json
import os
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

import app.api.routes.scan as scan_routes
import app.database.session as session_module
from app.core import scanner
from app.database.models import (AppSetting, AudioLanguageFlag, Base,
                                 MediaFile, QueueItem, RevertPoint)


def _probe_mkv():
    """An MKV that needs a conversion under the defaults."""
    return {"format": {"format_name": "matroska,webm", "duration": "1.0"},
            "streams": [
                {"index": 0, "codec_type": "video", "codec_name": "h264"},
                {"index": 1, "codec_type": "audio", "codec_name": "aac",
                 "channels": 2, "tags": {"language": "eng"},
                 "disposition": {"default": 1}},
            ]}


@pytest.fixture
def lib(tmp_path, monkeypatch):
    engine = create_engine(f"sqlite:///{tmp_path / 'remuxarr.db'}",
                           connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine)
    monkeypatch.setattr(session_module, "SessionLocal", factory)
    monkeypatch.setattr(scan_routes, "SessionLocal", factory)
    probed = []

    def probe(path, *_a, **_kw):
        probed.append(path)
        return _probe_mkv()

    monkeypatch.setattr(scanner, "probe_file", probe)

    root = tmp_path / "movies"
    folder = root / "Avatar (2009)"
    (folder / "Trailers").mkdir(parents=True)
    paths = SimpleNamespace(
        movie=folder / "Avatar (2009).mkv",
        trailer=folder / "Trailers" / "Teaser.mkv",
        featurette=folder / "Avatar (2009)-featurette.mkv",
    )
    for p in vars(paths).values():
        p.write_bytes(b"\0" * 64)

    def settings(**values):
        with factory() as db:
            for key, value in values.items():
                db.merge(AppSetting(key=key, value=json.dumps(value)))
            db.commit()

    settings(scan_paths=[str(root)], dry_run_mode=False)

    def scan(**kwargs):
        with factory() as db:
            return scanner.scan_library(db, [str(root)], force_probe=True, **kwargs)

    def records():
        with factory() as db:
            return sorted(os.path.basename(m.path) for m in db.query(MediaFile))

    def media_id(path):
        with factory() as db:
            return db.query(MediaFile).filter(MediaFile.path == str(path)).one().id

    yield SimpleNamespace(Session=factory, root=root, paths=paths, probed=probed,
                          settings=settings, scan=scan, records=records,
                          media_id=media_id)
    engine.dispose()


# ── A scan ───────────────────────────────────────────────────────────────────

def test_a_scan_passes_over_excluded_files_and_counts_them(lib):
    totals = []
    stats = lib.scan(progress_callback=lambda scanned, total: totals.append(total))

    assert lib.probed == [str(lib.paths.movie)]
    assert lib.records() == ["Avatar (2009).mkv"]
    assert (stats.total, stats.excluded, stats.queued) == (1, 2, 1)
    assert totals == [1]


def test_the_scan_summary_carries_the_count(lib, monkeypatch):
    sent = []
    monkeypatch.setattr(scan_routes, "broadcast_threadsafe",
                        lambda data, _loop: sent.append(data))

    scan_routes._run_scan([str(lib.root)], True, object())

    completed = [m for m in sent if m.get("event") == "scan_completed"]
    assert len(completed) == 1
    assert completed[0]["excluded"] == 2


# ── Records already held ─────────────────────────────────────────────────────

@pytest.fixture
def held(lib):
    """All three files recorded and queued before the switch is turned on,
    the trailer with a language review and a revert point."""
    lib.settings(skip_extras=False)
    lib.scan()
    assert len(lib.records()) == 3
    trailer_id = lib.media_id(lib.paths.trailer)
    with lib.Session() as db:
        db.add(AudioLanguageFlag(file_id=trailer_id, stream_index=1,
                                 detected_language="und"))
        db.add(RevertPoint(file_id=trailer_id, sidecar_path="/recycle/x.mkv",
                           original_path=str(lib.paths.trailer), manifest="{}"))
        db.commit()
    lib.settings(skip_extras=True)
    return trailer_id


def test_records_of_newly_excluded_files_are_removed_at_the_next_scan(lib, held):
    stats = lib.scan()

    assert lib.records() == ["Avatar (2009).mkv"]
    assert stats.removed == 2
    with lib.Session() as db:
        assert db.query(QueueItem).filter(QueueItem.file_id == held).count() == 0
        assert db.query(AudioLanguageFlag).count() == 0
        point = db.query(RevertPoint).one()
        assert (point.file_id, point.detached_at is not None) == (None, True)
    assert lib.paths.trailer.exists() and lib.paths.featurette.exists()


def test_they_are_removed_with_auto_cleanup_off(lib, held):
    lib.settings(auto_cleanup_on_scan=False)

    lib.scan()

    assert lib.records() == ["Avatar (2009).mkv"]


def test_a_record_whose_job_is_running_is_left_for_a_later_scan(lib, held):
    with lib.Session() as db:
        db.query(QueueItem).filter(QueueItem.file_id == held).update(
            {"status": "processing"})
        db.commit()

    lib.scan()

    assert lib.records() == ["Avatar (2009).mkv", "Teaser.mkv"]


def test_a_cancelled_scan_removes_nothing(lib, held):
    lib.scan(cancel_check=lambda: True)

    assert len(lib.records()) == 3


# ── A webhook and the single-file endpoint ───────────────────────────────────

def test_a_webhook_for_an_excluded_file_is_not_queued(lib):
    with lib.Session() as db:
        item = scanner.queue_single_file(db, str(lib.paths.trailer),
                                         sonarr_series_id=7)

    assert item is None
    assert lib.records() == []
    assert lib.probed == []


def test_the_single_file_endpoint_says_it_is_excluded(lib):
    body = scan_routes.FileScanRequest(path=str(lib.paths.trailer))

    answer = asyncio.run(scan_routes.scan_file(body))

    assert answer["queued"] is False
    assert answer["reason"].startswith("Excluded by your exclude settings")
    assert lib.records() == []


# ── Defaults ─────────────────────────────────────────────────────────────────

def test_an_upgrading_install_gets_the_switch_on(lib):
    """_seed_defaults writes a missing key with its default."""
    with lib.Session() as db:
        assert db.get(AppSetting, "skip_extras") is None
        session_module._seed_defaults(db)
        assert json.loads(db.get(AppSetting, "skip_extras").value) is True
        assert json.loads(db.get(AppSetting, "exclude_patterns").value) == []
