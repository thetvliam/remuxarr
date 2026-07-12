"""
Database Backup & Restore API
==============================
GET  /api/backup/export  — full database backup, as a downloadable zip
POST /api/backup/import  — replace the live database with an uploaded backup

Distinct from /api/settings/export|import, which only ever touches the
app_settings table. This is the whole database — every scanned file,
every track, the full queue and history, Forge job history, everything.

The one thing this deliberately does NOT try to do: rewrite MediaFile
paths to match a different system's mount layout. A restored backup
assumes the target system uses the same container-side paths
(/media/movies, /media/tv, etc.) as the system it was exported from —
if it doesn't, the affected rows simply won't correspond to real files
on disk, and the existing Orphaned Files tool (Settings > Maintenance)
is exactly the mechanism to clean those up afterward. Deliberately not
building path-rewriting logic here — too much complexity and fragility
for what a much simpler, already-existing tool already handles safely.
"""
import json
import logging
import os
import shutil
import sqlite3
import tempfile
import zipfile
from datetime import datetime
from pathlib import Path

from fastapi import APIRouter, Body, Depends, File, HTTPException, Response, UploadFile
from sqlalchemy.orm import Session

from app.config import settings as app_settings
from app.core.worker import pause_worker
from app.database.session import get_db

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/backup", tags=["backup"])

# Same four genuine-credential keys as /api/settings/export — kept as a
# separate copy rather than importing from settings.py, since this module
# operates on the raw database file directly (including while the app
# isn't necessarily fully initialized, e.g. mid-restore) rather than
# through the ORM/settings-service layer that module uses.
SECRET_KEYS = {"sonarr_api_key", "radarr_api_key", "plex_token", "email_password"}


def _wal_safe_backup(source_path: str, dest_path: str) -> None:
    """
    Copy the live database using SQLite's own online backup API — NOT a
    raw file copy. This app runs SQLite in WAL mode, so the .db file on
    disk alone doesn't necessarily reflect everything that's been
    committed; a plain file copy risks capturing an inconsistent
    snapshot if anything is mid-write. The backup API is specifically
    designed to produce a complete, consistent copy even while other
    connections are actively reading/writing the source.
    """
    source = sqlite3.connect(f"file:{source_path}?mode=ro", uri=True)
    dest = sqlite3.connect(dest_path)
    try:
        source.backup(dest)
    finally:
        source.close()
        dest.close()


def _redact_secrets(db_path: str) -> None:
    """
    Remove the four secret app_settings rows from a database FILE — never
    the live database, always a standalone copy already produced by
    _wal_safe_backup. Deleting the rows entirely (not blanking their
    value) so a later import's merge semantics — see
    /api/settings/import — correctly treat them as simply absent, not as
    an explicit empty value to apply.
    """
    conn = sqlite3.connect(db_path)
    try:
        placeholders = ",".join("?" * len(SECRET_KEYS))
        conn.execute(
            f"DELETE FROM app_settings WHERE key IN ({placeholders})",
            tuple(SECRET_KEYS),
        )
        conn.commit()
    finally:
        conn.close()


def _looks_like_sqlite(path: str) -> bool:
    """Cheap, fast sanity check via the file's own magic header bytes —
    checked before ever trying to open something as a real database."""
    try:
        with open(path, "rb") as f:
            return f.read(16) == b"SQLite format 3\x00"
    except OSError:
        return False


