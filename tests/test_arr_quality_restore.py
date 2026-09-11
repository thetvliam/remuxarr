"""
Putting a file's quality back after Remuxarr replaces it.

Sonarr and Radarr re-parse quality from the filename when a rescan finds a
file they have not seen. A container change therefore turns a WEBDL-1080p
download into HDTV-1080p, and the recorded Radarr case had
qualityCutoffNotMet flip to true on the new file — meaning it was queued to
be replaced by a fresh download that would overwrite the remux. This is the
mechanism that stops that.

THE FIXTURES ARE REAL
---------------------
HISTORY below is the history of one movie that this actually happened to,
taken from a live Radarr. It is awkward in ways an invented fixture would
not have been, which is why it is used rather than something tidier:

  * two movieFileDeleted records, both NEWER than the import that matters,
    both carrying a quality field of their own
  * a grabbed record, which is the release's claim at grab time rather than
    what was imported
  * an import from three weeks earlier whose importedPath is the same path
    the file ends up at again — the movie was an MP4, was upgraded to an
    MKV, and was remuxed back to MP4 at the original path

That last one is why selection is by event type and date and never by path.
Matching on path picks the August release. In the real data both happened
to be WEBRip-1080p, so it would have looked like it worked.

One thing is changed from the real records: each one is given a different
quality name. The live data had WEBRip-1080p on nearly all of them, which
means every wrong selection would still have produced the right answer and
the tests would have proved nothing.

Mutants, all confirmed surviving the suite before this file existed:

  selection   the eventType filter dropped                    killed
              the sort reversed to oldest-first               killed
              the newest import not checked against the file  killed
              Sonarr's episode scoping dropped                killed
  the write   the whole quality object not sent               killed
              the write aimed at the entity, not the file     killed
  polling     matched on basename instead of full path        killed
              the deadline made unbounded                     killed
  the wiring  the restore run before the rescan is queued     killed
              a job with no output path still restored        killed

One more group, from merging the two post-job wrappers into
restore_quality_best_effort, all run against the full suite before their
tests existed. The HTTP branch and the body in its log line were already
killed by test_an_http_failure_logs_what_the_service_said; the other
twelve survived:

  the logging the unreadable-body fallback text changed       killed
              the body no longer truncated                    killed
              an unreadable body not caught                   killed
              any other failure escaping                      killed
              the path dropped from either line               killed
              a delegation passing the other service          killed
              either service's noun wrong                     killed
              either line logged under arr_quality's name     killed

WHAT DECIDES THAT THERE IS NOTHING TO DO
----------------------------------------
Not a filename comparison — see the module docstring in arr_quality.py.
If the newest import describes the file that is live, nothing replaced it.
test_a_file_that_is_still_the_one_imported_is_left_alone is that condition,
and it is the reason this runs after every arr-notified job rather than
only after a container change.

Run from the project root:
    pytest tests/test_arr_quality_restore.py -v
"""
import asyncio
import threading

import pytest

from app.core.arr_quality import RADARR, SONARR, restore_quality

MOVIE_ID = 1522
NEW_FILE = 2071
PATH     = "/media/Toy Story 5 (2026)/Toy Story 5 (2026).mp4"


def _quality(name):
    return {
        "quality":  {"id": 15, "name": name, "source": "webrip",
                     "resolution": 1080, "modifier": "none"},
        "revision": {"version": 1, "real": 0, "isRepack": False},
    }


