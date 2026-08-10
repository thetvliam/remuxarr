"""
Shared HTTP client for *arr service APIs (Sonarr, Radarr).

Both sonarr.py and radarr.py need to POST to /api/v3/command with identical
logic — same headers, same serialisation, same error surface. Extracting it
here means any future change to authentication, timeouts, or error handling
is made once.
"""
import json
import urllib.request


def arr_post(base_url: str, api_key: str, body: dict) -> dict:
    """
    POST to /api/v3/command and return the parsed JSON response.

    base_url is rstripped of trailing slashes before the path is appended.
    Without it a stored URL ending in "/" produced "//api/v3/command", and the
    failure was invisible from every direction: settings.py rstrips these URLs
    inside its test-connection handlers but not on save, so "Test Connection"
    succeeded on exactly the URL that would then fail; and both notifiers
    swallow every exception by design, so the rejected command produced no
    error anywhere — the rescan simply never happened, the replaced file was
    never detected, and Plex never learned it changed. plex.py's
    _plex_request already normalised this way; these clients now agree.
    """
    url  = f"{base_url.rstrip('/')}/api/v3/command"
    data = json.dumps(body).encode()
    req  = urllib.request.Request(
        url,
        data    = data,
        headers = {
            "X-Api-Key":    api_key,
            "Content-Type": "application/json",
        },
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=15) as resp:
        return json.loads(resp.read())
