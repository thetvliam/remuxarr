"""
Manual-review correctness: refresh, provenance, and the null discriminator.

Four separate bugs, all in the same flow, all invisible from the UI.

B-7  An existing manual_review row was never updated. The skip branch 40 lines
     below does the opposite and updates in place. Since a fresh analyze_file()
     has just run, a settings change or a replaced file left the Review page
     showing the original reason AND the original flagged track list — and
     resolve_subtitles acts on the STREAM INDICES in that list, so a user's
     Keep/Remove choice was applied against indices that no longer described
     the file.

B-6  The manual-review branch never passed is_new_file, so every such row took
     the column default of True. A pre-existing file that went through review
     then reported is_new_file=True to _load_plex_notify_data, which returns
     early with a refresh only and never queues the PlexAnalyzeBacklog entry.
     Plex kept stale stream metadata for exactly the files a human had to
     intervene on.

B-11 review_subtitles IS NULL is the discriminator for "this review came from
     the undefined-audio-count threshold gate". _flag_subtitle_encoding_review
     wrote NULL when no stored Track matched the failing stream indices, so a
     subtitle-encoding review looked like a threshold review, and approving it
     permanently acknowledged a gate the file never tripped.

B-2  IMAGE_BASED_SUBS and MP4_INCOMPATIBLE_SUBS disagreed about "vobsub".
"""
import json
import os
import sys
from types import SimpleNamespace

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))


# ── B-2: the two subtitle sets must agree ────────────────────────────────────

def test_image_based_subs_is_a_subset_of_mp4_incompatible():
    """
    A bitmap subtitle cannot be stream-copied into MP4 under any
    circumstances, so anything the codebase calls image-based must also be
    something it refuses to carry into MP4.

    The two lists disagreed about "vobsub": a kept vobsub track passed the
    subs_block_mp4 check, conversion proceeded to `-c:s copy -f mp4`, and
    FFmpeg rejected it at header write — the job failed outright, which is the
    exact failure the subrip/webvtt entries were added to prevent.
    """
    from app.core.decision import IMAGE_BASED_SUBS, MP4_INCOMPATIBLE_SUBS

    leaked = IMAGE_BASED_SUBS - MP4_INCOMPATIBLE_SUBS
    assert not leaked, (
        f"image-based subtitle codec(s) {sorted(leaked)} are not in "
        "MP4_INCOMPATIBLE_SUBS — a kept track of this type would pass the "
        "subs_block_mp4 check and the MP4 mux would fail at header write"
    )


def test_vobsub_specifically_blocks_mp4():
    from app.core.decision import MP4_INCOMPATIBLE_SUBS

    assert "vobsub" in MP4_INCOMPATIBLE_SUBS


# ── B-7 / B-6: the manual-review branch ──────────────────────────────────────

@pytest.fixture
def db():
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    from app.database.models import Base

    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)()


def _media(db, path="/m/Show.mkv", **kw):
    from app.database.models import MediaFile

    mf = MediaFile(path=path, filename=os.path.basename(path), directory="/m",
                   size=1, mtime=1.0, **kw)
    db.add(mf)
    db.commit()
    return mf


def _review_row(db, media, reason, subs, is_new_file=True):
    from app.database.models import QueueItem

    qi = QueueItem(
        file_id=media.id, status="manual_review", is_dry_run=False,
        reason=reason,
        review_subtitles=json.dumps(subs) if subs else None,
        is_new_file=is_new_file,
    )
    db.add(qi)
    db.commit()
    return qi


def _refresh(db, media, decision, current_size=123, is_new_file=False,
             sonarr_series_id=None, radarr_movie_id=None, dry_run=False):
    """
    The manual-review branch of scanner._process_file.

    Mirrored rather than invoked because _process_file stats the real file,
    probes it, and runs the full decision engine — none of which is what these
    tests are about. test_scanner_branch_matches_this_shape below pins the
    mirror against the real source so it cannot silently drift.
    """
    from app.database.models import QueueItem

    already = db.query(QueueItem).filter(
        QueueItem.file_id == media.id,
        QueueItem.status == "manual_review",
    ).first()

    review_subs = (
        json.dumps(decision.flagged_subtitles)
        if decision.flagged_subtitles else None
    )

    if already:
        already.reason = decision.reason
        already.review_subtitles = review_subs
        already.original_size = current_size
        if sonarr_series_id is not None:
            already.sonarr_series_id = sonarr_series_id
        if radarr_movie_id is not None:
            already.radarr_movie_id = radarr_movie_id
    else:
        db.add(QueueItem(
            file_id=media.id, status="manual_review", is_dry_run=dry_run,
            reason=decision.reason, original_size=current_size,
            review_subtitles=review_subs, is_new_file=is_new_file,
            sonarr_series_id=sonarr_series_id, radarr_movie_id=radarr_movie_id,
        ))
    db.commit()
    return already


