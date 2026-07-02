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
    """POST to /api/v3/command and return the parsed JSON response."""
    url  = f"{base_url}/api/v3/command"
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
