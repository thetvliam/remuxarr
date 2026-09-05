"""
worker.py — the post-job notification decision layer.

Four functions decide what the outside world hears about a finished job:
_load_post_job_data (Sonarr and Radarr), _load_plex_notify_data,
_load_forge_plex_notify_data, and the three fire-and-forget triggers that
dispatch what they return. Between them they held 71 of worker.py's 239
uncovered statements and had no tests at all.

The transport beneath them is already pinned — test_arr_notifications.py
covers arr_post and both notifiers, test_plex_client.py covers the Plex
client. What was missing is the layer above: whether a notification happens
at all, and which URL and key it goes out with. That is the mirror image of
the gap test_email_notify.py closed, where the deciding half was covered
and the sending half was not.

Everything here fails silently by construction. All three triggers swallow
every exception, and every loader returns None on any missing piece of
configuration, so a wrong key or a skipped notification produces no
user-visible signal at all: Sonarr never rescans, Plex never re-reads the
file, and Remuxarr looks like it worked. test_arr_notifications.py records
the same consequence for the layer below it.

Confirmed unprotected before this file was written. Eight mutations, each
run against the whole 1202-test suite, all survived: cross-wiring Radarr to
read sonarr_api_key; ignoring the per-service enabled gate; dropping
rstrip("/") from the *arr URL; notifying a job that carries no *arr id; and
ignoring the plex_enabled and path-mappings gates in BOTH Plex loaders.

The two Plex loaders are near-identical, and that duplication is why the
last two mutations needed running twice — the gating logic exists in two
places and nothing noticed either copy losing it. Both are pinned here
separately, and test_the_two_plex_loaders_do_not_share_a_payload guards the
one thing that genuinely differs between them, in the same spirit as
test_arr_notifications.py's guard against its two notifiers converging.

Verified by mutation: 30 mutations applied across the four loaders and the
three triggers, all 30 killed. One further mutation is recorded as out of
scope rather than as a survivor: the same missing-job guard pattern appears
in _claim_next, and blanking it there changes nothing any test here
asserts. That is correct — _claim_next is part of the queue lifecycle this
file deliberately does not reach — but it is a real hole in worker.py's
remaining 168 uncovered statements, not an equivalent mutant.
"""
import asyncio
import json

import pytest

import app.core.worker as worker
from app.database.models import (
    Ac3ForgeJob,
    AppSetting,
    MediaFile,
    PlannedAction,
    PlexAnalyzeBacklog,
    QueueItem,
)


ARR_ON = {
    "sonarr_enabled": True,
    "sonarr_url":     "http://sonarr:8989/",
    "sonarr_api_key": "SONARR-KEY",
    "radarr_enabled": True,
    "radarr_url":     "http://radarr:7878/",
    "radarr_api_key": "RADARR-KEY",
}

PLEX_ON = {
    "plex_enabled":       True,
    "plex_url":           "http://plex:32400/",
    "plex_token":         "PLEX-TOKEN",
    "plex_path_mappings": [{"local": "/media", "plex": "/data"}],
}


# ── Harness ──────────────────────────────────────────────────────────────────

@pytest.fixture
def db(monkeypatch):
    """
    An in-memory database with worker.SessionLocal pointed at it.

    The loaders each open `with SessionLocal() as db:`, so the factory hands
    back the same Session every time and lets the with-block close it
    between calls — a Session reopens a transaction on next use, which is
    what makes the several-calls-in-one-test cases below work. Matches how
    test_assorted_regressions.py drives _load_email_notify_data.
    """
    from sqlalchemy.orm import sessionmaker

    from tests.conftest import memory_engine

    from app.database.models import Base

    engine = memory_engine()
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    monkeypatch.setattr(worker, "SessionLocal", lambda: session)
    return session


def settings(db, **values):
    for key, value in values.items():
        db.merge(AppSetting(key=key, value=json.dumps(value)))
    db.commit()


def media(db, file_id=1, path="/media/Show.mkv"):
    db.merge(MediaFile(id=file_id, path=path, filename=path.rsplit("/", 1)[-1],
                       directory=path.rsplit("/", 1)[0], size=1, mtime=1.0))
    db.commit()
    return file_id


def job(db, job_id=1, file_id=1, *, with_media=True, **fields):
    if with_media:
        media(db, file_id)
    fields.setdefault("status", "success")
    fields.setdefault("output_path", "/media/Show.mkv")
    db.add(QueueItem(id=job_id, file_id=file_id, **fields))
    db.commit()
    return job_id


def forge_job(db, forge_id=1, file_id=1, path="/media/Show.mkv"):
    media(db, file_id, path)
    db.add(Ac3ForgeJob(id=forge_id, file_id=file_id, status="success"))
    db.commit()
    return forge_id


