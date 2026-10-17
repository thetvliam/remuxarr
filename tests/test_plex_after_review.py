"""
A review that leaves a newly found file alone tells Plex.

Plex hears about a file when a job finishes. With Plex's own connection
removed from Sonarr or Radarr, Remuxarr is what tells Plex about an import,
and an import held for a question waits in review untold, which is right
while the answer might still change the file. When the review ended with
nothing to change it, no job followed, and Plex never heard of the file:

  • an answer that leaves the file needing nothing (the review is "skipped")
  • Skip, on the Review page or in a batch (the review is "cancelled")
  • a Language Review answer that leaves the file needing nothing, which
    re-checks the file from scratch and so replaces its review, or its
    pending job

Each now sends Plex a refresh, once everything is saved and in the
background. A newly found file is one whose review was raised the first time
Remuxarr saw it, which is what the queue item's is_new_file records: every
import, and a brand-new file a scan found, which Plex has not heard of either.
A file seen before is not told, and an answer that leads to a job leaves the
telling to the job, after it has changed the file.

Nothing is sent while dry run is on, whatever the answer: dry run sends
nothing to Plex, Sonarr or Radarr. The first version of this told Plex about
answers given in dry run, which is not what anyone running a dry run expects.
The check is made once, in plex.refresh_in_background, for every route.

The review routes are driven directly against an in-memory database, as
test_queue_routes.py and test_audio_language_review.py drive them. The
background refresh is the real one, waited for, with only the Plex call
recorded, so the dry-run check and the one refresh per folder are part of
what each route is tested through.

Verified by mutation, 15 applied, 15 killed, every one surviving the whole
suite without this file:

  • a file seen before told too
  • an answer that leaves the file needing nothing, or Skip, not counted (two)
  • an answer leading to a job told early
  • answers or batch skips never collected (two)
  • the Skip button never telling, or cancelling a pending job telling (two)
  • Language Review telling for any file, telling for a job, never sending,
    or counting only a review and not an import's pending job (four)
  • one refresh per file rather than per folder
  • the background refresh sent with Plex notifications off, or while dry
    run is on (two)
"""
import json

import pytest

PLEX_ON = {
    "plex_enabled":       True,
    "plex_url":           "http://plex:32400",
    "plex_token":         "PLEX-TOKEN",
    "plex_path_mappings": ["/m=/data"],
}


@pytest.fixture
def db():
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    from app.database.models import Base

    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)()


def _settings(db, *, dry_run, **values):
    from app.database.models import AppSetting

    for key, value in {**PLEX_ON, "dry_run_mode": dry_run, **values}.items():
        db.merge(AppSetting(key=key, value=json.dumps(value)))
    db.commit()


@pytest.fixture
def told(monkeypatch):
    """
    The paths Plex is sent, in order. The routes start the real background
    refresh; this waits for each thread it starts, so a test reads the
    outcome rather than racing it.
    """
    import app.api.routes._language_review as lr
    import app.api.routes.queue as queue
    import app.core.plex as plex

    sent = []
    monkeypatch.setattr(plex, "notify_plex_new_file",
                        lambda url, token, mappings, path: sent.append(path))

    real = plex.refresh_in_background

    def and_wait(cfg, paths, context):
        thread = real(cfg, paths, context)
        if thread is not None:
            thread.join(5)
        return thread

    monkeypatch.setattr(queue, "refresh_in_background", and_wait)
    monkeypatch.setattr(lr, "refresh_in_background", and_wait)
    return sent


# ── The Review page ──────────────────────────────────────────────────────────

def _review_file(db, file_id, *, first_seen=True):
    """A file waiting in review for one styled subtitle, stream 2, in a
    folder of its own. Keeping it leaves an MKV with nothing to change;
    removing it leaves a conversion."""
    from app.database.models import MediaFile, QueueItem, Track

    folder = f"/m/Show {file_id}"
    path = f"{folder}/Show {file_id}.mkv"
    db.add(MediaFile(id=file_id, path=path, filename=path.rsplit("/", 1)[-1],
                     directory=folder, size=1, mtime=1.0, container="mkv",
                     video_codec="h264"))
    db.add(Track(file_id=file_id, stream_index=0, track_type="video", codec="h264"))
    db.add(Track(file_id=file_id, stream_index=1, track_type="audio",
                 codec="aac", language="eng"))
    db.add(Track(file_id=file_id, stream_index=2, track_type="subtitle",
                 codec="ass", language="eng", title="Signs"))
    db.add(QueueItem(id=file_id, file_id=file_id, status="manual_review",
                     is_dry_run=False, is_new_file=first_seen,
                     reason="needs review", review_reason="font_attachments",
                     review_subtitles=json.dumps(
                         [{"stream_index": 2, "codec": "ass", "reason": "styled"}])))
    db.commit()
    return path


