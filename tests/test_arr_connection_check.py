"""
GET /api/settings/test-sonarr and /test-radarr — the Test buttons beside the
Sonarr and Radarr settings (settings._test_arr_connection).

It had no test. This one covers the HTTP error branch, which is the branch a
wrong API key takes: the error a failed request raises holds the server's
response, and the connection with it, until something closes it. Left to the
garbage collector, Python 3.14 reports it (ResourceWarning: Implicitly
cleaning up <HTTPError ...>), and earlier versions clean up without a word.

Verified by mutation, 1 applied, 1 killed: the handler's close removed.
"""
import io
import urllib.error
import urllib.request


def test_a_rejected_key_is_reported_and_its_http_error_closed(monkeypatch):
    """
    The test keeps its own reference to the error, so nothing but the
    handler can have closed it.
    """
    from app.api.routes import settings

    error = urllib.error.HTTPError(
        "http://sonarr:8989/api/v3/system/status", 401, "Unauthorized", {},
        io.BytesIO(b""))

    def _urlopen(*_a, **_kw):
        raise error

    monkeypatch.setattr(urllib.request, "urlopen", _urlopen)

    result = settings._test_arr_connection("http://sonarr:8989", "wrong-key", "Sonarr")

    assert result == {"success": False, "error": "HTTP 401: Unauthorized"}
    assert error.fp.closed, "the handler left the error, and its response, open"
