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

The newest-ID lookup has since moved to scanner.newest_arr_ids, shared
with scans that carry an earlier webhook's IDs; the tests below still kill
its mutants.

Reverts came later and reuse these pieces: the *arr target builder was
lifted out of _load_post_job_data into _arr_notify_target, and the forge's
Plex body into _plex_refresh_target, so jobs, forge runs and reverts share
one copy of each. Mutants for the revert path, run against the suite as it
stood first: the newest-item ordering reversed, the "carries an id" filter
dropped, Plex given the file's old path, and Radarr dispatched through
Sonarr's calls all survived it and are killed by the tests at the end.
Two were already killed through the shared helpers: the path left
untranslated, and the Analyze queued with its setting off. The route side
is in test_revert_routes.

Webhook IDs reaching the job, at the end of this file, came later still.
The loaders could only notify a job that carried an ID, and two scanner
branches dropped the webhook's before any job did: the one that finds a
job already pending or processing, and the one that records a skip. Ten
mutations, each run against the whole 1699-test suite before these tests
existed, all ten survived. Six took the ID away: from the waiting job, from
a new skipped row, and from an existing skipped row, each once for Sonarr
and once for Radarr. All six are killed, each only by the tests for its own
service and branch. The other four removed an "is not None" guard from each
of the four assignments, and survived these tests as well, including the
one that scans a row holding an ID with no ID in hand: newest_arr_ids has
already resolved a missing ID from the file's own queue items, of which the
row being updated is one. The guards were deleted rather than kept, so
those four are retired, not survivors. Four of the six were re-aimed at the
unguarded lines afterwards and re-run against the final code; that run
shows the code is pinned now, and the baseline above is what shows it was
not before.

Files that carry no ID at all, at the very end, came last: a file a scan
found was never announced, and is now looked up by its folder. Ten
mutations, each run against the whole 2061-test suite before these tests
existed. Nine survived: the job's lookup not dispatched and the revert's
not dispatched (one each, since they are separate call sites), the path
left untranslated, a look-alike folder taken for the file's own, Radarr
never asked, the ID not stored for a job and not stored for a revert
(separate lines again), a lookup made for a job that already carries an
ID, and an entry with no folder matching every file. Eight are killed
here; the job's dispatch is killed in test_post_job_broadcast.py. The
tenth, the enabled check removed, was already killed by
test_a_service_switched_off_is_not_notified, since the lookup shares that
check with the notifications, and is not claimed.
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
        "entity_id":   11,
        "url":         "http://sonarr:8989",
        "api_key":     "SONARR-KEY",
        "output_path": "/media/Show.mkv",
    }
    assert data["radarr"] == {
        "entity_id":   22,
        "url":         "http://radarr:7878",
        "api_key":     "RADARR-KEY",
        "output_path": "/media/Show.mkv",
    }


def test_the_output_path_is_translated_into_the_service_view(db):
    """
    The *arr reports its own view of the library, which is not Remuxarr's
    when the two containers mount it differently. The restore compares the
    path it was given against the record the service holds, so an
    untranslated path matches nothing, polls for its whole deadline and
    gives up — which is what happened on a real setup where Radarr saw
    /media and Remuxarr saw /media/movies.

    Closes: the translation dropped, and the prefixes passed the wrong way
    round. The webhook translates remote to local on the way in; this is
    the same function on the way back out, so the reversal produces a path
    that is wrong in a plausible-looking direction.
    """
    settings(db, **(ARR_ON | {
        "radarr_path_prefix_local":  "/media/movies",
        "radarr_path_prefix_remote": "/media",
    }))
    job(db, 1, radarr_movie_id=22,
        output_path="/media/movies/Toy Story 5 (2026)/Toy Story 5 (2026).mp4")

    data = worker._load_post_job_data(1)

    assert data["radarr"]["output_path"] == \
        "/media/Toy Story 5 (2026)/Toy Story 5 (2026).mp4"


