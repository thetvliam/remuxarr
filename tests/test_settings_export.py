"""
GET /api/settings/export — Export Settings, under Backup & Restore.

It had no test. Its one real decision is which keys leave the instance:
with include_secrets=false the four credentials (the Sonarr and Radarr API
keys, the Plex token and the email password) are left out, not blanked, so
the file carries none of them and importing it later leaves the target's
own credentials alone. Measured before this file existed: an export that
ignored include_secrets, and so wrote every credential into the file a user
had asked to have without them, passed the whole suite.

Six mutants of export_settings, each run against the whole suite first and
all six surviving it. Killed here:

  include_secrets ignored, every credential written  → without secrets, import
  the filter inverted, only the credentials written  → without secrets, import
  the default turned to leaving them out             → with secrets, by default
  includes_secrets written as true whatever it is    → without secrets
  the remuxarr_export marker the import checks for
    changed                                          → import
  the defaults written in place of what is stored    → without secrets, with
                                                       secrets both ways
"""
import io
import json

import pytest
from fastapi import FastAPI
from starlette.testclient import TestClient

from app.database.models import AppSetting, Base
from app.database.session import DEFAULT_APP_SETTINGS
from tests.conftest import memory_engine

# Written out rather than read from settings.SECRET_KEYS, so that the set
# losing a key fails here instead of changing what these tests expect.
CREDENTIALS = {
    "sonarr_api_key": "SONARR-KEY",
    "radarr_api_key": "RADARR-KEY",
    "plex_token": "PLEX-TOKEN",
    "email_password": "EMAIL-PASSWORD",
}


@pytest.fixture
def factory():
    from sqlalchemy.orm import sessionmaker

    engine = memory_engine()
    Base.metadata.create_all(engine)
    sessions = sessionmaker(bind=engine)
    with sessions() as db:
        for key, value in {**CREDENTIALS, "job_timeout_minutes": 45}.items():
            db.add(AppSetting(key=key, value=json.dumps(value)))
        db.commit()
    yield sessions
    engine.dispose()


@pytest.fixture
def client(factory):
    """
    The settings routes on an app of their own, overridden on the get_db the
    routes module holds, for the reason test_setting_values.py's client
    fixture gives.
    """
    import app.api.routes.settings as settings_routes
    app = FastAPI()
    app.include_router(settings_routes.router)
    app.dependency_overrides[settings_routes.get_db] = lambda: factory()
    return TestClient(app)


def _stored(factory, key):
    with factory() as db:
        row = db.get(AppSetting, key)
        return json.loads(row.value) if row else None


def test_without_secrets_the_four_credentials_are_left_out(client):
    reply = client.get("/api/settings/export?include_secrets=false")
    body = reply.json()

    assert reply.status_code == 200
    assert body["includes_secrets"] is False
    assert set(DEFAULT_APP_SETTINGS) - set(body["settings"]) == set(CREDENTIALS)
    assert body["settings"]["job_timeout_minutes"] == 45, (
        "the export wrote a default, not the stored value")
    leaked = [v for v in CREDENTIALS.values() if v in reply.text]
    assert leaked == [], f"credentials in a secrets-free export: {leaked}"


@pytest.mark.parametrize("query", ["?include_secrets=true", ""],
                         ids=["asked for", "by default"])
def test_with_secrets_the_credentials_are_written(client, query):
    """
    Included unless asked otherwise, which the endpoint documents: moving to
    a new system is the main use, and the target should work at once.
    """
    body = client.get(f"/api/settings/export{query}").json()

    assert body["includes_secrets"] is True
    assert {k: body["settings"][k] for k in CREDENTIALS} == CREDENTIALS


def test_a_secrets_free_export_imports_whole_and_keeps_the_targets_credentials(
        client, factory):
    """
    What leaving them out is for. Imported where the Plex token has since
    changed, every exported setting applies and the token stays as it is.
    """
    exported = client.get("/api/settings/export?include_secrets=false")
    with factory() as db:
        db.merge(AppSetting(key="plex_token", value=json.dumps("TARGETS-OWN")))
        db.commit()

    reply = client.post("/api/settings/import", files={
        "file": ("remuxarr-settings.json", io.BytesIO(exported.content),
                 "application/json")})
    body = reply.json()

    assert reply.status_code == 200, body
    assert body["applied_keys"] == sorted(exported.json()["settings"])
    assert (body["skipped_keys"], body["invalid_keys"]) == ([], {})
    assert _stored(factory, "plex_token") == "TARGETS-OWN"