def backlog_rows(db):
    return db.query(PlexAnalyzeBacklog).all()


def run(coro_fn):
    """asyncio.run around a driver that needs the running loop, as the
    triggers take the loop as an argument."""
    async def driver():
        return await coro_fn(asyncio.get_running_loop())

    return asyncio.run(driver())


# ── _load_post_job_data: the final state ─────────────────────────────────────

def test_the_final_state_is_read_from_the_job(db):
    job(db, 1, status="failed", error_message="ffmpeg exited 1",
        is_new_file=True, output_path="/media/out.mkv")

    final = worker._load_post_job_data(1)["final"]

    assert final == {
        "status":      "failed",
        "filename":    "Show.mkv",
        "error":       "ffmpeg exited 1",
        "is_new_file": True,
        "output_path": "/media/out.mkv",
    }


def test_a_job_whose_media_row_is_gone_reports_an_empty_filename(db):
    """
    The broadcast reads this straight through to the UI, so the guard is
    what stops a deleted media row turning a completion message into an
    AttributeError on a background task nobody is watching.
    """
    job(db, 1, file_id=99, with_media=False)

    assert worker._load_post_job_data(1)["final"]["filename"] == ""


def test_a_missing_job_returns_nothing_at_all(db):
    assert worker._load_post_job_data(404) is None


# ── _load_post_job_data: which *arr gets told ────────────────────────────────

def test_sonarr_and_radarr_each_get_their_own_url_and_key(db):
    """
    The copy-paste guard. Both services go through one helper called twice
    with different setting names, which is exactly the shape that produces
    a crossed key — and because the notifier below swallows everything, a
    crossed key means Sonarr silently never rescans. Asserting the whole
    dict per service is deliberate: a membership check would pass with the
    URLs swapped.
    """
    settings(db, **ARR_ON)
    job(db, 1, sonarr_series_id=11, radarr_movie_id=22)

    data = worker._load_post_job_data(1)

    assert data["sonarr"] == {
        "entity_id": 11,
        "url":       "http://sonarr:8989",
        "api_key":   "SONARR-KEY",
    }
    assert data["radarr"] == {
        "entity_id": 22,
        "url":       "http://radarr:7878",
        "api_key":   "RADARR-KEY",
    }


def test_a_service_switched_off_is_not_notified(db):
    settings(db, **(ARR_ON | {"sonarr_enabled": False}))
    job(db, 1, sonarr_series_id=11, radarr_movie_id=22)

    data = worker._load_post_job_data(1)

    assert data["sonarr"] is None
    assert data["radarr"] is not None


def test_a_job_carrying_no_arr_id_is_not_notified(db):
    """
    Only webhook-originated jobs carry these ids. A manually queued file
    has neither, and notifying on one would send an id of None to an *arr
    that has no idea what it refers to.
    """
    settings(db, **ARR_ON)
    job(db, 1)

    data = worker._load_post_job_data(1)

    assert data["sonarr"] is None
    assert data["radarr"] is None


@pytest.mark.parametrize("override", [
    {"sonarr_url": ""},
    {"sonarr_api_key": ""},
])
def test_an_enabled_but_unconfigured_service_is_skipped(db, override):
    settings(db, **(ARR_ON | override))
    job(db, 1, sonarr_series_id=11)

    assert worker._load_post_job_data(1)["sonarr"] is None


def test_arr_data_is_computed_even_when_the_job_failed(db):
    """
    Pins what the docstring promises: the read happens unconditionally and
    the caller is what gates on success. A future reader tempted to skip
    the work for failed jobs would be changing when the read happens, not
    what is sent.
    """
    settings(db, **ARR_ON)
    job(db, 1, status="failed", sonarr_series_id=11)

    assert worker._load_post_job_data(1)["sonarr"]["entity_id"] == 11


# ── _load_plex_notify_data ───────────────────────────────────────────────────

def test_plex_gets_the_refresh_payload_for_a_new_file(db):
    settings(db, **PLEX_ON)
    job(db, 1, is_new_file=True, output_path="/media/New.mkv")

    assert worker._load_plex_notify_data(1) == {
        "url":        "http://plex:32400",
        "token":      "PLEX-TOKEN",
        "mappings":   [{"local": "/media", "plex": "/data"}],
        "local_path": "/media/New.mkv",
    }


@pytest.mark.parametrize("override", [
    {"plex_enabled": False},
    {"plex_url": ""},
    {"plex_token": ""},
    {"plex_path_mappings": []},
])
def test_plex_is_skipped_when_off_or_unconfigured(db, override):
    settings(db, **(PLEX_ON | override))
    job(db, 1, is_new_file=True)

    assert worker._load_plex_notify_data(1) is None


def test_a_missing_job_gets_no_plex_notification(db):
    settings(db, **PLEX_ON)

    assert worker._load_plex_notify_data(404) is None


