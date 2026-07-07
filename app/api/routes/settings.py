"""
Settings API
============
GET  /api/settings        — all settings as a flat dict
GET  /api/settings/{key}  — single setting
PUT  /api/settings/{key}  — update single setting
PUT  /api/settings        — bulk update (body = {key: value, ...})

Values are arbitrary JSON (string, list, bool, int).
"""
import json
import urllib.error
import urllib.request
from typing import Any

from fastapi import APIRouter, Body, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.database.session import get_app_settings, get_db, update_app_setting

router = APIRouter(prefix="/api/settings", tags=["settings"])


class SettingValue(BaseModel):
    value: Any


def _test_arr_connection(url: str, api_key: str, app_name_fallback: str) -> dict:
    """
    Call /api/v3/system/status on an *arr instance and return a standard
    {success, version, app} / {success, error} dict.  Shared by the
    test-sonarr and test-radarr endpoints, which were previously identical
    apart from the settings keys they read and the appName fallback string.
    """
    if not url or not api_key:
        return {"success": False, "error": "URL or API key not configured"}
    try:
        req = urllib.request.Request(
            f"{url}/api/v3/system/status",
            headers={"X-Api-Key": api_key},
        )
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read())
        return {
            "success": True,
            "version": data.get("version", "?"),
            "app":     data.get("appName", app_name_fallback),
        }
    except urllib.error.HTTPError as e:
        return {"success": False, "error": f"HTTP {e.code}: {e.reason}"}
    except urllib.error.URLError as e:
        return {"success": False, "error": f"Connection failed: {e.reason}"}
    except Exception as e:
        return {"success": False, "error": str(e)}


@router.get("/")
def get_all(db: Session = Depends(get_db)):
    """Return all settings (merged with defaults for any missing keys)."""
    return get_app_settings(db)


@router.get("/schema")
def get_schema():
    """
    Return a UI-friendly schema so the frontend can render the config page
    without hard-coding field types.
    """
    return SETTINGS_SCHEMA


# PUT / must be declared before PUT /{key} so FastAPI doesn't swallow the
# bulk endpoint as a single-key update with key="".
@router.put("/")
def update_bulk(
    updates: dict[str, Any] = Body(..., description="Map of setting key → new value"),
    db: Session = Depends(get_db),
):
    """Update multiple settings in one request."""
    for key in updates:
        _validate_key(key)
    for key, value in updates.items():
        update_app_setting(db, key, value)
    return updates


@router.get("/test-sonarr")
def test_sonarr(db: Session = Depends(get_db)):
    """Test the configured Sonarr connection by calling /api/v3/system/status."""
    cfg = get_app_settings(db)
    return _test_arr_connection(
        (cfg.get("sonarr_url") or "").rstrip("/"),
        cfg.get("sonarr_api_key") or "",
        "Sonarr",
    )


@router.get("/test-radarr")
def test_radarr(db: Session = Depends(get_db)):
    """Test the configured Radarr connection by calling /api/v3/system/status."""
    cfg = get_app_settings(db)
    return _test_arr_connection(
        (cfg.get("radarr_url") or "").rstrip("/"),
        cfg.get("radarr_api_key") or "",
        "Radarr",
    )


@router.get("/test-plex")
def test_plex(db: Session = Depends(get_db)):
    """Test the configured Plex connection by calling /identity."""
    from app.core.plex import test_plex_connection
    cfg = get_app_settings(db)
    return test_plex_connection(
        (cfg.get("plex_url") or "").rstrip("/"),
        cfg.get("plex_token") or "",
    )


@router.get("/test-email")
def test_email(db: Session = Depends(get_db)):
    """Send a real test email to confirm SMTP settings work."""
    from app.core.email_notify import test_email_connection
    cfg = get_app_settings(db)
    return test_email_connection(cfg)



@router.get("/{key}")
def get_one(key: str, db: Session = Depends(get_db)):
    cfg = get_app_settings(db)
    if key not in cfg:
        raise HTTPException(404, f"Unknown setting: {key!r}")
    return {"key": key, "value": cfg[key]}