def test_each_service_translates_with_its_own_prefixes(db):
    """
    Closes: one service's prefix settings used for the other. Both are read
    from the same config dict two lines apart, which is the shape that
    produces a copy-paste defect, and the result is a path that translates
    cleanly to somewhere that does not exist.
    """
    settings(db, **(ARR_ON | {
        "sonarr_path_prefix_local":  "/media/tv",
        "sonarr_path_prefix_remote": "/tv",
        "radarr_path_prefix_local":  "/media/movies",
        "radarr_path_prefix_remote": "/movies",
    }))
    media(db, 1, "/media/tv/Show/ep.mkv")
    db.add(QueueItem(id=1, file_id=1, status="success",
                     sonarr_series_id=11,
                     output_path="/media/tv/Show/ep.mp4"))
    db.add(QueueItem(id=2, file_id=1, status="success",
                     radarr_movie_id=22,
                     output_path="/media/movies/Film/film.mp4"))
    db.commit()

    assert worker._load_post_job_data(1)["sonarr"]["output_path"] == \
        "/tv/Show/ep.mp4"
    assert worker._load_post_job_data(2)["radarr"]["output_path"] == \
        "/movies/Film/film.mp4"


def test_an_unconfigured_prefix_leaves_the_path_alone(db):
    """
    Both prefixes blank is the ordinary case: one container, or two that
    already agree. The path must go through untouched rather than being
    mangled by a half-configured pair.
    """
    settings(db, **ARR_ON)
    job(db, 1, radarr_movie_id=22, output_path="/media/Film/film.mp4")

    assert worker._load_post_job_data(1)["radarr"]["output_path"] == \
        "/media/Film/film.mp4"


def test_a_service_switched_off_is_not_notified(db):
    settings(db, **(ARR_ON | {"sonarr_enabled": False}))
    job(db, 1, sonarr_series_id=11, radarr_movie_id=22)

    data = worker._load_post_job_data(1)

    assert data["sonarr"] is None
    assert data["radarr"] is not None


