"""
arr_client.py / sonarr.py / radarr.py — post-job *arr notifications.

The smallest remaining gap: 21 uncovered lines across three modules. They are
tested together because arr_post is the shared seam and the two notifiers are
thin wrappers over it — covering the client without its callers would leave
the part that can actually be got wrong untested.

What can be got wrong here is the command payload. Sonarr wants
{"name": "RescanSeries", "seriesId": N} and Radarr wants
{"name": "RescanMovie", "movieId": N}. Both modules are near-identical
copies of each other, which is exactly the shape that produces a
copy-paste defect — and because both notifiers swallow every exception by
design, an *arr rejecting a malformed command produces no user-visible
signal at all. The consequence is silent: Sonarr never rescans, never
detects the replaced file, never fires EpisodeFileDelete, and Plex never
learns the file changed. All of that is invisible from Remuxarr.

So the payload contents are pinned per module, and
test_the_two_notifiers_do_not_share_a_payload guards the specific
copy-paste failure directly.

FIXED IN THIS COMMIT:
  arr_post did not strip a trailing slash from base_url, so a stored URL
  ending in "/" produced "//api/v3/command". settings.py rstrips these same
  URLs but only inside its test-connection handlers, so "Test Connection"
  passed on exactly the URL that then failed in normal use — and the swallow
  above meant the failure was silent. plex.py's _plex_request already
  normalised this way; the clients now agree. See
  test_trailing_slashes_do_not_double_up.

Verified by mutation: 28 mutations across the three modules, of which 25 are
killed by at least one test here. All three survivors are equivalent:
narrowing either notifier's `except urllib.error.HTTPError` changes only which
log line fires, because a bare `except Exception` sits directly below it in
both files (plex.py has the identical shape); and strip("/") for rstrip("/")
in arr_post cannot be distinguished, because separating them needs a base_url
with a leading slash and urllib rejects a schemeless URL outright.
"""
import json
import urllib.error

import pytest


# ── Harness ──────────────────────────────────────────────────────────────────

@pytest.fixture
def http(monkeypatch):
    """
    Fake urllib.request.urlopen at the arr_client module. Records the Request
    object so headers, method and body can be asserted, and returns a
    configurable JSON payload.
    """
    import app.core.arr_client as arr_client

    state = {"requests": [], "response": {"id": 42}, "raise": None}

    class _Resp:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def read(self):
            return json.dumps(state["response"]).encode()

    def _urlopen(req, timeout=None):
        state["requests"].append({"req": req, "timeout": timeout})
        if state["raise"]:
            raise state["raise"]
        return _Resp()

    monkeypatch.setattr(arr_client.urllib.request, "urlopen", _urlopen)
    arr_client._state = state
    return arr_client


@pytest.fixture
def arr(monkeypatch):
    """
    Patch arr_post where each notifier imported it — both do a from-import, so
    the name has to be replaced on the notifier module, not on arr_client.
    """
    import app.core.radarr as radarr_mod
    import app.core.sonarr as sonarr_mod

    calls = []
    state = {"raise": None, "response": {"id": 7}}

    def _arr_post(base_url, api_key, body):
        calls.append({"base_url": base_url, "api_key": api_key, "body": body})
        if state["raise"]:
            raise state["raise"]
        return state["response"]

    monkeypatch.setattr(sonarr_mod, "arr_post", _arr_post)
    monkeypatch.setattr(radarr_mod, "arr_post", _arr_post)

    class _Arr:
        sonarr = sonarr_mod
        radarr = radarr_mod

    _Arr.calls = calls
    _Arr.state = state
    return _Arr


def _http_error(code=500, reason="Server Error"):
    return urllib.error.HTTPError("u", code, reason, {}, None)


# ── arr_post ─────────────────────────────────────────────────────────────────

def test_the_command_endpoint_is_addressed(http):
    http.arr_post("http://sonarr:8989", "key", {"name": "RescanSeries"})

    assert http._state["requests"][0]["req"].full_url == \
        "http://sonarr:8989/api/v3/command"


