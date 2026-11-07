"""
Setting values of the wrong kind, on the way in and on the way out.

Values are stored as arbitrary JSON, and nothing checked them. Measured with
analyze_file before the fix: keep_subtitle_languages stored as "eng" became
the set {"e", "n", "g"} and the English subtitle was dropped; "eng,ger" for
audio dropped the German track; and prefer_mp4_container stored as "false"
converted anyway, any non-empty string being true. The Settings page always
sends the right kind of value, so these came from an import of a hand-edited
file, a script calling the API, a full backup restore, or a hand edit.

Now settings.setting_problem checks a value against the schema (its type, a
select's options, an integer's minimum, and for the settings with no schema
entry their default's type). The two PUT routes refuse a bad value, the bulk
one writing nothing at all if any is bad; an import skips and reports it.
get_app_settings reads a stored value of the wrong basic type as the default,
logging it once, which covers what no route sees.

Verified by mutation, 15 applied 15 killed, each surviving the whole
2336-test suite first:

  the routes   the bulk PUT not checking            → bulk refused
               the bulk PUT writing the good ones   → bulk refused
               the single PUT not checking          → single refused
               the import not checking              → import skips
  the check    a list's items not checked           → the bad-value table
               a select's options not checked       → the bad-value table
               true or false not checked            → the bad-value table
               true taken for a whole number        → the bad-value table
               a minimum not checked                → the bad-value table
               text not checked                     → the bad-value table
               no-schema settings not checked       → the bad-value table
  reading      the guard off                        → each read test
               a list's items not checked           → a list of numbers
               true taken for a whole number        → true for a number
               warned at every read                 → warned once

The import toast's two mutants are recorded in BackupRestoreSection.test.jsx.
"""
import io
import json
import logging

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.orm import sessionmaker

import app.database.session as session_module
from app.api.routes.settings import SETTINGS_SCHEMA, setting_problem
from app.database.models import AppSetting, Base
from app.database.session import DEFAULT_APP_SETTINGS, get_app_settings
from tests.conftest import memory_engine


@pytest.fixture
def factory():
    engine = memory_engine()
    Base.metadata.create_all(engine)
    yield sessionmaker(bind=engine)
    engine.dispose()


@pytest.fixture
def client(factory):
    """
    The settings routes on an app of their own. The override is keyed on the
    get_db the routes module holds: another test reloads the session module,
    after which its get_db is a different function from the one Depends
    captured, and an override keyed on that would never apply.
    """
    import app.api.routes.settings as settings_routes
    app = FastAPI()
    app.include_router(settings_routes.router)
    app.dependency_overrides[settings_routes.get_db] = lambda: factory()
    return TestClient(app)


def _stored(factory, key):
    with factory() as db:
        row = db.get(AppSetting, key)
        return None if row is None else json.loads(row.value)


def _store_raw(factory, key, value):
    with factory() as db:
        db.merge(AppSetting(key=key, value=json.dumps(value)))
        db.commit()


@pytest.fixture(autouse=True)
def _fresh_warnings(monkeypatch):
    """Each test starts with no setting already warned about."""
    monkeypatch.setattr(session_module, "_ignored_settings", set())


# ── The check ────────────────────────────────────────────────────────────────

def test_every_default_passes():
    """The defaults are what a fresh install saves; none may be refused."""
    assert {k: setting_problem(k, v) for k, v in DEFAULT_APP_SETTINGS.items()
            if setting_problem(k, v)} == {}


def test_every_choice_the_settings_page_offers_passes():
    for entry in SETTINGS_SCHEMA:
        for option in entry.get("options", []):
            assert setting_problem(entry["key"], option["value"]) is None


@pytest.mark.parametrize("key, value, says", [
    ("keep_subtitle_languages", "eng", "must be a list of text"),
    ("keep_audio_languages", "eng,ger", "must be a list of text"),
    ("keep_audio_languages", ["eng", 2], "holds something else"),
    ("fix_undefined_language", "fix", "must be one of always_fix"),
    ("fix_undefined_language", False, "must be text"),
    ("prefer_mp4_container", "false", "must be true or false"),
    ("max_concurrent_jobs", True, "must be a whole number"),
    ("job_timeout_minutes", "120", "must be a whole number"),
    ("revert_retention_days", -1, "must be at least 0"),
    ("sonarr_url", 8989, "must be text"),
    ("scheduled_scan_times", "03:00", "must be a list of text"),
    ("scheduled_scan_enabled", "yes", "must be true or false"),
])
def test_a_bad_value_is_named_with_what_it_should_be(key, value, says):
    problem = setting_problem(key, value)

    assert problem and problem.startswith(key) and says in problem