def test_a_job_carrying_no_arr_id_is_not_notified(db):
    """
    The ids come from a webhook, or from a lookup by folder made after the
    job (_find_then_notify), never from here. A job with neither gets no
    target, since notifying on one would send an id of None to an *arr that
    has no idea what it refers to.
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
    assert [(r.file_id, r.expected_audio_languages) for r in backlog_rows(db)] == [(1, None)]


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


def _audio_tracks(db, *languages):
    from app.database.models import Track

    for n, language in enumerate(languages, start=1):
        db.add(Track(file_id=1, stream_index=n, track_type="audio", codec="aac",
                     language=language))
    db.add(Track(file_id=1, stream_index=9, track_type="subtitle", codec="subrip",
                 language="fre"))
    db.commit()


def test_the_backlog_records_every_audio_language_the_job_left(db):
    """
    The drain loop uses this to check whether Plex's own maintenance
    already applied the tags, and skips the Analyze call when it did.
    Losing it turns every language fix into an unconditional Analyze. Every
    audio track, in order, from the file the job wrote: one language
    passed whenever any track had it.
    """
    settings(db, **(PLEX_ON | {"plex_analyze_backlog_enabled": True}))
    job(db, 1, is_new_file=False)
    # As _finish_job left them: the second track retagged English, the
    # third still undefined.
    _audio_tracks(db, "eng", "eng", None)
    db.add(PlannedAction(queue_item_id=1, order=0, action_type="copy_track",
                         description="Fix undefined language tag", track_type="audio",
                         stream_index=2, target_language="eng"))
    db.commit()

    worker._load_plex_notify_data(1)

    assert json.loads(backlog_rows(db)[0].expected_audio_languages) == ["eng", "eng", "und"]


def test_a_subtitle_language_fix_records_no_audio_check(db):
    """A subtitle's language used to stand in for the audio's."""
    settings(db, **(PLEX_ON | {"plex_analyze_backlog_enabled": True}))
    job(db, 1, is_new_file=False)
    _audio_tracks(db, "eng")
    db.add(PlannedAction(queue_item_id=1, order=0, action_type="copy_track",
                         description="Fix subtitle language", track_type="subtitle",
                         stream_index=9, target_language="fre"))
    db.commit()

    worker._load_plex_notify_data(1)

    assert backlog_rows(db)[0].expected_audio_languages is None


def test_the_backlog_records_no_language_when_the_job_set_none(db):
    settings(db, **(PLEX_ON | {"plex_analyze_backlog_enabled": True}))
    job(db, 1, is_new_file=False)
    db.add(PlannedAction(queue_item_id=1, order=0, action_type="strip_subs",
                         description="Remove subtitles"))
    db.commit()

    worker._load_plex_notify_data(1)

    assert backlog_rows(db)[0].expected_audio_languages is None


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


def test_a_forge_job_queues_the_backlog_with_no_audio_check(db):
    """
    Forge changes the codec layout, never a language tag, so there is
    nothing for the drain loop to verify against — a language here would
    make it skip an Analyze the forge specifically needs.
    """
    settings(db, **(PLEX_ON | {"plex_analyze_backlog_enabled": True}))
    forge_job(db, 1)

    worker._load_forge_plex_notify_data(1)

    assert [(r.file_id, r.expected_audio_languages) for r in backlog_rows(db)] == [(1, None)]


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
        data, loop, lambda *a: calls.append(a), lambda *a: None, "Sonarr"))

    assert calls == [("http://sonarr:8989", "K", 11)]


def test_a_failing_arr_notifier_never_escapes_the_trigger(db):
    """
    These are fire-and-forget tasks with nothing awaiting them. An escaping
    exception is not reported anywhere useful, it just kills the task.
    """
    def _boom(*_):
        raise RuntimeError("sonarr is down")

    run(lambda loop: worker._trigger_arr_notify(
        {"url": "u", "api_key": "k", "entity_id": 1}, loop, _boom,
        lambda *a: None, "Sonarr"))


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


# ── After a revert ───────────────────────────────────────────────────────────
#
# A revert changes the tracks and often the extension, so the services hear
# about it as they do after a job: _load_revert_notify_data decides who, and
# notify_after_revert dispatches through the same triggers.

def test_a_revert_takes_each_id_from_the_newest_queue_item_carrying_it(db):
    """
    A file processed twice has two queue items, and the newer one is the
    service's current view of it — a series re-added in Sonarr gets a new
    id. The older one is history.
    """
    settings(db, **ARR_ON)
    job(db, 1, sonarr_series_id=11, radarr_movie_id=21)
    job(db, 2, sonarr_series_id=12, radarr_movie_id=22, with_media=False)

    data = worker._load_revert_notify_data(1, "/media/Show.mkv")

    assert data["sonarr"]["entity_id"] == 12
    assert data["radarr"]["entity_id"] == 22


def test_a_newer_queue_item_without_an_id_does_not_hide_an_older_one(db):
    """A later scan-queued job carries no id; the webhook's earlier one still counts."""
    settings(db, **ARR_ON)
    job(db, 1, radarr_movie_id=21)
    job(db, 2, with_media=False)

    data = worker._load_revert_notify_data(1, "/media/Show.mkv")

    assert data["radarr"]["entity_id"] == 21


def test_a_revert_announces_the_restored_path(db):
    """
    Converted to MP4, reverted to MKV: the services have to look for the
    MKV. Sonarr and Radarr get it translated into their own view; Plex gets
    it as Remuxarr sees it, and maps it itself.
    """
    settings(db, **ARR_ON, **PLEX_ON, sonarr_path_prefix_local="/media",
             sonarr_path_prefix_remote="/tv")
    media(db, 1, "/media/Show.mp4")
    job(db, 1, with_media=False, sonarr_series_id=11)

    data = worker._load_revert_notify_data(1, "/media/Show.mkv")

    assert data["sonarr"]["output_path"] == "/tv/Show.mkv"
    assert data["plex"]["local_path"] == "/media/Show.mkv"


