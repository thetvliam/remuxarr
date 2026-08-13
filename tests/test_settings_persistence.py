"""
session.py — the settings read/write path.

WHY THIS FILE EXISTS
--------------------
get_app_settings and update_app_setting are the read and write halves of every
setting in the app: the decision engine's whole configuration arrives through
get_app_settings, and every Settings-page save lands in update_app_setting.
Between them they had no direct tests — mutation testing found the write path
completely unprotected (always-insert-never-update, and storing values raw
instead of JSON-encoded both survived the full suite).

THE DEEPCOPY
  get_app_settings' docstring records that six defaults are mutable — two lists
  of language codes and four empty lists. A shallow copy hands every caller the
  same list object that lives in module-level DEFAULT_APP_SETTINGS, so a single
  cfg["keep_audio_languages"].append(...) would corrupt the default for the
  lifetime of the process, for every later request and every worker job. The
  docstring is explicit that nothing does this today and that the deep copy
  exists to make it impossible rather than merely unexercised — which is
  precisely the kind of guarantee that needs a test, because no feature will
  ever fail if it is removed.

THE JSON ROUND TRIP
  Values are stored JSON-encoded and read back with json.loads. Booleans and
  lists are the cases that matter: str(True) is "True" and str(["eng"]) uses
  single quotes, neither of which is valid JSON, so both degrade to the raw
  string on read. A list setting silently becoming the string "['eng']" is not
  something the decision engine can detect — it would iterate the characters.
"""
import json

import pytest

from app.database.models import AppSetting, Base
from app.database.session import (
    DEFAULT_APP_SETTINGS,
    get_app_settings,
    update_app_setting,
)


@pytest.fixture
def db():
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)()


# ── Reading ──────────────────────────────────────────────────────────────────

def test_an_empty_database_yields_the_defaults(db):
    cfg = get_app_settings(db)
    assert cfg["keep_audio_languages"] == DEFAULT_APP_SETTINGS["keep_audio_languages"]
    assert cfg["und_audio_threshold"] == DEFAULT_APP_SETTINGS["und_audio_threshold"]


def test_a_stored_row_overrides_the_default(db):
    db.add(AppSetting(key="und_audio_threshold", value=json.dumps(7)))
    db.commit()

    assert get_app_settings(db)["und_audio_threshold"] == 7


def test_unset_keys_still_fall_back_while_others_are_overridden(db):
    db.add(AppSetting(key="und_audio_threshold", value=json.dumps(7)))
    db.commit()

    cfg = get_app_settings(db)
    assert cfg["und_audio_threshold"] == 7
    assert cfg["keep_audio_languages"] == DEFAULT_APP_SETTINGS["keep_audio_languages"]


def test_a_corrupt_value_degrades_to_the_raw_string_rather_than_raising(db):
    """
    A half-written or hand-edited row must not take the whole app down: every
    request that reads settings goes through here, so raising makes the app
    unbootable rather than misconfigured.
    """
    db.add(AppSetting(key="undefined_language_value", value="not-valid-json{"))
    db.commit()

    cfg = get_app_settings(db)
    assert cfg["undefined_language_value"] == "not-valid-json{"


# ── The deepcopy guarantee ───────────────────────────────────────────────────

def test_mutating_a_returned_list_does_not_corrupt_the_module_defaults(db):
    """
    The scenario the docstring describes. Without deepcopy the returned list
    IS DEFAULT_APP_SETTINGS' list, so this append would persist for the
    lifetime of the process and leak into every later caller.
    """
    original = list(DEFAULT_APP_SETTINGS["keep_audio_languages"])

    cfg = get_app_settings(db)
    cfg["keep_audio_languages"].append("zzz")

    assert DEFAULT_APP_SETTINGS["keep_audio_languages"] == original
    assert "zzz" not in DEFAULT_APP_SETTINGS["keep_audio_languages"]


def test_two_callers_do_not_share_mutable_values(db):
    first = get_app_settings(db)
    second = get_app_settings(db)

    first["keep_subtitle_languages"].append("zzz")

    assert "zzz" not in second["keep_subtitle_languages"]
    assert first["keep_subtitle_languages"] is not second["keep_subtitle_languages"]


@pytest.mark.parametrize("key", [
    "keep_audio_languages",
    "keep_subtitle_languages",
    "scan_paths",
    "plex_path_mappings",
    "email_recipients",
    "scheduled_scan_times",
])
def test_every_mutable_default_is_returned_as_a_fresh_object(db, key):
    """
    Named in the docstring as the six mutable defaults. Parametrised so a
    seventh added later without a copy fails here by name.
    """
    assert get_app_settings(db)[key] is not DEFAULT_APP_SETTINGS[key]


# ── Writing ──────────────────────────────────────────────────────────────────

def test_a_new_setting_is_inserted_and_reads_back(db):
    update_app_setting(db, "und_audio_threshold", 5)

    assert get_app_settings(db)["und_audio_threshold"] == 5


