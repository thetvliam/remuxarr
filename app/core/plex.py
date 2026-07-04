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
      call on it directly.

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
import time
import urllib.error
import urllib.parse
import urllib.request

logger = logging.getLogger(__name__)

# ── Section item cache ─────────────────────────────────────────────────────
# _find_rating_key_for_path must fetch EVERY item in a library section to
# match by file path — Plex has no server-side "search by path" endpoint.
# During a large backlog drain (e.g. 939 movies, one every 8 seconds) that
# would mean 939 full-section fetches from the same section, most returning
# identical data.
#
# This cache stores the most recently fetched {plex_path → rating_key}
# mapping per section, keyed by (base_url, section_id). It expires after
# SECTION_CACHE_TTL seconds so a new import that lands mid-drain still
# gets picked up on the next fetch cycle, rather than sitting invisible
# in a stale cache for hours.
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
        local_norm = local_prefix.rstrip("/")
        if local_path == local_norm or local_path.startswith(local_norm + "/"):
            suffix = local_path[len(local_norm):]
            return plex_prefix.rstrip("/") + suffix

    return None


def _find_section_for_path(base_url: str, token: str, plex_path: str) -> int | None:
    """
    Return the library section ID whose configured folder location is a
    prefix of plex_path, or None if no section matches.
    """
    try:
        data = _plex_request(base_url, token, "/library/sections")
    except Exception as exc:
        logger.error("Plex: failed to list library sections: %s", exc)
        return None

    directories = data.get("MediaContainer", {}).get("Directory", [])
    for d in directories:
        locations = d.get("Location", [])
        for loc in locations:
            loc_path = (loc.get("path") or "").rstrip("/")
            if loc_path and (plex_path == loc_path or plex_path.startswith(loc_path + "/")):
                return int(d["key"])
    return None


def _find_rating_key_for_path(
    base_url: str, token: str, section_id: int, plex_path: str,
) -> int | None:
    """
    Return the ratingKey of the Plex item whose file path matches plex_path.

    Plex has no server-side "search by exact file path" endpoint, so this
    must fetch every item in the section and match client-side. The result
    is cached in _SECTION_CACHE for _SECTION_CACHE_TTL seconds so repeated
    calls during a large backlog drain (hundreds of items in the same section)
    only pay the full-fetch cost once per TTL window rather than once per item.
    """
    cache_key = (base_url, section_id)
    now       = time.monotonic()
    entry     = _SECTION_CACHE.get(cache_key)

    if entry is None or entry["expires"] < now:
        # Cache miss or expired — fetch fresh data from Plex
        try:
            data = _plex_request(
                base_url, token, f"/library/sections/{section_id}/all",
            )
        except Exception as exc:
            logger.error("Plex: failed to list items in section %d: %s", section_id, exc)
            return None

        mapping: dict[str, int] = {}
        for item in data.get("MediaContainer", {}).get("Metadata", []):
            for media in item.get("Media", []):
                for part in media.get("Part", []):
                    if part.get("file"):
                        mapping[part["file"]] = int(item["ratingKey"])

        _SECTION_CACHE[cache_key] = {
            "expires": now + _SECTION_CACHE_TTL,
            "mapping": mapping,
        }
        logger.debug(
            "Plex: section %d cache refreshed (%d items, TTL %ds)",
            section_id, len(mapping), _SECTION_CACHE_TTL,
        )
    else:
        mapping = entry["mapping"]

    return mapping.get(plex_path)


def _audio_language_matches(
    base_url: str, token: str, rating_key: int, expected_language: str,
) -> bool | None:
    """
    Check whether ANY audio stream on this Plex item already reports the
    expected language, by fetching the item's own full metadata directly —
    the same data Plex's "View XML" option shows.

    Plex exposes THREE separate language fields per stream once analyzed:
      language     — full human-readable name, e.g. "English"
      languageTag  — ISO 639-1 two-letter code, e.g. "en"
      languageCode — ISO 639-2/B three-letter code, e.g. "eng"
    Confirmed directly by comparing a user's before/after "View XML" dumps
    of the same file. Remuxarr writes ISO 639-2/B codes (e.g. "eng") via
    ffmpeg's -metadata:s:a:N language=eng flag, so languageCode is the only
    one of the three that's directly comparable without a name/code lookup
    table — checking "language" (the full name) would never match and this
    verification would silently never succeed, always falling through to
    the explicit Analyze call with zero actual benefit.

    An unanalyzed stream has NONE of these three keys present at all
    (confirmed: the same file's "before" XML omits all three), so a plain
    dict .get() correctly returns None/empty for a not-yet-analyzed file.

    Returns:
      True  — at least one audio stream already matches expected_language.
              Plex's own maintenance already caught this file; no need for
              Remuxarr to force an explicit Analyze.
      False — no audio stream matches yet. Plex hasn't picked it up (or
              hasn't gotten to it yet) — fall back to an explicit Analyze.
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
        return None

    expected = expected_language.strip().lower()
    for item in items:
        for media in item.get("Media", []):
            for part in media.get("Part", []):
                for stream in part.get("Stream", []):
                    if str(stream.get("streamType")) != "2":
                        continue   # not an audio stream
                    lang_code = (stream.get("languageCode") or "").strip().lower()
                    if lang_code == expected:
                        return True
    return False


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
    except Exception as exc:
        logger.error("Plex: refresh failed for %s: %s", folder, exc)


def notify_plex_reprocessed_file(
    base_url: str, token: str, mappings: list[str], local_path: str,
    expected_language: str | None = None,
) -> None:
    """
    Fire-and-forget explicit Analyze call for a file at a path Plex has
    already indexed. Finds the matching item by file path, then issues
    PUT /library/metadata/{ratingKey}/analyze.

    If expected_language is provided (this reprocess was a language-tag
    fix), first checks whether Plex's own scheduled maintenance has
    already picked up the change — confirmed via manual testing to happen
    for most files, just not reliably every single one. If it already
    matches, the explicit Analyze is skipped as unnecessary. If the check
    is inconclusive (network error, item not found) or doesn't match yet,
    falls through to the same explicit Analyze as before this existed —
    correctness is never traded away for the optimization; a failed or
    uncertain check just means doing the guaranteed-correct thing.

    Best-effort — failures are logged but never affect the Remuxarr job's
    recorded status.
    """
    plex_path = translate_path_to_plex(local_path, mappings)
    if not plex_path:
        logger.warning(
            "Plex: no path mapping matched %s — skipping analyze "
            "(check Plex Path Mappings in Settings)", local_path,
        )
        return

    section_id = _find_section_for_path(base_url, token, plex_path)
    if section_id is None:
        logger.warning(
            "Plex: no library section found for %s — skipping analyze",
            plex_path,
        )
        return

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
        return

    if expected_language:
        already_correct = _audio_language_matches(
            base_url, token, rating_key, expected_language,
        )
        if already_correct:
            logger.info(
                "Plex: %s already shows language=%s — Plex's own "
                "maintenance already caught this, skipping analyze",
                plex_path, expected_language,
            )
            return
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
    except Exception as exc:
        logger.error("Plex: analyze failed for ratingKey %d: %s", rating_key, exc)


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