def test_a_file_that_never_came_from_a_webhook_only_reaches_plex(db):
    settings(db, **ARR_ON, **PLEX_ON)
    job(db, 1)

    data = worker._load_revert_notify_data(1, "/media/Show.mkv")

    assert data["sonarr"] is None and data["radarr"] is None
    assert data["plex"] is not None


def test_after_a_revert_each_service_gets_its_own_notifier(db, monkeypatch):
    """The copy-paste guard again, at the dispatch: Radarr through Radarr's calls."""
    calls = []

    async def arr(data, _loop, notify_fn, restore_fn, name):
        calls.append((name, data["entity_id"], notify_fn, restore_fn))

    async def plex(data, _loop):
        calls.append(("Plex", data["local_path"]))

    monkeypatch.setattr(worker, "_trigger_arr_notify", arr)
    monkeypatch.setattr(worker, "_trigger_plex_notify", plex)
    monkeypatch.setattr(worker, "_load_revert_notify_data", lambda _f, _p: {
        "sonarr": {"entity_id": 11}, "radarr": {"entity_id": 22},
        "lookup": [], "plex": {"local_path": "/media/Show.mkv"},
    })

    async def driver():
        await worker.notify_after_revert(1, "/media/Show.mkv")
        await asyncio.sleep(0)

    asyncio.run(driver())

    assert sorted(calls, key=lambda c: c[0]) == [
        ("Plex", "/media/Show.mkv"),
        ("Radarr", 22, worker.notify_radarr, worker.restore_movie_quality),
        ("Sonarr", 11, worker.notify_sonarr, worker.restore_episode_quality),
    ]


# ── Webhook IDs reaching the job ─────────────────────────────────────────────
#
# The loaders above can only notify a job that carries an ID, and the ID
# arrives with a webhook. Two ways it used to be dropped before any job
# carried it: a webhook finding the file already queued (a scan got there
# first) left the waiting job without it, and a webhook whose file needed
# nothing recorded the skip without it, so a later job for the same file had
# nothing to inherit. Driven through the real _process_file, via
# queue_single_file, with only the probe faked.

SERVICES = [("sonarr", "sonarr_series_id"), ("radarr", "radarr_movie_id")]


def _probe_mkv():
    """An MKV that needs only a container change under the defaults."""
    return {"format": {"format_name": "matroska,webm", "duration": "1.0"},
            "streams": [
                {"index": 0, "codec_type": "video", "codec_name": "h264"},
                {"index": 1, "codec_type": "audio", "codec_name": "aac",
                 "channels": 2, "tags": {"language": "eng"},
                 "disposition": {"default": 1}},
            ]}


@pytest.fixture
def episode(db, tmp_path, monkeypatch):
    from app.core import scanner

    monkeypatch.setattr(scanner, "probe_file", lambda *_a, **_kw: _probe_mkv())
    settings(db, **ARR_ON)
    path = tmp_path / "Show - S01E01.mkv"
    path.write_bytes(b"\0" * 64)
    return str(path)


def _arrives(db, path, **ids):
    """
    A webhook when given an ID, a full scan's per-file pass when not: both
    are queue_single_file, which probes whatever the stored size and mtime.

    Run on an emptied session, closed before and after, because each
    webhook gets a session of its own in production (webhooks._queue_sync).
    Arriving on a session that still holds rows the test loaded is not a
    faithful stand-in: queueing bulk-deletes the skipped row, SQLite can
    hand its id to the new job, and the held row then makes SQLAlchemy warn
    about a clashing identity. Confirmed against the scanner both before
    and after this change: the warning needs the held row in the same
    session, and does not occur with a session per arrival.
    """
    from app.core.scanner import queue_single_file

    db.close()
    queue_single_file(db, path, **ids)
    db.close()


