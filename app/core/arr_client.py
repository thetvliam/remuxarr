"""
Shared HTTP client for *arr service APIs (Sonarr, Radarr).

sonarr.py and radarr.py both POST to /api/v3/command with identical logic —
same headers, same serialisation, same error surface. Restoring a file's
quality after a container change adds GET and PUT against other paths, so
all three verbs go through one builder rather than repeating the URL rule
three times.

That rule is the one with history. arr_post did not strip a trailing slash
from base_url, so a stored URL ending in "/" produced "//api/v3/command",
and the failure was invisible from every direction: settings.py rstrips
these URLs inside its test-connection handlers but not on save, so "Test
Connection" succeeded on exactly the URL that would then fail; and both
notifiers swallow every exception by design, so the rejected command
produced no error anywhere — the rescan simply never happened, the replaced
file was never detected, and Plex never learned it changed. plex.py's
_plex_request already normalised this way; these clients now agree.

Errors are not caught here. The notifiers swallow them on purpose, because a
failed rescan costs a delayed refresh; a failed quality restore leaves the
file mislabelled and the *arr already looking for a replacement, so its
caller needs to see the failure.
"""
import json
import urllib.parse
import urllib.request

_TIMEOUT = 15


def _request(
    base_url: str,
    api_key:  str,
    path:     str,
    method:   str,
    body:     dict | None = None,
    params:   dict | None = None,
):
    """
    Send one *arr API request and return the parsed JSON response.

    Returns None for an empty response body: a 202 with nothing in it is a
    successful write, and reporting it as a JSON parse error would send the
    caller looking in the wrong place.
    """
    url = f"{base_url.rstrip('/')}{path}"
    if params:
        url = f"{url}?{urllib.parse.urlencode(params)}"

    req = urllib.request.Request(
        url,
        data    = json.dumps(body).encode() if body is not None else None,
        headers = {
            "X-Api-Key":    api_key,
            "Content-Type": "application/json",
        },
        method=method,
    )
    with urllib.request.urlopen(req, timeout=_TIMEOUT) as resp:
        raw = resp.read()

    return json.loads(raw) if raw else None


def arr_post(base_url: str, api_key: str, body: dict) -> dict:
    """POST to /api/v3/command. See _request for the base_url rule."""
    return _request(base_url, api_key, "/api/v3/command", "POST", body=body)


def arr_get(base_url: str, api_key: str, path: str, params: dict | None = None):
    """GET an *arr resource. Parameters are query-encoded, never interpolated."""
    return _request(base_url, api_key, path, "GET", params=params)


def arr_put(base_url: str, api_key: str, path: str, body: dict):
    """PUT to an *arr resource path."""
    return _request(base_url, api_key, path, "PUT", body=body)
