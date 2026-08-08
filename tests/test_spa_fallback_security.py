"""
SPA fallback handler — path containment.

WHY THIS FILE EXISTS
--------------------
The static-file branch of `spa_fallback` built its path as

    candidate = _static_dir / request.url.path.lstrip("/")

pathlib's `/` operator sanitises nothing, so that happily produced
`<root>/../../config/remuxarr.db`, `.is_file()` confirmed it, and
`FileResponse` served it. Unauthenticated arbitrary file read, and the app has
no authentication of its own — reaching the port was the only precondition.
`remuxarr.db` holds app_settings in plaintext: plex_token, sonarr_api_key,
radarr_api_key, email_password.

THE DETAIL THAT MAKES THE TEST SHAPE MATTER
-------------------------------------------
The literal `../` form is NOT the one that works. Browsers, curl and httpx all
collapse `..` client-side before the request is sent, so a test written with
`GET /../../secret.txt` never reaches the handler with `..` intact and passes
against the vulnerable code — proving nothing.

Starlette percent-decodes before populating `request.url.path`, so the ENCODED
form arrives already decoded:

    GET /%2e%2e/secret.txt   ->   request.url.path == "/../secret.txt"

That is the form an ordinary HTTP client can send and the form any scanner
probing for traversal will use, precisely because it survives client-side
normalisation. Tests below assert against the encoded form for that reason. If
these are ever rewritten with literal `../`, they stop testing anything.

These are also the suite's first tests that exercise FastAPI itself rather than
calling route handlers as plain functions.
"""

import pytest


from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

try:
    from starlette.testclient import TestClient
except (ImportError, RuntimeError):  # pragma: no cover - missing httpx
    TestClient = None

pytestmark = pytest.mark.skipif(
    TestClient is None, reason="starlette TestClient requires httpx"
)

SECRET = "TOPSECRET-CREDENTIALS"


@pytest.fixture
def client(tmp_path):
    """
    Mirrors the shipped container layout, because traversal depth only works
    when every path component before a `..` actually exists — `stat()` fails
    on a missing intermediate directory, so a flat fixture would make deep
    payloads look harmless when they are not.

        <tmp>/app/frontend/dist/    <- _static_dir in the image
        <tmp>/config/remuxarr.db    <- the credential store, three levels up

    Three `..` from dist reaches <tmp>, which is exactly the relationship
    between /app/frontend/dist and /config in the real image.
    """
    root = tmp_path / "app" / "frontend" / "dist"
    root.mkdir(parents=True)
    (root / "index.html").write_text("<html>spa</html>")
    (root / "favicon.ico").write_text("icon-bytes")

    config = tmp_path / "config"
    config.mkdir()
    (config / "remuxarr.db").write_text(SECRET)
    (tmp_path / "secret.txt").write_text(SECRET)

    static_root = root.resolve()

    def _safe_static_path(url_path: str):
        candidate = (static_root / url_path.lstrip("/")).resolve()
        try:
            candidate.relative_to(static_root)
        except ValueError:
            return None
        return candidate

    app = FastAPI()

    @app.get("/api/ping")
    def ping():
        return {"ok": True}

    @app.exception_handler(StarletteHTTPException)
    async def spa_fallback(request: Request, exc: StarletteHTTPException):
        path = request.url.path
        if (
            exc.status_code == 404
            and not path.startswith("/api")
            and not path.startswith("/assets")
            and path != "/ws"
        ):
            candidate = _safe_static_path(path)
            if candidate and candidate.is_file():
                return FileResponse(str(candidate))
            index = static_root / "index.html"
            if index.is_file():
                return FileResponse(str(index))
        return JSONResponse({"detail": exc.detail}, status_code=exc.status_code)

    return TestClient(app)


# ── The vulnerability ────────────────────────────────────────────────────────

@pytest.mark.parametrize("attack", [
    # Every one of these leaks against the pre-fix handler. Verified by
    # reverting the fixture body — a payload that merely "should" be blocked
    # but cannot reach anything in the fixture layout proves nothing.
    "/%2e%2e/%2e%2e/%2e%2e/config/remuxarr.db",      # the real target
    "/%2e%2e%2f%2e%2e%2f%2e%2e%2fconfig/remuxarr.db",
    "/%2e%2e/%2e%2e/%2e%2e/secret.txt",
    "/%2e%2e/%2e%2e/%2e%2e/config/%2e%2e/secret.txt",
])
def test_encoded_traversal_cannot_escape_the_static_root(client, attack):
    """
    Each of these arrives at the handler with url.path already decoded to a
    real `..` sequence. None may return anything from outside the root.
    """
    r = client.get(attack)
    assert SECRET not in r.text, f"LEAKED via {attack}"
    # Traversal attempts are answered with the ordinary SPA response, so the
    # response body does not reveal whether the requested file exists.
    assert r.status_code == 200
    assert "spa" in r.text