# The live records, with the qualities made distinguishable. Newest first,
# as Radarr returned them.
HISTORY = [
    {"id": 1758, "movieId": MOVIE_ID, "eventType": "movieFileDeleted",
     "date": "2026-09-07T21:47:21Z", "quality": _quality("Bluray-1080p"),
     "sourceTitle": "/media/Toy Story 5 (2026)/Toy Story 5 (2026).mkv",
     "data": {"reason": "MissingFromDisk", "releaseGroup": "Lootera"}},

    {"id": 1757, "movieId": MOVIE_ID, "eventType": "downloadFolderImported",
     "date": "2026-09-07T21:45:00Z", "quality": _quality("WEBRip-1080p"),
     "sourceTitle": "Toy Story 5 2026 1080p WebRip Opus 5 1 x265-Lootera",
     "data": {"fileId": "2070", "releaseGroup": "Lootera",
              "importedPath": "/media/Toy Story 5 (2026)/Toy Story 5 (2026).mkv"}},

    {"id": 1756, "movieId": MOVIE_ID, "eventType": "movieFileDeleted",
     "date": "2026-09-07T21:43:47Z", "quality": _quality("DVD"),
     "sourceTitle": PATH, "data": {"reason": "Upgrade", "releaseGroup": "YTS"}},

    {"id": 1340, "movieId": MOVIE_ID, "eventType": "downloadFolderImported",
     "date": "2026-08-17T23:41:18Z", "quality": _quality("HDTV-1080p"),
     "sourceTitle": "Toy Story 5 (2026) [1080p] [WEBRip] [5.1] [YTS.GG]",
     "data": {"fileId": "1931", "releaseGroup": "YTS",
              "importedPath": PATH}},

    {"id": 1339, "movieId": MOVIE_ID, "eventType": "grabbed",
     "date": "2026-08-17T23:34:06Z", "quality": _quality("Remux-1080p"),
     "sourceTitle": "Toy Story 5 (2026) 1080p WEBRip 5.1 x265 -YTS",
     "data": {"releaseGroup": "YTS"}},
]


@pytest.fixture
def arr(monkeypatch):
    """
    Stand in for the HTTP client, not for the flow under test.

    arr_get and arr_put are replaced on arr_quality because it imports them
    by name. Everything above them — which record is chosen, what is sent,
    when the polling stops — is the real code.
    """
    import app.core.arr_quality as q

    state = {
        "files":    [{"id": NEW_FILE, "path": PATH}],
        "history":  list(HISTORY),
        "episodes": [],
        "gets":     [],
        "puts":     [],
    }

    def _get(base_url, api_key, path, params=None):
        state["gets"].append({"path": path, "params": params})
        if path.endswith("/episode"):
            return state["episodes"]
        if "/history/" in path:
            return state["history"]
        return state["files"]

    def _put(base_url, api_key, path, body):
        state["puts"].append({"path": path, "body": body})
        return {}

    monkeypatch.setattr(q, "arr_get", _get)
    monkeypatch.setattr(q, "arr_put", _put)
    return state


def _restore(service=RADARR, entity=MOVIE_ID, path=PATH, timeout=5.0, **kw):
    """
    Call restore_quality on a worker thread, with a join.

    Its polling loop is bounded by a deadline in the code under test, and
    the mutant that removes that deadline does not fail a direct call — it
    never returns from one, so any test whose file record never appears
    hangs the run instead of failing it. Bounding every call from outside
    keeps that a failure. The thread's exception is carried back rather
    than raised where nobody is watching.
    """
    kw.setdefault("deadline", 0.05)
    kw.setdefault("interval", 0.01)
    outcome = []

    def _call():
        try:
            outcome.append(("ok", restore_quality(
                service, "http://radarr:7878", "key", entity, path, **kw)))
        except BaseException as exc:          # carried to the caller below
            outcome.append(("raised", exc))

    caller = threading.Thread(target=_call, daemon=True)
    caller.start()
    caller.join(timeout)

    assert outcome, f"restore_quality did not return within {timeout}s"
    kind, value = outcome[0]
    if kind == "raised":
        raise value
    return value


def _written(state):
    return state["puts"][0]["body"]["quality"]["quality"]["name"]


# ── Choosing the record ───────────────────────────────────────────────────────

def test_the_quality_of_the_import_that_was_replaced_is_written_back(arr):
    """The whole point: WEBRip-1080p goes back on the file now on disk."""
    assert _restore() is True
    assert _written(arr) == "WEBRip-1080p"


def test_a_deletion_record_is_not_read_as_an_import(arr):
    """
    Closes: the eventType filter dropped.

    Both deletions in this history are newer than the import that matters
    and both carry a quality, so sorting by date without filtering reads
    one of them. In the live data they held the right value by coincidence.
    """
    _restore()

    assert _written(arr) != "Bluray-1080p", "read the deletion record"
    assert _written(arr) != "DVD"