def test_a_reprocessed_file_still_gets_the_immediate_refresh(db):
    """
    The refresh is unconditional by design, so that a file wrongly
    classified as reprocessed is not left waiting on the backlog window for
    something Plex would have picked up straight away.
    """
    settings(db, **PLEX_ON)
    job(db, 1, is_new_file=False)

    assert worker._load_plex_notify_data(1) is not None
    assert backlog_rows(db) == []


def test_a_reprocessed_file_is_queued_for_the_backlog_when_enabled(db):
    settings(db, **(PLEX_ON | {"plex_analyze_backlog_enabled": True}))
    job(db, 1, is_new_file=False)

    assert worker._load_plex_notify_data(1) is not None
    assert [(r.file_id, r.expected_language) for r in backlog_rows(db)] == [(1, None)]


def test_a_new_file_is_never_queued_for_the_backlog(db):
    """
    The early return for new files sits above the enqueue. Moving it below
    would fill the backlog with files Plex has nothing stale to re-read
    for, and the Analyze call is the expensive one the queue exists to
    ration.
    """
    settings(db, **(PLEX_ON | {"plex_analyze_backlog_enabled": True}))
    job(db, 1, is_new_file=True)

    worker._load_plex_notify_data(1)

    assert backlog_rows(db) == []


def test_a_second_run_does_not_queue_the_same_file_twice(db):
    settings(db, **(PLEX_ON | {"plex_analyze_backlog_enabled": True}))
    job(db, 1, is_new_file=False)

    worker._load_plex_notify_data(1)
    worker._load_plex_notify_data(1)

    assert len(backlog_rows(db)) == 1


def test_the_backlog_records_the_language_the_job_set(db):
    """
    The drain loop uses this to check whether Plex's own maintenance
    already applied the tag, and skips the Analyze call when it did.
    Losing it turns every language fix into an unconditional Analyze.
    """
    settings(db, **(PLEX_ON | {"plex_analyze_backlog_enabled": True}))
    job(db, 1, is_new_file=False)
    db.add(PlannedAction(queue_item_id=1, order=0, action_type="set_language",
                         description="Fix undefined language tag",
                         target_language="eng"))
    db.commit()

    worker._load_plex_notify_data(1)

    assert backlog_rows(db)[0].expected_language == "eng"


def test_the_backlog_records_no_language_when_the_job_set_none(db):
    settings(db, **(PLEX_ON | {"plex_analyze_backlog_enabled": True}))
    job(db, 1, is_new_file=False)
    db.add(PlannedAction(queue_item_id=1, order=0, action_type="strip_subs",
                         description="Remove subtitles"))
    db.commit()

    worker._load_plex_notify_data(1)

    assert backlog_rows(db)[0].expected_language is None


# ── _load_forge_plex_notify_data ─────────────────────────────────────────────

def test_a_forge_job_gets_the_refresh_payload_for_its_media_path(db):
    settings(db, **PLEX_ON)
    forge_job(db, 1, path="/media/Forged.mkv")

    assert worker._load_forge_plex_notify_data(1) == {
        "url":        "http://plex:32400",
        "token":      "PLEX-TOKEN",
        "mappings":   [{"local": "/media", "plex": "/data"}],
        "local_path": "/media/Forged.mkv",
    }


@pytest.mark.parametrize("override", [
    {"plex_enabled": False},
    {"plex_url": ""},
    {"plex_token": ""},
    {"plex_path_mappings": []},
])
def test_the_forge_path_is_skipped_when_off_or_unconfigured(db, override):
    settings(db, **(PLEX_ON | override))
    forge_job(db, 1)

    assert worker._load_forge_plex_notify_data(1) is None


def test_a_missing_forge_job_or_media_row_returns_nothing(db):
    settings(db, **PLEX_ON)
    db.add(Ac3ForgeJob(id=2, file_id=77, status="success"))
    db.commit()

    assert worker._load_forge_plex_notify_data(404) is None
    assert worker._load_forge_plex_notify_data(2) is None


def test_a_forge_job_queues_the_backlog_with_no_expected_language(db):
    """
    Forge changes the codec layout, never a language tag, so there is
    nothing for the drain loop to verify against — a language here would
    make it skip an Analyze the forge specifically needs.
    """
    settings(db, **(PLEX_ON | {"plex_analyze_backlog_enabled": True}))
    forge_job(db, 1)

    worker._load_forge_plex_notify_data(1)

    assert [(r.file_id, r.expected_language) for r in backlog_rows(db)] == [(1, None)]


def test_a_forge_job_does_not_queue_the_backlog_when_the_toggle_is_off(db):
    settings(db, **PLEX_ON)
    forge_job(db, 1)

    worker._load_forge_plex_notify_data(1)

    assert backlog_rows(db) == []


