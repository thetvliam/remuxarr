"""
Forge selection, ordering, and counting.

Each produced a wrong external call, a wrong ordering, or a wrong number, and
the app carried on. That is what makes them worth pinning: nothing in the logs
would ever have pointed at any of them.

  * The forge path queued a Plex Analyze backlog row without the dedup the
    main pipeline applies, so forge-then-undo accumulated one row per
    operation and issued a duplicate Analyze per row — the expensive call the
    backlog queue exists to rate-limit.

  * queue_forge_job picked its AAC 5.1 source with an unordered .first(),
    while get_candidates orders by stream_index specifically so that "the
    first AAC 5.1 track" is deterministic. On a file with an English and a
    commentary track the user could be shown one and get the other.

  * list_processed ordered by completed_at DESC, but undo_job resets
    completed_at to None and undo_pending is one of the listed statuses — so
    SQLite sorted the row the user had just clicked Undo on to the very bottom
    of the list.

  * ignore_flags counted every file that existed rather than every flag it
    cleared, so re-submitting a stale selection reported work it had not
    done.
"""
import pytest


@pytest.fixture
def db():
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    from app.database.models import Base

    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)()


def _media(db, name="Movie.mkv"):
    from app.database.models import MediaFile

    mf = MediaFile(path=f"/m/{name}", filename=name, directory="/m",
                   size=1, mtime=1.0)
    db.add(mf)
    db.commit()
    return mf


# ── Which AAC 5.1 track gets forged ──────────────────────────────────────────

def test_forge_selects_the_lowest_indexed_aac_51_track(db):
    """
    get_candidates orders by stream_index and its comment says that is so
    "first AAC 5.1" is deterministic. queue_forge_job has to agree, or the
    candidate row the user clicked describes a different track from the one
    that gets forged — an AC3 built from the commentary instead of the feature.

    Rows are inserted highest-index-first so an unordered .first() has a real
    chance of returning the wrong one.
    """
    from app.core.forge import queue_forge_job
    from app.database.models import Track

    media = _media(db)
    for si, lang in ((5, "eng"), (3, "eng"), (7, "com")):
        db.add(Track(file_id=media.id, track_type="audio", codec="aac",
                     channels=6, stream_index=si, language=lang))
    db.add(Track(file_id=media.id, track_type="video", codec="h264",
                 stream_index=0))
    db.commit()

    job = queue_forge_job(db, media.id)
    assert job.aac_stream_index == 3, (
        f"forging stream {job.aac_stream_index}, but the candidates list shows "
        "the lowest-indexed AAC 5.1 track (3)"
    )


def test_forge_raises_when_no_aac_51_track_exists(db):
    """The pre-existing guard must survive the ordering change."""
    from app.core.forge import queue_forge_job
    from app.database.models import Track

    media = _media(db)
    db.add(Track(file_id=media.id, track_type="audio", codec="aac",
                 channels=2, stream_index=1))
    db.commit()

    with pytest.raises(ValueError, match="No AAC 5.1"):
        queue_forge_job(db, media.id)


# ── Processed-list ordering ──────────────────────────────────────────────────

def test_undone_job_does_not_sink_to_the_bottom(db):
    """
    undo_job nulls completed_at, and SQLite sorts NULL last under DESC — so
    the job the user just acted on landed below every historical entry, which
    is exactly backwards.
    """
    import datetime as dt

    from app.api.routes.forge import list_processed
    from app.database.models import Ac3ForgeJob

    media = _media(db)
    base = dt.datetime(2026, 1, 1, 12, 0)
    # Three older, completed jobs.
    for i, offset in enumerate([3, 2, 1]):
        db.add(Ac3ForgeJob(file_id=media.id, status="success",
                           created_at=base - dt.timedelta(days=offset + 10),
                           completed_at=base - dt.timedelta(days=offset)))
    # The one just sent to undo: newest created_at, no completed_at.
    db.add(Ac3ForgeJob(file_id=media.id, status="undo_pending",
                       created_at=base, completed_at=None))
    db.commit()

    rows = list_processed(db)
    assert rows[0]["status"] == "undo_pending", (
        f"undo_pending sorted to position "
        f"{[r['status'] for r in rows].index('undo_pending')} of {len(rows)} — "
        "the row the user is waiting on is below the historical entries"
    )


