"""
plex.py — the post-job library notification client.

Every failure in this module is invisible by design. It is best-effort
throughout: nothing here can fail a Remuxarr job, nothing surfaces in the UI,
and the symptom a user eventually reports is "Plex doesn't show the new audio
track" — weeks later, with no error anywhere to connect it to. That is what
makes it worth testing properly despite being the least consequential module
in the app by strict outcome.

Three things carry real weight.

  THE RETURN VALUE IS A PACING SIGNAL, NOT A SUCCESS FLAG.
  notify_plex_reprocessed_file returns True only when the analyze command was
  actually attempted — including when it raised, deliberately. The drain loop
  waits the full interval on True and a much shorter one on False. Every skip,
  fallback and early exit therefore has to return False, or the drain bursts
  Plex; and a real analyze has to return True even on failure, or the drain
  paces the expensive call as if it were free. Each of the six exit paths is
  pinned separately.

  THE LANGUAGE CHECK MUST FAIL TOWARDS DOING THE WORK.
  _audio_language_matches returns True/False/None, and None means the check
  itself broke. Both False and None have to fall through to the explicit
  analyze — correctness is never traded for the optimisation. A check that
  returned True on error would silently skip analysis on exactly the files
  whose metadata could not be read.

  THE CACHE IS SHARED MODULE STATE.
  _SECTION_CACHE is keyed by (base_url, section_id) and persists across calls.
  Tests that leave entries behind would leak into each other and, worse, could
  make a broken lookup look like it worked. The autouse fixture clears it, and
  test_a_stale_cache_entry_is_refetched pins the TTL rather than trusting it.

Requests are faked at _plex_request, the single seam every call goes through,
so the tests exercise the real response-shape handling — the nested
Metadata/Media/Part/Stream walking is where the actual bugs recorded in this
module's docstrings live.

Verified by mutation: 48 mutations of plex.py, of which 46 are killed by at
least one test here. Both survivors are equivalent, checked rather than
assumed:

  • dropping `if "=" not in entry: continue` from translate_path_to_plex —
    the later `if local and plex` guard rejects the same entries, and the
    outputs are identical across every malformed mapping shape.
  • narrowing notify_plex_new_file's `except urllib.error.HTTPError` — the
    bare `except Exception` immediately below it catches the same error, so
    only the log message changes, not the behaviour.
"""
import urllib.error

import pytest


# ── Harness ──────────────────────────────────────────────────────────────────

URL   = "http://plex:32400"
TOKEN = "tok"


@pytest.fixture(autouse=True)
def _clear_cache():
    """_SECTION_CACHE is module state — leaking it between tests hides bugs."""
    import app.core.plex as plex

    plex._SECTION_CACHE.clear()
    yield
    plex._SECTION_CACHE.clear()


@pytest.fixture
def plex(monkeypatch):
    """
    Fake _plex_request — the one seam every Plex call goes through. Routes are
    matched by path; anything unrouted raises, so an unexpected request is a
    visible failure rather than a silent empty dict.
    """
    import app.core.plex as plex_mod

    routes = {}
    calls  = []

    def _request(base_url, token, path, method="GET", params=None, timeout=15):
        calls.append({"base_url": base_url, "token": token, "path": path,
                      "method": method, "params": params})
        for prefix, responder in routes.items():
            if path == prefix:
                if isinstance(responder, Exception):
                    raise responder
                return responder(path) if callable(responder) else responder
        raise AssertionError(f"unrouted Plex request: {method} {path}")

    monkeypatch.setattr(plex_mod, "_plex_request", _request)
    plex_mod._routes = routes
    plex_mod._calls  = calls
    return plex_mod


def _sections(*locations):
    """A /library/sections response. locations is (section_id, path) pairs."""
    return {"MediaContainer": {"Directory": [
        {"key": str(sid), "Location": [{"path": path}]}
        for sid, path in locations
    ]}}