@router.put("/{key}")
def update_one(key: str, body: SettingValue, db: Session = Depends(get_db)):
    """Update a single setting."""
    _validate_key(key)
    setting = update_app_setting(db, key, body.value)
    return {"key": setting.key, "value": body.value}


@router.post("/clear-database")
def clear_database(db: Session = Depends(get_db)):
    """
    Wipe all scanned-file data — media files, tracks, queue items, planned
    actions, history, and AC3 forge jobs — while leaving app_settings
    (scan paths, language preferences, dry-run mode, etc.) untouched.

    After this runs, the next scan treats every file on disk as brand new,
    exactly like a first-run baseline scan.
    """
    from app.database.models import (
        Ac3ForgeJob, MediaFile, PlannedAction, QueueItem, Track,
    )

    # Delete in FK-dependency order — children before parents.
    deleted = {
        "planned_actions": db.query(PlannedAction).delete(),
        "queue_items":     db.query(QueueItem).delete(),
        "forge_jobs":      db.query(Ac3ForgeJob).delete(),
        "tracks":          db.query(Track).delete(),
        "media_files":     db.query(MediaFile).delete(),
    }
    db.commit()

    return {"success": True, "deleted": deleted}


# ── Validation ─────────────────────────────────────────────────────────────────

KNOWN_KEYS = {
    "keep_audio_languages",
    "keep_subtitle_languages",
    "keep_forced_subtitles",
    "keep_default_audio",
    "transcode_aac_51_to_ac3",
    "prefer_mp4_container",
    "dry_run_mode",
    "scan_paths",
    "und_audio_threshold",
    "extract_text_subtitles_to_srt",
    "image_subtitle_handling",
    "add_faststart_to_mp4",
    "max_concurrent_jobs",
    "auto_start_jobs",
    "job_timeout_minutes",
    "fix_undefined_language",
    "undefined_language_value",
    "undefined_language_mode",
    "plex_enabled",
    "plex_url",
    "plex_token",
    "plex_path_mappings",
    "plex_analyze_backlog_enabled",
    "plex_analyze_window_start",
    "plex_analyze_window_end",
    "email_enabled",
    "email_smtp_host",
    "email_smtp_port",
    "email_encryption",
    "email_username",
    "email_password",
    "email_from",
    "email_recipients",
    "email_failure_threshold",
    "sonarr_enabled",
    "sonarr_url",
    "sonarr_api_key",
    "sonarr_path_prefix_remote",
    "sonarr_path_prefix_local",
    "radarr_enabled",
    "radarr_url",
    "radarr_api_key",
    "radarr_path_prefix_remote",
    "radarr_path_prefix_local",
    "auto_cleanup_on_scan",
    "scheduled_scan_enabled",
    "scheduled_scan_times",
}


def _validate_key(key: str) -> None:
    if key not in KNOWN_KEYS:
        raise HTTPException(400, f"Unknown setting key: {key!r}. "
                                 f"Valid keys: {sorted(KNOWN_KEYS)}")


# ── Schema (consumed by the config UI) ────────────────────────────────────────

