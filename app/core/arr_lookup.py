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
import posixpath

from app.core.arr_client import arr_get
from app.core.langcodes import from_arr_language
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
            "%s not asked for %s: %s_url or %s_api_key not configured",
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


def original_languages(cfg: dict):
    """
    A lookup from a file's path to its film's or series' original language,
    built by asking each enabled service once for everything it manages.

    For Re-check Scanned Files and Force Full Rescan while Original Language
    Audio is on (scanner.scan_library); imports bring the language with them
    and routine scans do not ask. Each entry's folder is in the service's
    terms, so each file's path is translated with that service's Container
    Paths first, as find_arr_id's caller does. Matched on whole folder names
    by looking each of the path's parent folders up, which is find_arr_id's
    rule without a pass over every series for every file.

    Sonarr is asked about a path first, then Radarr. A service that cannot
    be reached is logged and left out; the files it would have answered for
    keep whatever language they have stored.
    """
    services = []
    for service in ("sonarr", "radarr"):
        connection = arr_connection(cfg, service, None, "the original-language lookup")
        if connection is None:
            continue
        try:
            entries = arr_get(connection["url"], connection["api_key"],
                              _RESOURCE[service]) or []
        except Exception as exc:
            logger.warning("Could not ask %s for original languages: %s", service, exc)
            continue
        folders = {}
        for entry in entries:
            folder = (entry.get("path") or "").rstrip("/")
            code = from_arr_language(entry.get("originalLanguage"))
            if folder and code:
                folders[folder] = code
        services.append((folders,
                         cfg.get(f"{service}_path_prefix_local", "") or "",
                         cfg.get(f"{service}_path_prefix_remote", "") or ""))

    def for_path(local_path: str) -> str | None:
        for folders, local_prefix, remote_prefix in services:
            folder = posixpath.dirname(translate_path(local_path, local_prefix, remote_prefix))
            while folder and folder != "/":
                if folder in folders:
                    return folders[folder]
                parent = posixpath.dirname(folder)
                if parent == folder:
                    break
                folder = parent
        return None

    return for_path