def _movie_section(*files):
    """A movie section listing: (rating_key, file_path) pairs."""
    return {"MediaContainer": {"Metadata": [
        {"ratingKey": str(rk), "Media": [{"Part": [{"file": path}]}]}
        for rk, path in files
    ]}}


def _streams(*specs):
    """A single-item metadata response. specs is (stream_type, code) pairs."""
    return {"MediaContainer": {"Metadata": [{"Media": [{"Part": [{"Stream": [
        {"streamType": st, "languageCode": code} if code is not None
        else {"streamType": st}
        for st, code in specs
    ]}]}]}]}}


def _paths(plex_mod):
    return [c["path"] for c in plex_mod._calls]


# ── translate_path_to_plex ───────────────────────────────────────────────────

def test_a_matching_prefix_is_translated(plex):
    assert plex.translate_path_to_plex(
        "/media/tv/Show/ep.mkv", ["/media=/data"]
    ) == "/data/tv/Show/ep.mkv"


def test_an_unmatched_path_returns_none_rather_than_guessing(plex):
    """
    The caller skips the notification entirely on None. Guessing would send
    Plex a path that doesn't exist on its side, which fails silently.
    """
    assert plex.translate_path_to_plex("/other/ep.mkv", ["/media=/data"]) is None


def test_the_longest_matching_prefix_wins(plex):
    """
    Overlapping mappings resolve to the most specific one, so a general
    /media rule can't shadow a specific /media/tv rule.
    """
    assert plex.translate_path_to_plex(
        "/media/tv/ep.mkv", ["/media=/data", "/media/tv=/tvshows"]
    ) == "/tvshows/ep.mkv"


@pytest.mark.parametrize("mapping", ["no equals sign", "=/data", "/media=", ""])
def test_malformed_mappings_are_ignored(plex, mapping):
    """A half-configured row must not translate anything."""
    assert plex.translate_path_to_plex("/media/ep.mkv", [mapping]) is None


def test_trailing_slashes_do_not_double_up(plex):
    assert plex.translate_path_to_plex(
        "/media/ep.mkv", ["/media/=/data/"]
    ) == "/data/ep.mkv"


def test_a_sibling_directory_sharing_a_prefix_is_not_matched(plex):
    """
    "/media" must not match "/media2/..." — the same separator-boundary bug
    the scanner's cleanup scoping guards against.
    """
    assert plex.translate_path_to_plex("/media2/ep.mkv", ["/media=/data"]) is None


def test_an_exact_prefix_match_translates_to_the_bare_target(plex):
    assert plex.translate_path_to_plex("/media", ["/media=/data"]) == "/data"


# ── _find_section_for_path ───────────────────────────────────────────────────

def test_the_section_owning_the_path_is_found(plex):
    plex._routes["/library/sections"] = _sections((3, "/data/tv"), (7, "/data/films"))

    assert plex._find_section_for_path(URL, TOKEN, "/data/films/a.mkv") == 7


def test_a_path_under_no_section_returns_none(plex):
    plex._routes["/library/sections"] = _sections((3, "/data/tv"))

    assert plex._find_section_for_path(URL, TOKEN, "/elsewhere/a.mkv") is None


def test_a_section_whose_name_is_a_prefix_of_another_is_not_matched(plex):
    plex._routes["/library/sections"] = _sections((3, "/data/tv"))

    assert plex._find_section_for_path(URL, TOKEN, "/data/tv2/a.mkv") is None


def test_a_failed_section_listing_returns_none_rather_than_raising(plex):
    """
    Best-effort: a Plex outage must not propagate into the worker, which
    calls this from a run_in_executor during job finalisation.
    """
    plex._routes["/library/sections"] = ConnectionError("plex down")

    assert plex._find_section_for_path(URL, TOKEN, "/data/tv/a.mkv") is None


# ── _get_section_items: movies, shows, and the cache ─────────────────────────