def test_the_newest_import_is_used_not_the_first_one_ever(arr):
    """
    Closes: the sort reversed.

    The August import is a real record for a release replaced three weeks
    before the remux, and its importedPath is the path the file ends up at
    again — so it is exactly what a path-based match would have found too.
    """
    _restore()

    assert _written(arr) != "HDTV-1080p", "restored a three-week-old release"


def test_a_grabbed_record_is_not_used(arr):
    """
    A grab is what the indexer claimed, before anything was imported. The
    import record is what the file actually was.
    """
    _restore()

    assert _written(arr) != "Remux-1080p"


def test_a_file_that_is_still_the_one_imported_is_left_alone(arr):
    """
    Closes: the newest import not checked against the live file.

    This is also what makes running after every job safe rather than only
    after a container change. If the newest import names the file that is
    live, nothing replaced it.

    The obvious near-miss is to filter the live file out of the candidates
    and take the next newest instead. That passes a fixture with one
    import and fails on this one: underneath the current release sits the
    August import, and restoring that writes back a quality superseded
    three weeks ago. It is the mutant this test exists for.
    """
    arr["files"] = [{"id": 2070, "path": PATH}]

    assert _restore() is False
    assert arr["puts"] == []


def test_nothing_is_written_when_the_file_was_never_imported(arr):
    """
    A library pointed at existing files has no import records. There is
    nothing to restore and nothing to complain about.
    """
    arr["history"] = [r for r in HISTORY if r["eventType"] != "downloadFolderImported"]

    assert _restore() is False
    assert arr["puts"] == []


# ── Sonarr's extra hop ────────────────────────────────────────────────────────

def test_sonarr_reads_history_only_for_the_episodes_on_this_file(arr):
    """
    Closes: the episodeId scoping dropped.

    Sonarr scopes history by series, so every other episode's imports come
    back in the same response. Without the scoping, the newest import in
    the whole series wins — which for a series being actively downloaded is
    almost never the episode that was just remuxed.
    """
    arr["files"] = [{"id": 4001, "path": PATH}]
    arr["episodes"] = [
        {"id": 900, "episodeFileId": 4001},          # ours
        {"id": 901, "episodeFileId": 4002},          # a different file
    ]
    arr["history"] = [
        {"id": 2, "episodeId": 901, "eventType": "downloadFolderImported",
         "date": "2026-09-08T10:00:00Z", "quality": _quality("Bluray-2160p"),
         "data": {"fileId": "4002"}},
        {"id": 1, "episodeId": 900, "eventType": "downloadFolderImported",
         "date": "2026-09-07T21:45:00Z", "quality": _quality("WEBDL-1080p"),
         "data": {"fileId": "3999"}},
    ]

    assert _restore(service=SONARR, entity=288) is True
    assert _written(arr) == "WEBDL-1080p", "took another episode's import"


# ── The write ─────────────────────────────────────────────────────────────────

def test_the_write_names_the_new_file_and_sends_the_whole_quality_object(arr):
    """
    Closes: the wrong file id written, and the quality object rebuilt
    field by field instead of copied.

    The object goes verbatim because the two services do not agree on its
    shape — Radarr's carries a modifier key Sonarr's does not — and
    because revision is part of a valid QualityModel rather than an
    optional extra. Sending it whole means a field added in a future
    version passes through untouched.

    The editor endpoint rather than PUT on the file itself: Radarr answers
    500 to a quality-only body on /api/v3/moviefile/{id}, from a null
    dereference in SetMovieFile, because it deserialises into a full
    resource. Both services take this shape.
    """
    _restore()

    assert arr["puts"][0]["path"] == "/api/v3/moviefile/editor"
    assert arr["puts"][0]["body"] == {
        "movieFileIds": [NEW_FILE],
        "quality":      _quality("WEBRip-1080p"),
    }


