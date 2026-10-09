"""
A file Sonarr or Radarr renames keeps its record.

Before this, a rename looked like a deletion plus a new file. The new name
got a fresh record, and the next scan deleted the old one together with
everything hung off its id: the user's review answers, the file's history,
the link to its revert point, and a pending job, which first failed with
"File not found on disk". The next job on the file counted as a new file
for Plex, too.

The Rename webhook carries each file's old full path beside its new one
(previousPath and path), so the record is moved instead, as soon as the
webhook arrives. Only when the disk agrees: something must be at the new
name and nothing left at the old one, and a record already at the new name
is only replaced when no job is running on it.

These drive the real webhook handlers against a file-backed database, with
only the probe stubbed — what a file contains is not the question here,
which record it belongs to is. The debounce is shortened so the queueing
step it schedules runs inside the test.

Confirmed unprotected before this file was written: 12 mutations, each run
against the whole 1734-test suite, all 12 survived. Each service's handler
not moving anything; the old path left untranslated; the filename, or the
folder, not updated; a record already at the new name not removed; each of
the four guards removed (nothing recorded at the old name, the old name
still on disk, nothing at the new name, a job running on the new name's
record); a subtitle flag's path moved without checking the renamed sidecar
exists; and the flag's path not moved at all. 12 applied, 12 killed.

The folder mutation survived the first version of these tests, which all
renamed within one folder, where the stored directory is the same before
and after. test_a_rename_into_another_folder_moves_the_folder_too is the
scenario that tells them apart.
"""
import asyncio
import json
import os
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.api.routes import webhooks
from app.config import settings as app_settings
from app.core import scanner
from app.database.models import (AppSetting, Base, MediaFile, QueueItem,
                                 RevertPoint, SubtitleLanguageFlag)


# ── Harness ──────────────────────────────────────────────────────────────────

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
    monkeypatch.setattr(webhooks, "SessionLocal", factory)
    monkeypatch.setattr(scanner, "probe_file", lambda *_a, **_kw: _probe_mkv())
    monkeypatch.setattr(app_settings, "WEBHOOK_DEBOUNCE_SECONDS", 0.01)
    folder = tmp_path / "tv" / "Show" / "Season 01"
    folder.mkdir(parents=True)
    yield SimpleNamespace(Session=factory, root=tmp_path, folder=folder)
    engine.dispose()


def _settings(lib, **values):
    with lib.Session() as db:
        for key, value in values.items():
            db.merge(AppSetting(key=key, value=json.dumps(value)))
        db.commit()


def _file(path):
    path.write_bytes(b"\0" * 64)
    return str(path)


def _imported(lib, path, **ids):
    """The file as an import webhook leaves it: a record and a queued job."""
    with lib.Session() as db:
        item = scanner.queue_single_file(db, path, **ids)
        return item.file_id, item.id


class _Request:
    def __init__(self, payload):
        self._payload = payload

    async def json(self):
        return self._payload


def _webhook(handler, payload):
    async def go():
        response = await handler(_Request(payload))
        await asyncio.sleep(0.3)        # let the debounced queueing run
        return response
    return asyncio.run(go())


def _sonarr_rename(old, new):
    return webhooks.sonarr_webhook, {
        "eventType": "Rename", "series": {"id": 7},
        "renamedEpisodeFiles": [{"previousPath": old, "path": new}],
    }


def _radarr_rename(old, new):
    return webhooks.radarr_webhook, {
        "eventType": "Rename", "movie": {"id": 9},
        "renamedMovieFiles": [{"previousPath": old, "path": new}],
    }


def _records(lib):
    with lib.Session() as db:
        return [(m.id, m.path, m.filename, m.directory)
                for m in db.query(MediaFile).order_by(MediaFile.id)]


def _items(lib, file_id):
    with lib.Session() as db:
        return [(i.id, i.status, i.is_new_file)
                for i in db.query(QueueItem).filter(QueueItem.file_id == file_id)
                                            .order_by(QueueItem.id)]


# ── The rename ───────────────────────────────────────────────────────────────

@pytest.mark.parametrize("rename", [_sonarr_rename, _radarr_rename],
                         ids=["sonarr", "radarr"])
