"""
Plex API client — post-job library notification.

Two distinct operations, used for two distinct situations:

  notify_plex_new_file()
      For a file Remuxarr has never seen at this exact path before.
      Fires a lightweight path-scoped library refresh. Plex always runs a
      full deep analysis automatically on any path it has never indexed,
      so a plain refresh is sufficient — no need to look up a ratingKey.

  notify_plex_reprocessed_file()
      For a file at a path Plex has ALREADY indexed (a re-process, a
      retry, or a normal rescan that replaced the file in place). A plain
      refresh does NOT force re-analysis of an already-known path, so this
      finds the matching item by file path and issues an explicit Analyze
      call on it directly — unless a language-fix verification check
      (see _audio_languages_match) finds Plex's own scheduled maintenance
      already caught it, in which case the Analyze call is skipped as
      unnecessary. That check makes its own dedicated per-item request —
      an earlier version tried reading language data from the same bulk
      listing used to resolve the ratingKey, but direct testing showed
      that listing doesn't reliably carry Stream-level detail on every
      server, so the check reverted to the single-item endpoint confirmed
      to work.

Why no library-section-ID setting is needed:
  Plex's /library/sections listing includes each section's configured
  folder path(s). Remuxarr translates the local file path to its Plex-
  side equivalent, then matches that against each section's folder path
  to determine which section the file belongs to — automatically, on
  every call. This avoids asking the user to manually configure and keep
  in sync a separate "library ID" setting for each of their libraries.

Uses only Python's stdlib urllib — no extra dependencies required.
Called via asyncio loop.run_in_executor() from worker.py, same as
sonarr.py / radarr.py.
"""

import json
import logging
import os
import threading
import time
import urllib.error
import urllib.parse
import urllib.request

from app.core.pathmap import swap_prefix

logger = logging.getLogger(__name__)

# ── Section item cache ─────────────────────────────────────────────────────
# The ratingKey lookup must fetch every item in a library section to match
# by file path — Plex has no server-side "search by path" endpoint. During
# a large backlog drain (e.g. 939 movies, one every 8 seconds) that would
# mean 939 full-section fetches from the same section, most returning
# identical data.
#
# This cache stores the most recently fetched {plex_path: rating_key}
# mapping per section, keyed by (base_url, section_id). For TV sections,
# building this mapping requires one additional request per show to pull
# episode-level file paths (see _get_section_items) — top-level section
# listings only return shows, not episodes.
#
# Does NOT also cache audio language data — an earlier version tried
# extracting that from this same bulk response too, on the assumption it
# carried the same Stream-level detail as a single-item lookup. Direct
# A/B testing showed that assumption didn't hold (the bulk listing didn't
# reliably return usable Stream data, causing the language check to
# silently never find a match for any file). Language verification makes
# its own dedicated per-item request instead — see _audio_languages_match.
#
# Expires after SECTION_CACHE_TTL seconds so a new import that lands
# mid-drain still gets picked up on the next fetch cycle, rather than
# sitting invisible in a stale cache for hours.
#
# Thread-safety: drain ticks run sequentially (one every 8 s in a single
# asyncio task), so concurrent writes are not a concern.
_SECTION_CACHE: dict[tuple, dict] = {}
_SECTION_CACHE_TTL = 300  # seconds — refresh at most once every 5 minutes