def test_each_service_names_the_ids_field_its_own_api_expects(arr):
    """
    Closes: one service's ids field used for the other.

    episodeFileIds against Radarr is not an error it reports — the body
    parses, the ids field it wanted is absent, and the write silently
    affects nothing.
    """
    arr["files"] = [{"id": 4001, "path": PATH}]
    arr["episodes"] = [{"id": 900, "episodeFileId": 4001}]
    arr["history"] = [
        {"id": 1, "episodeId": 900, "eventType": "downloadFolderImported",
         "date": "2026-09-07T21:45:00Z", "quality": _quality("WEBDL-1080p"),
         "data": {"fileId": "3999"}},
    ]

    _restore(service=SONARR, entity=288)

    assert arr["puts"][0]["path"] == "/api/v3/episodefile/editor"
    assert arr["puts"][0]["body"]["episodeFileIds"] == [4001]


# ── Finding the file ──────────────────────────────────────────────────────────

def test_the_file_is_matched_on_its_whole_path(arr):
    """
    Closes: matching on the basename.

    Season folders make the same episode filename appear more than once
    under a series, so a basename match can settle on a file in another
    season and write the wrong episode's quality.
    """
    arr["files"] = [
        {"id": 7001, "path": "/media/Other (2019)/Toy Story 5 (2026).mp4"},
    ]

    assert _restore() is False
    assert arr["puts"] == []


def test_the_poll_gives_up_at_the_deadline(arr):
    """
    Closes: the deadline removed.

    The file record only appears once the *arr finishes the rescan it
    queued, so this has to wait — but an unbounded wait against a service
    that is down is a thread parked for the life of the process.

    Every call in this module goes through a join for this mutant's sake
    — see _restore. Without it the loop polls and sleeps forever, and a
    hanging test reports nothing about itself.
    """
    arr["files"] = []

    assert _restore() is False
    assert len(arr["gets"]) > 1, "gave up without retrying"


# ── The wiring ────────────────────────────────────────────────────────────────

def test_the_rescan_is_queued_before_the_restore_looks_for_it():
    """
    Closes: the restore running before or instead of the notification.

    The restore polls for a file record that only exists once the rescan
    has run. Starting it first spends the whole deadline waiting for work
    that has not been requested yet, and then gives up.
    """
    from app.core import worker

    order = []

    def _notify(*_):
        order.append("rescan")

    def _restore_fn(*_):
        order.append("restore")

    loop = asyncio.new_event_loop()
    try:
        loop.run_until_complete(worker._trigger_arr_notify(
            {"url": "u", "api_key": "k", "entity_id": 1,
             "output_path": "/media/Show.mp4"},
            loop, _notify, _restore_fn, "Sonarr",
        ))
    finally:
        loop.close()

    assert order == ["rescan", "restore"]


def test_a_job_with_no_output_path_is_not_restored():
    """
    Closes: the output_path guard dropped. There is no file to look for,
    and the restore would poll for a path of None until its deadline.
    """
    from app.core import worker

    calls = []

    loop = asyncio.new_event_loop()
    try:
        loop.run_until_complete(worker._trigger_arr_notify(
            {"url": "u", "api_key": "k", "entity_id": 1, "output_path": None},
            loop, lambda *_: None, lambda *a: calls.append(a), "Sonarr",
        ))
    finally:
        loop.close()

    assert calls == []


# ── Failure reporting ─────────────────────────────────────────────────────────

def test_an_http_failure_logs_what_the_service_said(monkeypatch, caplog):
    """
    Closes: the response body dropped from the log line.

    Radarr answers a bad write with 500 and puts the exception and the
    controller line that threw it in the body. Code and reason alone read
    "HTTP 500 Internal Server Error", which is indistinguishable from the
    service being broken and cost a round trip to diagnose once already.

    The file is on disk and correct at this point, so the write is
    best-effort and must not fail the job — which makes the log the only
    place this is ever reported.
    """
    import io
    import urllib.error

    from app.core import arr_quality, radarr

    def _explode(*_a, **_kw):
        raise urllib.error.HTTPError(
            "http://radarr:7878/api/v3/moviefile/editor", 500,
            "Internal Server Error", {},
            io.BytesIO(b'{"message": "Nullable object must have a value."}'),
        )

    monkeypatch.setattr(arr_quality, "restore_quality", _explode)

    with caplog.at_level("ERROR"):
        radarr.restore_movie_quality("http://radarr:7878", "k", 1531, PATH)

    assert "Nullable object must have a value" in caplog.text
    assert "1531" in caplog.text