def test_the_api_key_is_sent_as_a_header_not_a_query_param(http):
    """
    A key in the URL ends up in access logs and error messages. Header-only
    keeps it out of anything that might get shared in a bug report.
    """
    http.arr_post("http://sonarr:8989", "secret-key", {"name": "RescanSeries"})

    req = http._state["requests"][0]["req"]
    assert req.get_header("X-api-key") == "secret-key"
    assert "secret-key" not in req.full_url


def test_the_body_is_sent_as_json(http):
    http.arr_post("http://sonarr:8989", "key",
                  {"name": "RescanSeries", "seriesId": 5})

    req = http._state["requests"][0]["req"]
    assert req.get_header("Content-type") == "application/json"
    assert json.loads(req.data) == {"name": "RescanSeries", "seriesId": 5}


def test_the_request_is_a_post(http):
    http.arr_post("http://sonarr:8989", "key", {"name": "RescanSeries"})

    assert http._state["requests"][0]["req"].method == "POST"


def test_a_timeout_is_always_applied(http):
    """
    Called from a run_in_executor thread during job finalisation. With no
    timeout an unresponsive *arr would hold that thread open indefinitely.
    """
    http.arr_post("http://sonarr:8989", "key", {"name": "RescanSeries"})

    assert http._state["requests"][0]["timeout"] == 15


def test_the_parsed_response_is_returned(http):
    http._state["response"] = {"id": 99, "status": "queued"}

    assert http.arr_post("http://sonarr:8989", "key", {"name": "X"}) == \
        {"id": 99, "status": "queued"}


def test_errors_propagate_out_of_the_client(http):
    """
    arr_post itself does not swallow anything — the notifiers own the
    best-effort policy. Catching here would deny them the chance to log which
    command failed and why.
    """
    http._state["raise"] = _http_error(401, "Unauthorized")

    with pytest.raises(urllib.error.HTTPError):
        http.arr_post("http://sonarr:8989", "key", {"name": "X"})


@pytest.mark.parametrize("base_url", [
    "http://sonarr:8989",
    "http://sonarr:8989/",
    "http://sonarr:8989///",
])
def test_trailing_slashes_do_not_double_up(http, base_url):
    """
    Regression test. arr_post used to interpolate base_url directly, so a
    stored URL ending in "/" produced "http://sonarr:8989//api/v3/command".

    That failure was invisible from every direction, which is why it survived:
    settings.py rstrips sonarr_url/radarr_url inside its test-connection
    handlers but NOT on save, so "Test Connection" succeeded on precisely the
    URL that would then fail in normal use; and both notifiers swallow every
    exception by design, so an *arr rejecting the doubled path logged nothing
    a user would see. The rescan just never happened — the replaced file was
    never detected, EpisodeFileDelete/MovieFileDelete never fired, and Plex
    never learned the file had changed.

    plex.py's _plex_request already rstripped; the two clients now agree.
    """
    http.arr_post(base_url, "key", {"name": "RescanSeries"})

    assert http._state["requests"][0]["req"].full_url == \
        "http://sonarr:8989/api/v3/command"


def test_a_path_component_in_the_base_url_survives(http):
    """
    rstrip strips characters, not a suffix, so an *arr behind a reverse proxy
    subpath must keep it.

    Note this does NOT distinguish rstrip("/") from strip("/"): a base_url
    with a leading slash would, but urllib.request.Request rejects a
    schemeless URL outright, so no such input can reach this function. The
    two are equivalent for every value that can actually arrive here, and no
    test can separate them — rstrip is used because it says what is meant.
    """
    http.arr_post("http://host/sonarr/", "key", {"name": "RescanSeries"})

    assert http._state["requests"][0]["req"].full_url == \
        "http://host/sonarr/api/v3/command"


# ── notify_sonarr ────────────────────────────────────────────────────────────

def test_sonarr_is_asked_to_rescan_the_series(arr):
    arr.sonarr.notify_sonarr("http://sonarr:8989", "key", 31)

    assert arr.calls[0]["body"] == {"name": "RescanSeries", "seriesId": 31}


def test_sonarr_credentials_are_passed_through(arr):
    arr.sonarr.notify_sonarr("http://sonarr:8989", "sonarr-key", 31)

    assert arr.calls[0]["base_url"] == "http://sonarr:8989"
    assert arr.calls[0]["api_key"] == "sonarr-key"