def test_forging_twice_does_not_queue_the_same_file_twice(db):
    """
    Forge and undo against one file is an ordinary sequence, and each
    duplicate row is another Analyze against the same ratingKey — the
    expensive call the backlog exists to rate-limit.
    """
    settings(db, **(PLEX_ON | {"plex_analyze_backlog_enabled": True}))
    forge_job(db, 1)

    worker._load_forge_plex_notify_data(1)
    worker._load_forge_plex_notify_data(1)

    assert len(backlog_rows(db)) == 1


def test_the_two_plex_loaders_do_not_share_a_payload(db):
    """
    The two loaders are near-identical and their gating has already drifted
    into two copies. What must not converge is the path they report: the
    main pipeline writes a new file and refreshes job.output_path, while
    forge replaces in place and must refresh the media row's own path.
    Swapping either would refresh a path Plex does not have.
    """
    settings(db, **PLEX_ON)
    media(db, 1, "/media/OnDisk.mkv")
    db.add(QueueItem(id=1, file_id=1, status="success", is_new_file=True,
                     output_path="/media/JobOutput.mkv"))
    db.add(Ac3ForgeJob(id=1, file_id=1, status="success"))
    db.commit()

    assert worker._load_plex_notify_data(1)["local_path"] == "/media/JobOutput.mkv"
    assert worker._load_forge_plex_notify_data(1)["local_path"] == "/media/OnDisk.mkv"


# ── Triggers ─────────────────────────────────────────────────────────────────

def test_the_arr_trigger_passes_url_key_and_entity_through(db):
    calls = []
    data = {"url": "http://sonarr:8989", "api_key": "K", "entity_id": 11}

    run(lambda loop: worker._trigger_arr_notify(
        data, loop, lambda *a: calls.append(a), "Sonarr"))

    assert calls == [("http://sonarr:8989", "K", 11)]


def test_a_failing_arr_notifier_never_escapes_the_trigger(db):
    """
    These are fire-and-forget tasks with nothing awaiting them. An escaping
    exception is not reported anywhere useful, it just kills the task.
    """
    def _boom(*_):
        raise RuntimeError("sonarr is down")

    run(lambda loop: worker._trigger_arr_notify(
        {"url": "u", "api_key": "k", "entity_id": 1}, loop, _boom, "Sonarr"))


def test_the_plex_trigger_passes_the_refresh_payload_through(db, monkeypatch):
    calls = []
    monkeypatch.setattr(worker, "notify_plex_new_file",
                        lambda *a: calls.append(a))
    data = {"url": "http://plex:32400", "token": "T",
            "mappings": [{"local": "/media"}], "local_path": "/media/S.mkv"}

    run(lambda loop: worker._trigger_plex_notify(data, loop))

    assert calls == [("http://plex:32400", "T",
                      [{"local": "/media"}], "/media/S.mkv")]


def test_a_failing_plex_notifier_never_escapes_the_trigger(db, monkeypatch):
    def _boom(*_):
        raise RuntimeError("plex is down")

    monkeypatch.setattr(worker, "notify_plex_new_file", _boom)

    run(lambda loop: worker._trigger_plex_notify(
        {"url": "u", "token": "t", "mappings": [], "local_path": "/p"}, loop))


def test_the_email_trigger_sends_the_kind_the_breaker_chose(db, monkeypatch):
    """
    One dict feeds two different senders and the only thing selecting
    between them is data["kind"]. Getting it wrong sends the paused-
    notifications warning for an ordinary failure, or suppresses the one
    warning that explains why the emails stopped.
    """
    sent = []
    monkeypatch.setattr(worker, "send_failure_email",
                        lambda *a: sent.append(("failure", a)))
    monkeypatch.setattr(worker, "send_breaker_tripped_email",
                        lambda *a: sent.append(("tripped", a)))

    run(lambda loop: worker._trigger_email_notify(
        {"kind": "tripped", "cfg": {"c": 1}, "count": 5}, loop))
    run(lambda loop: worker._trigger_email_notify(
        {"kind": "failure", "cfg": {"c": 1}, "filename": "S.mkv",
         "error": "boom", "count": 2}, loop))

    assert sent == [
        ("tripped", ({"c": 1}, 5)),
        ("failure", ({"c": 1}, "S.mkv", "boom", 2)),
    ]


def test_a_failing_email_send_never_escapes_the_trigger(db, monkeypatch):
    def _boom(*_):
        raise RuntimeError("smtp is down")

    monkeypatch.setattr(worker, "send_failure_email", _boom)

    run(lambda loop: worker._trigger_email_notify(
        {"kind": "failure", "cfg": {}, "filename": "S.mkv",
         "error": "e", "count": 1}, loop))