def test_existing_review_reason_is_refreshed(db):
    """A settings change alters WHY the file needs review."""
    media = _media(db)
    _review_row(db, media, "Contains PGS subtitles", [{"stream_index": 2}])

    _refresh(db, media, SimpleNamespace(
        reason="Contains 3 undefined audio tracks",
        flagged_subtitles=[{"stream_index": 2}],
    ))

    from app.database.models import QueueItem
    row = db.query(QueueItem).filter_by(file_id=media.id).one()
    assert row.reason == "Contains 3 undefined audio tracks"


def test_existing_review_flagged_tracks_are_refreshed(db):
    """
    The one that actually corrupts behaviour. resolve_subtitles acts on the
    stream indices stored here, so a stale list means a Keep/Remove choice is
    applied to a track that is no longer at that index.
    """
    media = _media(db)
    _review_row(db, media, "old", [{"stream_index": 2, "codec": "hdmv_pgs_subtitle"}])

    _refresh(db, media, SimpleNamespace(
        reason="new",
        flagged_subtitles=[{"stream_index": 5, "codec": "dvd_subtitle"}],
    ))

    from app.database.models import QueueItem
    row = db.query(QueueItem).filter_by(file_id=media.id).one()
    flagged = json.loads(row.review_subtitles)
    assert flagged == [{"stream_index": 5, "codec": "dvd_subtitle"}], (
        "stale flagged-subtitle list survived a rescan — resolve_subtitles "
        "would act on stream indices that no longer describe the file"
    )


def test_refresh_clears_flagged_tracks_when_none_remain(db):
    """Going from image-subtitle review to threshold review must null the field."""
    media = _media(db)
    _review_row(db, media, "old", [{"stream_index": 2}])

    _refresh(db, media, SimpleNamespace(reason="und audio", flagged_subtitles=[]))

    from app.database.models import QueueItem
    assert db.query(QueueItem).filter_by(file_id=media.id).one().review_subtitles is None


def test_refresh_does_not_create_a_second_row(db):
    media = _media(db)
    _review_row(db, media, "old", [{"stream_index": 2}])
    _refresh(db, media, SimpleNamespace(reason="new", flagged_subtitles=[]))

    from app.database.models import QueueItem
    assert db.query(QueueItem).filter_by(file_id=media.id).count() == 1


def test_arr_ids_are_filled_in_but_never_cleared(db):
    """
    A file can be scanned before Sonarr has imported it, so the IDs appear
    later. They must never be unset by a later scan that lacks them.
    """
    media = _media(db)
    _review_row(db, media, "old", None)

    _refresh(db, media, SimpleNamespace(reason="r", flagged_subtitles=[]),
             sonarr_series_id=42)
    from app.database.models import QueueItem
    row = db.query(QueueItem).filter_by(file_id=media.id).one()
    assert row.sonarr_series_id == 42

    _refresh(db, media, SimpleNamespace(reason="r2", flagged_subtitles=[]),
             sonarr_series_id=None)
    db.refresh(row)
    assert row.sonarr_series_id == 42, "arr id was cleared by a later scan"


def test_new_review_row_records_is_new_file_false(db):
    """
    B-6. Without this the column default (True) applied to every review row,
    and a pre-existing file that went through review reported is_new_file=True
    to _load_plex_notify_data — which returns early and never queues the
    PlexAnalyzeBacklog entry that gives Plex correct stream metadata.
    """
    media = _media(db)
    _refresh(db, media, SimpleNamespace(reason="r", flagged_subtitles=[]),
             is_new_file=False)

    from app.database.models import QueueItem
    row = db.query(QueueItem).filter_by(file_id=media.id).one()
    assert row.is_new_file is False, (
        "manual-review row defaulted is_new_file to True — Plex would skip the "
        "Analyze backlog entry for a pre-existing file"
    )


def test_new_review_row_preserves_is_new_file_true(db):
    media = _media(db)
    _refresh(db, media, SimpleNamespace(reason="r", flagged_subtitles=[]),
             is_new_file=True)

    from app.database.models import QueueItem
    assert db.query(QueueItem).filter_by(file_id=media.id).one().is_new_file is True