def test_a_movie_section_maps_files_directly(plex):
    plex._routes["/library/sections/1/all"] = _movie_section(
        (11, "/data/films/a.mkv"), (12, "/data/films/b.mkv"))

    items = plex._get_section_items(URL, TOKEN, 1)

    assert items == {"/data/films/a.mkv": {"rating_key": 11},
                     "/data/films/b.mkv": {"rating_key": 12}}


def test_a_tv_section_is_drilled_into_for_episode_files(plex):
    """
    A show's own Metadata entry carries no Media/Part/file — the files live on
    episodes, two levels down. Any top-level item with no direct file is
    treated as a show and resolved via /grandchildren.
    """
    plex._routes["/library/sections/2/all"] = {
        "MediaContainer": {"Metadata": [{"ratingKey": "50"}]}
    }
    plex._routes["/library/metadata/50/grandchildren"] = _movie_section(
        (501, "/data/tv/Show/s01e01.mkv"), (502, "/data/tv/Show/s01e02.mkv"))

    items = plex._get_section_items(URL, TOKEN, 2)

    assert items == {"/data/tv/Show/s01e01.mkv": {"rating_key": 501},
                     "/data/tv/Show/s01e02.mkv": {"rating_key": 502}}


def test_a_show_that_fails_to_expand_does_not_lose_the_rest_of_the_section(plex):
    """One unreachable show must not empty the whole cache entry."""
    plex._routes["/library/sections/2/all"] = {
        "MediaContainer": {"Metadata": [
            {"ratingKey": "50"},
            {"ratingKey": "60", "Media": [{"Part": [{"file": "/data/tv/x.mkv"}]}]},
        ]}
    }
    plex._routes["/library/metadata/50/grandchildren"] = ConnectionError("nope")

    items = plex._get_section_items(URL, TOKEN, 2)

    assert items == {"/data/tv/x.mkv": {"rating_key": 60}}


def test_items_without_a_rating_key_are_skipped(plex):
    plex._routes["/library/sections/1/all"] = {
        "MediaContainer": {"Metadata": [
            {"Media": [{"Part": [{"file": "/data/films/no-key.mkv"}]}]},
            {"ratingKey": "12", "Media": [{"Part": [{"file": "/data/films/b.mkv"}]}]},
        ]}
    }

    assert plex._get_section_items(URL, TOKEN, 1) == {
        "/data/films/b.mkv": {"rating_key": 12}}


def test_a_failed_section_fetch_returns_empty_and_is_not_cached(plex):
    """
    Caching a failure would suppress retries for the whole TTL — five minutes
    of a drain doing nothing because one request timed out.
    """
    plex._routes["/library/sections/1/all"] = ConnectionError("plex down")

    assert plex._get_section_items(URL, TOKEN, 1) == {}
    assert plex._SECTION_CACHE == {}


def test_a_second_lookup_is_served_from_the_cache(plex):
    """
    The reason the cache exists: a 939-movie backlog drain would otherwise
    refetch the entire section once every 8 seconds.
    """
    plex._routes["/library/sections/1/all"] = _movie_section((11, "/data/films/a.mkv"))

    plex._get_section_items(URL, TOKEN, 1)
    plex._get_section_items(URL, TOKEN, 1)

    assert _paths(plex).count("/library/sections/1/all") == 1


def test_a_stale_cache_entry_is_refetched(plex, monkeypatch):
    """
    The TTL exists so an import landing mid-drain is picked up rather than
    sitting invisible behind a stale cache for hours.
    """
    plex._routes["/library/sections/1/all"] = _movie_section((11, "/data/films/a.mkv"))

    clock = [1000.0]
    monkeypatch.setattr(plex.time, "monotonic", lambda: clock[0])

    plex._get_section_items(URL, TOKEN, 1)
    clock[0] += plex._SECTION_CACHE_TTL + 1
    plex._get_section_items(URL, TOKEN, 1)

    assert _paths(plex).count("/library/sections/1/all") == 2


def test_different_sections_are_cached_independently(plex):
    plex._routes["/library/sections/1/all"] = _movie_section((11, "/data/films/a.mkv"))
    plex._routes["/library/sections/2/all"] = _movie_section((22, "/data/tv/b.mkv"))

    assert plex._get_section_items(URL, TOKEN, 1) != plex._get_section_items(URL, TOKEN, 2)