# ── The routes ───────────────────────────────────────────────────────────────

def test_the_bulk_save_refuses_a_bad_value_and_writes_nothing(client, factory):
    """One bad value, one good: neither is written, so no half save."""
    reply = client.put("/api/settings/", json={
        "keep_subtitle_languages": "eng",
        "job_timeout_minutes": 30,
    })

    assert reply.status_code == 400
    assert "keep_subtitle_languages must be a list of text" in reply.json()["detail"]
    assert _stored(factory, "keep_subtitle_languages") is None
    assert _stored(factory, "job_timeout_minutes") is None


def test_the_bulk_save_still_saves_good_values(client, factory):
    reply = client.put("/api/settings/", json={"keep_subtitle_languages": ["eng", "jpn"]})

    assert reply.status_code == 200
    assert _stored(factory, "keep_subtitle_languages") == ["eng", "jpn"]


def test_a_single_save_refuses_a_bad_value(client, factory):
    reply = client.put("/api/settings/prefer_mp4_container", json={"value": "false"})

    assert reply.status_code == 400
    assert _stored(factory, "prefer_mp4_container") is None


def test_an_import_skips_a_bad_value_and_applies_the_rest(client, factory):
    export = {"remuxarr_export": "settings", "settings": {
        "keep_subtitle_languages": "eng",
        "job_timeout_minutes": 30,
        "no_such_setting": 1,
    }}
    reply = client.post("/api/settings/import", files={
        "file": ("settings.json", io.BytesIO(json.dumps(export).encode()), "application/json")})

    body = reply.json()
    assert reply.status_code == 200
    assert body["applied_keys"] == ["job_timeout_minutes"]
    assert body["skipped_keys"] == ["no_such_setting"]
    assert list(body["invalid_keys"]) == ["keep_subtitle_languages"]
    assert "must be a list of text" in body["invalid_keys"]["keep_subtitle_languages"]
    assert _stored(factory, "keep_subtitle_languages") is None
    assert _stored(factory, "job_timeout_minutes") == 30


# ── Reading ──────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("key, stored", [
    ("keep_subtitle_languages", "eng"),
    ("prefer_mp4_container", "false"),
    ("job_timeout_minutes", "120"),
])
def test_a_stored_value_of_the_wrong_kind_reads_as_the_default(factory, key, stored):
    """What a full backup restore or a hand edit can leave."""
    _store_raw(factory, key, stored)

    with factory() as db:
        assert get_app_settings(db)[key] == DEFAULT_APP_SETTINGS[key]


def test_a_list_of_numbers_reads_as_the_default(factory):
    _store_raw(factory, "keep_audio_languages", [1, 2])

    with factory() as db:
        assert get_app_settings(db)["keep_audio_languages"] == ["eng"]


def test_true_for_a_number_reads_as_the_default(factory):
    """
    A setting whose default is not 1, since true == 1 in Python, and a
    setting defaulting to 1 could not tell true from its default.
    """
    _store_raw(factory, "job_timeout_minutes", True)

    with factory() as db:
        assert get_app_settings(db)["job_timeout_minutes"] == 120


def test_a_good_stored_value_is_read_as_it_is(factory):
    _store_raw(factory, "keep_subtitle_languages", ["jpn"])

    with factory() as db:
        assert get_app_settings(db)["keep_subtitle_languages"] == ["jpn"]


def test_a_value_of_the_wrong_kind_is_warned_about_once(factory, caplog):
    _store_raw(factory, "keep_subtitle_languages", "eng")

    with caplog.at_level(logging.WARNING, logger="app.database.session"):
        with factory() as db:
            get_app_settings(db)
            get_app_settings(db)

    warnings = [r for r in caplog.records if "keep_subtitle_languages" in r.getMessage()]
    assert len(warnings) == 1


def test_an_old_true_or_false_for_fix_undefined_language_can_be_saved_over(client, factory):
    """
    fix_undefined_language was true or false before it took three values,
    and nothing converted the stored one. Read as its default, which is
    also how the engine treated it, the Settings page's save, which sends
    every setting it read, goes through.
    """
    _store_raw(factory, "fix_undefined_language", False)

    current = client.get("/api/settings/").json()
    assert current["fix_undefined_language"] == "always_leave"
    shown = {entry["key"]: current[entry["key"]] for entry in SETTINGS_SCHEMA}

    assert client.put("/api/settings/", json=shown).status_code == 200
    assert _stored(factory, "fix_undefined_language") == "always_leave"
