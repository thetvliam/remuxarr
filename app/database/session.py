"""
Database session management and settings helpers.
"""
import json
import logging
from pathlib import Path
from typing import Any, Generator

from sqlalchemy import create_engine, inspect, text
from sqlalchemy.orm import Session, sessionmaker

from app.config import settings
from app.database.models import AppSetting, Base

logger = logging.getLogger(__name__)

# ── Default user-configurable settings (seeded on first run) ─────────────────
DEFAULT_APP_SETTINGS: dict[str, Any] = {
    # Which audio language codes to keep (ISO 639-2/B)
    "keep_audio_languages": ["eng"],
    # Which subtitle language codes to keep
    "keep_subtitle_languages": ["eng"],
    # Always keep forced subtitle tracks regardless of language
    "keep_forced_subtitles": True,
    # Keep the default-flagged audio track even if its language isn't in the list
    "keep_default_audio": True,
    # Transcode AAC 5.1 → AC3 5.1 for AVR passthrough compatibility
    "transcode_aac_51_to_ac3": True,
    # Remux to MP4 when all tracks are container-compatible
    "prefer_mp4_container": True,
    # Global dry-run toggle — no files are modified when True
    "dry_run_mode": False,
    # Directories to scan (populated via UI or env)
    "scan_paths": [],
    # How many "und" audio tracks trigger a manual-review flag
    "und_audio_threshold": 2,
    # Extract kept text-based subtitle tracks (SubRip, mov_text, ASS/SSA) to
    # an external .srt sidecar file (Plex naming: Movie.en.srt /
    # Movie.en.forced.srt) and remove them from the muxed output. Kept
    # image-based subtitles (PGS, VOBSUB, DVD/DVB) can't be converted —
    # see image_subtitle_handling below for how that's resolved.
    "extract_text_subtitles_to_srt": True,
    # What to do with a KEPT image-based subtitle track (PGS, VOBSUB,
    # DVD/DVB) when extraction above is enabled and it can't be converted:
    #   "always_ask"    — flag for manual review (the original, and still
    #                      the default, behavior)
    #   "always_keep"    — leave it embedded, no review needed
    #   "always_remove"  — drop it, no review needed
    "image_subtitle_handling": "always_ask",
    # Detect MP4 files missing the moov atom at the front (i.e. not
    # web-optimised / fast-start) and rewrite them with -movflags +faststart
    # so that players and Plex can begin streaming before the full download.
    "add_faststart_to_mp4": True,
    # Maximum number of files processed simultaneously.
    "max_concurrent_jobs": 1,
    # When True (default), queued files are processed immediately after a scan.
    # When False, jobs queue up but the worker starts paused — the user must
    # click Resume on the dashboard to begin processing.
    "auto_start_jobs":          True,
    "job_timeout_minutes":      120,
    # ── Metadata ───────────────────────────────────────────────────────────
    "fix_undefined_language":   False,
    "undefined_language_value": "eng",
    "undefined_language_mode":  "all_undefined_per_type",
    # ── Plex ───────────────────────────────────────────────────────────────
    "plex_enabled":        False,
    "plex_url":             "",
    "plex_token":           "",
    # Each entry: "remuxarr_local_prefix=plex_container_prefix"
    # e.g. "/media/movies=/Media/Movies"
    "plex_path_mappings":   [],
    # ── Plex Analyze Backlog (separate opt-in — see settings.py) ────────────
    # Off by default. Confirmed via direct testing across a 1,300-item
    # backlog that Plex's own scheduled maintenance, combined with the
    # immediate refresh above, already catches the overwhelming majority
    # of reprocessed files on its own — this backlog only exists to catch
    # the rare remainder. Most installs will never need to turn this on;
    # it's here for large backfills or if files are noticed sitting with
    # stale Plex metadata longer than expected.
    "plex_analyze_backlog_enabled": False,
    # Reprocessed files (RE-PROCESS / retry / replaced-in-place) are queued
    # rather than analyzed immediately — drained only within this window,
    # one item every few seconds, so a large backfill doesn't burst-fire
    # hundreds of calls at once. Defaults roughly match Plex's own default
    # maintenance window (2 AM–5 AM). Irrelevant while the toggle above is
    # off.
    "plex_analyze_window_start": "02:00",
    "plex_analyze_window_end":   "06:00",
    # ── Email ──────────────────────────────────────────────────────────────
    "email_enabled":            False,
    "email_smtp_host":          "",
    "email_smtp_port":          587,
    "email_encryption":         "starttls",   # starttls | ssl | none
    "email_username":           "",
    "email_password":           "",
    "email_from":               "",
    "email_recipients":         [],
    # Consecutive failures before notifications pause. Protects against a
    # configuration mistake (bad codec, bad path, etc.) causing every queued
    # file to fail and flooding the inbox with hundreds of identical emails.
    "email_failure_threshold":  5,
    # ── Maintenance ────────────────────────────────────────────────────────
    "auto_cleanup_on_scan":   True,   # remove deleted-file DB rows after each scan
    "scheduled_scan_enabled": False,  # run library scans automatically
    "scheduled_scan_times":   [],     # list of "HH:MM" times in 24-hour format
    # ── Sonarr integration ─────────────────────────────────────────────────
    # When enabled, Remuxarr accepts On Import / On Upgrade webhooks from
    # Sonarr and — after a job completes — calls Sonarr's RescanSeries so
    # Sonarr picks up the processed file. RenameFiles is NOT called (it was
    # in an earlier version — see sonarr.py's own module docstring for why
    # it was removed).
    "sonarr_enabled": False,
    "sonarr_url":     "",   # e.g. http://sonarr:8989
    "sonarr_api_key": "",   # Settings → General → API Key
    # Path mapping: Sonarr's container may see a different root path than
    # Remuxarr's container for the same files on disk. Set these when they
    # differ — e.g. Sonarr reports /media/... but Remuxarr sees /media/tv/...
    "sonarr_path_prefix_remote": "",   # prefix in Sonarr's paths, e.g. /media
    "sonarr_path_prefix_local":  "",   # what that maps to on disk, e.g. /media/tv
    # ── Radarr integration ─────────────────────────────────────────────────
    "radarr_enabled":            False,
    "radarr_url":                "",   # e.g. http://radarr:7878
    "radarr_api_key":            "",   # Settings → General → API Key
    "radarr_path_prefix_remote": "",   # prefix in Radarr's paths, e.g. /media
    "radarr_path_prefix_local":  "",   # what that maps to on disk, e.g. /media/movies
}