# ── _audio_language_matches ──────────────────────────────────────────────────

def test_a_matching_audio_language_is_detected(plex):
    plex._routes["/library/metadata/9"] = _streams(("2", "eng"))

    assert plex._audio_language_matches(URL, TOKEN, 9, "eng") is True


def test_a_non_matching_audio_language_returns_false(plex):
    plex._routes["/library/metadata/9"] = _streams(("2", "dut"))

    assert plex._audio_language_matches(URL, TOKEN, 9, "eng") is False


def test_video_and_subtitle_streams_are_not_consulted(plex):
    """
    Only streamType 2 is audio. Matching a subtitle's language would skip the
    analyze on a file whose AUDIO tag was never fixed.
    """
    plex._routes["/library/metadata/9"] = _streams(("1", "eng"), ("3", "eng"),
                                                   ("2", "dut"))

    assert plex._audio_language_matches(URL, TOKEN, 9, "eng") is False


def test_the_stream_type_is_compared_as_a_string(plex):
    """
    Plex's JSON API has been seen representing numeric fields inconsistently
    across versions, so an int 2 must match as readily as "2".
    """
    plex._routes["/library/metadata/9"] = _streams((2, "eng"))

    assert plex._audio_language_matches(URL, TOKEN, 9, "eng") is True


def test_the_language_comparison_ignores_case_and_padding(plex):
    plex._routes["/library/metadata/9"] = _streams(("2", " ENG "))

    assert plex._audio_language_matches(URL, TOKEN, 9, "Eng") is True


def test_an_unanalyzed_stream_has_no_language_key_at_all(plex):
    """
    Plex omits all three language fields until a stream is analyzed, so a
    plain .get() has to be the thing that handles it.
    """
    plex._routes["/library/metadata/9"] = _streams(("2", None))

    assert plex._audio_language_matches(URL, TOKEN, 9, "eng") is False


def test_a_failed_language_check_returns_none_not_false(plex):
    """
    None means "the check itself broke", which the caller distinguishes from
    a genuine no-match only in logging — but conflating them at the source
    would hide a systematic outage as a stream of ordinary misses.
    """
    plex._routes["/library/metadata/9"] = ConnectionError("plex down")

    assert plex._audio_language_matches(URL, TOKEN, 9, "eng") is None


def test_an_empty_metadata_response_returns_none(plex):
    plex._routes["/library/metadata/9"] = {"MediaContainer": {}}

    assert plex._audio_language_matches(URL, TOKEN, 9, "eng") is None


# ── notify_plex_new_file ─────────────────────────────────────────────────────

def test_a_new_file_refreshes_its_containing_folder(plex):
    """
    Path-scoped rather than whole-section: Plex deep-analyses any path it has
    never indexed, so a targeted refresh is enough.
    """
    plex._routes["/library/sections"] = _sections((3, "/data/tv"))
    plex._routes["/library/sections/3/refresh"] = {}

    plex.notify_plex_new_file(URL, TOKEN, ["/media=/data"], "/media/tv/Show/ep.mkv")

    call = next(c for c in plex._calls if c["path"] == "/library/sections/3/refresh")
    assert call["params"] == {"path": "/data/tv/Show"}


def test_an_unmapped_new_file_sends_nothing(plex):
    plex.notify_plex_new_file(URL, TOKEN, ["/media=/data"], "/other/ep.mkv")

    assert plex._calls == []


def test_a_new_file_in_no_section_sends_no_refresh(plex):
    plex._routes["/library/sections"] = _sections((3, "/data/tv"))

    plex.notify_plex_new_file(URL, TOKEN, ["/media=/data"], "/media/films/a.mkv")

    assert "/library/sections/3/refresh" not in _paths(plex)