def test_a_renamed_file_keeps_its_record_and_everything_on_it(lib, rename):
    old = _file(lib.folder / "Show - S01E01 - TBA.mkv")
    file_id, first_job = _imported(lib, old)
    with lib.Session() as db:
        media = db.get(MediaFile, file_id)
        media.audio_language_ignored = True
        media.subtitle_overrides = json.dumps({"descriptor": "keep"})
        db.get(QueueItem, first_job).status = "success"     # history
        db.add(RevertPoint(file_id=file_id, sidecar_path="/recycle/x.mkv",
                           original_path=old, manifest="{}"))
        db.commit()
    new = str(lib.folder / "Show - S01E01 - The Title.mkv")
    os.rename(old, new)

    handler, payload = rename(old, new)
    _webhook(handler, payload)

    assert _records(lib) == [(file_id, new, os.path.basename(new), str(lib.folder))]
    with lib.Session() as db:
        media = db.get(MediaFile, file_id)
        assert media.audio_language_ignored is True
        assert media.subtitle_overrides == json.dumps({"descriptor": "keep"})
        assert db.query(RevertPoint).one().file_id == file_id
    items = _items(lib, file_id)
    assert items[0] == (first_job, "success", True), "history was lost"
    # The re-check the webhook queues is a reprocess of a known file, not
    # a new one — which decides how Plex is told about it.
    assert items[-1][1:] == ("pending", False)

    with lib.Session() as db:
        scanner.scan_library(db, [str(lib.root / "tv")])
    assert [r[0] for r in _records(lib)] == [file_id], "the next scan dropped the record"


def test_a_rename_into_another_folder_moves_the_folder_too(lib):
    """
    A rename can change the folder as well as the name — a new season
    folder format moves every episode. The stored directory is read on its
    own, not derived from the path: revert matching offers the records in
    a detached point's folder as candidates (revert_match), and a stale one
    would list the file under its old folder and leave it out of its new one.
    """
    old = _file(lib.folder / "Show - S01E10.mkv")
    file_id, _ = _imported(lib, old)
    new_folder = lib.root / "tv" / "Show" / "Season 1"
    new_folder.mkdir()
    new = str(new_folder / "Show - S01E10.mkv")
    os.rename(old, new)

    _webhook(*_sonarr_rename(old, new))

    assert _records(lib) == [(file_id, new, "Show - S01E10.mkv", str(new_folder))]


def test_a_waiting_job_follows_the_file_to_its_new_name(lib):
    old = _file(lib.folder / "Show - S01E02 - TBA.mkv")
    file_id, job = _imported(lib, old)
    new = str(lib.folder / "Show - S01E02 - The Title.mkv")
    os.rename(old, new)

    _webhook(*_sonarr_rename(old, new))

    assert _items(lib, file_id) == [(job, "pending", True)]
    with lib.Session() as db:
        assert os.path.exists(db.get(QueueItem, job).media_file.path)


def test_the_old_path_is_translated_like_the_new_one(lib):
    """
    Sonarr reports paths as its own container mounts them. Translating
    only the new one would look up the old name under Sonarr's prefix,
    find no record, and fall back to a fresh one.
    """
    local = str(lib.root / "tv")
    _settings(lib, sonarr_path_prefix_remote="/sonarr/tv",
              sonarr_path_prefix_local=local)
    old = _file(lib.folder / "Show - S01E03 - TBA.mkv")
    file_id, _ = _imported(lib, old)
    new = str(lib.folder / "Show - S01E03 - The Title.mkv")
    os.rename(old, new)

    def remote(path):
        return "/sonarr/tv" + path[len(local):]

    _webhook(*_sonarr_rename(remote(old), remote(new)))

    assert [(r[0], r[1]) for r in _records(lib)] == [(file_id, new)]


def test_a_record_a_scan_made_at_the_new_name_is_replaced(lib):
    """
    A scan between the rename and the webhook gives the new name a record
    of its own. That one has no history; the old one is the file's.
    """
    old = _file(lib.folder / "Show - S01E04 - TBA.mkv")
    file_id, _ = _imported(lib, old)
    new = str(lib.folder / "Show - S01E04 - The Title.mkv")
    os.rename(old, new)
    scanned_id, _ = _imported(lib, new)
    assert scanned_id != file_id

    _webhook(*_sonarr_rename(old, new))

    assert [(r[0], r[1]) for r in _records(lib)] == [(file_id, new)]
    assert _items(lib, scanned_id) == []


