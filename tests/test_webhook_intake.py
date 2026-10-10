"""
A webhook accepted just before a stop is queued on the next start.

The handlers answer "accepted" at once and queue the file after a debounce,
held until then by an in-memory timer. A stop or a crash inside that window
(ten seconds by default) used to lose the file: the timer went with the
event loop, Sonarr or Radarr had been told it was accepted, and the file
waited for a scan, if one was scheduled at all, without the IDs the webhook
carried.

Each translated path is now written to webhook_intake before the webhook is
answered, deleted once the queue attempt has run, and replayed through the
same debounce on the next start. These drive the real handlers, the real
replay and the real main.lifespan against a file-backed database, with only
the probe stubbed. The debounce is shortened so the timer fires inside the
test. A "stop" is asyncio.run returning with the timer still pending, which
cancels it the way the server's event loop does on shutdown.

Confirmed unprotected before this file was written: ten mutations, each
run against the whole 1759-test suite, all ten survived. The path not
recorded on arrival; a second event not replacing the first event's row;
the row deleted by path rather than by its own id; deleted only when the
queue attempt succeeds; never deleted; a failed write failing the webhook;
the replay arming nothing; the replay dropping the stored IDs;
main.lifespan not calling the replay; and the table without AUTOINCREMENT.
All ten are killed here.

The last was not in the plan. The first version of the table had a plain
integer id, and test_a_first_attempt_finishing_late_leaves_the_newer_record
failed against it: SQLite gave the replacing row the replaced row's id, so
deleting by id was deleting by path after all.
"""
import asyncio
import threading
import time
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

import app.database.session as session_module
import app.main as main
from app.api.routes import webhooks
from app.config import settings as app_settings
from app.core import scanner
from app.database.models import Base, QueueItem, WebhookIntake


def _probe_mkv():
    """An MKV that needs a conversion under the defaults."""
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

    media = tmp_path / "tv" / "Show" / "Season 01" / "Show - S01E01.mkv"
    media.parent.mkdir(parents=True)
    media.write_bytes(b"\0" * 64)
    path = str(media)

    def sonarr(series_id):
        return webhooks.sonarr_webhook(_Request({
            "eventType": "Download", "series": {"id": series_id},
            "episodeFile": {"path": path}}))

    def radarr(movie_id):
        return webhooks.radarr_webhook(_Request({
            "eventType": "Download", "movie": {"id": movie_id},
            "movieFile": {"path": path}}))

    def rows():
        with factory() as db:
            return [(r.path, r.sonarr_series_id, r.radarr_movie_id)
                    for r in db.query(WebhookIntake).order_by(WebhookIntake.id)]

    def items():
        with factory() as db:
            return [(i.status, i.sonarr_series_id, i.radarr_movie_id)
                    for i in db.query(QueueItem).order_by(QueueItem.id)]

    def run(coro_fn):
        # A fresh lock per event loop. The module's lock is created once at
        # import, and a lock that ever had to wait belongs to that loop;
        # production has one loop, these tests start several.
        monkeypatch.setattr(webhooks, "_lock", asyncio.Lock())
        return asyncio.run(coro_fn())

    yield SimpleNamespace(path=path, sonarr=sonarr, radarr=radarr, rows=rows,
                          items=items, run=run, engine=engine)
    engine.dispose()


async def _until(predicate, timeout=5.0):
    deadline = time.monotonic() + timeout
    while not predicate():
        assert time.monotonic() < deadline, "timed out waiting"
        await asyncio.sleep(0.02)


# ── On arrival ───────────────────────────────────────────────────────────────

@pytest.mark.parametrize("service,expected", [
    ("sonarr", (7, None)),
    ("radarr", (None, 7)),
])
def test_an_accepted_webhook_is_on_record_before_it_is_answered(lib, service, expected):
    async def receive_then_stop():
        response = await getattr(lib, service)(7)
        return response, lib.rows()

    response, rows = lib.run(receive_then_stop)

    assert response["status"] == "accepted"
    assert rows == [(lib.path, *expected)]


def test_the_record_goes_once_the_file_is_queued(lib):
    async def receive_and_wait():
        await lib.sonarr(7)
        await _until(lambda: lib.items() and not lib.rows())

    lib.run(receive_and_wait)

    assert lib.items() == [("pending", 7, None)]
    assert lib.rows() == []


# ── Across a stop ────────────────────────────────────────────────────────────

