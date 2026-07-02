"""
Database models for Remuxarr.

Tables
------
media_files    — one row per file on disk
tracks         — one row per A/V/S stream inside a file
queue_items    — pending / active / completed processing jobs
planned_actions— individual steps that will/did happen for a job
app_settings   — key/value config store (editable via UI)
"""
from datetime import datetime
from sqlalchemy import (
    BigInteger, Boolean, Column, DateTime, Float,
    ForeignKey, Integer, String, Text,
)
from sqlalchemy.orm import DeclarativeBase, relationship


class Base(DeclarativeBase):
    pass


# ---------------------------------------------------------------------------
# MediaFile
# ---------------------------------------------------------------------------
class MediaFile(Base):
    __tablename__ = "media_files"

    id        = Column(Integer, primary_key=True, index=True)
    path      = Column(String,  unique=True, nullable=False, index=True)
    filename  = Column(String,  nullable=False)
    directory = Column(String,  nullable=False)

    # Delta-scan fingerprint
    size  = Column(BigInteger, nullable=False)
    mtime = Column(Float,      nullable=False)

    # Format info (from ffprobe)
    container   = Column(String)   # mkv | mp4 | avi | …
    duration    = Column(Float)    # seconds
    video_codec = Column(String)   # first video stream codec

    # Lifecycle state
    # unprocessed | queued | processing | processed | skipped | manual_review | error
    status = Column(String, default="unprocessed", nullable=False)

    last_scanned   = Column(DateTime, default=datetime.utcnow)
    last_processed = Column(DateTime)
    created_at     = Column(DateTime, default=datetime.utcnow)

    # JSON dict mapping stream_index (as string) -> "keep" | "remove",
    # set when the user resolves a manual-review flag for a non-convertible
    # (image-based) subtitle track. Persists the choice across re-scans so
    # the decision engine can act on it instead of re-flagging the same track.
    subtitle_overrides = Column(Text)

    tracks      = relationship("Track",     back_populates="media_file",
                               cascade="all, delete-orphan")
    queue_items = relationship("QueueItem", back_populates="media_file",
                               cascade="all, delete-orphan")


# ---------------------------------------------------------------------------
# Track
# ---------------------------------------------------------------------------
class Track(Base):
    __tablename__ = "tracks"

    id      = Column(Integer, primary_key=True, index=True)
    file_id = Column(Integer, ForeignKey("media_files.id", ondelete="CASCADE"),
                     nullable=False)

    stream_index   = Column(Integer, nullable=False)
    track_type     = Column(String,  nullable=False)  # video | audio | subtitle
    codec          = Column(String)
    language       = Column(String)   # ISO 639-2/B  ("eng", "fre", "und", …)
    channels       = Column(Integer)  # audio only
    channel_layout = Column(String)   # "5.1", "stereo", …
    is_default     = Column(Boolean, default=False)
    is_forced      = Column(Boolean, default=False)
    # SDH (Subtitles for the Deaf and Hard-of-hearing) — used to give SDH
    # subtitle tracks a distinct ".sdh.srt" filename on extraction instead
    # of colliding with a same-language non-SDH track.
    is_hearing_impaired = Column(Boolean, default=False)
    is_dub              = Column(Boolean, default=False)
    title          = Column(String)

    # ── Write-only columns — removal candidates ───────────────────────────────
    # These four columns are populated during scanning but never read back in
    # the decision engine, workers, or any API serialiser. They consume storage
    # with no downstream consumer. Removing them requires a schema migration
    # (ALTER TABLE tracks DROP COLUMN …) and removing the corresponding writes
    # in scanner.py (_process_file's Track(...) constructor call).
    #
    # Removal priority:
    #   raw_ffprobe — highest: stores the full ffprobe JSON blob per track
    #                 (potentially several KB each), zero consumers.
    #   codec_long  — human-readable codec description, never queried.
    #   sample_rate — populated from probe, never queried.
    #   bit_rate    — populated from probe, never queried.
    codec_long     = Column(String)
    raw_ffprobe    = Column(Text)
    sample_rate    = Column(Integer)
    bit_rate       = Column(Integer)

    media_file = relationship("MediaFile", back_populates="tracks")


# ---------------------------------------------------------------------------
# QueueItem
# ---------------------------------------------------------------------------
class QueueItem(Base):
    __tablename__ = "queue_items"

    id      = Column(Integer, primary_key=True, index=True)
    file_id = Column(Integer, ForeignKey("media_files.id", ondelete="CASCADE"), nullable=False)

    # pending | processing | success | failed | manual_review | skipped | cancelled
    status     = Column(String, default="pending", nullable=False, index=True)
    priority   = Column(Integer, default=5)   # 1 = highest, 10 = lowest
    is_dry_run = Column(Boolean, default=False)

    reason         = Column(Text)    # human-readable: "Remove 2 French audio tracks; Convert to MP4"
    error_message  = Column(Text)
    progress       = Column(Float, default=0.0)  # 0–100
    current_action = Column(String)              # "Remuxing to MP4", "Transcoding AAC 5.1 → AC3"

    # JSON list of flagged subtitle tracks for manual_review items caused by
    # non-convertible (image-based) subtitles. Each entry:
    #   {stream_index, language, codec, is_forced, title}
    # Null/empty for other manual_review causes (e.g. undefined-language audio).
    review_subtitles = Column(Text)

    # Size tracking (populated after success)
    output_path   = Column(String)
    original_size = Column(BigInteger)
    output_size   = Column(BigInteger)

    created_at   = Column(DateTime, default=datetime.utcnow)
    started_at   = Column(DateTime)
    completed_at = Column(DateTime)

    # Set when this job was triggered by a Sonarr webhook. After the job
    # completes successfully, Remuxarr calls Sonarr's RescanSeries so
    # Sonarr updates its DB with the processed file's new path/extension.
    # NULL for jobs queued via a manual library scan.
    sonarr_series_id = Column(Integer, nullable=True)

    # Same as above but for Radarr — calls RescanMovie after completion.
    radarr_movie_id  = Column(Integer, nullable=True)

    # True when this is the first time Remuxarr has ever probed this exact
    # file path (i.e. MediaFile row was created fresh, not updated).
    # False for any re-probe of an already-known path — including normal
    # rescans of a replaced file, and any RE-PROCESS / retry action.
    #
    # Used by the Plex notification logic to decide which call to make:
    #   True  → lightweight path-scoped library refresh (Plex has never
    #           seen this path; refresh alone triggers a full deep analyze
    #           on genuinely new content automatically).
    #   False → explicit ratingKey lookup + Analyze call (Plex already has
    #           a record for this exact path with now-stale stream
    #           metadata; a plain refresh does NOT force re-analysis of
    #           paths it has already indexed).
    is_new_file = Column(Boolean, default=True)

    media_file      = relationship("MediaFile",    back_populates="queue_items")
    planned_actions = relationship("PlannedAction", back_populates="queue_item",
                                   cascade="all, delete-orphan",
                                   order_by="PlannedAction.order")