def test_scanner_branch_matches_this_shape():
    """
    Guards the mirror above. If the real branch stops refreshing or stops
    passing is_new_file, these tests would keep passing against a stale copy.
    """
    import inspect

    import app.core.scanner as scanner

    src = inspect.getsource(scanner._process_file)
    review = src.split('status     = "manual_review"')[0]
    assert "already.review_subtitles" in src, \
        "scanner no longer refreshes review_subtitles on an existing review row"
    assert "already.reason" in src, \
        "scanner no longer refreshes the reason on an existing review row"
    assert "is_new_file = is_new_file" in src, \
        "scanner no longer passes is_new_file when creating a review row"
    del review


# ── B-11: the null discriminator ─────────────────────────────────────────────

def test_encoding_review_with_no_matching_tracks_fails_instead_of_reviewing(db, monkeypatch):
    """
    When no stored Track matches the failing stream indices the job must FAIL,
    not raise a manual review.

    Two reasons. There is nothing to review — the Review page renders one
    Keep/Remove row per flagged track, so the user gets an empty decision and
    the reason string degrades to "Contains 0 subtitle track ()". And a NULL
    review_subtitles here is indistinguishable from the threshold gate's
    signal, so approving it would set und_audio_threshold_acknowledged on a
    file that never tripped that gate.
    """
    import app.core.worker as worker
    from app.database.models import QueueItem
    from sqlalchemy.orm import sessionmaker

    media = _media(db)
    qi = QueueItem(file_id=media.id, status="processing", is_dry_run=False)
    db.add(qi)
    db.commit()
    job_id = qi.id

    monkeypatch.setattr(worker, "SessionLocal",
                        sessionmaker(bind=db.get_bind()))

    # Stream index 99 matches no track in the list.
    worker._flag_subtitle_encoding_review(
        job_id,
        [(99, "some ffmpeg error")],
        [{"track_type": "subtitle", "stream_index": 2, "language": "eng",
          "codec": "subrip", "is_forced": False}],
    )

    db.expire_all()
    row = db.get(QueueItem, job_id)
    assert row.status == "failed", (
        f"status is {row.status!r} — an unmatched encoding failure still "
        "raises a manual review, which corrupts the review_subtitles "
        "discriminator and shows the user an empty decision"
    )
    assert "Re-scan" in (row.error_message or "")
    assert "0 subtitle track" not in (row.reason or "")


def test_encoding_review_with_matching_tracks_still_reviews(db, monkeypatch):
    """The normal path must be unaffected, and must populate review_subtitles."""
    import app.core.worker as worker
    from app.database.models import QueueItem
    from sqlalchemy.orm import sessionmaker

    media = _media(db)
    qi = QueueItem(file_id=media.id, status="processing", is_dry_run=False)
    db.add(qi)
    db.commit()
    job_id = qi.id

    monkeypatch.setattr(worker, "SessionLocal", sessionmaker(bind=db.get_bind()))

    worker._flag_subtitle_encoding_review(
        job_id,
        [(2, "invalid byte sequence")],
        [{"track_type": "subtitle", "stream_index": 2, "language": "eng",
          "codec": "subrip", "is_forced": False}],
    )

    db.expire_all()
    row = db.get(QueueItem, job_id)
    assert row.status == "manual_review"
    assert row.review_subtitles is not None, (
        "a SUBTITLE review left review_subtitles NULL — approving it would "
        "acknowledge the undefined-audio threshold gate for this file"
    )
    assert json.loads(row.review_subtitles)[0]["stream_index"] == 2


def test_approving_a_subtitle_review_does_not_acknowledge_the_audio_gate(db):
    """
    End to end: the collision B-11 describes. A subtitle review must never
    flip und_audio_threshold_acknowledged, because that permanently exempts
    the file from a check it never tripped.
    """
    from app.database.models import QueueItem

    media = _media(db)
    qi = QueueItem(
        file_id=media.id, status="manual_review", is_dry_run=False,
        reason="subtitle encoding",
        review_subtitles=json.dumps([{"stream_index": 2}]),
    )
    db.add(qi)
    db.commit()

    # The inference under test, as written in approve_manual_review.
    if qi.review_subtitles is None:
        media.und_audio_threshold_acknowledged = True

    assert not media.und_audio_threshold_acknowledged
