"""
An import that no job will change is shown to Plex at once.

Plex hears about a file at the end of a job that succeeded. Someone who has
removed Plex's own connection from Sonarr or Radarr, as the Enable Plex
Notifications setting suggests, relies on that alone, so an import that never
got such a job never reached Plex: one that needed no changes, one the
exclude settings match, and every import while dry run is on, whose job ends
as a dry run. Each stayed out of Plex until Plex's own periodic scan found
it. They now get the folder refresh a job sends, once the queue attempt has
run (webhooks._tell_plex_if_unchanged).

An import that will be processed is still left to its job, so Plex does not
index the original first and then need to read the file again.

These drive the real Sonarr handler, debounce and queue against a
file-backed database, as test_webhook_intake.py does, with the probe
stubbed and the Plex call recorded rather than sent.

Verified by mutation, 7 applied, 7 killed. Six survived the whole suite
without this file: dry-run imports not told; imports with a real job told
too; excluded imports not told; an earlier skip of the same path counting as
this one; every import that returned no item told, a failed probe included;
and the debounce never asking. The seventh, Plex told with its notifications
off, was already killed by test_post_job_notify.py through the settings
check this shares with the post-job notification, and is killed here too.
"""
import asyncio
import json
import time
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

import app.database.session as session_module
from app.api.routes import webhooks
from app.config import settings as app_settings
from app.core import scanner
from app.core.probe import ProbeError
from app.database.models import AppSetting, Base, QueueItem, WebhookIntake

PLEX_ON = {
    "plex_enabled":       True,
    "plex_url":           "http://plex:32400/",
    "plex_token":         "PLEX-TOKEN",
    "plex_path_mappings": ["/media=/data"],
}
PLEX_CALL = ("http://plex:32400", "PLEX-TOKEN", ["/media=/data"])


def _probe_mkv():
    """H.264 and one English AAC track in MKV. The defaults remux it to MP4;
    with prefer_mp4_container off it needs nothing."""
    return {"format": {"format_name": "matroska,webm", "duration": "1.0"},
            "streams": [
                {"index": 0, "codec_type": "video", "codec_name": "h264"},
                {"index": 1, "codec_type": "audio", "codec_name": "aac",
                 "channels": 2, "tags": {"language": "eng"},
                 "disposition": {"default": 1}},
            ]}


class _Request:
    def __init__(self, payload):
        self._payload = payload

    async def json(self):
        return self._payload


@pytest.fixture
def lib(tmp_path, monkeypatch):
    engine = create_engine(f"sqlite:///{tmp_path / 'remuxarr.db'}",
                           connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine)
    monkeypatch.setattr(webhooks, "SessionLocal", factory)
    monkeypatch.setattr(session_module, "SessionLocal", factory)
    monkeypatch.setattr(scanner, "probe_file", lambda *_a, **_kw: _probe_mkv())
    monkeypatch.setattr(app_settings, "WEBHOOK_DEBOUNCE_SECONDS", 0.05)
    monkeypatch.setattr(webhooks, "_pending", {})

    told = []
    monkeypatch.setattr(webhooks, "notify_plex_new_file",
                        lambda url, token, mappings, path:
                        told.append(((url, token, mappings), path)))

    folder = tmp_path / "tv" / "Show" / "Season 01"
    folder.mkdir(parents=True)

    def media(name="Show - S01E01.mkv"):
        f = folder / name
        f.write_bytes(b"\0" * 64)
        return str(f)

    def settings(**values):
        with factory() as db:
            for key, value in values.items():
                db.merge(AppSetting(key=key, value=json.dumps(value)))
            db.commit()

    def items():
        with factory() as db:
            return [(i.status, i.is_dry_run) for i in db.query(QueueItem).order_by(QueueItem.id)]

    def rows():
        with factory() as db:
            return db.query(WebhookIntake).count()

    def import_(path):
        """Sonarr's webhook for path, waited out to the end of its timer."""
        monkeypatch.setattr(webhooks, "_lock", asyncio.Lock())

        async def go():
            await webhooks.sonarr_webhook(_Request({
                "eventType": "Download", "series": {"id": 7},
                "episodeFile": {"path": path}}))
            await _until(lambda: not webhooks._pending and not rows())

        asyncio.run(go())

    yield SimpleNamespace(media=media, settings=settings, items=items,
                          import_=import_, told=told)
    engine.dispose()


async def _until(predicate, timeout=5.0):
    deadline = time.monotonic() + timeout
    while not predicate():
        assert time.monotonic() < deadline, "timed out waiting"
        await asyncio.sleep(0.02)


# ── Told at once ─────────────────────────────────────────────────────────────

def test_an_import_that_needs_no_changes_is_shown_to_plex(lib):
    lib.settings(dry_run_mode=False, prefer_mp4_container=False, **PLEX_ON)
    path = lib.media()

    lib.import_(path)

    assert lib.items() == [("skipped", False)]
    assert lib.told == [(PLEX_CALL, path)]


def test_an_excluded_import_is_shown_to_plex(lib):
    lib.settings(dry_run_mode=False, **PLEX_ON)
    path = lib.media("Show - S01E01-trailer.mkv")

    lib.import_(path)

    assert lib.items() == [], "the trailer was not excluded"
    assert lib.told == [(PLEX_CALL, path)]


def test_an_import_while_dry_run_is_on_is_shown_to_plex(lib):
    """Its job will end as a dry run, which tells Plex nothing."""
    lib.settings(dry_run_mode=True, **PLEX_ON)
    path = lib.media()

    lib.import_(path)

    assert lib.items() == [("pending", True)]
    assert lib.told == [(PLEX_CALL, path)]


# ── Left to the job, or not told at all ──────────────────────────────────────

def test_an_import_a_job_will_change_waits_for_the_job(lib):
    """Told now, Plex would index the original and then have to re-read the
    file once the job had changed it. Dry run is on by default, so turned
    off here."""
    lib.settings(dry_run_mode=False, **PLEX_ON)
    path = lib.media()

    lib.import_(path)

    assert lib.items() == [("pending", False)]
    assert lib.told == []


def test_nothing_is_sent_with_plex_notifications_off(lib):
    """Set up in full but switched off, so the switch alone decides."""
    lib.settings(prefer_mp4_container=False, **{**PLEX_ON, "plex_enabled": False})
    path = lib.media()

    lib.import_(path)

    assert lib.items() == [("skipped", False)]
    assert lib.told == []


def test_an_earlier_skip_is_not_this_import(lib, monkeypatch):
    """
    Sonarr replaces an episode that needed no changes, and the new file
    cannot be probed. The skip on record describes the old file; nothing was
    decided about this one, so Plex is not told on the strength of it.
    """
    lib.settings(prefer_mp4_container=False, **PLEX_ON)
    path = lib.media()
    lib.import_(path)
    assert lib.told == [(PLEX_CALL, path)]

    def unreadable(*_a, **_kw):
        raise ProbeError("not a media file")

    monkeypatch.setattr(scanner, "probe_file", unreadable)
    time.sleep(0.01)
    lib.import_(path)

    assert lib.told == [(PLEX_CALL, path)], "the earlier skip was taken for this one"


def test_a_plex_failure_leaves_the_import_dealt_with(lib, monkeypatch):
    lib.settings(prefer_mp4_container=False, **PLEX_ON)
    path = lib.media()

    def down(*_a, **_kw):
        raise OSError("Plex is down")

    monkeypatch.setattr(webhooks, "notify_plex_new_file", down)
    lib.import_(path)

    assert lib.items() == [("skipped", False)]
