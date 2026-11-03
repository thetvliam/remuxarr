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
import logging

from app.core.arr_client import arr_get
from app.core.pathmap import swap_prefix, translate_path

logger = logging.getLogger(__name__)

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


def arr_connection(cfg: dict, service: str, local_path: str | None,
                   context: str) -> dict | None:
    """
    The url, key and translated path for talking to Sonarr or Radarr about a
    file, or None when the service is off or its url or key is missing.
    worker._arr_notify_target adds the ID; worker._arr_lookup_candidates
    uses it to find one. Here rather than in the worker so the scanner can
    read the same settings the same way; the scanner cannot import the
    worker, which imports it.
    """
    name = "Sonarr" if service == "sonarr" else "Radarr"
    if not cfg.get(f"{service}_enabled", False):
        return None
    url     = (cfg.get(f"{service}_url") or "").rstrip("/")
    api_key = (cfg.get(f"{service}_api_key") or "")
    if not url or not api_key:
        logger.warning(
            "%s notification skipped for %s: %s_url or %s_api_key not configured",
            name, context, service, service,
        )
        return None
    # The *arr reports its own view of the path, which is not Remuxarr's
    # when the two containers mount the library differently. Sent already
    # translated so the restore can compare against what the service will
    # report, using the same prefixes the webhook uses in the opposite
    # direction.
    return {
        "url":         url,
        "api_key":     api_key,
        "output_path": translate_path(
            local_path or "",
            cfg.get(f"{service}_path_prefix_local", "") or "",
            cfg.get(f"{service}_path_prefix_remote", "") or "",
        ) or None,
    }