def _plex_request(
    base_url: str, token: str, path: str, method: str = "GET",
    params: dict | None = None, timeout: int = 15,
) -> dict:
    """
    Make a request against the Plex API and return the parsed JSON body.

    Plex returns XML by default — passing Accept: application/json gets a
    JSON response instead, which avoids needing an XML parser entirely.
    The token can be sent as a header; using the header (rather than a
    query param) keeps it out of any URL that might get logged.
    """
    query = f"?{urllib.parse.urlencode(params)}" if params else ""
    url   = f"{base_url.rstrip('/')}{path}{query}"
    req   = urllib.request.Request(
        url,
        method  = method,
        headers = {
            "X-Plex-Token": token,
            "Accept":       "application/json",
        },
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        body = resp.read()
        return json.loads(body) if body else {}


def translate_path_to_plex(local_path: str, mappings: list[str]) -> str | None:
    """
    Translate a Remuxarr-local file path to the equivalent path Plex would
    use, using the configured "local=plex" prefix pairs.

    Returns None if no mapping's local prefix matches — the caller should
    skip the notification in that case rather than guess.

    Mappings are checked longest-prefix-first so that overlapping prefixes
    (unlikely, but possible) resolve to the most specific match.
    """
    pairs: list[tuple[str, str]] = []
    for entry in mappings:
        if "=" not in entry:
            continue
        local, _, plex = entry.partition("=")
        local, plex = local.strip(), plex.strip()
        if local and plex:
            pairs.append((local, plex))

    # Longest local prefix first so the most specific mapping wins
    pairs.sort(key=lambda p: len(p[0]), reverse=True)

    for local_prefix, plex_prefix in pairs:
        translated = swap_prefix(local_path, local_prefix, plex_prefix)
        if translated is not None:
            return translated

    return None


def _find_section_for_path(base_url: str, token: str, plex_path: str) -> int | None:
    """
    Return the library section ID whose configured folder location is a
    prefix of plex_path, or None if no section matches.
    """
    try:
        data = _plex_request(base_url, token, "/library/sections")
    except Exception:
        logger.exception("Plex: failed to list library sections")
        return None

    directories = data.get("MediaContainer", {}).get("Directory", [])
    for d in directories:
        locations = d.get("Location", [])
        for loc in locations:
            loc_path = (loc.get("path") or "").rstrip("/")
            if loc_path and (plex_path == loc_path or plex_path.startswith(loc_path + "/")):
                return int(d["key"])
    return None


def _get_section_items(
    base_url: str, token: str, section_id: int,
) -> dict[str, dict]:
    """
    Return {plex_path: {"rating_key": int}} for every item in the given
    library section, using the cache above.

    Handles two different section shapes:

      Movie sections — /library/sections/{id}/all returns each movie
      directly, with its own Media/Part/file data attached. No further
      requests needed.

      TV show sections — /library/sections/{id}/all returns each SHOW,
      not each episode. A show's own Metadata entry has no Media/Part/
      file at all (confirmed against Plex's own community-documented
      example responses); the actual video files only exist on episodes,
      nested two levels deeper (show → season → episode). Any top-level
      item found with no direct file is treated as a show and drilled
      into via /library/metadata/{ratingKey}/grandchildren — Plex's own
      developer documentation describes this endpoint family explicitly
      for "browsing a hierarchical tree of media (e.g. show to
      episodes)". This costs one extra request per show (not per
      episode) the first time a section's cache is built or refreshed,
      which is still far cheaper than a request per file for a library
      the size of a long-running anime or TV catalog.
    """
    cache_key = (base_url, section_id)
    now       = time.monotonic()
    entry     = _SECTION_CACHE.get(cache_key)

    if entry is not None and entry["expires"] >= now:
        return entry["items"]

    # Cache miss or expired — fetch fresh data from Plex
    try:
        data = _plex_request(
            base_url, token, f"/library/sections/{section_id}/all",
        )
    except Exception:
        logger.exception("Plex: failed to list items in section %d", section_id)
        return {}

    items: dict[str, dict] = {}
    show_rating_keys: list[int] = []

    for item in data.get("MediaContainer", {}).get("Metadata", []):
        rating_key = item.get("ratingKey")
        if rating_key is None:
            continue
        found_leaf = False
        for media in item.get("Media", []):
            for part in media.get("Part", []):
                file_path = part.get("file")
                if file_path:
                    items[file_path] = {"rating_key": int(rating_key)}
                    found_leaf = True
        if not found_leaf:
            # No direct file at this level. A movie always has one; a TV
            # show never does (its episodes carry the files instead) —
            # queue this ratingKey for the grandchildren drill-down below.
            show_rating_keys.append(int(rating_key))

    for show_key in show_rating_keys:
        try:
            ep_data = _plex_request(
                base_url, token, f"/library/metadata/{show_key}/grandchildren",
            )
        except Exception as exc:
            logger.warning(
                "Plex: failed to list episodes for show ratingKey %d: %s",
                show_key, exc,
            )
            continue
        for ep in ep_data.get("MediaContainer", {}).get("Metadata", []):
            ep_rating_key = ep.get("ratingKey")
            if ep_rating_key is None:
                continue
            for media in ep.get("Media", []):
                for part in media.get("Part", []):
                    file_path = part.get("file")
                    if file_path:
                        items[file_path] = {"rating_key": int(ep_rating_key)}

    _SECTION_CACHE[cache_key] = {"expires": now + _SECTION_CACHE_TTL, "items": items}
    logger.debug(
        "Plex: section %d cache refreshed (%d shows drilled into, %d "
        "total file entries, TTL %ds)",
        section_id, len(show_rating_keys), len(items), _SECTION_CACHE_TTL,
    )
    return items


def _find_rating_key_for_path(
    base_url: str, token: str, section_id: int, plex_path: str,
) -> int | None:
    """
    Return the ratingKey of the Plex item whose file path matches plex_path,
    or None if no item in the section matches.
    """
    entry = _get_section_items(base_url, token, section_id).get(plex_path)
    return entry["rating_key"] if entry else None


def _audio_languages_match(
    base_url: str, token: str, rating_key: int, expected: list[str],
) -> bool | None:
    """
    Check whether this Plex item's audio streams already report exactly the
    expected languages, in order, by fetching the item's own full metadata
    directly — the same data Plex's "View XML" option shows.

    Every stream, not any one. This used to pass when ANY audio stream had
    the one expected language, so a file that already had an English track
    skipped the Analyze while the track the job had just retagged English
    still showed its old tag in Plex. A different language, a different
    number of streams (Plex still listing one the job dropped) or a stream
    with no tag yet all mean the Analyze still runs. "und" and no tag at all
    count as the same.

    IMPORTANT: this makes its own dedicated /library/metadata/{ratingKey}
    request rather than reading from the shared section-listing cache.
    An earlier version tried reading Stream/language data from the bulk
    /library/sections/{id}/all response instead, on the assumption
    (based on a Plex support article) that it carried the same detail as
    this single-item endpoint. Direct testing disproved that: the bulk
    listing did not return usable Stream data on the server it was tested
    against, so the language check silently never found a match for any
    file — correct or not — always falling through to an explicit
    Analyze. The single-item endpoint here is the one confirmed, via
    direct "View XML" comparison across multiple real files, to reliably
    include languageCode. Only the ratingKey lookup benefits from the
    shared bulk-listing cache; this check does not.

    Plex exposes THREE separate language fields per stream once analyzed:
      language     — full human-readable name, e.g. "English"
      languageTag  — ISO 639-1 two-letter code, e.g. "en"
      languageCode — ISO 639-2/B three-letter code, e.g. "eng"
    Remuxarr writes ISO 639-2/B codes (e.g. "eng") via ffmpeg's
    -metadata:s:a:N language=eng flag, so languageCode is the only one of
    the three directly comparable without a name/code lookup table.

    An unanalyzed stream has NONE of these three keys present at all, so
    a plain dict .get() correctly returns None/empty for a not-yet-
    analyzed file. The streams are taken in the order Plex lists them,
    assumed to be the file's own stream order (not checked against a real
    server; were it not, the lists would differ and the Analyze would run,
    which is the safe direction).

    Returns:
      True  — every audio stream already matches, in order. Plex's own
              maintenance already caught this file; no need for Remuxarr
              to force an explicit Analyze.
      False — they do not match yet. Plex hasn't picked it up (or hasn't
              gotten to it yet) — fall back to an explicit Analyze.
      None  — the check itself failed (network error, item not found,
              unexpected response shape). Caller should treat this the
              same as False — when in doubt, do the explicit Analyze
              rather than silently skipping it.

    streamType is compared as a string since Plex's JSON API has been
    observed to represent some numeric fields inconsistently across
    versions/endpoints — comparing as a string avoids a brittle int-only
    match. The language comparison is case-insensitive for the same reason.
    """
    try:
        data = _plex_request(base_url, token, f"/library/metadata/{rating_key}")
    except Exception as exc:
        logger.warning(
            "Plex: language check failed for ratingKey %d: %s", rating_key, exc,
        )
        return None

    items = data.get("MediaContainer", {}).get("Metadata", [])
    if not items:
        logger.warning(
            "Plex: language check for ratingKey %d — no Metadata in "
            "response (raw keys: %s)",
            rating_key, list(data.get("MediaContainer", {}).keys()),
        )
        return None

    def code(value) -> str:
        value = (value or "").strip().lower()
        return value or "und"

    found = [
        code(stream.get("languageCode"))
        for item in items
        for media in item.get("Media", [])
        for part in media.get("Part", [])
        for stream in part.get("Stream", [])
        if str(stream.get("streamType")) == "2"      # audio
    ]
    wanted = [code(value) for value in expected]
    if found == wanted:
        logger.debug("Plex: language check for ratingKey %d — all match (%s)",
                     rating_key, found)
        return True

    logger.info(
        "Plex: language check for ratingKey %d — no match. "
        "expected=%s, found audio languageCode(s)=%s",
        rating_key, wanted, found or "(none)",
    )
    return False


def plex_connection(cfg: dict, context: str) -> tuple[str, str, list[str]] | None:
    """
    The URL, token and path mappings a Plex call needs, read from the app
    settings, or None when Plex notifications are off or not fully set up.

    Off is silent; on but incomplete is a warning, since that is a setup the
    user meant to work. context names the caller in it ("job 12", "an
    import of /media/tv/...").

    One place for what each caller used to write out for itself: the
    post-job notification, the forge and revert refresh, the refresh for an
    import that needs no job, and the one after a review that leaves a file
    alone (refresh_in_background).
    """
    if not cfg.get("plex_enabled", False):
        return None

    url   = (cfg.get("plex_url") or "").rstrip("/")
    token = cfg.get("plex_token") or ""
    if not url or not token:
        logger.warning(
            "Plex notification skipped for %s: plex_url or "
            "plex_token not configured",
            context,
        )
        return None

    mappings = cfg.get("plex_path_mappings", [])
    if not mappings:
        logger.warning(
            "Plex notification skipped for %s: no "
            "plex_path_mappings configured",
            context,
        )
        return None

    return url, token, mappings


def refresh_in_background(cfg: dict, paths: list[str],
                          context: str) -> threading.Thread | None:
    """
    Refresh each distinct folder among paths in Plex, on a thread of its own,
    and return that thread; None when there is nothing to send, dry run is
    on, or Plex notifications are off or not set up.

    Dry run sends nothing to Plex, Sonarr or Radarr, whatever it decides
    about a file: it is for watching what Remuxarr would do before trusting
    it, and a refresh is something Remuxarr does. Checked here, once, for
    every route that leaves files alone.

    For a request handler that has just left files alone: answering a page
    of reviews must not wait on Plex, and must not fail because Plex is down.
    A refresh is scoped to a folder, so one per folder does for a season of
    episodes answered together. Each failure is logged and the rest carry on.
    """
    if not paths or cfg.get("dry_run_mode", False):
        return None
    connection = plex_connection(cfg, context)
    if connection is None:
        return None

    one_per_folder: dict[str, str] = {}
    for path in paths:
        one_per_folder.setdefault(os.path.dirname(path), path)

    def run() -> None:
        for path in one_per_folder.values():
            try:
                notify_plex_new_file(*connection, path)
            except Exception:
                logger.exception("Plex refresh failed for %s after %s",
                                 path, context)

    thread = threading.Thread(target=run, name="plex-refresh", daemon=True)
    thread.start()
    return thread


def notify_plex_new_file(
    base_url: str, token: str, mappings: list[str], local_path: str,
) -> None:
    """
    Fire-and-forget path-scoped library refresh for a file Plex has never
    indexed before. Best-effort — failures are logged but never affect the
    Remuxarr job's recorded status, since the file was already processed
    successfully regardless of whether Plex picks it up immediately.
    """
    plex_path = translate_path_to_plex(local_path, mappings)
    if not plex_path:
        logger.warning(
            "Plex: no path mapping matched %s — skipping refresh "
            "(check Plex Path Mappings in Settings)", local_path,
        )
        return

    section_id = _find_section_for_path(base_url, token, plex_path)
    if section_id is None:
        logger.warning(
            "Plex: no library section found for %s — skipping refresh",
            plex_path,
        )
        return

    folder = os.path.dirname(plex_path)
    logger.info("Plex: refreshing section %d, path %s", section_id, folder)
    try:
        _plex_request(
            base_url, token, f"/library/sections/{section_id}/refresh",
            params={"path": folder},
        )
    except urllib.error.HTTPError as exc:
        logger.error("Plex: refresh HTTP %d for %s: %s", exc.code, folder, exc.reason)
    except Exception:
        logger.exception("Plex: refresh failed for %s", folder)


def notify_plex_reprocessed_file(
    base_url: str, token: str, mappings: list[str], local_path: str,
    expected_audio_languages: list[str] | None = None,
) -> bool:
    """
    Fire-and-forget explicit Analyze call for a file at a path Plex has
    already indexed. Finds the matching item by file path, then issues
    PUT /library/metadata/{ratingKey}/analyze.

    If expected_audio_languages is provided (this reprocess changed an
    audio track's language), first checks whether Plex's own scheduled
    maintenance has already picked up the change — confirmed via manual testing to happen
    for most files, just not reliably every single one. If it already
    matches, the explicit Analyze is skipped as unnecessary. If the check
    is inconclusive (network error, item not found) or doesn't match yet,
    falls through to the same explicit Analyze as before this existed —
    correctness is never traded away for the optimization; a failed or
    uncertain check just means doing the guaranteed-correct thing.

    Returns True only when the actual analyze command was attempted —
    False for every skip, fallback, or early-exit path. The drain loop
    uses this to pace itself: the full interval only applies after a real
    analyze (the genuinely expensive operation on Plex's side); a skip or
    a plain refresh fallback is cheap and can move on to the next item
    almost immediately.

    Best-effort — failures are logged but never affect the Remuxarr job's
    recorded status.
    """
    plex_path = translate_path_to_plex(local_path, mappings)
    if not plex_path:
        logger.warning(
            "Plex: no path mapping matched %s — skipping analyze "
            "(check Plex Path Mappings in Settings)", local_path,
        )
        return False

    section_id = _find_section_for_path(base_url, token, plex_path)
    if section_id is None:
        logger.warning(
            "Plex: no library section found for %s — skipping analyze",
            plex_path,
        )
        return False

    rating_key = _find_rating_key_for_path(base_url, token, section_id, plex_path)
    if rating_key is None:
        logger.warning(
            "Plex: no existing item found for %s — falling back to a "
            "path-scoped refresh instead of analyze", plex_path,
        )
        # Fallback: the item may genuinely not exist in Plex yet (e.g. it
        # was deleted and re-added outside Plex's awareness) — a refresh
        # is a reasonable best-effort substitute for an explicit analyze.
        notify_plex_new_file(base_url, token, mappings, local_path)
        return False

    if expected_audio_languages:
        already_correct = _audio_languages_match(
            base_url, token, rating_key, expected_audio_languages,
        )
        if already_correct:
            logger.info(
                "Plex: %s already shows audio languages %s — Plex's own "
                "maintenance already caught this, skipping analyze",
                plex_path, expected_audio_languages,
            )
            return False
        # already_correct is False or None (check failed/inconclusive) —
        # fall through to the explicit analyze below, same as always.

    logger.info("Plex: triggering Analyze for ratingKey %d (%s)", rating_key, plex_path)
    try:
        _plex_request(
            base_url, token, f"/library/metadata/{rating_key}/analyze",
            method="PUT",
        )
    except urllib.error.HTTPError as exc:
        logger.error(
            "Plex: analyze HTTP %d for ratingKey %d: %s",
            exc.code, rating_key, exc.reason,
        )
    except Exception:
        logger.exception("Plex: analyze failed for ratingKey %d", rating_key)

    # Reached here regardless of whether the PUT succeeded or raised a
    # caught exception above — either way, the analyze was attempted, so
    # the caller should still apply the full pacing interval rather than
    # the fast one. A network-level failure that never reached Plex at
    # all is the only case where this is overly conservative, and that's
    # the safe direction to be wrong in.
    return True


def test_plex_connection(base_url: str, token: str) -> dict:
    """
    Test the configured Plex connection by calling /identity (no auth
    required for this endpoint, but token is sent anyway for consistency).
    Returns {"success": True, "version": ..., "app": "Plex"} or
    {"success": False, "error": ...}.
    """
    if not base_url or not token:
        return {"success": False, "error": "URL or token not configured"}
    try:
        data = _plex_request(base_url, token, "/identity")
        version = data.get("MediaContainer", {}).get("version", "?")
        return {"success": True, "version": version, "app": "Plex"}
    except urllib.error.HTTPError as e:
        return {"success": False, "error": f"HTTP {e.code}: {e.reason}"}
    except urllib.error.URLError as e:
        return {"success": False, "error": f"Connection failed: {e.reason}"}
    except Exception as e:
        return {"success": False, "error": str(e)}