# ── When the disk does not agree, nothing moves ──────────────────────────────

def _move(lib, old, new):
    with lib.Session() as db:
        return scanner.move_renamed_file(db, old, new)


def test_nothing_recorded_at_the_old_name_is_not_an_error(lib):
    old = str(lib.folder / "Never Seen.mkv")
    new = _file(lib.folder / "Never Seen - Renamed.mkv")

    assert _move(lib, old, new) == "no_record"
    assert _records(lib) == []


def test_a_file_still_at_its_old_name_was_not_renamed(lib):
    """Both names on disk — a copy, or a hardlink — is not a move."""
    old = _file(lib.folder / "Show - S01E05.mkv")
    file_id, _ = _imported(lib, old)
    new = _file(lib.folder / "Show - S01E05 - Copy.mkv")

    assert _move(lib, old, new) == "still_at_old_path"
    assert [(r[0], r[1]) for r in _records(lib)] == [(file_id, old)]


def test_nothing_at_the_new_name_leaves_the_record_where_it_is(lib):
    old = _file(lib.folder / "Show - S01E06.mkv")
    file_id, _ = _imported(lib, old)
    os.remove(old)
    new = str(lib.folder / "Show - S01E06 - Elsewhere.mkv")

    assert _move(lib, old, new) == "missing_at_new_path"
    assert [(r[0], r[1]) for r in _records(lib)] == [(file_id, old)]


def test_a_job_running_on_the_new_name_is_not_pulled_out_from_under_it(lib):
    old = _file(lib.folder / "Show - S01E07 - TBA.mkv")
    file_id, _ = _imported(lib, old)
    new = str(lib.folder / "Show - S01E07 - The Title.mkv")
    os.rename(old, new)
    scanned_id, scanned_job = _imported(lib, new)
    with lib.Session() as db:
        db.get(QueueItem, scanned_job).status = "processing"
        db.commit()

    assert _move(lib, old, new) == "new_path_busy"
    assert [(r[0], r[1]) for r in _records(lib)] == [(file_id, old), (scanned_id, new)]
    assert _items(lib, scanned_id)[0][1] == "processing"


# ── Extracted subtitles ──────────────────────────────────────────────────────

def _flagged(lib, file_id, sidecar):
    with lib.Session() as db:
        db.add(SubtitleLanguageFlag(file_id=file_id, stream_index=2,
                                    detected_language="und",
                                    extracted_path=sidecar))
        db.commit()


def _flag_path(lib):
    with lib.Session() as db:
        return db.query(SubtitleLanguageFlag).one().extracted_path


def test_a_subtitle_renamed_with_its_file_keeps_its_review_flag(lib):
    """
    The flag records where the .srt went, and the review page renames that
    file when the language is answered. Left pointing at the old name, the
    next scan finds nothing there and deletes the flag with the question.
    """
    old = _file(lib.folder / "Show - S01E08 - TBA.mkv")
    file_id, _ = _imported(lib, old)
    old_srt = _file(lib.folder / "Show - S01E08 - TBA.und.srt")
    _flagged(lib, file_id, old_srt)
    new = str(lib.folder / "Show - S01E08 - The Title.mkv")
    new_srt = str(lib.folder / "Show - S01E08 - The Title.und.srt")
    os.rename(old, new)
    os.rename(old_srt, new_srt)

    assert _move(lib, old, new) == "moved"
    assert _flag_path(lib) == new_srt


def test_a_subtitle_left_behind_keeps_its_old_path(lib):
    """Whether the service moved the sidecar is not assumed either way."""
    old = _file(lib.folder / "Show - S01E09 - TBA.mkv")
    file_id, _ = _imported(lib, old)
    old_srt = _file(lib.folder / "Show - S01E09 - TBA.und.srt")
    _flagged(lib, file_id, old_srt)
    new = str(lib.folder / "Show - S01E09 - The Title.mkv")
    os.rename(old, new)

    assert _move(lib, old, new) == "moved"
    assert _flag_path(lib) == old_srt