def _rows(db):
    return db.query(QueueItem).order_by(QueueItem.id).all()


def _notified_entity(db, job_id, service):
    target = worker._load_post_job_data(job_id)[service]
    return target["entity_id"] if target else None


@pytest.mark.parametrize("status", ["pending", "processing"])
@pytest.mark.parametrize("service, column", SERVICES)
def test_a_webhook_gives_its_id_to_the_job_already_queued(
        db, episode, service, column, status):
    """
    A scan queued the file first, with no ID; the webhook then finds the
    job waiting, or already running, and queues nothing of its own. The ID
    must land on that job, which is the one the post-job loader reads.
    """
    _arrives(db, episode)
    [job] = _rows(db)
    assert job.status == "pending"
    job.status = status
    db.commit()

    _arrives(db, episode, **{column: 42})

    [job] = _rows(db)
    assert getattr(job, column) == 42
    assert _notified_entity(db, job.id, service) == 42


@pytest.mark.parametrize("service, column", SERVICES)
def test_a_skipped_file_keeps_the_webhook_id_for_a_later_job(
        db, episode, service, column):
    """
    Imported when it needed nothing, so the webhook's only record is the
    skip. A later setting makes it need a conversion, and the job a full
    scan queues for it must still tell the service the file has moved.
    """
    settings(db, prefer_mp4_container=False)
    _arrives(db, episode, **{column: 42})
    [skip] = _rows(db)
    assert skip.status == "skipped"
    assert getattr(skip, column) == 42

    settings(db, prefer_mp4_container=True)
    _arrives(db, episode)

    [job] = _rows(db)
    assert job.status == "pending"
    assert getattr(job, column) == 42
    assert _notified_entity(db, job.id, service) == 42


@pytest.mark.parametrize("service, column", SERVICES)
def test_a_skip_already_recorded_takes_the_webhook_id(db, episode, service, column):
    """A scan recorded the skip first; the webhook updates that same row."""
    settings(db, prefer_mp4_container=False)
    _arrives(db, episode)
    _arrives(db, episode, **{column: 42})

    [skip] = _rows(db)
    assert skip.status == "skipped"
    assert getattr(skip, column) == 42


@pytest.mark.parametrize("prefer_mp4, status", [(True, "pending"), (False, "skipped")])
@pytest.mark.parametrize("service, column", SERVICES)
def test_a_later_scan_never_clears_an_id(db, episode, service, column,
                                         prefer_mp4, status):
    """A scan brings no ID, and that is not the service saying there is none."""
    settings(db, prefer_mp4_container=prefer_mp4)
    _arrives(db, episode, **{column: 42})
    _arrives(db, episode)

    [row] = _rows(db)
    assert row.status == status
    assert getattr(row, column) == 42


# ── Files that carry no Sonarr or Radarr ID ──────────────────────────────────
#
# The IDs came only from each service's webhook, so a file a scan found was
# never announced to Sonarr or Radarr. Reproduced against a real server
# with a fake Sonarr recording every request: a scanned file converted from
# MKV to MP4 sent nothing, where a webhook file got RescanSeries. It is now
# looked up by the folder it sits in, and the ID found is stored so the
# file's later jobs and reverts reuse it.

TRANSLATED = ARR_ON | {
    "sonarr_path_prefix_local": "/media", "sonarr_path_prefix_remote": "/tv",
    "radarr_path_prefix_local": "/media", "radarr_path_prefix_remote": "/movies",
}


