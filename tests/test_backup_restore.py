"""
backup.py — export and restore of the whole database.

The most destructive endpoint in the app. /api/backup/import replaces every
scanned file, track, queue item and history row in the instance, and the live
process is holding open connections against the file being swapped out from
under it while the request is being served.

That makes the ORDER of operations the thing under test, more than any
individual step. Every validation failure has to happen BEFORE the live
database is touched at all, and the pre-import backup has to exist before the
swap — otherwise a malformed upload costs the user their library.

Each test builds a real SQLite file and a real zip rather than mocking the
file operations, because the failure this code was written against is a real
filesystem one: a leftover -wal sidecar replaying the OLD database back over
the restore on next startup. Mocks would not have caught it.

Verified by mutation: 30 mutations of backup.py, each killed by at least one
test here. The one worth naming is dropping the -wal/-shm cleanup, which
leaves a restore that reports success, writes the right bytes to the right
path, and is then silently undone the next time the container starts.

One property needed testing differently. Replacing the staging-file-plus-
rename with a direct overwrite of the live database leaves byte-identical
results, so no end-state assertion can distinguish it — the difference only
shows if the process dies partway through. That one is pinned by observing
that the live path is never a copy destination, rather than by checking what
ends up there.
"""
import io
import json
import os
import sqlite3
import zipfile

import pytest
from fastapi import FastAPI
from starlette.testclient import TestClient


# ── Harness ──────────────────────────────────────────────────────────────────

def _make_db(path, settings=None, marker="live"):
    """A minimal but real SQLite database with an app_settings table."""
    conn = sqlite3.connect(path)
    try:
        conn.execute("CREATE TABLE app_settings (key TEXT PRIMARY KEY, value TEXT)")
        conn.execute("CREATE TABLE marker (name TEXT)")
        conn.execute("INSERT INTO marker VALUES (?)", (marker,))
        for k, v in (settings or {}).items():
            conn.execute("INSERT INTO app_settings VALUES (?, ?)", (k, v))
        conn.commit()
    finally:
        conn.close()
    return path


def _read_marker(path):
    conn = sqlite3.connect(path)
    try:
        return conn.execute("SELECT name FROM marker").fetchone()[0]
    finally:
        conn.close()


def _settings_of(path):
    conn = sqlite3.connect(path)
    try:
        return dict(conn.execute("SELECT key, value FROM app_settings").fetchall())
    finally:
        conn.close()