def _apply(db, files=(), skips=()):
    from app.api.routes.queue import (ReviewApplyRequest, ReviewFileDecision,
                                      apply_review_decisions)

    return apply_review_decisions(
        ReviewApplyRequest(
            files=[ReviewFileDecision(file_id=fid, answers=answers)
                   for fid, answers in files],
            skips=list(skips),
        ),
        db,
    )


def _statuses(db):
    from app.database.models import QueueItem

    return [(i.status, i.is_dry_run) for i in db.query(QueueItem).order_by(QueueItem.id)]


def test_an_answer_that_leaves_nothing_to_do_tells_plex(db, told):
    _settings(db, dry_run=False)
    path = _review_file(db, 1)

    _apply(db, files=[(1, {2: "keep"})])

    assert _statuses(db) == [("skipped", False)]
    assert told == [path]


def test_an_answer_that_leads_to_a_job_leaves_plex_to_the_job(db, told):
    """Told now, Plex would index the file before the job changed it."""
    _settings(db, dry_run=False)
    _review_file(db, 1)

    _apply(db, files=[(1, {2: "remove"})])

    assert _statuses(db) == [("pending", False)]
    assert told == []


@pytest.mark.parametrize("answer,expected", [
    ({"files": [(1, {2: "keep"})]}, ("skipped", False)),
    ({"files": [(1, {2: "remove"})]}, ("pending", True)),
    ({"skips": [1]}, ("cancelled", False)),
], ids=["needs nothing", "a dry-run job", "skipped"])
def test_nothing_is_sent_while_dry_run_is_on(db, told, answer, expected):
    """Dry run sends nothing to Plex, Sonarr or Radarr, whatever the answer."""
    _settings(db, dry_run=True)
    _review_file(db, 1)

    _apply(db, **answer)

    assert _statuses(db) == [expected]
    assert told == []


def test_a_file_seen_before_is_not_told(db, told):
    """Its review was raised on a later look, so Plex already knows it."""
    _settings(db, dry_run=False)
    _review_file(db, 1, first_seen=False)

    _apply(db, files=[(1, {2: "keep"})])

    assert _statuses(db) == [("skipped", False)]
    assert told == []


def test_a_page_of_answers_and_skips_is_told_together(db, told):
    """The files the answers and the skips left alone, after every commit."""
    _settings(db, dry_run=False)
    kept = _review_file(db, 1)
    _review_file(db, 2)
    skipped = _review_file(db, 3)

    _apply(db, files=[(1, {2: "keep"}), (2, {2: "remove"})], skips=[3])

    assert _statuses(db) == [("skipped", False), ("pending", False),
                             ("cancelled", False)]
    assert told == [kept, skipped]


def test_the_skip_button_tells_plex(db, told):
    from app.api.routes.queue import cancel_item

    _settings(db, dry_run=False)
    path = _review_file(db, 1)

    cancel_item(1, db)

    assert told == [path]


def test_the_skip_button_sends_nothing_while_dry_run_is_on(db, told):
    from app.api.routes.queue import cancel_item

    _settings(db, dry_run=True)
    _review_file(db, 1)

    cancel_item(1, db)

    assert told == []


def test_cancelling_a_pending_job_does_not(db, told):
    """Not a review: the file goes back to the next scan, which queues it
    again, and that job tells Plex."""
    from app.api.routes.queue import cancel_item
    from app.database.models import MediaFile, QueueItem

    _settings(db, dry_run=False)
    db.add(MediaFile(id=1, path="/m/Show/Show 1.mkv", filename="Show 1.mkv",
                     directory="/m/Show", size=1, mtime=1.0))
    db.add(QueueItem(id=1, file_id=1, status="pending", is_new_file=True))
    db.commit()

    cancel_item(1, db)

    assert told == []


# ── Language Review ──────────────────────────────────────────────────────────