def test_a_job_with_no_id_asks_sonarr_then_radarr(db):
    """
    Each candidate carries its own service's connection, with the path
    already in that service's terms, since the folders it lists are.
    """
    settings(db, **TRANSLATED)
    job(db, 1, output_path="/media/Show/ep.mp4")

    lookup = worker._load_post_job_data(1)["lookup"]

    assert lookup == [
        {"service": "sonarr", "url": "http://sonarr:8989", "api_key": "SONARR-KEY",
         "output_path": "/tv/Show/ep.mp4"},
        {"service": "radarr", "url": "http://radarr:7878", "api_key": "RADARR-KEY",
         "output_path": "/movies/Show/ep.mp4"},
    ]


def test_a_job_carrying_an_id_looks_nothing_up(db):
    """The webhook already said which service it belongs to."""
    settings(db, **ARR_ON)
    job(db, 1, sonarr_series_id=11)

    assert worker._load_post_job_data(1)["lookup"] == []


def test_a_service_switched_off_is_not_asked(db):
    settings(db, **(ARR_ON | {"sonarr_enabled": False}))
    job(db, 1)

    assert [c["service"] for c in worker._load_post_job_data(1)["lookup"]] == ["radarr"]


@pytest.fixture
def listing(monkeypatch):
    """find_arr_id's request, answered from a fixed list per resource."""
    import app.core.arr_lookup as arr_lookup

    state = {"lists": {}, "asked": []}

    def fake_get(base_url, api_key, path, params=None):
        state["asked"].append((base_url, api_key, path))
        return state["lists"].get(path, [])

    monkeypatch.setattr(arr_lookup, "arr_get", fake_get)
    return state


def test_the_series_whose_folder_holds_the_file_is_found(listing):
    from app.core.arr_lookup import find_arr_id

    listing["lists"]["/api/v3/series"] = [
        {"id": 3, "path": "/tv/Other Show"},
        {"id": 7, "path": "/tv/Show/"},
    ]

    assert find_arr_id("sonarr", "http://s", "K", "/tv/Show/Season 1/ep.mkv") == 7
    assert listing["asked"] == [("http://s", "K", "/api/v3/series")]


def test_a_movie_is_found_in_radarrs_list(listing):
    from app.core.arr_lookup import find_arr_id

    listing["lists"]["/api/v3/movie"] = [{"id": 22, "path": "/movies/Film (2020)"}]

    assert find_arr_id("radarr", "http://r", "K", "/movies/Film (2020)/Film.mp4") == 22


def test_a_folder_that_only_starts_the_same_is_not_a_match(listing):
    """
    /tv/Show 2 is another series. Taking it for /tv/Show would rescan the
    wrong series, and the quality restore would then look for the file in
    a series that does not hold it.
    """
    from app.core.arr_lookup import find_arr_id

    listing["lists"]["/api/v3/series"] = [{"id": 7, "path": "/tv/Show"}]

    assert find_arr_id("sonarr", "http://s", "K", "/tv/Show 2/ep.mkv") is None


def test_an_entry_with_no_folder_matches_nothing(listing):
    """An empty prefix is the root to the matcher, which every path is under."""
    from app.core.arr_lookup import find_arr_id

    listing["lists"]["/api/v3/series"] = [
        {"id": 9, "path": ""},
        {"id": 7, "path": "/tv/Show"},
    ]

    assert find_arr_id("sonarr", "http://s", "K", "/tv/Show/ep.mkv") == 7


SONARR_CANDIDATE = {"service": "sonarr", "url": "http://sonarr:8989",
                    "api_key": "S", "output_path": "/tv/Film (2020)/Film.mp4"}
RADARR_CANDIDATE = {"service": "radarr", "url": "http://radarr:7878",
                    "api_key": "R", "output_path": "/movies/Film (2020)/Film.mp4"}