SETTINGS_SCHEMA = [
    # ── Library ────────────────────────────────────────────────────────────
    {
        "key":         "scan_paths",
        "group":       "Library",
        "label":       "Media Library Paths",
        "type":        "string_list",
        "description": "Absolute paths to scan for media files.",
    },
    {
        "key":         "prefer_mp4_container",
        "group":       "Library",
        "label":       "Prefer MP4 Container",
        "type":        "boolean",
        "description": "Remux to .mp4 when all tracks are compatible. "
                       "Files with incompatible tracks (DTS, PGS subs, etc.) stay as-is.",
    },
    {
        "key":         "add_faststart_to_mp4",
        "group":       "Library",
        "label":       "Add Fast Start to MP4 Files",
        "type":        "boolean",
        "description": "Detect MP4 files whose moov atom is not at the front "
                       "of the file (i.e. not web-optimised) and rewrite them "
                       "with -movflags +faststart. This lets Plex and other "
                       "players begin streaming before the full file downloads. "
                       "Only existing MP4 files are checked — files converted "
                       "from MKV always get fast start applied automatically.",
    },
    # ── Metadata ───────────────────────────────────────────────────────────
    {
        "key":         "fix_undefined_language",
        "group":       "Metadata",
        "label":       "Fix Undefined Language Tags",
        "type":        "boolean",
        "description": "Automatically tag audio and subtitle tracks whose "
                       "language is undefined (und) with the primary language "
                       "configured below. Video tracks are never tagged. Only "
                       "tracks that are being kept in the output are affected — "
                       "dropped and extracted tracks are ignored.",
    },
    {
        "key":         "undefined_language_value",
        "group":       "Metadata",
        "label":       "Primary Language",
        "type":        "string",
        "placeholder": "eng",
        "description": "ISO 639-2/B language code to apply to undefined tracks "
                       "(e.g. eng, fre, jpn). Must match the codes used in "
                       "Keep Audio Languages and Keep Subtitle Languages.",
    },
    {
        "key":     "undefined_language_mode",
        "group":   "Metadata",
        "label":   "Apply To",
        "type":    "select",
        "options": [
            {
                "value": "all_undefined",
                "label": "All undefined tracks",
            },
            {
                "value": "all_undefined_per_type",
                "label": "Only when all tracks of that type are undefined",
            },
            {
                "value": "single_per_type",
                "label": "Only when there is exactly one undefined track of that type",
            },
        ],
        "description": "Controls which undefined tracks get language tags. "
                       "'All undefined' tags every und track. "
                       "'All of that type' is safer — it only tags audio tracks "
                       "when every audio track is und (same rule for subtitles "
                       "independently), avoiding guesses on mixed-language files. "
                       "'Single per type' is most conservative — only tags when "
                       "there is exactly one und track of that type.",
    },
    # ── Audio ──────────────────────────────────────────────────────────────
    {
        "key":         "keep_audio_languages",
        "group":       "Audio",
        "label":       "Keep Audio Languages",
        "type":        "string_list",
        "description": "ISO 639-2/B codes (e.g. eng, fre, jpn). "
                       "Tracks in other languages will be removed.",
    },
    {
        "key":         "keep_default_audio",
        "group":       "Audio",
        "label":       "Always Keep Default Audio Track",
        "type":        "boolean",
        "description": "Retain the default-flagged audio track as a safety net "
                       "when no preferred-language track exists — prevents "
                       "accidentally removing the only audio from a file. Has "
                       "no effect when a preferred-language track is present.",
    },
    {
        "key":         "transcode_aac_51_to_ac3",
        "group":       "Audio",
        "label":       "Transcode AAC 5.1 → AC3 5.1",
        "type":        "boolean",
        "description": "Converts AAC 5.1 tracks to AC3 for AVR bitstream passthrough.",
    },
    {
        "key":         "und_audio_threshold",
        "group":       "Audio",
        "label":       "Undefined Audio Track Threshold",
        "type":        "integer",
        "min":         1,
        "description": "Flag a file for manual review when it contains this "
                       "many or more audio tracks with an undefined language. "
                       "Minimum 1 — a threshold of 0 would match every file, "
                       "including ones with no undefined tracks at all.",
    },
    # ── Subtitles ──────────────────────────────────────────────────────────
    {
        "key":         "keep_subtitle_languages",
        "group":       "Subtitles",
        "label":       "Keep Subtitle Languages",
        "type":        "string_list",
        "description": "ISO 639-2/B codes. Subtitles in other languages will be removed.",
    },
    {
        "key":         "keep_forced_subtitles",
        "group":       "Subtitles",
        "label":       "Always Keep Forced Subtitles",
        "type":        "boolean",
        "description": "Retain forced subtitle tracks regardless of language.",
    },
    {
        "key":         "extract_text_subtitles_to_srt",
        "group":       "Subtitles",
        "label":       "Extract Subtitles to External SRT",
        "type":        "boolean",
        "description": "Extract kept text-based subtitle tracks (SubRip, "
                       "mov_text, ASS/SSA) to an external .srt file next to "
                       "the media (e.g. Movie.en.srt, Movie.en.forced.srt) "
                       "and remove them from the file — improves Plex direct "
                       "play compatibility. Kept image-based subtitles (PGS, "
                       "VOBSUB, DVD/DVB) can't be converted — see Image-Based "
                       "Subtitle Handling below for how that's resolved.",
    },
    {
        "key":     "image_subtitle_handling",
        "group":   "Subtitles",
        "label":   "Image-Based Subtitle Handling",
        "type":    "select",
        "options": [
            {
                "value": "always_ask",
                "label": "Always ask (flag for manual review)",
            },
            {
                "value": "always_keep",
                "label": "Always keep (leave embedded)",
            },
            {
                "value": "always_remove",
                "label": "Always remove (drop the track)",
            },
        ],
        "description": "What to do with a kept image-based subtitle track "
                       "(PGS, VOBSUB, DVD/DVB) when extraction above is "
                       "enabled and it can't be converted to SRT. Only "
                       "applies going forward — existing items already "
                       "sitting in manual review for this reason can be "
                       "resolved in bulk from the Review tab once this is "
                       "set to Always Keep or Always Remove.",
    },
    # ── Worker ─────────────────────────────────────────────────────────────
    {
        "key":         "max_concurrent_jobs",
        "group":       "Worker",
        "label":       "Concurrent Jobs",
        "type":        "integer",
        "description": "Maximum number of files that can be processed "
                       "simultaneously. Increasing this speeds up large queues "
                       "if your CPU and storage can keep up. Changes take "
                       "effect immediately without a restart.",
    },
    {
        "key":         "auto_start_jobs",
        "group":       "Worker",
        "label":       "Auto-Start Processing After Scan",
        "type":        "boolean",
        "description": "When enabled (default), queued files begin processing "
                       "immediately after a scan completes. When disabled, "
                       "files are queued but the worker starts paused — use "
                       "the Resume button on the dashboard when you are ready "
                       "to begin processing.",
    },
    {
        "key":         "job_timeout_minutes",
        "group":       "Worker",
        "label":       "Job Timeout (minutes)",
        "type":        "integer",
        "description": "Maximum time in minutes a single FFmpeg job may run "
                       "before it is killed and marked as failed. Protects the "
                       "queue from stalling if FFmpeg hangs on a corrupt or "
                       "unusual file. Set to 0 to disable the timeout entirely. "
                       "Default: 120 (2 hours), which comfortably covers any "
                       "legitimate 4K file.",
    },
    {
        "key":         "dry_run_mode",
        "group":       "Worker",
        "label":       "Dry Run Mode",
        "type":        "boolean",
        "description": "Populate the queue with planned actions but do NOT "
                       "execute FFmpeg or modify any files.",
    },
    # ── Sonarr ─────────────────────────────────────────────────────────────
    {
        "key":         "sonarr_enabled",
        "group":       "Sonarr",
        "label":       "Enable Sonarr Integration",
        "type":        "boolean",
        "description": "When enabled, Remuxarr accepts On Import / On Upgrade "
                       "webhooks from Sonarr and calls Sonarr's RescanSeries "
                       "after each job completes.",
    },
    {
        "key":         "sonarr_url",
        "group":       "Sonarr",
        "label":       "Sonarr URL",
        "type":        "string",
        "placeholder": "http://sonarr:8989",
        "description": "Base URL of your Sonarr instance (no trailing slash).",
    },
    {
        "key":         "sonarr_api_key",
        "group":       "Sonarr",
        "label":       "Sonarr API Key",
        "type":        "string",
        "sensitive":   True,
        "placeholder": "your-api-key-here",
        "description": "Found in Sonarr → Settings → General → Security → API Key.",
    },
    {
        "key":         "sonarr_path_prefix_remote",
        "group":       "Sonarr",
        "label":       "Sonarr Path Prefix (Remote)",
        "type":        "string",
        "placeholder": "/media",
        "description": "The path prefix that Sonarr uses in its webhook "
                       "payloads. Leave blank if Sonarr and Remuxarr see "
                       "the same paths.",
    },
    {
        "key":         "sonarr_path_prefix_local",
        "group":       "Sonarr",
        "label":       "Sonarr Path Prefix (Local)",
        "type":        "string",
        "placeholder": "/media/tv",
        "description": "The actual path prefix on Remuxarr's filesystem that "
                       "corresponds to the remote prefix above. Both prefix "
                       "settings must be set together — if either is blank, "
                       "no translation is applied.",
    },
    # ── Radarr ─────────────────────────────────────────────────────────────
    {
        "key":         "radarr_enabled",
        "group":       "Radarr",
        "label":       "Enable Radarr Integration",
        "type":        "boolean",
        "description": "When enabled, Remuxarr accepts On Import / On Upgrade "
                       "webhooks from Radarr and calls Radarr's RescanMovie "
                       "after each job completes.",
    },
    {
        "key":         "radarr_url",
        "group":       "Radarr",
        "label":       "Radarr URL",
        "type":        "string",
        "placeholder": "http://radarr:7878",
        "description": "Base URL of your Radarr instance (no trailing slash).",
    },
    {
        "key":         "radarr_api_key",
        "group":       "Radarr",
        "label":       "Radarr API Key",
        "type":        "string",
        "sensitive":   True,
        "placeholder": "your-api-key-here",
        "description": "Found in Radarr → Settings → General → Security → API Key.",
    },
    {
        "key":         "radarr_path_prefix_remote",
        "group":       "Radarr",
        "label":       "Radarr Path Prefix (Remote)",
        "type":        "string",
        "placeholder": "/media",
        "description": "The path prefix that Radarr uses in its webhook payloads. "
                       "Leave blank if Radarr and Remuxarr see the same paths.",
    },
    {
        "key":         "radarr_path_prefix_local",
        "group":       "Radarr",
        "label":       "Radarr Path Prefix (Local)",
        "type":        "string",
        "placeholder": "/media/movies",
        "description": "The actual path prefix on Remuxarr's filesystem that "
                       "corresponds to the remote prefix above. Both prefix "
                       "settings must be set together.",
    },
    # ── Plex ───────────────────────────────────────────────────────────────
    {
        "key":         "plex_enabled",
        "group":       "Plex",
        "label":       "Enable Plex Notifications",
        "type":        "boolean",
        "description": "When enabled, Remuxarr notifies Plex directly after "
                       "every successful job with a lightweight, path-scoped "
                       "library refresh — confirmed via testing to reliably "
                       "pick up most changes on its own, including files "
                       "Plex already had indexed. This is independent of "
                       "Sonarr/Radarr — if you remove Plex's own connection "
                       "inside Sonarr/Radarr, enable this so Plex still gets "
                       "notified. For the rare cases this refresh doesn't "
                       "catch, see the separate Plex Analyze Backlog section "
                       "below — most installs won't need it.",
    },
    {
        "key":         "plex_url",
        "group":       "Plex",
        "label":       "Plex URL",
        "type":        "string",
        "placeholder": "http://plex:32400",
        "description": "Base URL of your Plex Media Server (no trailing slash).",
    },
    {
        "key":         "plex_token",
        "group":       "Plex",
        "label":       "Plex Token",
        "type":        "string",
        "sensitive":   True,
        "placeholder": "your-plex-token-here",
        "description": "Your Plex authentication token (X-Plex-Token). "
                       "Search 'Finding an authentication token' on Plex's "
                       "support site for instructions on retrieving yours.",
    },
    {
        "key":     "plex_path_mappings",
        "group":   "Plex",
        "label":   "Plex Path Mappings",
        "type":    "string_list",
        "description": "Maps each Remuxarr scan path to the equivalent path "
                       "inside the Plex container, formatted as "
                       "local_path=plex_path — e.g. /media/movies=/Media/Movies "
                       "and /media/tv=/Media/TV. One entry per scan path. "
                       "Required for Plex notifications to work — without a "
                       "matching mapping, notifications for that path are "
                       "skipped.",
    },
    # ── Plex Analyze Backlog ─────────────────────────────────────────────────
    # Split out from the main Plex section deliberately — this is an opt-in
    # safety net, not part of everyday operation. Direct testing across a
    # 1,300-item backlog showed the refresh above (combined with Plex's own
    # scheduled maintenance) already catches the overwhelming majority of
    # reprocessed files on its own; this only exists for the rare remainder.
    {
        "key":         "plex_analyze_backlog_enabled",
        "group":       "Plex Analyze Backlog",
        "label":       "Enable Analyze Backlog",
        "type":        "boolean",
        "description": "Off by default. When enabled, reprocessed files "
                       "(RE-PROCESS, retry, or a file replaced in place) are "
                       "queued and, during the window below, checked against "
                       "Plex's current data — if Plex hasn't already picked "
                       "up the change on its own, an explicit re-analyze is "
                       "sent to force it. Most installs won't need this: the "
                       "plain refresh above already handles the vast "
                       "majority of cases. Worth turning on temporarily "
                       "during a large backfill, or if you notice specific "
                       "files sitting with stale Plex metadata longer than "
                       "expected.",
    },
    {
        "key":         "plex_analyze_window_start",
        "group":       "Plex Analyze Backlog",
        "label":       "Analyze Window Start",
        "type":        "string",
        "placeholder": "02:00",
        "description": "24-hour HH:MM time. Only relevant while the toggle "
                       "above is enabled. This avoids bursting hundreds of "
                       "Plex API calls at once during a large backfill — the "
                       "queue only drains between this start time and the "
                       "end time below.",
    },
    {
        "key":         "plex_analyze_window_end",
        "group":       "Plex Analyze Backlog",
        "label":       "Analyze Window End",
        "type":        "string",
        "placeholder": "06:00",
        "description": "End of the window above. If this is earlier than "
                       "the start time (e.g. start 22:00, end 02:00), the "
                       "window is treated as spanning midnight.",
    },
    # ── Email ──────────────────────────────────────────────────────────────
    {
        "key":         "email_enabled",
        "group":       "Email",
        "label":       "Enable Email Notifications",
        "type":        "boolean",
        "description": "Send an email when a job fails. Protected against "
                       "configuration mistakes that cause every file to "
                       "fail: after several consecutive failures (see "
                       "threshold below), one combined warning email is "
                       "sent and notifications pause automatically until "
                       "a job succeeds — so a bad config can never flood "
                       "this inbox.",
    },
    {
        "key":         "email_smtp_host",
        "group":       "Email",
        "label":       "SMTP Host",
        "type":        "string",
        "placeholder": "smtp.gmail.com",
        "description": "Hostname of your outgoing mail server.",
    },
    {
        "key":         "email_smtp_port",
        "group":       "Email",
        "label":       "SMTP Port",
        "type":        "integer",
        "description": "Common values: 587 (STARTTLS), 465 (SSL), 25 (none, rare).",
    },
    {
        "key":     "email_encryption",
        "group":   "Email",
        "label":   "Encryption",
        "type":    "select",
        "options": [
            {"value": "starttls", "label": "STARTTLS (recommended)"},
            {"value": "ssl",      "label": "SSL/TLS"},
            {"value": "none",     "label": "None"},
        ],
        "description": "Most providers (Gmail, Outlook, etc.) use STARTTLS "
                       "on port 587.",
    },
    {
        "key":         "email_username",
        "group":       "Email",
        "label":       "SMTP Username",
        "type":        "string",
        "placeholder": "you@example.com",
        "description": "Leave blank if your SMTP server doesn't require authentication.",
    },
    {
        "key":         "email_password",
        "group":       "Email",
        "label":       "SMTP Password",
        "type":        "string",
        "sensitive":   True,
        "placeholder": "your-password-or-app-password",
        "description": "Many providers (Gmail included) require an "
                       "app-specific password rather than your normal "
                       "account password when sending via SMTP.",
    },
    {
        "key":         "email_from",
        "group":       "Email",
        "label":       "From Address",
        "type":        "string",
        "placeholder": "remuxarr@example.com",
        "description": "Defaults to the SMTP username above if left blank.",
    },
    {
        "key":   "email_recipients",
        "group": "Email",
        "label": "Recipients",
        "type":  "string_list",
        "description": "One or more email addresses to notify on failure.",
    },
    {
        "key":         "email_failure_threshold",
        "group":       "Email",
        "label":       "Consecutive Failure Threshold",
        "type":        "integer",
        "description": "After this many consecutive job failures, one "
                       "combined warning email is sent and individual "
                       "failure emails pause automatically — protecting "
                       "against a configuration mistake flooding this "
                       "inbox with hundreds of near-identical emails. "
                       "Notifications resume automatically the next time "
                       "a job succeeds.",
    },
]