# ── Engine ────────────────────────────────────────────────────────────────────
Path(settings.DATABASE_PATH).parent.mkdir(parents=True, exist_ok=True)

from sqlalchemy import event as _sa_event

engine = create_engine(
    f"sqlite:///{settings.DATABASE_PATH}",
    connect_args={
        "check_same_thread": False,
        # Wait up to 30 s for a write lock before raising OperationalError.
        # Without this, concurrent _finish_job calls from multiple jobs can
        # collide and immediately fail with "database is locked".
        "timeout": 30,
    },
    # SQLite only ever serves one writer at a time regardless of pool size —
    # a bigger pool doesn't buy real write parallelism. Its value here is
    # purely headroom: with many independent short-lived sessions now open
    # across the worker's per-job notification loaders, the Plex backlog
    # drain, and the scheduler, a burst of near-simultaneous activity can
    # occasionally need more than the default 5+10=15 slots even though
    # each individual session is short-lived and well-behaved. Raising this
    # doesn't fix contention — see _load_post_job_data's docstring for the
    # actual fix — it just gives more room before hitting the ceiling.
    pool_size=10,
    max_overflow=20,
    echo=settings.DEBUG,
)


@_sa_event.listens_for(engine, "connect")
def _set_sqlite_pragmas(dbapi_connection, _connection_record):
    """
    Apply per-connection SQLite PRAGMAs that improve concurrent write safety.

    WAL (Write-Ahead Log) journal mode
    ------------------------------------
    SQLite's default journal mode ("DELETE") requires exclusive file locks
    while writing, so any concurrent reader or writer is blocked.  WAL mode
    separates readers from writers: readers never block writers, writers
    never block readers.  Multiple concurrent jobs can read freely while
    one job is committing without hitting "database is locked".

    NORMAL synchronous mode
    -----------------------
    The default FULL mode calls fsync() after every commit, which is safe
    but adds latency.  NORMAL mode skips some fsyncs that are redundant when
    WAL is active, giving better write throughput with no practical loss of
    durability for our use case (worst case: last transaction lost on power
    failure, not corruption).
    """
    cursor = dbapi_connection.cursor()
    cursor.execute("PRAGMA journal_mode=WAL")
    cursor.execute("PRAGMA synchronous=NORMAL")
    cursor.close()

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