@router.get("/export")
def export_backup(include_secrets: bool = True):
    """
    Full database backup as a downloadable zip — database.db (a WAL-safe
    copy, secrets redacted first if requested) plus manifest.json
    describing the export.
    """
    with tempfile.TemporaryDirectory() as tmp:
        db_copy_path = os.path.join(tmp, "database.db")
        _wal_safe_backup(app_settings.DATABASE_PATH, db_copy_path)

        if not include_secrets:
            _redact_secrets(db_copy_path)

        manifest = {
            "remuxarr_export": "full_backup",
            "exported_at": datetime.utcnow().isoformat() + "Z",
            "includes_secrets": include_secrets,
            "note": (
                "Restoring this on a different system assumes the same "
                "container-side media paths (/media/movies, /media/tv, "
                "etc.) as the system it was exported from. If they don't "
                "match, use Settings > Maintenance > Orphaned Files "
                "afterward to clean up any entries that no longer "
                "correspond to real files."
            ),
        }
        manifest_path = os.path.join(tmp, "manifest.json")
        with open(manifest_path, "w") as f:
            json.dump(manifest, f, indent=2)

        zip_path = os.path.join(tmp, "backup.zip")
        with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
            zf.write(db_copy_path, "database.db")
            zf.write(manifest_path, "manifest.json")

        with open(zip_path, "rb") as f:
            content = f.read()

    filename = f"remuxarr-backup-{datetime.utcnow().strftime('%Y%m%d-%H%M%S')}.zip"
    return Response(
        content=content,
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.post("/import")
async def import_backup(file: UploadFile = File(...)):
    """
    Replace the live database with one from a previously exported backup.

    Genuinely destructive — replaces every scanned file, track, queue
    item, and history entry currently in this instance. Safety measures,
    in order: the current database is backed up (via the same WAL-safe
    method) to a timestamped file in /config before anything is touched,
    so there's a real, findable way back if this goes wrong; the worker
    is paused immediately, before any file operation, so nothing can be
    mid-write against a database that's about to be replaced out from
    under it; the actual file swap is atomic (write to a temp path, then
    a single os.replace() into place — never a truncate-and-overwrite of
    the live file, which would leave a window where it's invalid).

    Does NOT attempt to make the new database take effect immediately.
    The live process already holds connections against the old file —
    swapping the file on disk doesn't retroactively fix those. The
    response tells the caller a restart is required; the frontend is
    responsible for making that unmistakable rather than silent.
    """
    with tempfile.TemporaryDirectory() as tmp:
        upload_path = os.path.join(tmp, "upload.zip")
        with open(upload_path, "wb") as f:
            f.write(await file.read())

        try:
            with zipfile.ZipFile(upload_path) as zf:
                names = zf.namelist()
                if "manifest.json" not in names or "database.db" not in names:
                    raise HTTPException(
                        400,
                        "This doesn't look like a Remuxarr backup — missing "
                        "manifest.json or database.db inside the zip.",
                    )
                zf.extractall(tmp)
        except zipfile.BadZipFile:
            raise HTTPException(400, "That file isn't a valid zip archive.")

        manifest_path = os.path.join(tmp, "manifest.json")
        try:
            with open(manifest_path) as f:
                manifest = json.load(f)
        except json.JSONDecodeError:
            raise HTTPException(400, "manifest.json inside the zip isn't valid JSON.")

        if manifest.get("remuxarr_export") != "full_backup":
            raise HTTPException(
                400,
                "This doesn't look like a Remuxarr full database backup "
                "(missing or incorrect 'remuxarr_export' marker in "
                "manifest.json).",
            )

        new_db_path = os.path.join(tmp, "database.db")
        if not _looks_like_sqlite(new_db_path):
            raise HTTPException(
                400,
                "database.db inside the zip doesn't look like a valid "
                "SQLite database.",
            )

        # From here on we're actually touching the live system — pause
        # first, before any file operation.
        pause_worker()

        live_path = app_settings.DATABASE_PATH
        pre_import_backup = os.path.join(
            os.path.dirname(live_path),
            f"remuxarr.db.before-import-{datetime.utcnow().strftime('%Y%m%d-%H%M%S')}",
        )
        try:
            _wal_safe_backup(live_path, pre_import_backup)
        except Exception:
            logger.exception("Failed to back up the current database before import")
            raise HTTPException(
                500,
                "Could not back up the current database before importing — "
                "aborted without touching anything, to be safe.",
            )

        # Atomic swap: write fully in place first, then one rename.
        staging_path = live_path + ".importing"
        shutil.copy2(new_db_path, staging_path)
        os.replace(staging_path, live_path)

        # Critical: also remove any stale WAL/SHM sidecar files left over
        # from the database we just replaced. The live app's own
        # connection to the OLD database is necessarily still open at
        # this exact moment — it's what's serving this very request — so
        # the old database's -wal file is very likely still populated
        # with recent writes (e.g. whatever Clear Database or a settings
        # change just wrote). Without removing it here, SQLite sees that
        # leftover WAL sitting next to the freshly-restored main file on
        # next startup and replays it, silently re-applying the OLD
        # state right back on top of the restore. Verified this exact
        # failure mode directly, and confirmed this fix resolves it,
        # before shipping it.
        for suffix in ("-wal", "-shm"):
            sidecar = live_path + suffix
            if os.path.exists(sidecar):
                os.remove(sidecar)
                logger.info("Removed stale %s file from the replaced database", suffix)

        logger.warning(
            "Database replaced via import. Previous database backed up to "
            "%s. A container restart is required for this to take effect.",
            pre_import_backup,
        )

    return {
        "success": True,
        "restart_required": True,
        "previous_database_backup": pre_import_backup,
    }