def _drive_find(monkeypatch, answers):
    """
    _find_then_notify with the lookup answered per service (an ID, None, or
    an exception to raise) and the trigger and the store recorded.
    """
    asked, notified, stored = [], [], []

    def fake_find(service, url, api_key, path):
        asked.append((service, path))
        answer = answers[service]
        if isinstance(answer, Exception):
            raise answer
        return answer

    async def fake_trigger(data, _loop, notify_fn, restore_fn, name):
        notified.append((name, data, notify_fn, restore_fn))

    monkeypatch.setattr(worker, "find_arr_id", fake_find)
    monkeypatch.setattr(worker, "_trigger_arr_notify", fake_trigger)
    run(lambda loop: worker._find_then_notify(
        [SONARR_CANDIDATE, RADARR_CANDIDATE], loop,
        lambda service, entity_id: stored.append((service, entity_id)), "job 1"))
    return asked, notified, stored


def test_a_scanned_movie_falls_through_to_radarr(monkeypatch):
    asked, notified, stored = _drive_find(monkeypatch, {"sonarr": None, "radarr": 22})

    assert [service for service, _ in asked] == ["sonarr", "radarr"]
    assert stored == [("radarr", 22)]
    assert notified == [("Radarr",
                         {"entity_id": 22, "url": "http://radarr:7878", "api_key": "R",
                          "output_path": "/movies/Film (2020)/Film.mp4"},
                         worker.notify_radarr, worker.restore_movie_quality)]


def test_a_file_sonarr_holds_is_not_looked_up_in_radarr(monkeypatch):
    asked, notified, stored = _drive_find(monkeypatch, {"sonarr": 7, "radarr": 22})

    assert [service for service, _ in asked] == ["sonarr"]
    assert stored == [("sonarr", 7)]
    assert [n[0] for n in notified] == ["Sonarr"]


def test_a_failed_sonarr_lookup_still_asks_radarr(monkeypatch):
    """A Sonarr that is down costs Sonarr's notification, not Radarr's."""
    asked, notified, _ = _drive_find(
        monkeypatch, {"sonarr": OSError("connection refused"), "radarr": 22})

    assert [service for service, _ in asked] == ["sonarr", "radarr"]
    assert [n[0] for n in notified] == ["Radarr"]


def test_a_file_neither_service_holds_tells_neither(monkeypatch):
    _, notified, stored = _drive_find(monkeypatch, {"sonarr": None, "radarr": None})

    assert notified == [] and stored == []


def test_the_found_id_is_kept_on_the_job_for_the_files_later_jobs(db):
    """
    Stored on the job's row, newest_arr_ids gives it to the next job a scan
    queues for the file and to a revert, so the lookup is made once.
    """
    from app.core.scanner import newest_arr_ids

    job(db, 1)

    worker._remember_arr_id_on_job(1, "sonarr", 7)

    assert newest_arr_ids(db, 1) == (7, None)


def test_a_revert_keeps_the_found_id_on_the_files_newest_item(db):
    from app.core.scanner import newest_arr_ids

    job(db, 1)
    job(db, 2, with_media=False)

    worker._remember_arr_id_on_file(1, "radarr", 22)

    assert db.get(QueueItem, 2).radarr_movie_id == 22
    assert db.get(QueueItem, 1).radarr_movie_id is None
    assert newest_arr_ids(db, 1) == (None, 22)


def test_a_revert_of_a_file_with_no_id_is_looked_up(db, monkeypatch):
    """The revert's dispatcher hands the lookup on, storing on the file."""
    found = []

    async def fake_find(candidates, _loop, remember, context):
        found.append((candidates, remember, context))

    monkeypatch.setattr(worker, "_find_then_notify", fake_find)
    monkeypatch.setattr(worker, "_load_revert_notify_data", lambda _f, _p: {
        "sonarr": None, "radarr": None, "lookup": [SONARR_CANDIDATE], "plex": None,
    })

    async def driver():
        await worker.notify_after_revert(5, "/media/Show.mkv")
        await asyncio.sleep(0)

    asyncio.run(driver())

    assert len(found) == 1
    candidates, remember, context = found[0]
    assert candidates == [SONARR_CANDIDATE]
    assert remember.func is worker._remember_arr_id_on_file
    assert remember.args == (5,)