def test_assets_prefix_is_declined_by_the_handler(client):
    """
    `/assets%2f%2e%2e/...` decodes to a path starting with /assets, which the
    handler's own guard excludes before the static branch is reached — so it
    never becomes an SPA 200 and never touches _safe_static_path.

    Recorded separately because the guard, not the containment check, is what
    stops it, and because in production /assets is a StaticFiles mount with
    its own traversal protection (see test_staticfiles_mount_blocks_traversal).
    Asserting the SPA 200 here would be asserting the wrong mechanism.
    """
    r = client.get("/assets%2f%2e%2e/%2e%2e/%2e%2e/%2e%2e/secret.txt")
    assert SECRET not in r.text
    assert r.status_code == 404


def test_staticfiles_mount_blocks_traversal(tmp_path):
    """
    The other route into the static tree. /assets is served by Starlette's
    StaticFiles rather than the handler above, so its protection is a separate
    mechanism and is pinned separately.
    """
    from fastapi.staticfiles import StaticFiles

    assets = tmp_path / "app" / "frontend" / "dist" / "assets"
    assets.mkdir(parents=True)
    (assets / "app.js").write_text("console.log(1)")
    (tmp_path / "secret.txt").write_text(SECRET)

    app = FastAPI()
    app.mount("/assets", StaticFiles(directory=str(assets)), name="assets")
    c = TestClient(app)

    assert c.get("/assets/app.js").text == "console.log(1)"
    for attack in ("/assets/%2e%2e/%2e%2e/%2e%2e/secret.txt",
                   "/assets/%2e%2e%2f%2e%2e%2f%2e%2e%2fsecret.txt"):
        r = c.get(attack)
        assert SECRET not in r.text, f"StaticFiles leaked via {attack}"


def test_starlette_decodes_the_encoded_form(client):
    """
    Pins the assumption the tests above rest on. If a future Starlette stops
    decoding before url.path, these tests would pass without exercising
    anything, and this one fails to say so.
    """
    seen = {}

    app = FastAPI()

    @app.exception_handler(StarletteHTTPException)
    async def capture(request: Request, exc: StarletteHTTPException):
        seen["path"] = request.url.path
        return JSONResponse({"detail": "nf"}, status_code=404)

    TestClient(app).get("/%2e%2e/secret.txt")
    assert seen["path"] == "/../secret.txt", (
        f"url.path was {seen['path']!r} — Starlette's decoding behaviour "
        "changed; the traversal tests in this file need revisiting"
    )


# ── Everything the fix must not break ────────────────────────────────────────

def test_legitimate_root_file_is_still_served(client):
    r = client.get("/favicon.ico")
    assert r.status_code == 200
    assert r.text == "icon-bytes"


def test_spa_route_still_falls_back_to_index(client):
    r = client.get("/dashboard")
    assert r.status_code == 200
    assert "spa" in r.text


def test_api_404_is_not_swallowed_by_the_spa_fallback(client):
    """An unknown /api path must stay a JSON 404, not become index.html."""
    r = client.get("/api/does-not-exist")
    assert r.status_code == 404
    assert "spa" not in r.text


def test_real_api_route_unaffected(client):
    r = client.get("/api/ping")
    assert r.status_code == 200
    assert r.json() == {"ok": True}


def test_nested_legitimate_path_within_root(client, tmp_path):
    sub = tmp_path / "app" / "frontend" / "dist" / "static"
    sub.mkdir()
    (sub / "logo.svg").write_text("<svg/>")
    r = client.get("/static/logo.svg")
    assert r.status_code == 200
    assert r.text == "<svg/>"


# ── The helper in production must match what is tested here ──────────────────

def test_matches_production_helper(tmp_path):
    """
    The fixture reimplements the handler body, which would let production drift
    from what these tests assert. This pins the containment helper itself by
    exercising app.main._safe_static_path when it is importable.
    """
    import app.main as m

    helper = getattr(m, "_safe_static_path", None)
    if helper is None:
        pytest.skip("no frontend dir at import time — helper not defined")

    root = m._static_root
    assert helper("/../../etc/passwd") is None
    assert helper("/%2e%2e") is None or helper("/%2e%2e") == (root / "%2e%2e")
    inside = helper("/index.html")
    assert inside is not None and inside.parent == root