def test_a_stop_inside_the_window_keeps_it_and_the_next_start_queues_it(lib):
    async def receive_then_stop():
        await lib.sonarr(7)

    lib.run(receive_then_stop)
    assert lib.items() == [], "the timer fired before the stop"
    assert lib.rows() == [(lib.path, 7, None)]

    async def next_start():
        await webhooks.replay_webhook_intake()
        await _until(lambda: lib.items() and not lib.rows())

    lib.run(next_start)

    assert lib.items() == [("pending", 7, None)]
    assert lib.rows() == []


def test_the_start_replays_what_the_last_run_left(lib, monkeypatch):
    """The real lifespan, with everything but the replay stubbed out."""
    async def nothing():
        pass

    monkeypatch.setattr(main, "init_db", lambda: None)
    monkeypatch.setattr(main, "_cleanup_orphaned_temp_files", lambda: None)
    monkeypatch.setattr(main, "start_worker", nothing)
    monkeypatch.setattr(main, "stop_worker", nothing)
    monkeypatch.setattr(main, "_spawn", lambda coro, name: coro.close())

    async def receive_then_stop():
        await lib.sonarr(7)

    lib.run(receive_then_stop)

    async def next_start():
        async with main.lifespan(main.app):
            await _until(lambda: lib.items() and not lib.rows())

    lib.run(next_start)

    assert lib.items() == [("pending", 7, None)]


# ── More than one event for a file ───────────────────────────────────────────

def test_a_second_event_replaces_the_first(lib):
    async def receive_twice():
        await lib.sonarr(7)
        await lib.sonarr(8)
        rows = lib.rows()
        await _until(lambda: lib.items() and not lib.rows())
        return rows

    assert lib.run(receive_twice) == [(lib.path, 8, None)]
    assert lib.items() == [("pending", 8, None)]


def test_a_first_attempt_finishing_late_leaves_the_newer_record(lib, monkeypatch):
    """
    The first timer has fired and its queue attempt is running in a thread
    when the second event arrives. Cancelling the first timer does not stop
    the thread, which goes on to finish and delete a row. It must delete
    its own, already replaced, and not the second event's, which the stop
    that follows leaves for the next start.
    """
    entered, release = threading.Event(), threading.Event()
    real_queue = webhooks.queue_single_file
    calls = []

    def held_first(db, path, **ids):
        calls.append(ids)
        if len(calls) == 1:
            entered.set()
            release.wait(5)
        return real_queue(db, path, **ids)

    monkeypatch.setattr(webhooks, "queue_single_file", held_first)

    # Wrapped around the whole attempt, so "finished" includes whatever the
    # attempt deletes on its way out.
    finished = []
    real_queue_sync = webhooks._queue_sync

    def queue_sync(*args):
        try:
            return real_queue_sync(*args)
        finally:
            finished.append(args)

    monkeypatch.setattr(webhooks, "_queue_sync", queue_sync)

    async def overlap_then_stop():
        await lib.sonarr(7)
        await _until(entered.is_set)
        # The second timer must still be waiting when the first attempt
        # ends, so the row it holds is the one left at the stop.
        monkeypatch.setattr(app_settings, "WEBHOOK_DEBOUNCE_SECONDS", 30)
        await lib.sonarr(8)
        release.set()
        await _until(lambda: finished)

    lib.run(overlap_then_stop)

    assert len(calls) == 1
    assert lib.rows() == [(lib.path, 8, None)]


# ── Failures ─────────────────────────────────────────────────────────────────

def test_a_failed_attempt_is_not_replayed(lib, monkeypatch):
    """Logged and dropped, as before; kept, it would fail at every start."""
    attempts = []

    def broken(db, path, **ids):
        attempts.append(path)
        raise RuntimeError("probe blew up")

    monkeypatch.setattr(webhooks, "queue_single_file", broken)

    async def receive_and_wait():
        await lib.sonarr(7)
        await _until(lambda: attempts)
        await _until(lambda: not lib.rows(), timeout=2.0)

    lib.run(receive_and_wait)

    assert lib.rows() == []
    assert lib.items() == []


def test_a_webhook_that_cannot_be_recorded_is_still_queued(lib):
    Base.metadata.tables["webhook_intake"].drop(lib.engine)

    async def receive_and_wait():
        response = await lib.sonarr(7)
        await _until(lambda: lib.items())
        return response

    assert lib.run(receive_and_wait)["status"] == "accepted"
    assert lib.items() == [("pending", 7, None)]