def test_completed_jobs_still_sort_newest_first(db):
    """The change must not disturb ordering for rows that do have a time."""
    import datetime as dt

    from app.api.routes.forge import list_processed
    from app.database.models import Ac3ForgeJob

    media = _media(db)
    base = dt.datetime(2026, 1, 1, 12, 0)
    for days, size in ((5, 100), (1, 300), (3, 200)):
        db.add(Ac3ForgeJob(file_id=media.id, status="success",
                           created_at=base - dt.timedelta(days=days + 10),
                           completed_at=base - dt.timedelta(days=days),
                           output_size=size))
    db.commit()

    rows = list_processed(db)
    assert [r["output_size"] for r in rows] == [300, 200, 100]


# ── The ignore count ─────────────────────────────────────────────────────────

def test_ignore_counts_only_flags_actually_cleared(db):
    """
    The count is the only feedback this action gives. Counting files that
    merely exist meant re-submitting a stale selection — a list another client
    already resolved, or a page left open across a rescan — reported
    "Ignoring 3 files" having ignored none.
    """
    from app.api.routes._language_review import IgnoreRequest
    from app.api.routes.audio_language import ignore_flags
    from app.database.models import AudioLanguageFlag

    flagged = _media(db, "flagged.mkv")
    unflagged_a = _media(db, "a.mkv")
    unflagged_b = _media(db, "b.mkv")
    db.add(AudioLanguageFlag(file_id=flagged.id, stream_index=1,
                             detected_language="dut"))
    db.commit()

    result = ignore_flags(
        IgnoreRequest(file_ids=[flagged.id, unflagged_a.id, unflagged_b.id]), db
    )
    assert result == {"ignored": 1}


def test_ignore_still_marks_unflagged_files_as_ignored(db):
    """
    Not counting them must not mean skipping them: setting the column is
    idempotent and is what stops the file being flagged by a future scan.
    """
    from app.api.routes._language_review import IgnoreRequest
    from app.api.routes.audio_language import ignore_flags

    media = _media(db)
    ignore_flags(IgnoreRequest(file_ids=[media.id]), db)
    db.refresh(media)
    assert media.audio_language_ignored is True


def test_ignore_reports_zero_for_an_entirely_stale_selection(db):
    from app.api.routes._language_review import IgnoreRequest
    from app.api.routes.subtitle_language import ignore_flags

    media = _media(db)
    assert ignore_flags(IgnoreRequest(file_ids=[media.id, 9999]), db) == {"ignored": 0}


# ── Plex Analyze backlog dedup ───────────────────────────────────────────────

def test_forge_backlog_enqueue_is_deduplicated(db, monkeypatch):
    """
    Analyze is the expensive Plex call the backlog queue exists to
    rate-limit, so one row per forge operation on the same file defeats the
    queue's purpose and multiplies load on what is usually a NAS.
    """
    import app.core.worker as worker
    from sqlalchemy.orm import sessionmaker

    from app.database.models import Ac3ForgeJob, PlexAnalyzeBacklog

    media = _media(db)
    job = Ac3ForgeJob(file_id=media.id, status="success")
    db.add(job)
    db.commit()

    monkeypatch.setattr(worker, "SessionLocal", sessionmaker(bind=db.get_bind()))
    monkeypatch.setattr(worker, "get_app_settings", lambda _db: {
        "plex_enabled": True,
        "plex_url": "http://plex:32400",
        "plex_token": "t",
        "plex_analyze_backlog_enabled": True,
        # Required: the function returns early without a mapping, so an empty
        # list never reaches the enqueue this test is about.
        "plex_path_mappings": [{"local": "/m", "plex": "/media"}],
    })

    for _ in range(3):
        worker._load_forge_plex_notify_data(job.id)

    assert db.query(PlexAnalyzeBacklog).filter_by(file_id=media.id).count() == 1, (
        "one backlog row per forge operation — each issues its own Analyze "
        "against the same ratingKey"
    )