def _flagged(db, tmp_path, item_status, *, first_seen=True):
    """A real file with one flagged audio track and an active queue item."""
    from app.database.models import AudioLanguageFlag, MediaFile, QueueItem, Track

    path = tmp_path / "Show.mkv"
    path.write_bytes(b"video")
    media = MediaFile(path=str(path), filename="Show.mkv",
                      directory=str(tmp_path), size=5, mtime=1.0)
    db.add(media)
    db.commit()
    db.add(Track(file_id=media.id, stream_index=1, track_type="audio",
                 codec="aac", language="und", channels=6, channel_layout="5.1"))
    db.add(AudioLanguageFlag(file_id=media.id, stream_index=1,
                             detected_language="und", origin="threshold"))
    db.add(QueueItem(file_id=media.id, status=item_status, reason=item_status,
                     is_new_file=first_seen))
    db.commit()
    return media.path, db.query(AudioLanguageFlag).one().id


def _answer(db, monkeypatch, flag_id, *, dry_run, outcome):
    """Apply English, with the re-check landing on `outcome`, as the ScanStats
    _process_file fills in say: "skipped", "queued" or "manual_review"."""
    import app.api.routes._language_review as lr
    from app.api.routes._language_review import ApplyRequest
    from app.api.routes.audio_language import apply_language

    def recheck(_db, _path, _cfg, *, stats, **_kw):
        setattr(stats, outcome, 1)

    monkeypatch.setattr(lr, "get_app_settings",
                        lambda _db: {**PLEX_ON, "dry_run_mode": dry_run})
    monkeypatch.setattr(lr, "_process_file", recheck)
    result = apply_language(ApplyRequest(flag_ids=[flag_id], target_language="eng"), db)
    assert result["applied"] == 1, result["errors"]


def test_a_language_answer_that_leaves_a_review_alone_tells_plex(
        db, tmp_path, monkeypatch, told):
    path, flag_id = _flagged(db, tmp_path, "manual_review")

    _answer(db, monkeypatch, flag_id, dry_run=False, outcome="skipped")

    assert told == [path]


@pytest.mark.parametrize("outcome", ["skipped", "queued"],
                         ids=["needs nothing", "a dry-run job"])
def test_a_language_answer_sends_nothing_while_dry_run_is_on(
        db, tmp_path, monkeypatch, told, outcome):
    _, flag_id = _flagged(db, tmp_path, "manual_review")

    _answer(db, monkeypatch, flag_id, dry_run=True, outcome=outcome)

    assert told == []


@pytest.mark.parametrize("outcome", ["queued", "manual_review"],
                         ids=["a job", "another review"])
def test_a_language_answer_that_leads_on_does_not(
        db, tmp_path, monkeypatch, told, outcome):
    _, flag_id = _flagged(db, tmp_path, "manual_review")

    _answer(db, monkeypatch, flag_id, dry_run=False, outcome=outcome)

    assert told == []


def test_a_language_answer_for_a_file_seen_before_does_not(
        db, tmp_path, monkeypatch, told):
    _, flag_id = _flagged(db, tmp_path, "manual_review", first_seen=False)

    _answer(db, monkeypatch, flag_id, dry_run=False, outcome="skipped")

    assert told == []


def test_a_language_answer_that_cancels_an_imports_job_tells_plex(
        db, tmp_path, monkeypatch, told):
    """The pending job it replaces was what would have told Plex."""
    path, flag_id = _flagged(db, tmp_path, "pending")

    _answer(db, monkeypatch, flag_id, dry_run=False, outcome="skipped")

    assert told == [path]


# ── The background refresh ───────────────────────────────────────────────────

@pytest.fixture
def sent(monkeypatch):
    import app.core.plex as plex

    calls = []
    monkeypatch.setattr(plex, "notify_plex_new_file",
                        lambda url, token, mappings, path: calls.append(path))
    return calls


def test_the_background_refresh_sends_one_per_folder(sent):
    """Answering a season sends one refresh for its folder, not one per
    episode: a refresh is scoped to the folder anyway."""
    import app.core.plex as plex

    thread = plex.refresh_in_background(
        {**PLEX_ON, "dry_run_mode": False},
        ["/m/Show/S01E01.mkv", "/m/Show/S01E02.mkv", "/m/Film/Film.mkv"],
        "a test")
    thread.join(5)

    assert sent == ["/m/Show/S01E01.mkv", "/m/Film/Film.mkv"]


@pytest.mark.parametrize("cfg", [
    {**PLEX_ON, "dry_run_mode": False, "plex_enabled": False},
    {**PLEX_ON, "dry_run_mode": True},
], ids=["Plex off", "dry run on"])
def test_the_background_refresh_sends_nothing(sent, cfg):
    import app.core.plex as plex

    thread = plex.refresh_in_background(cfg, ["/m/Show/S01E01.mkv"], "a test")

    assert thread is None
    assert sent == []