@pytest.mark.parametrize("failure", [
    urllib.error.HTTPError("u", 500, "Server Error", {}, None),
    ConnectionError("plex down"),
])
def test_a_failed_refresh_never_raises(plex, failure):
    """
    Called from the worker during job finalisation. An exception here would
    turn a successful remux into a failed one over a Plex outage.
    """
    plex._routes["/library/sections"] = _sections((3, "/data/tv"))
    plex._routes["/library/sections/3/refresh"] = failure

    plex.notify_plex_new_file(URL, TOKEN, ["/media=/data"], "/media/tv/ep.mkv")


# ── notify_plex_reprocessed_file: the pacing contract ────────────────────────

def _wire_reprocess(plex, section=3, rating_key=99):
    plex._routes["/library/sections"] = _sections((section, "/data/tv"))
    plex._routes[f"/library/sections/{section}/all"] = _movie_section(
        (rating_key, "/data/tv/ep.mkv"))
    plex._routes[f"/library/metadata/{rating_key}/analyze"] = {}


def test_a_reprocessed_file_is_analyzed_not_merely_refreshed(plex):
    """
    A plain refresh does not force re-analysis of an already-indexed path —
    the whole reason this function exists separately.
    """
    _wire_reprocess(plex)

    assert plex.notify_plex_reprocessed_file(
        URL, TOKEN, ["/media=/data"], "/media/tv/ep.mkv") is True

    call = next(c for c in plex._calls if c["path"] == "/library/metadata/99/analyze")
    assert call["method"] == "PUT"


def test_an_unmapped_path_returns_false_without_calling_plex(plex):
    assert plex.notify_plex_reprocessed_file(
        URL, TOKEN, ["/media=/data"], "/other/ep.mkv") is False
    assert plex._calls == []


def test_no_matching_section_returns_false(plex):
    plex._routes["/library/sections"] = _sections((3, "/data/tv"))

    assert plex.notify_plex_reprocessed_file(
        URL, TOKEN, ["/media=/data"], "/media/films/a.mkv") is False


def test_an_unknown_item_falls_back_to_a_refresh_and_returns_false(plex):
    """
    False because a refresh is cheap — the drain can move to the next item
    almost immediately rather than waiting out the analyze interval.
    """
    plex._routes["/library/sections"] = _sections((3, "/data/tv"))
    plex._routes["/library/sections/3/all"] = _movie_section((11, "/data/tv/other.mkv"))
    plex._routes["/library/sections/3/refresh"] = {}

    assert plex.notify_plex_reprocessed_file(
        URL, TOKEN, ["/media=/data"], "/media/tv/ep.mkv") is False
    assert "/library/sections/3/refresh" in _paths(plex)


def test_an_analyze_that_fails_still_returns_true(plex):
    """
    True is a pacing signal, not a success flag. The call was made, so Plex
    may well be working on it — pacing as if nothing happened would burst it.
    """
    _wire_reprocess(plex)
    plex._routes["/library/metadata/99/analyze"] = ConnectionError("plex down")

    assert plex.notify_plex_reprocessed_file(
        URL, TOKEN, ["/media=/data"], "/media/tv/ep.mkv") is True


def test_an_analyze_http_error_still_returns_true(plex):
    _wire_reprocess(plex)
    plex._routes["/library/metadata/99/analyze"] = urllib.error.HTTPError(
        "u", 503, "Unavailable", {}, None)

    assert plex.notify_plex_reprocessed_file(
        URL, TOKEN, ["/media=/data"], "/media/tv/ep.mkv") is True


# ── notify_plex_reprocessed_file: the language shortcut ──────────────────────

def test_an_already_correct_language_skips_the_analyze(plex):
    """
    Plex's own scheduled maintenance catches most files. Skipping saves the
    expensive call — and returns False so the drain moves on quickly.
    """
    _wire_reprocess(plex)
    plex._routes["/library/metadata/99"] = _streams(("2", "eng"))

    assert plex.notify_plex_reprocessed_file(
        URL, TOKEN, ["/media=/data"], "/media/tv/ep.mkv",
        expected_language="eng") is False
    assert "/library/metadata/99/analyze" not in _paths(plex)