# ---------------------------------------------------------------------------
# PlannedAction
# ---------------------------------------------------------------------------
class PlannedAction(Base):
    __tablename__ = "planned_actions"

    id            = Column(Integer, primary_key=True, index=True)
    queue_item_id = Column(Integer, ForeignKey("queue_items.id", ondelete="CASCADE"),
                           nullable=False)

    order       = Column(Integer, default=0)
    # copy_track | drop_track | transcode_track | change_container | flag_manual_review
    action_type = Column(String, nullable=False)
    description = Column(String, nullable=False)   # human-readable
    track_type  = Column(String)                   # audio | subtitle | video | None
    stream_index = Column(Integer)

    queue_item = relationship("QueueItem", back_populates="planned_actions")


# ---------------------------------------------------------------------------
# AppSetting
# ---------------------------------------------------------------------------
class AppSetting(Base):
    __tablename__ = "app_settings"

    key        = Column(String, primary_key=True)
    value      = Column(Text,   nullable=False)   # JSON-encoded
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


# ---------------------------------------------------------------------------
# Ac3ForgeJob  —  on-demand "add AC3 5.1 alongside AAC 5.1" per-file jobs
# ---------------------------------------------------------------------------
class Ac3ForgeJob(Base):
    __tablename__ = "ac3_forge_jobs"

    id      = Column(Integer, primary_key=True, index=True)
    file_id = Column(Integer, ForeignKey("media_files.id", ondelete="CASCADE"),
                     nullable=False)

    # pending | processing | success | failed | cancelled
    # undo_pending | undone | undo_failed
    status  = Column(String, default="pending", nullable=False, index=True)

    # True while this row represents an undo operation
    is_undo = Column(Boolean, default=False)

    # Stream metadata captured at job-creation time.
    # aac_stream_index  → global ffprobe index of the AAC 5.1 track
    # audio_track_count → number of audio tracks BEFORE adding AC3
    #   The AC3 is always appended as output audio stream [audio_track_count],
    #   so undo can always use: -map -0:a:{audio_track_count}
    aac_stream_index  = Column(Integer)
    audio_track_count = Column(Integer)

    progress       = Column(Float,  default=0.0)
    current_action = Column(String)
    error_message  = Column(Text)

    original_size = Column(BigInteger)
    output_size   = Column(BigInteger)

    created_at   = Column(DateTime, default=datetime.utcnow)
    started_at   = Column(DateTime)
    completed_at = Column(DateTime)

    media_file = relationship("MediaFile", backref="forge_jobs")


class PlexAnalyzeBacklog(Base):
    """
    Queue of files awaiting an explicit Plex Analyze call.

    Reprocessed files (RE-PROCESS, retry, or a normal rescan that replaced
    a file already known to Plex) need an explicit Analyze to force Plex
    to re-read stream metadata — a plain refresh doesn't do this for paths
    Plex already has indexed. Rather than firing these immediately and
    potentially bursting hundreds of calls at once (each of which also
    requires fetching the full library section listing to find the right
    item), they're queued here and drained slowly by the background
    scheduler, only during the configured time window.

    Entries are deleted once attempted — this is best-effort, matching the
    rest of the Plex integration's philosophy: a failure here never affects
    the Remuxarr job's own recorded success.
    """
    __tablename__ = "plex_analyze_backlog"

    id      = Column(Integer, primary_key=True, index=True)
    file_id = Column(Integer, ForeignKey("media_files.id", ondelete="CASCADE"),
                     nullable=False)

    created_at = Column(DateTime, default=datetime.utcnow)

    media_file = relationship("MediaFile", backref="plex_backlog_entries")


class NotificationState(Base):
    """
    Singleton row (id is always 1) tracking the consecutive-failure
    circuit breaker for email notifications.

    consecutive_failures increments on every real job failure and resets
    to 0 on every success (or successful dry-run preview). Once it reaches
    the configured threshold, breaker_tripped is set and stays set — no
    further individual failure emails are sent, only the one combined
    "notifications paused" email — until a success resets it.

    Persisted in the database (not held in memory) specifically so a
    container restart mid-flood doesn't accidentally clear the breaker and
    let a fresh batch of emails through.
    """
    __tablename__ = "notification_state"

    id                   = Column(Integer, primary_key=True)
    consecutive_failures = Column(Integer, default=0)
    breaker_tripped       = Column(Boolean, default=False)