@pytest.mark.parametrize("failure", [
    _http_error(500, "Server Error"),
    _http_error(401, "Unauthorized"),
    ConnectionError("sonarr unreachable"),
    ValueError("malformed json"),
])
def test_a_failing_sonarr_notification_never_raises(arr, failure):
    """
    Called from worker job finalisation. Raising here would turn a successful
    remux into a failed one because a downstream service was unavailable.
    """
    arr.state["raise"] = failure

    arr.sonarr.notify_sonarr("http://sonarr:8989", "key", 31)


def test_sonarr_does_not_poll_for_completion(arr):
    """
    Fire-and-forget: exactly one request. Polling was removed with RenameFiles
    (which needed the rescan to finish to learn the new file ID), and
    reintroducing a wait would hold the executor thread for the duration of a
    full series rescan.
    """
    arr.sonarr.notify_sonarr("http://sonarr:8989", "key", 31)

    assert len(arr.calls) == 1


def test_a_missing_command_id_in_the_response_is_tolerated(arr, caplog):
    """
    The id is only logged, so an *arr that omits it must not be treated as a
    failure. Asserting "does not raise" is not enough here — the notifier
    swallows everything, so an unguarded resp["id"] would raise a KeyError,
    get caught, and be logged as a failed rescan while the test still passed.
    So this asserts nothing was logged at ERROR or above.
    """
    import logging

    arr.state["response"] = {}

    with caplog.at_level(logging.DEBUG, logger="app.core.sonarr"):
        arr.sonarr.notify_sonarr("http://sonarr:8989", "key", 31)

    errors = [r for r in caplog.records if r.levelno >= logging.ERROR]
    assert not errors, (
        f"a response without a command id was logged as a failure: "
        f"{[r.getMessage() for r in errors]}"
    )


# ── notify_radarr ────────────────────────────────────────────────────────────

def test_radarr_is_asked_to_rescan_the_movie(arr):
    arr.radarr.notify_radarr("http://radarr:7878", "key", 88)

    assert arr.calls[0]["body"] == {"name": "RescanMovie", "movieId": 88}


def test_radarr_credentials_are_passed_through(arr):
    arr.radarr.notify_radarr("http://radarr:7878", "radarr-key", 88)

    assert arr.calls[0]["base_url"] == "http://radarr:7878"
    assert arr.calls[0]["api_key"] == "radarr-key"


@pytest.mark.parametrize("failure", [
    _http_error(500, "Server Error"),
    ConnectionError("radarr unreachable"),
    ValueError("malformed json"),
])
def test_a_failing_radarr_notification_never_raises(arr, failure):
    arr.state["raise"] = failure

    arr.radarr.notify_radarr("http://radarr:7878", "key", 88)


def test_radarr_does_not_poll_for_completion(arr):
    arr.radarr.notify_radarr("http://radarr:7878", "key", 88)

    assert len(arr.calls) == 1


# ── The copy-paste guard ─────────────────────────────────────────────────────

def test_the_two_notifiers_do_not_share_a_payload(arr):
    """
    The specific defect these two near-identical modules invite: Sonarr's
    command sent with Radarr's key name, or vice versa. An *arr rejects the
    malformed command, both notifiers swallow the error, and nothing anywhere
    reports that the rescan never happened — so the replaced file is never
    detected, EpisodeFileDelete/MovieFileDelete never fires, and Plex never
    learns the file changed.

    Asserting the two payloads are disjoint catches the swap in either
    direction, which neither module's own test can do alone.
    """
    arr.sonarr.notify_sonarr("http://sonarr:8989", "key", 31)
    arr.radarr.notify_radarr("http://radarr:7878", "key", 88)

    sonarr_body, radarr_body = arr.calls[0]["body"], arr.calls[1]["body"]

    assert sonarr_body["name"] != radarr_body["name"]
    assert set(sonarr_body) & set(radarr_body) == {"name"}, (
        "the two commands share an id field — one module has the other's "
        "payload shape"
    )
    assert "movieId" not in sonarr_body
    assert "seriesId" not in radarr_body