def test_a_language_that_has_not_caught_up_still_analyzes(plex):
    _wire_reprocess(plex)
    plex._routes["/library/metadata/99"] = _streams(("2", "dut"))

    assert plex.notify_plex_reprocessed_file(
        URL, TOKEN, ["/media=/data"], "/media/tv/ep.mkv",
        expected_language="eng") is True
    assert "/library/metadata/99/analyze" in _paths(plex)


def test_a_broken_language_check_falls_through_to_the_analyze(plex):
    """
    The correctness-over-optimisation rule. An inconclusive check must do the
    guaranteed-correct thing, not assume the file is fine — otherwise a Plex
    outage silently skips analysis on every file it touches.
    """
    _wire_reprocess(plex)
    plex._routes["/library/metadata/99"] = ConnectionError("plex down")

    assert plex.notify_plex_reprocessed_file(
        URL, TOKEN, ["/media=/data"], "/media/tv/ep.mkv",
        expected_language="eng") is True
    assert "/library/metadata/99/analyze" in _paths(plex)


def test_without_an_expected_language_no_check_is_made(plex):
    """
    The check costs a request per item. A reprocess that wasn't a language fix
    has nothing to verify, so it goes straight to the analyze.
    """
    _wire_reprocess(plex)

    plex.notify_plex_reprocessed_file(URL, TOKEN, ["/media=/data"],
                                      "/media/tv/ep.mkv")

    assert "/library/metadata/99" not in _paths(plex)


def test_the_language_check_does_not_reuse_the_bulk_listing(plex):
    """
    It makes its own per-item request. An earlier version read Stream data
    from the section listing, which does not reliably carry Stream-level
    detail — so the check never matched anything and every file fell through
    to an analyze, silently defeating the optimisation.
    """
    _wire_reprocess(plex)
    plex._routes["/library/metadata/99"] = _streams(("2", "eng"))

    plex.notify_plex_reprocessed_file(URL, TOKEN, ["/media=/data"],
                                      "/media/tv/ep.mkv", expected_language="eng")

    assert "/library/metadata/99" in _paths(plex)


# ── test_plex_connection ─────────────────────────────────────────────────────

def test_a_working_connection_reports_the_version(plex):
    plex._routes["/identity"] = {"MediaContainer": {"version": "1.40.2"}}

    assert plex.test_plex_connection(URL, TOKEN) == {
        "success": True, "version": "1.40.2", "app": "Plex"}


@pytest.mark.parametrize("url,token", [("", TOKEN), (URL, ""), ("", "")])
def test_an_unconfigured_connection_is_reported_without_calling_plex(plex, url, token):
    result = plex.test_plex_connection(url, token)

    assert result["success"] is False
    assert plex._calls == []


def test_an_http_error_is_reported_with_its_status(plex):
    plex._routes["/identity"] = urllib.error.HTTPError(
        "u", 401, "Unauthorized", {}, None)

    result = plex.test_plex_connection(URL, TOKEN)

    assert result["success"] is False
    assert "401" in result["error"]


def test_a_connection_failure_is_reported_as_such(plex):
    plex._routes["/identity"] = urllib.error.URLError("no route to host")

    result = plex.test_plex_connection(URL, TOKEN)

    assert result["success"] is False
    assert "Connection failed" in result["error"]


def test_an_unexpected_error_is_still_reported_not_raised(plex):
    """
    This one feeds a Settings "Test Connection" button — it has to return a
    renderable result for any failure, not 500 the request.
    """
    plex._routes["/identity"] = ValueError("malformed json")

    result = plex.test_plex_connection(URL, TOKEN)

    assert result["success"] is False
    assert "malformed json" in result["error"]


def test_a_missing_version_field_does_not_break_the_report(plex):
    plex._routes["/identity"] = {"MediaContainer": {}}

    assert plex.test_plex_connection(URL, TOKEN)["version"] == "?"
