"""
/api/health is the only place the app says which build it is.

The version it reported was a hardcoded "0.1.0" that never changed across
the life of the project, so the bug report template asked for a version the
app could not produce while looking like it had been answered. A filled-in
but meaningless field is worse than a missing one: it stops anyone asking
the question again.

The wiring that stamps a real value in lives in the Dockerfile and
publish.yml and is checked in test_deployment_config.py. This file checks
the other end - that the endpoint reports whatever was stamped, rather than
anything of its own.

Reads the values back out of settings rather than asserting a literal. The
suite runs from a source checkout where nothing is stamped, so hardcoding
"dev" here would pin the absence of a build rather than the reporting of
one, and would keep passing if the endpoint stopped consulting settings at
all.
"""

import pytest

try:
    from starlette.testclient import TestClient
except (ImportError, RuntimeError):  # pragma: no cover - missing httpx
    TestClient = None

pytestmark = pytest.mark.skipif(
    TestClient is None, reason="starlette TestClient requires httpx"
)


@pytest.fixture
def client():
    from app.main import app

    with TestClient(app) as c:
        yield c


def test_health_reports_the_stamped_build(client):
    from app.config import settings

    body = client.get("/api/health").json()

    assert body["status"] == "ok"
    assert body["version"] == settings.VERSION
    assert body["commit"] == settings.COMMIT


def test_health_offers_a_short_commit_matching_the_full_one(client):
    """
    The UI, the bug report template and anyone curling this all want the
    same seven characters, and three separate consumers slicing the SHA
    themselves is three chances to disagree about the length. The full
    value stays available so it can be checked out unambiguously.
    """
    body = client.get("/api/health").json()

    assert body["commit_short"] == body["commit"][:7]
    assert len(body["commit_short"]) <= 7


def test_health_does_not_report_the_old_hardcoded_version(client):
    """
    The specific regression: "0.1.0" was returned by two separate call
    sites and was true of neither. If it reappears, something has stopped
    consulting settings.
    """
    body = client.get("/api/health").json()

    assert body["version"] != "0.1.0", (
        "health is reporting the old hardcoded version again"
    )


def test_openapi_version_tracks_the_same_source(client):
    """
    FastAPI's own version field held the same hardcoded string, so the
    generated OpenAPI document disagreed with any build that was not
    0.1.0 - which was all of them. Kept in step here rather than left to
    drift, since it is the version anyone generating a client sees.
    """
    from app.config import settings

    spec = client.get("/openapi.json").json()

    assert spec["info"]["version"] == settings.VERSION