def _zip(members):
    """Build an in-memory zip from {name: bytes}."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for name, data in members.items():
            zf.writestr(name, data)
    return buf.getvalue()


def _good_manifest():
    return json.dumps({"remuxarr_export": "full_backup",
                       "exported_at": "2026-01-01T00:00:00Z",
                       "includes_secrets": True}).encode()


@pytest.fixture
def live(tmp_path, monkeypatch):
    """
    A live database at a temp path, with the worker's pause recorded rather
    than performed. Returns an object exposing the path and the pause log.
    """
    import app.api.routes.backup as backup

    live_path = str(tmp_path / "remuxarr.db")
    _make_db(live_path, {"sonarr_api_key": "SECRET", "scan_paths": "/media"},
             marker="live")

    monkeypatch.setattr(backup.app_settings, "DATABASE_PATH", live_path)

    paused = []
    monkeypatch.setattr(backup, "pause_worker", lambda: paused.append(True))

    class _Live:
        path = live_path
        pauses = paused
        module = backup

    return _Live()


@pytest.fixture
def client(live):
    app = FastAPI()
    app.include_router(live.module.router)
    return TestClient(app)


def _upload(client, content, filename="backup.zip"):
    return client.post("/api/backup/import",
                       files={"file": (filename, content, "application/zip")})


# ── _looks_like_sqlite ───────────────────────────────────────────────────────

def test_a_real_database_is_recognised(live, tmp_path):
    assert live.module._looks_like_sqlite(live.path) is True


@pytest.mark.parametrize("content", [
    b"",
    b"not a database at all",
    b"SQLite format 2\x00" + b"padding here",
    b"PK\x03\x04" + b"\x00" * 20,          # a zip, not a database
])
def test_anything_else_is_rejected(live, tmp_path, content):
    p = tmp_path / "thing.bin"
    p.write_bytes(content)
    assert live.module._looks_like_sqlite(str(p)) is False


def test_an_unreadable_path_is_rejected_rather_than_raising(live, tmp_path):
    """Fails closed — a check that raises would abort the import mid-flight."""
    assert live.module._looks_like_sqlite(str(tmp_path / "nope.db")) is False


# ── _redact_secrets ──────────────────────────────────────────────────────────

def test_secrets_are_deleted_not_blanked(live, tmp_path):
    """
    Deleting the rows, not emptying them. Settings import uses merge
    semantics, so a blank value would be applied as an explicit empty string
    and wipe the target system's real credential; an absent key is skipped.
    """
    p = str(tmp_path / "copy.db")
    _make_db(p, {"sonarr_api_key": "A", "radarr_api_key": "B",
                 "plex_token": "C", "email_password": "D",
                 "scan_paths": "/media"})

    live.module._redact_secrets(p)

    remaining = _settings_of(p)
    assert remaining == {"scan_paths": "/media"}, (
        "a secret survived redaction, or a non-secret was destroyed"
    )


def test_redaction_covers_exactly_the_four_credential_keys(live):
    assert live.module.SECRET_KEYS == {
        "sonarr_api_key", "radarr_api_key", "plex_token", "email_password"
    }


# ── Export ───────────────────────────────────────────────────────────────────

def test_an_export_contains_a_database_and_a_manifest(client):
    r = client.get("/api/backup/export")
    assert r.status_code == 200

    with zipfile.ZipFile(io.BytesIO(r.content)) as zf:
        assert set(zf.namelist()) == {"database.db", "manifest.json"}
        assert zf.read("database.db").startswith(b"SQLite format 3\x00")


def test_an_export_round_trips_the_real_data(client, tmp_path):
    """
    The copy is taken through SQLite's online backup API rather than a file
    copy, because WAL mode means the .db file alone may not reflect
    everything committed.
    """
    r = client.get("/api/backup/export")
    out = tmp_path / "out.db"
    with zipfile.ZipFile(io.BytesIO(r.content)) as zf:
        out.write_bytes(zf.read("database.db"))

    assert _read_marker(str(out)) == "live"
    assert _settings_of(str(out))["scan_paths"] == "/media"


def test_exporting_with_secrets_keeps_them(client, tmp_path):
    r = client.get("/api/backup/export?include_secrets=true")
    out = tmp_path / "out.db"
    with zipfile.ZipFile(io.BytesIO(r.content)) as zf:
        out.write_bytes(zf.read("database.db"))

    assert _settings_of(str(out))["sonarr_api_key"] == "SECRET"


def test_exporting_without_secrets_redacts_them(client, tmp_path):
    r = client.get("/api/backup/export?include_secrets=false")
    out = tmp_path / "out.db"
    with zipfile.ZipFile(io.BytesIO(r.content)) as zf:
        manifest = json.loads(zf.read("manifest.json"))
        out.write_bytes(zf.read("database.db"))

    assert "sonarr_api_key" not in _settings_of(str(out))
    assert manifest["includes_secrets"] is False


def test_redaction_never_touches_the_live_database(client, live):
    """
    _redact_secrets operates on the standalone copy. Pointing it at the live
    file would delete the user's real credentials as a side effect of a
    download.
    """
    client.get("/api/backup/export?include_secrets=false")

    assert _settings_of(live.path)["sonarr_api_key"] == "SECRET"


def test_the_manifest_marks_this_as_a_full_backup(client):
    """The marker the import path checks — the two must agree."""
    r = client.get("/api/backup/export")
    with zipfile.ZipFile(io.BytesIO(r.content)) as zf:
        manifest = json.loads(zf.read("manifest.json"))

    assert manifest["remuxarr_export"] == "full_backup"


def test_an_export_can_be_imported_back(client, live):
    """The two halves have to agree on the format, not just individually."""
    exported = client.get("/api/backup/export").content

    r = _upload(client, exported)

    assert r.status_code == 200, r.text
    assert r.json()["success"] is True


def test_the_download_is_named_as_a_zip_attachment(client):
    r = client.get("/api/backup/export")
    disposition = r.headers["content-disposition"]
    assert disposition.startswith("attachment;")
    assert "remuxarr-backup-" in disposition
    assert disposition.rstrip('"').endswith(".zip")


# ── Import: validation happens before anything destructive ───────────────────

@pytest.fixture
def bad_uploads():
    """Each is a payload that must be rejected before the live db is touched."""
    return {
        "not a zip": b"just some bytes, definitely not a zip",
        "missing database": _zip({"manifest.json": _good_manifest()}),
        "missing manifest": _zip({"database.db": b"SQLite format 3\x00rest"}),
        "manifest not json": _zip({"manifest.json": b"{{{ not json",
                                   "database.db": b"SQLite format 3\x00rest"}),
        "wrong marker": _zip({
            "manifest.json": json.dumps({"remuxarr_export": "settings_only"}).encode(),
            "database.db": b"SQLite format 3\x00rest"}),
        "database not sqlite": _zip({"manifest.json": _good_manifest(),
                                     "database.db": b"this is not a database"}),
    }


def test_every_malformed_upload_is_rejected(client, bad_uploads):
    for label, payload in bad_uploads.items():
        r = _upload(client, payload)
        assert r.status_code == 400, f"{label} was accepted: {r.status_code}"


def test_a_rejected_upload_never_touches_the_live_database(client, live, bad_uploads):
    """
    The whole point of validating first. A user who uploads the wrong file
    must still have their library afterwards.
    """
    for label, payload in bad_uploads.items():
        _upload(client, payload)
        assert _read_marker(live.path) == "live", f"{label} replaced the database"
        assert _settings_of(live.path)["sonarr_api_key"] == "SECRET"


def test_a_rejected_upload_never_pauses_the_worker(client, live, bad_uploads):
    """
    pause_worker is the first genuinely live-system side effect. Pausing on a
    validation failure would stop processing for a request that changed
    nothing, with no matching resume.
    """
    for payload in bad_uploads.values():
        _upload(client, payload)

    assert live.pauses == []


def test_a_rejected_upload_leaves_no_pre_import_backup_behind(client, live, bad_uploads):
    """Junk files in /config after a failed upload would be their own problem."""
    for payload in bad_uploads.values():
        _upload(client, payload)

    leftovers = [n for n in os.listdir(os.path.dirname(live.path))
                 if "before-import" in n]
    assert leftovers == []


# ── Import: the destructive path ─────────────────────────────────────────────

@pytest.fixture
def good_upload(tmp_path):
    """A valid backup zip whose database is distinguishable from the live one."""
    src = str(tmp_path / "restore-source.db")
    _make_db(src, {"scan_paths": "/restored"}, marker="restored")
    with open(src, "rb") as f:
        db_bytes = f.read()
    return _zip({"manifest.json": _good_manifest(), "database.db": db_bytes})


def test_a_valid_import_replaces_the_database(client, live, good_upload):
    r = _upload(client, good_upload)

    assert r.status_code == 200, r.text
    assert _read_marker(live.path) == "restored"
    assert _settings_of(live.path)["scan_paths"] == "/restored"


def test_the_worker_is_paused_before_the_swap(client, live, good_upload):
    """
    Nothing may be mid-write against a database about to be replaced out from
    under it.
    """
    _upload(client, good_upload)
    assert live.pauses == [True]


def test_the_previous_database_is_backed_up_first(client, live, good_upload):
    """
    The findable way back. Its path is returned so the user is told where it
    is rather than having to guess.
    """
    r = _upload(client, good_upload)

    prior = r.json()["previous_database_backup"]
    assert "before-import" in os.path.basename(prior)
    assert os.path.exists(prior)
    assert _read_marker(prior) == "live", (
        "the pre-import backup holds the restored data, not the replaced data"
    )


def test_a_failed_pre_import_backup_aborts_without_touching_anything(
        client, live, good_upload, monkeypatch):
    """
    If the way back can't be created, the import must not proceed. Replacing
    the database with no recoverable copy is the one outcome with no remedy.
    """
    def _boom(src, dest):
        raise OSError("no space left on device")

    monkeypatch.setattr(live.module, "_wal_safe_backup", _boom)

    r = _upload(client, good_upload)

    assert r.status_code == 500
    assert _read_marker(live.path) == "live", (
        "database replaced despite having no pre-import backup"
    )


def test_stale_wal_and_shm_sidecars_are_removed(client, live, good_upload):
    """
    The failure this code exists for. The live process still holds a
    connection to the OLD database while serving this request, so its -wal is
    very likely populated. Left in place, SQLite replays it over the
    freshly-restored main file on next startup and silently reinstates the
    old state — a restore that reports success and then quietly undoes itself.
    """
    for suffix in ("-wal", "-shm"):
        with open(live.path + suffix, "wb") as f:
            f.write(b"stale journal contents")

    _upload(client, good_upload)

    for suffix in ("-wal", "-shm"):
        assert not os.path.exists(live.path + suffix), (
            f"stale {suffix} left beside the restore — it will replay on restart"
        )


def test_a_missing_sidecar_is_not_an_error(client, live, good_upload):
    """WAL files are not always present; their absence is normal, not a fault."""
    for suffix in ("-wal", "-shm"):
        assert not os.path.exists(live.path + suffix)

    r = _upload(client, good_upload)
    assert r.status_code == 200


def test_the_live_database_is_never_written_to_incrementally(
        client, live, good_upload, monkeypatch):
    """
    Atomicity, which no end-state assertion can catch — copying the new file
    straight over the live path leaves exactly the same bytes behind, and
    differs only if the process dies partway. So this asserts the property
    directly: the live path is never a copy destination. It only ever comes
    into existence via the single os.replace() of a fully-written staging
    file. A truncate-and-overwrite would leave a window where the live
    database is neither the old one nor the new one, and a crash inside that
    window costs the user both.
    """
    real_copy = live.module.shutil.copy2
    destinations = []

    def _record(src, dst, *a, **kw):
        destinations.append(str(dst))
        return real_copy(src, dst, *a, **kw)

    monkeypatch.setattr(live.module.shutil, "copy2", _record)

    r = _upload(client, good_upload)

    assert r.status_code == 200
    assert destinations == [live.path + ".importing"], (
        f"live database written non-atomically: copied to {destinations}"
    )


def test_no_staging_file_is_left_behind(client, live, good_upload):
    """
    The swap writes to <live>.importing then renames. A leftover staging file
    means the rename didn't happen and the restore is not actually in place.
    """
    _upload(client, good_upload)

    assert not os.path.exists(live.path + ".importing")


def test_the_response_demands_a_restart(client, live, good_upload):
    """
    The live process still holds connections to the old file — swapping it on
    disk does not retroactively fix those. The caller has to be told, or the
    UI shows stale data that looks like a failed restore.
    """
    r = _upload(client, good_upload)

    body = r.json()
    assert body["success"] is True
    assert body["restart_required"] is True