# ── Initialization ─────────────────────────────────────────────────────────
def init_db() -> None:
    """Create all tables, run lightweight migrations, and seed default settings."""
    Base.metadata.create_all(bind=engine)
    _migrate_schema()
    with SessionLocal() as db:
        _seed_defaults(db)
    logger.info("Database ready: %s", settings.DATABASE_PATH)


def _migrate_schema() -> None:
    """
    Add columns introduced after the initial release to existing databases.

    Base.metadata.create_all() only creates missing TABLES — it never alters
    existing ones. SQLite supports simple ADD COLUMN migrations, so for each
    (table, column, DDL) below we check whether the column already exists and
    add it if not. Safe to run on every startup.
    """
    migrations = [
        ("tracks", "is_hearing_impaired",
         "ALTER TABLE tracks ADD COLUMN is_hearing_impaired BOOLEAN DEFAULT 0"),
        ("tracks", "is_dub",
         "ALTER TABLE tracks ADD COLUMN is_dub BOOLEAN DEFAULT 0"),
        ("media_files", "subtitle_overrides",
         "ALTER TABLE media_files ADD COLUMN subtitle_overrides TEXT"),
        ("media_files", "audio_language_overrides",
         "ALTER TABLE media_files ADD COLUMN audio_language_overrides TEXT"),
        ("media_files", "audio_language_ignored",
         "ALTER TABLE media_files ADD COLUMN audio_language_ignored BOOLEAN DEFAULT 0"),
        ("queue_items", "review_subtitles",
         "ALTER TABLE queue_items ADD COLUMN review_subtitles TEXT"),
        ("queue_items", "sonarr_series_id",
         "ALTER TABLE queue_items ADD COLUMN sonarr_series_id INTEGER"),
        ("queue_items", "radarr_movie_id",
         "ALTER TABLE queue_items ADD COLUMN radarr_movie_id INTEGER"),
        ("queue_items", "is_new_file",
         "ALTER TABLE queue_items ADD COLUMN is_new_file BOOLEAN DEFAULT 1"),
        ("planned_actions", "target_language",
         "ALTER TABLE planned_actions ADD COLUMN target_language TEXT"),
        ("plex_analyze_backlog", "expected_language",
         "ALTER TABLE plex_analyze_backlog ADD COLUMN expected_language TEXT"),
    ]

    inspector = inspect(engine)
    existing_tables = set(inspector.get_table_names())

    with engine.begin() as conn:
        for table, column, ddl in migrations:
            if table not in existing_tables:
                continue  # table created fresh by create_all() — already correct
            existing_columns = {c["name"] for c in inspector.get_columns(table)}
            if column not in existing_columns:
                logger.info("Migrating database: adding %s.%s", table, column)
                conn.execute(text(ddl))


def _seed_defaults(db: Session) -> None:
    for key, value in DEFAULT_APP_SETTINGS.items():
        if db.get(AppSetting, key) is None:
            db.add(AppSetting(key=key, value=json.dumps(value)))
    db.commit()


# ── Session helpers ───────────────────────────────────────────────────────────
def get_db() -> Generator[Session, None, None]:
    """FastAPI dependency — yields a DB session per request."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


# ── Settings helpers ──────────────────────────────────────────────────────────
def get_app_settings(db: Session) -> dict[str, Any]:
    """Load all app settings from DB into a plain dict."""
    rows = db.query(AppSetting).all()
    result = dict(DEFAULT_APP_SETTINGS)          # fall back to defaults
    for row in rows:
        try:
            result[row.key] = json.loads(row.value)
        except json.JSONDecodeError:
            result[row.key] = row.value          # store as raw string if broken
    return result


def update_app_setting(db: Session, key: str, value: Any) -> AppSetting:
    """Upsert a single setting value."""
    setting = db.get(AppSetting, key)
    if setting:
        setting.value = json.dumps(value)
    else:
        setting = AppSetting(key=key, value=json.dumps(value))
        db.add(setting)
    db.commit()
    db.refresh(setting)
    return setting
