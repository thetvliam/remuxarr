"""
Finding which Sonarr series or Radarr movie a file belongs to, by its folder.

Sonarr and Radarr are told about a file Remuxarr has changed by its series
or movie ID, and the only place that ID came from was the service's own
webhook. A file found by a scan has none, so a scanned library, which is
most of anyone's library, was never announced: after a file was converted
from MKV to MP4, Sonarr went on recording the old .mkv until its own next
refresh.

Each service lists everything it manages with the folder it lives in
(GET /api/v3/series, GET /api/v3/movie, each entry's "path"), and a file
belongs to the entry whose folder it sits under. Sonarr's parse endpoint
also takes a path, but Radarr's takes only a title, so the folder match is
the one approach that works the same way for both.

The path has to be in the service's terms already: the caller translates it
with the service's Container Path settings, as it does for the rescan and
the quality restore.
"""
from app.core.arr_client import arr_get
from app.core.pathmap import swap_prefix

_RESOURCE = {"sonarr": "/api/v3/series", "radarr": "/api/v3/movie"}


def find_arr_id(service: str, base_url: str, api_key: str, path: str) -> int | None:
    """
    The ID of the series (Sonarr) or movie (Radarr) whose folder holds path,
    or None when none does.

    The match is on whole folder names, through swap_prefix, so a series at
    /tv/Show does not claim /tv/Show 2/... An entry with no folder is passed
    over: to swap_prefix an empty prefix is the root, which every path sits
    under. Errors from the request are not caught here; the caller decides
    what a failed lookup costs.
    """
    for entry in arr_get(base_url, api_key, _RESOURCE[service]) or []:
        folder = (entry.get("path") or "").rstrip("/")
        if folder and swap_prefix(path, folder, folder) is not None:
            return entry.get("id")
    return None
