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

    # JSON dict mapping stream_index (as string) -> ISO 639-2/B language
    # code, set via the Audio Language Review section when a track has a
    # DEFINED but wrong language (e.g. an English show whose only audio
    # track is mistagged "dut"). Distinct from the "fix undefined language"
    # feature, which only ever touches "und" tracks — this handles the
    # opposite case: a language tag that's present but incorrect.
    audio_language_overrides = Column(Text)

    # Set when a human has explicitly confirmed the file's current audio
    # language is correct despite not matching keep_audio_languages (e.g.
    # anime that's genuinely, correctly Japanese). Once set, the file is
    # never re-flagged in Audio Language Review again, regardless of what
    # else changes about it on future scans.
    audio_language_ignored = Column(Boolean, default=False)

    # Set when a manual-review item caused specifically by the
    # undefined-audio-count threshold gate (decision.py) is approved via
    # the generic approve_manual_review endpoint. Unlike the image-subtitle
    # manual-review gate, which already has its own dedicated resolution
    # flow (resolve_subtitles) that persists an exemption via
    # subtitle_overrides, this gate had no persistence mechanism at all —
    # every fresh analyze_file() call (including the one the worker does
    # at job-pickup time, after "Keep" was already clicked) would
    # re-evaluate the track count and re-trigger the same gate forever,
    # since a track's language tag never changes on its own. Confirmed
    # this directly: decision.actions ended up as only [flag_manual_review]
    # at the exact point a retry was being built, which is what silently
    # produced a no-op retry rather than the intended one.
    und_audio_threshold_acknowledged = Column(Boolean, default=False)

    # Parallel fields for subtitle tracks — see Subtitle Language Review.
    # Distinct table/column set from the audio ones above even though the
    # shape is identical, since a file can independently have an audio
    # override, a subtitle override, both, or neither.
    subtitle_language_overrides = Column(Text)
    subtitle_language_ignored   = Column(Boolean, default=False)

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
    # ISO 639-2/B code this track's language was rewritten to, if the
    # language-fix feature touched it (e.g. "eng"). NULL for actions that
    # didn't involve a language rewrite. Used to let the Plex backlog drain
    # verify whether Plex's own maintenance has already picked up the
    # change before falling back to an explicit Analyze call.
    target_language = Column(String, nullable=True)

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

    # Set only when this reprocess involved a language-tag fix (copied from
    # the job's PlannedAction rows at queue-time). NULL for any other kind
    # of reprocess (track removal, container change, etc.) — in that case
    # there's no cheap way to verify Plex already caught up, so the drain
    # loop skips the verification check and goes straight to Analyze,
    # exactly as before this feature existed.
    #
    # When set, the drain loop checks Plex's own metadata for this file
    # before firing an explicit Analyze — if Plex's own scheduled
    # maintenance has already picked up the change (confirmed to happen
    # for most files, just not reliably every time), the Analyze call is
    # skipped entirely as unnecessary.
    expected_language = Column(String, nullable=True)

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


class AudioLanguageFlag(Base):
    """
    One row per file whose kept audio track has a DEFINED but non-preferred
    language — e.g. an English sitcom whose only audio track is mistagged
    "dut", or a Danish tag on an American show. Distinct from the
    "undefined language" manual-review gate: this is specifically for
    tracks that already have a language set, just an incorrect one.

    Unlike the existing manual_review QueueItem status, a flagged file
    here is NOT held back from processing — it's fully processed and
    playable (using whatever audio survived the normal keep/drop and
    safety-net logic) the whole time it sits flagged. The flag is purely
    informational, surfaced in the Audio Language Review section, until
    the user either relabels the track (via MediaFile.audio_language_overrides)
    or confirms it's already correct (via MediaFile.audio_language_ignored)
    — either action removes this row.

    One row per file (file_id is unique) — re-detecting the same mismatch
    on a later scan updates detected_language in place rather than creating
    a duplicate entry.
    """
    __tablename__ = "audio_language_flags"

    id      = Column(Integer, primary_key=True, index=True)
    file_id = Column(Integer, ForeignKey("media_files.id", ondelete="CASCADE"),
                     nullable=False, unique=True)

    # Which audio track this refers to — needed so the "apply" action
    # knows exactly which stream to write the corrected language to; the
    # detected language alone isn't enough to reliably re-identify the
    # track later (a file could have multiple tracks sharing a language).
    stream_index = Column(Integer, nullable=False)

    # The language code currently on that track at the moment it was
    # flagged (e.g. "dut") — shown to the user so they can tell at a
    # glance what's actually on the file without opening it.
    detected_language = Column(String)

    created_at = Column(DateTime, default=datetime.utcnow)

    media_file = relationship("MediaFile", backref="audio_language_flag")


class SubtitleLanguageFlag(Base):
    """
    Subtitle-track counterpart to AudioLanguageFlag — one row per file with
    a kept subtitle track flagged for language review, surfaced in the
    Subtitle Language Review section.

    Unlike AudioLanguageFlag (which only ever flags a DEFINED but wrong
    language), this table's rows always originate from an UNDEFINED ("und")
    tag — specifically, a track fix_undefined_language's "always_ask" mode
    decided qualifies for tagging but left for a human to actually choose,
    rather than auto-guessing. Same resolution mechanism either way though:
    the user picks the correct language (persisted on
    MediaFile.subtitle_language_overrides) or confirms it's fine to leave
    undefined (via MediaFile.subtitle_language_ignored) — either action
    removes this row.

    Same non-blocking behavior as AudioLanguageFlag too — a flagged file is
    fully processed and playable the entire time it sits flagged, this is
    purely informational bookkeeping, never something that holds up
    processing the way the general manual_review queue does.
    """
    __tablename__ = "subtitle_language_flags"

    id      = Column(Integer, primary_key=True, index=True)
    file_id = Column(Integer, ForeignKey("media_files.id", ondelete="CASCADE"),
                     nullable=False, unique=True)
    stream_index = Column(Integer, nullable=False)
    detected_language = Column(String)

    created_at = Column(DateTime, default=datetime.utcnow)

    media_file = relationship("MediaFile", backref="subtitle_language_flag")