def test_writing_the_same_key_twice_updates_rather_than_duplicating(db):
    """
    key is the primary key, so a second insert raises rather than shadowing —
    but only when something actually saves the same setting twice, which no
    other test did.
    """
    update_app_setting(db, "und_audio_threshold", 5)
    update_app_setting(db, "und_audio_threshold", 9)

    assert db.query(AppSetting).filter_by(key="und_audio_threshold").count() == 1
    assert get_app_settings(db)["und_audio_threshold"] == 9


@pytest.mark.parametrize("key,value", [
    ("und_audio_threshold",    3),
    ("keep_default_audio",     True),
    ("prefer_mp4_container",   False),
    ("keep_audio_languages",   ["eng", "jpn"]),
    ("scan_paths",             []),
    ("undefined_language_value", "eng"),
    ("fix_undefined_language", "always_leave"),
])
def test_values_survive_the_round_trip_with_their_type_intact(db, key, value):
    """
    str() instead of json.dumps() is the failure mode here, and it is silent:
    True becomes "True" and ["eng"] becomes "['eng']", neither of which parses
    back as JSON, so both degrade to a raw string. A list setting that has
    become a string is then iterated character by character downstream.
    """
    update_app_setting(db, key, value)

    read_back = get_app_settings(db)[key]
    assert read_back == value
    assert type(read_back) is type(value)


def test_a_stored_boolean_is_not_a_string(db):
    update_app_setting(db, "keep_default_audio", False)

    value = get_app_settings(db)["keep_default_audio"]
    assert value is False
    assert value != "False"      # the str() failure mode is truthy


@pytest.mark.parametrize("key,first,second", [
    ("keep_default_audio",   True,           False),
    ("keep_audio_languages", ["eng"],        ["eng", "jpn"]),
    ("scan_paths",           ["/media/tv"],  []),
])
def test_overwriting_an_existing_setting_preserves_the_type_too(db, key, first, second):
    """
    update_app_setting has two branches — insert and update — and they encode
    the value independently. Writing each key once only ever exercises the
    insert branch, so the update branch can silently stop JSON-encoding
    without any single-write test noticing.
    """
    update_app_setting(db, key, first)
    update_app_setting(db, key, second)

    read_back = get_app_settings(db)[key]
    assert read_back == second
    assert type(read_back) is type(second)


# ── Seeding ──────────────────────────────────────────────────────────────────
#
# The Phase 3 audit flagged _seed_defaults as still untested: its own mutation
# (SES-06) was a no-op — `list(x) * 1` is `list(x)` — so its SURVIVED result
# said nothing, and the guard was never actually mutated. These pin the guard
# it was aiming at.

def test_seeding_writes_every_default(db):
    from app.database.session import _seed_defaults

    _seed_defaults(db)

    assert db.query(AppSetting).count() == len(DEFAULT_APP_SETTINGS)


def test_seeded_values_are_json_encoded(db):
    """
    Same contract as update_app_setting's, and a separate code path that
    encodes independently — a list seeded via str() reads back as the string
    "['eng']" and is then iterated character by character downstream.
    """
    from app.database.session import _seed_defaults

    _seed_defaults(db)

    cfg = get_app_settings(db)
    assert cfg["keep_audio_languages"] == DEFAULT_APP_SETTINGS["keep_audio_languages"]
    assert isinstance(cfg["keep_audio_languages"], list)
    assert cfg["keep_default_audio"] is DEFAULT_APP_SETTINGS["keep_default_audio"]


def test_seeding_never_overwrites_a_value_the_user_has_set(db):
    """
    THE guard. _seed_defaults runs at startup, so without the `is None` check
    every container restart would silently reset the user's entire
    configuration back to defaults — and nothing would report it, because
    resetting to a valid default looks exactly like a working app.
    """
    update_app_setting(db, "und_audio_threshold", 9)

    from app.database.session import _seed_defaults
    _seed_defaults(db)

    assert get_app_settings(db)["und_audio_threshold"] == 9, (
        "startup seeding overwrote a user-configured setting"
    )


def test_seeding_is_idempotent(db):
    """
    key is the primary key, so a second unguarded insert raises rather than
    duplicating — which on the startup path means the app fails to boot.
    """
    from app.database.session import _seed_defaults

    _seed_defaults(db)
    _seed_defaults(db)

    assert db.query(AppSetting).count() == len(DEFAULT_APP_SETTINGS)


def test_seeding_fills_only_the_gaps(db):
    """A partially-populated table gets the missing keys and keeps the rest."""
    update_app_setting(db, "und_audio_threshold", 9)

    from app.database.session import _seed_defaults
    _seed_defaults(db)

    cfg = get_app_settings(db)
    assert cfg["und_audio_threshold"] == 9
    assert cfg["keep_audio_languages"] == DEFAULT_APP_SETTINGS["keep_audio_languages"]
    assert db.query(AppSetting).count() == len(DEFAULT_APP_SETTINGS)
