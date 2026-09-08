"""
arr_client — the GET and PUT surface, and the builder all three verbs share.

Restoring a file's quality after a container change needs to read from the
*arr APIs and write back to them, which arr_client could not do: it POSTed
to /api/v3/command and nothing else. All three verbs now go through one
_request, so the URL rule with the history behind it — the trailing slash
that produced "//api/v3/command" and failed silently — has one copy rather
than three.

This module is deliberately narrow. Thirteen mutations were applied to the
rebuilt client and run against the suite before any test here existed. Six
were already dead, killed through arr_post by test_arr_notifications.py,
which is what sharing the builder bought: the base_url rstrip, the
X-Api-Key header, the method pass-through for POST, the timeout, errors
propagating rather than being swallowed, and the JSON content type are all
covered there and are not covered again here.

Seven survived, and they are all on surface those tests cannot reach:

  query params  ignored entirely                          killed
                interpolated raw instead of encoded       killed
  bodies        a body sent on a GET                      killed
  responses     an empty body treated as a parse error    killed
  the verbs     the builder forcing POST for every caller killed
                arr_get sending PUT                       killed
                arr_put sending GET                       killed

The last three are three separate wirings rather than one rule, so they
were mutated separately. arr_post's own wiring is already pinned next door.

What makes these worth pinning rather than obvious: every one of them
fails at the *arr rather than here. A GET with an unencoded path parameter
returns someone else's episode or a 400, a PUT sent as a GET returns the
resource unchanged with a 200, and the caller cannot tell the difference
between a write that happened and one that did not. The restore then
believes it succeeded and the file keeps the wrong quality — which is the
state that has Radarr queueing a replacement download.

Run from the project root:
    pytest tests/test_arr_client.py -v
"""
import urllib.parse

import pytest


@pytest.fixture
def http(monkeypatch):
    """
    Fake urlopen at the arr_client module, recording the Request objects so
    the URL, method, headers and body can be asserted.

    Unlike the fixture in test_arr_notifications.py this hands the state
    back rather than hanging it off the module, so nothing is left on
    arr_client for whatever runs next.
    """
    import app.core.arr_client as arr_client

    state = {"requests": [], "body": b'{"id": 42}'}

    class _Resp:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def read(self):
            return state["body"]

    def _urlopen(req, timeout=None):
        state["requests"].append({"req": req, "timeout": timeout})
        return _Resp()

    monkeypatch.setattr(arr_client.urllib.request, "urlopen", _urlopen)

    class _Harness:
        module = arr_client
        arr_get = staticmethod(arr_client.arr_get)
        arr_put = staticmethod(arr_client.arr_put)
        arr_post = staticmethod(arr_client.arr_post)

        @property
        def sent(self):
            return state["requests"][-1]["req"]

        @staticmethod
        def responds_with(raw: bytes):
            state["body"] = raw

    return _Harness()


# ── Query parameters ──────────────────────────────────────────────────────────

def test_a_get_carries_its_parameters_in_the_query_string(http):
    """
    Closes: the params block skipped entirely.

    Dropped parameters do not fail. GET /api/v3/moviefile with no movieId
    returns every file Radarr knows about, and the caller picks the first
    match out of someone else's library.
    """
    http.arr_get("http://radarr:7878", "key", "/api/v3/moviefile",
                 params={"movieId": 1522})

    query = urllib.parse.parse_qs(urllib.parse.urlparse(http.sent.full_url).query)
    assert query == {"movieId": ["1522"]}


def test_parameters_are_encoded_rather_than_interpolated(http):
    """
    Closes: params joined raw instead of urlencoded.

    Library paths are the parameters here, and they contain spaces,
    brackets and ampersands as a matter of course — "Toy Story 5 (2026)"
    is a real one from the data this was built against.
    """
    path = "/media/Fast & Furious (2009)/Fast & Furious (2009).mkv"

    http.arr_get("http://radarr:7878", "key", "/api/v3/moviefile",
                 params={"path": path})

    url = http.sent.full_url
    assert " " not in url, "an unescaped space reached the URL"
    query = urllib.parse.parse_qs(urllib.parse.urlparse(url).query)
    assert query["path"] == [path], "the ampersand split the value into two params"


# ── Bodies and responses ──────────────────────────────────────────────────────

def test_a_get_sends_no_body(http):
    """Closes: the body serialised unconditionally, so a GET carries "null"."""
    http.arr_get("http://radarr:7878", "key", "/api/v3/moviefile",
                 params={"movieId": 1522})

    assert http.sent.data is None


def test_an_empty_response_body_is_a_success_not_a_parse_error(http):
    """
    Closes: the empty-body branch removed.

    A write that returns 202 with nothing in it succeeded. Raising
    JSONDecodeError there sends the caller looking at its payload for a
    fault that is not in it.
    """
    http.responds_with(b"")

    result = http.arr_put("http://radarr:7878", "key",
                          "/api/v3/moviefile/2071", {"quality": {}})

    assert result is None


# ── The verbs ─────────────────────────────────────────────────────────────────

def test_each_verb_sends_the_method_it_names(http):
    """
    Closes: the builder forcing one method for every caller, arr_get
    sending PUT, and arr_put sending GET — three separate wirings, so
    three separate mutants.

    A PUT that leaves as a GET returns 200 and the unchanged resource, so
    nothing distinguishes it from a write that worked.
    """
    http.arr_get("http://radarr:7878", "key", "/api/v3/moviefile")
    assert http.sent.method == "GET"

    http.arr_put("http://radarr:7878", "key", "/api/v3/moviefile/2071",
                 {"quality": {}})
    assert http.sent.method == "PUT"

    http.arr_post("http://radarr:7878", "key", {"name": "RescanMovie"})
    assert http.sent.method == "POST"