# ── The post-job wrappers ─────────────────────────────────────────────────────
#
# sonarr.restore_episode_quality and radarr.restore_movie_quality were two
# copies of the same error handling, and only Radarr's HTTP branch ever ran
# in a test. They now delegate to arr_quality.restore_quality_best_effort,
# passing their own logger so every line keeps the module name it had.

def _http_error(body=b"Nullable object must have a value.", unreadable=False):
    import io
    import urllib.error

    class _Unreadable(urllib.error.HTTPError):
        def read(self, *_a, **_kw):
            raise OSError("connection reset while reading the body")

    cls = _Unreadable if unreadable else urllib.error.HTTPError
    return cls("http://arr/api/v3/editor", 500, "Internal Server Error", {},
               io.BytesIO(body))


@pytest.mark.parametrize("module, wrapper, service, name, noun", [
    ("sonarr", "restore_episode_quality", "SONARR", "Sonarr", "series"),
    ("radarr", "restore_movie_quality",   "RADARR", "Radarr", "movie"),
])
def test_each_service_logs_a_failed_write_as_it_always_has(
        monkeypatch, caplog, module, wrapper, service, name, noun):
    """
    Closes: a delegation passing the other service, a wrong noun, and the
    line moving to arr_quality's logger.

    The exact line each module wrote before the merge, under that module's
    name, which is what the log view shows beside it.
    """
    import importlib

    from app.core import arr_quality

    seen = []

    def _refuse(svc, *_a, **_kw):
        seen.append(svc)
        raise _http_error()

    monkeypatch.setattr(arr_quality, "restore_quality", _refuse)
    mod = importlib.import_module(f"app.core.{module}")

    with caplog.at_level("ERROR"):
        getattr(mod, wrapper)("http://arr", "k", 42, PATH)

    assert seen == [getattr(arr_quality, service)]
    [record] = caplog.records
    assert record.name == f"app.core.{module}"
    assert record.getMessage() == (
        f"{name}: quality restore HTTP 500 for {noun} 42 ({PATH}): "
        "Internal Server Error — Nullable object must have a value."
    )


def _best_effort(monkeypatch, caplog, failure):
    import logging

    from app.core import arr_quality

    def _fail(*_a, **_kw):
        raise failure

    monkeypatch.setattr(arr_quality, "restore_quality", _fail)
    with caplog.at_level("ERROR"):
        arr_quality.restore_quality_best_effort(
            arr_quality.RADARR, "http://arr", "k", 42, PATH,
            logging.getLogger("app.core.radarr"),
        )
    [record] = caplog.records
    return record


def test_a_long_error_body_is_cut_to_its_first_500_characters(monkeypatch, caplog):
    """
    Closes: the body logged whole.

    The usual body is a stack trace, and the first line is the part that
    identifies it. The rest fills the log view with frames nobody reads.
    """
    body = "".join(f"frame {n:04d} " for n in range(60))

    record = _best_effort(monkeypatch, caplog, _http_error(body.encode()))

    assert record.getMessage().endswith("— " + body[:500])


def test_an_unreadable_error_body_is_logged_as_missing(monkeypatch, caplog):
    """
    Closes: a failure to read the body escaping the handler meant for it.

    The connection can drop between the status line and the body. An
    exception raised inside an except clause is not caught by the clause
    beside it, so without its own guard this would escape the wrapper and
    reach the job.
    """
    record = _best_effort(monkeypatch, caplog, _http_error(unreadable=True))

    assert record.getMessage().endswith("Internal Server Error — <no body>")


def test_any_other_failure_is_logged_with_the_path_and_goes_no_further(
        monkeypatch, caplog):
    """
    Closes: a non-HTTP failure escaping, and its line losing the path.

    The file is on disk and correct by now, so nothing here may fail the
    job. The traceback goes to the log with the path, under the caller's
    name, because the log is the only place this is ever reported.
    """
    record = _best_effort(monkeypatch, caplog, ValueError("no quality in history"))

    assert record.name == "app.core.radarr"
    assert record.getMessage() == (
        f"Radarr: quality restore failed for movie 42 ({PATH})"
    )
    assert record.exc_info is not None
