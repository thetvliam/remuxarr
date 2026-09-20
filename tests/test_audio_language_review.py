"""
Audio Language Review — filtering and facet counts, and one flag per track.

The list is server-paginated, so both filters have to run on the server.
Narrowing only the loaded page would report fewer matches than exist, and
the section's "select all" acts on what the server returned — so an
under-reported list means a bulk action silently skips files the user
believes are included.

The flags are one row per track, written by scanner._upsert_language_flags.
That function's audio half had no test at all before it moved to that
shape: each of the mutations below survived the full 1539-test suite, and
each is killed by the tests at the end of this file.

  • the row for a track still flagged deleted as stale
  • that row deleted and re-added, under a new id
  • rows for tracks no longer flagged kept
  • an ignored file still flagged
  • the list rows not saying where a flag came from

Each answer is recorded on the switch for its flag's origin, so taking back
one kind of answer cannot undo the other on the same file. Six mutations of
that survived the full 1547-test suite, and each is killed by the tests in
the last section:

  • threshold flags answered on audio_language_ignored
  • the audio review configured without origin switches
  • only the first flag's origin counted when confirming a file
  • Set language clearing every switch
  • Set language clearing none
  • Set language clearing only audio_language_ignored, as it used to

Two more were already killed in test_forge_selection_and_counts.py: mismatch
flags answered on the acknowledgement, and a file with no flags left
getting no switch at all.

Two more came with the threshold's own flags, both surviving the suite
before the last two tests in this file existed:

  • audio_language_ignored silencing threshold flags as well as mismatches
  • a row keeping its old origin when the route that flagged it changes
"""



def _db():
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    from app.database.models import Base

    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)()


def _flag(db, filename, language, file_id):
    """One flagged file: a MediaFile plus the AudioLanguageFlag pointing at it."""
    from app.database.models import AudioLanguageFlag, MediaFile

    media = MediaFile(
        id=file_id,
        path=f"/media/tv/{filename}",
        filename=filename,
        directory="/media/tv",
        size=1_000,
        mtime=0.0,
        container="mkv",
    )
    db.add(media)
    db.flush()
    db.add(AudioLanguageFlag(
        file_id=file_id,
        stream_index=1,
        detected_language=language,
    ))
    db.commit()
    return media


def _seed(db):
    # Two shows. "King of the Hill" is mistagged two different ways, which is
    # the case the language filter exists for: correcting the Dutch-tagged
    # episodes should not touch the Danish-tagged ones.
    _flag(db, "King of the Hill S01E01.mkv", "dut", 1)
    _flag(db, "King of the Hill S01E02.mkv", "dut", 2)
    _flag(db, "King of the Hill S01E03.mkv", "dan", 3)
    _flag(db, "Cowboy Bebop S01E01.mkv",     "jpn", 4)
    _flag(db, "Cowboy Bebop S01E02.mkv",     "jpn", 5)


def _list(db, **kwargs):
    from app.api.routes.audio_language import list_flags

    params = {"search": "", "language": "", "limit": 50, "offset": 0}
    params.update(kwargs)
    return list_flags(db=db, **params)


def test_language_filter_narrows_to_one_tag():
    db = _db()
    _seed(db)

    result = _list(db, language="dut")

    assert result["total"] == 2
    assert {i["filename"] for i in result["items"]} == {
        "King of the Hill S01E01.mkv",
        "King of the Hill S01E02.mkv",
    }


def test_search_and_language_combine_with_and():
    """
    The whole point of having both: one show, one of its two wrong tags.
    If these ORed, applying a correction would hit the Danish-tagged
    episode and the unrelated anime as well.
    """
    db = _db()
    _seed(db)

    result = _list(db, search="king of the hill", language="dut")

    assert result["total"] == 2
    assert all("King of the Hill" in i["filename"] for i in result["items"])
    assert all(i["detected_language"] == "dut" for i in result["items"])


def test_language_filter_is_case_insensitive():
    db = _db()
    _seed(db)

    assert _list(db, language="DUT")["total"] == 2


def test_facets_list_every_tag_with_counts():
    db = _db()
    _seed(db)

    langs = {e["language"]: e["count"] for e in _list(db)["languages"]}

    assert langs == {"dut": 2, "dan": 1, "jpn": 2}


def test_facets_are_ordered_by_count_descending():
    """Most common wrong tag first — that is the one worth fixing in bulk."""
    db = _db()
    _seed(db)

    counts = [e["count"] for e in _list(db)["languages"]]

    assert counts == sorted(counts, reverse=True)


def test_facets_honour_search():
    """
    Searching a show should narrow the dropdown to the tags that show
    actually has, so the options offered are all non-empty.
    """
    db = _db()
    _seed(db)

    langs = {e["language"]: e["count"] for e in _list(db, search="king of the hill")["languages"]}

    assert langs == {"dut": 2, "dan": 1}
    assert "jpn" not in langs


def test_facets_ignore_the_language_filter():
    """
    Faceting on the language filter itself would collapse the dropdown to
    whichever option was selected, leaving no way to switch to another
    without clearing first. Selecting "dut" must still show that "dan"
    exists within the current search.
    """
    db = _db()
    _seed(db)

    result = _list(db, search="king of the hill", language="dut")
    langs = {e["language"]: e["count"] for e in result["languages"]}

    assert result["total"] == 2, "the item list should be filtered"
    assert langs == {"dut": 2, "dan": 1}, (
        "the dropdown should still offer the alternatives — got "
        f"{langs}, which would strand the user on their own selection"
    )


def test_untagged_flags_are_reported_as_und():
    """
    detected_language is nullable. A null would render as an empty option
    in the dropdown that no one could identify or select meaningfully.
    """
    db = _db()
    _flag(db, "Mystery.mkv", None, 9)

    langs = {e["language"]: e["count"] for e in _list(db)["languages"]}

    assert langs == {"und": 1}


def test_pagination_reflects_the_filtered_total():
    """
    total drives "select all" and the result count. If it counted unfiltered
    rows while items were filtered, the UI would claim more matches than it
    could ever show.
    """
    db = _db()
    _seed(db)

    result = _list(db, language="jpn", limit=1)

    assert result["total"] == 2, "total should count all matches, not the page"
    assert len(result["items"]) == 1, "items should honour the limit"


def test_search_treats_underscore_and_percent_as_literal_characters():
    """
    The search box takes a filename, not a LIKE pattern.

    Worse here than a wrong result list, because of the module docstring's
    own point: "select all" acts on what the server returned. An
    unescaped `_` pulls in files the user never searched for, and the
    next click applies a language tag to all of them.

    Only the audio router is exercised — list_flags is the shared
    _language_review implementation, so the subtitle router runs the same
    query builder. See test_language_review_isolation.py for what is NOT
    shared between them.
    """
    db = _db()
    _flag(db, "The_Movie.mkv",      "dut", 1)
    _flag(db, "TheXMovie.mkv",      "dut", 2)
    _flag(db, "Show 100% Real.mkv", "jpn", 3)
    _flag(db, "Show 100Z Real.mkv", "jpn", 4)

    underscore = _list(db, search="The_Movie")
    assert [i["filename"] for i in underscore["items"]] == ["The_Movie.mkv"]
    assert underscore["total"] == 1

    percent = _list(db, search="100%")
    assert [i["filename"] for i in percent["items"]] == ["Show 100% Real.mkv"]
    assert percent["total"] == 1


def test_facet_counts_also_honour_wildcard_escaping():
    """
    The facets are built from the same filtered query, so an unescaped
    term inflates the language dropdown too — offering tags that only
    appear on files the search should not have matched.
    """
    db = _db()
    _flag(db, "The_Movie.mkv", "dut", 1)
    _flag(db, "TheXMovie.mkv", "jpn", 2)

    langs = {e["language"]: e["count"] for e in _list(db, search="The_Movie")["languages"]}

    assert langs == {"dut": 1}


# ── One row per track, kept up to date by the scanner ────────────────────────

def _upsert(db, media, mismatch, undefined=()):
    """
    Run the scanner's flag update for a decision reporting `mismatch` and
    `undefined` (the per-track entries the threshold and always_ask raise).
    """
    from types import SimpleNamespace

    from app.core.scanner import _upsert_language_flags

    _upsert_language_flags(db, media, SimpleNamespace(
        audio_language_mismatch=mismatch,
        undefined_audio_flags=list(undefined),
        subtitle_language_mismatches=[],
    ))
    db.commit()


def _rows(db, file_id):
    from app.database.models import AudioLanguageFlag

    return (db.query(AudioLanguageFlag)
              .filter(AudioLanguageFlag.file_id == file_id)
              .order_by(AudioLanguageFlag.stream_index)
              .all())


def test_a_rescan_keeps_the_row_of_a_track_still_flagged():
    """
    Updated in place, under the same id. The review page selects rows by id,
    so a scan landing while someone is choosing must not swap them out. A
    second file's row sits above this one, so a re-added row could not get
    its old id back by accident.
    """
    db = _db()
    media = _flag(db, "Show S01E01.mkv", "dut", 1)
    _flag(db, "Show S01E02.mkv", "dut", 2)
    (before,) = _rows(db, 1)

    _upsert(db, media, {"stream_index": 1, "language": "dan"})

    (after,) = _rows(db, 1)
    assert after.id == before.id
    assert after.detected_language == "dan"


def test_a_track_no_longer_flagged_loses_its_row():
    db = _db()
    media = _flag(db, "Show S01E01.mkv", "dut", 1)

    _upsert(db, media, {"stream_index": 2, "language": "dut"})

    assert [(r.stream_index, r.origin) for r in _rows(db, 1)] == [(2, "mismatch")]


def test_an_ignored_file_keeps_no_audio_flags():
    """Confirm correct is what set this, and it promises the file stays unflagged."""
    db = _db()
    media = _flag(db, "Show S01E01.mkv", "dut", 1)
    media.audio_language_ignored = True
    db.commit()

    _upsert(db, media, {"stream_index": 1, "language": "dut"})

    assert _rows(db, 1) == []


def test_each_listed_row_says_where_it_came_from():
    """The origin is what tells a threshold flag from a mismatch flag."""
    db = _db()
    _flag(db, "Show S01E01.mkv", "dut", 1)

    (row,) = _list(db)["items"]

    assert row["origin"] == "mismatch"


# ── Which switch an answer is recorded on ────────────────────────────────────

def _flagged_file(db, tmp_path, flags, **media):
    """
    A real file on disk with audio flags given as (stream_index, origin),
    and the tracks those flags describe: an answer is stored against what a
    track is, so a flag naming a stream the file's tracks do not have is
    reported rather than answered.
    """
    from app.database.models import AudioLanguageFlag, MediaFile, Track

    path = tmp_path / "Show.mkv"
    path.write_bytes(b"video")
    file = MediaFile(path=str(path), filename="Show.mkv",
                     directory=str(tmp_path), size=5, mtime=1.0, **media)
    db.add(file)
    db.commit()
    for stream_index, origin in flags:
        db.add(Track(file_id=file.id, stream_index=stream_index,
                     track_type="audio", codec="aac", language="und",
                     channels=6, channel_layout="5.1"))
        db.add(AudioLanguageFlag(file_id=file.id, stream_index=stream_index,
                                 detected_language="und", origin=origin))
    db.commit()
    return file


def _real_run(monkeypatch):
    import app.api.routes._language_review as lr

    monkeypatch.setattr(lr, "get_app_settings",
                        lambda _db: {"dry_run_mode": False})
    monkeypatch.setattr(lr, "_process_file", lambda *a, **k: None)


def _confirm(db, file, monkeypatch):
    from app.api.routes._language_review import IgnoreRequest
    from app.api.routes.audio_language import ignore_flags

    _real_run(monkeypatch)
    ignore_flags(IgnoreRequest(file_ids=[file.id]), db)
    db.refresh(file)


def _choose(db, file, stream_index, monkeypatch):
    from app.api.routes._language_review import ApplyRequest
    from app.api.routes.audio_language import apply_language
    from app.database.models import AudioLanguageFlag

    flag = (db.query(AudioLanguageFlag)
              .filter(AudioLanguageFlag.file_id == file.id,
                      AudioLanguageFlag.stream_index == stream_index)
              .one())
    _real_run(monkeypatch)
    apply_language(ApplyRequest(flag_ids=[flag.id], target_language="eng"), db)
    db.refresh(file)


def test_confirming_a_threshold_flag_sets_the_acknowledgement_only(
        tmp_path, monkeypatch):
    """
    The acknowledgement is the threshold's own switch, the one Clear
    acknowledged takes back. Recorded on audio_language_ignored instead, the
    answer could not be taken back that way, and it would also silence any
    unanswered language-mismatch question on the same file.
    """
    db = _db()
    file = _flagged_file(db, tmp_path, [(1, "threshold")])

    _confirm(db, file, monkeypatch)

    assert file.und_audio_threshold_acknowledged is True
    assert file.audio_language_ignored is not True


def test_confirming_a_file_with_both_kinds_sets_both(tmp_path, monkeypatch):
    """Confirm correct acts on the whole file, so every question on it is answered."""
    db = _db()
    file = _flagged_file(db, tmp_path, [(1, "mismatch"), (2, "threshold")])

    _confirm(db, file, monkeypatch)

    assert file.audio_language_ignored is True
    assert file.und_audio_threshold_acknowledged is True


def test_choosing_a_language_for_a_mismatch_leaves_the_acknowledgement(
        tmp_path, monkeypatch):
    """
    Choosing a language takes back the switch for the question it answers —
    a more specific, more recent decision. A mismatch answer says nothing
    about the threshold, so an acknowledgement on the same file stands.
    """
    db = _db()
    file = _flagged_file(db, tmp_path, [(1, "mismatch")],
                         audio_language_ignored=True,
                         und_audio_threshold_acknowledged=True)

    _choose(db, file, 1, monkeypatch)

    assert file.audio_language_ignored is False
    assert file.und_audio_threshold_acknowledged is True


def test_choosing_a_language_for_a_threshold_flag_takes_back_only_its_switch(
        tmp_path, monkeypatch):
    """
    The mirror of the test above, and the one that catches Set language
    still resetting only audio_language_ignored, as it did before flags had
    an origin.
    """
    db = _db()
    file = _flagged_file(db, tmp_path, [(1, "threshold")],
                         audio_language_ignored=True,
                         und_audio_threshold_acknowledged=True)

    _choose(db, file, 1, monkeypatch)

    assert file.und_audio_threshold_acknowledged is False
    assert file.audio_language_ignored is True


def test_an_ignored_file_still_gets_its_threshold_flags():
    """
    audio_language_ignored answers language mismatches, and only those. The
    undefined tracks over the threshold are a different question, so
    confirming a mismatch must not silence them — the two switches exist
    precisely so one answer cannot stand in for the other.
    """
    db = _db()
    media = _flag(db, "Show S01E01.mkv", "dut", 1)
    media.audio_language_ignored = True
    db.commit()

    _upsert(db, media, {"stream_index": 1, "language": "dut"},
            undefined=[{"stream_index": 2, "origin": "threshold"}])

    assert [(r.stream_index, r.origin) for r in _rows(db, 1)] == [(2, "threshold")]


def test_a_rows_origin_follows_the_route_that_flagged_it():
    """
    always_ask asks about an undefined track below the threshold; the same
    track belongs to the threshold once the file reaches it. Left on the old
    origin, the row would be answered on the wrong switch — Confirm correct
    would write audio_language_ignored for a threshold question.
    """
    db = _db()
    media = _flag(db, "Show S01E01.mkv", "und", 1)
    (before,) = _rows(db, 1)
    assert before.origin == "mismatch"

    _upsert(db, media, None,
            undefined=[{"stream_index": 1, "origin": "threshold"}])

    (after,) = _rows(db, 1)
    assert after.id == before.id
    assert after.origin == "threshold"


def test_a_flag_whose_track_is_gone_is_reported_not_answered(tmp_path, monkeypatch):
    """
    The flag names a stream the file's tracks no longer have, so the file was
    re-probed under the page: the answer cannot be tied to a track, and the
    flag itself describes one that may be gone. Reported, and the file left
    alone — flags and all — rather than counted as applied while nothing was
    recorded.
    """
    from app.api.routes._language_review import ApplyRequest
    from app.api.routes.audio_language import apply_language
    from app.database.models import AudioLanguageFlag, Track

    db = _db()
    file = _flagged_file(db, tmp_path, [(1, "mismatch")])
    db.query(Track).filter(Track.file_id == file.id).delete()
    db.commit()
    flag_id = db.query(AudioLanguageFlag).one().id

    _real_run(monkeypatch)
    result = apply_language(
        ApplyRequest(flag_ids=[flag_id], target_language="eng"), db)

    db.expire_all()
    assert result["applied"] == 0
    assert [e["file_id"] for e in result["errors"]] == [file.id]
    assert db.query(AudioLanguageFlag).count() == 1
    assert db.get(type(file), file.id).audio_language_overrides in (None, "{}")


# ── What Apply does to the file's queue items ────────────────────────────────
#
# The audio and subtitle reviews share one implementation now, so these run
# against the audio router only: a second copy through the subtitle router
# would re-run the same function with a different flag table, which
# test_language_review_isolation.py already pins.
#
# This is the half of apply_language the merge was FOR. Its comments record
# each of these as a bug found once and then fixed again separately in the
# other copy — and each of the three mutations below survived the full
# 1599-test suite before these tests existed:
#
#   • the active-item delete unfiltered by status
#   • a file with a running job cleared rather than skipped
#   • the arr ids not carried into the reprocess
#
# Sharing the code means a regression in any of them now breaks both reviews
# at once, which is the trade the merge made: one place to fix, and one
# place where nothing was watching.
#
# EQUIVALENT MUTANT, recorded rather than killed: adding "processing" to the
# status list of the active-item delete changes nothing, because the guard
# above it returns first and a "processing" row therefore never reaches the
# delete. It becomes reachable the moment that guard goes, which is what
# test_a_file_whose_job_is_running_is_reported_not_requeued holds in place.


def _queue_item(db, file_id, status, **kw):
    from app.database.models import QueueItem

    item = QueueItem(file_id=file_id, status=status, reason=status, **kw)
    db.add(item)
    db.commit()
    return item


def test_applying_leaves_the_files_history_rows_alone(tmp_path, monkeypatch):
    """
    Only the ACTIVE queue item is cleared to make way for the reprocess.

    A file accumulates a row per past scan — completed, failed, cancelled —
    and those are its history, read by the History tabs. An unfiltered
    delete takes them with it, and worse, an unordered .first() can return
    one of them INSTEAD of the live row: the active item then survives,
    _process_file's own in-progress check finds it and declines to create a
    new one, and the override is saved while the reprocess that applies it
    never runs, with nothing shown anywhere.
    """
    from app.api.routes._language_review import ApplyRequest
    from app.api.routes.audio_language import apply_language
    from app.database.models import AudioLanguageFlag, QueueItem

    db = _db()
    file = _flagged_file(db, tmp_path, [(1, "mismatch")])
    _queue_item(db, file.id, "completed")
    _queue_item(db, file.id, "failed")
    _queue_item(db, file.id, "pending")
    flag_id = db.query(AudioLanguageFlag).one().id

    _real_run(monkeypatch)
    result = apply_language(
        ApplyRequest(flag_ids=[flag_id], target_language="eng"), db)

    assert result["applied"] == 1, result["errors"]
    left = sorted(i.status for i in db.query(QueueItem).all())
    assert left == ["completed", "failed"], (
        f"the active item should go and the history should stay, got {left}"
    )


def test_a_file_whose_job_is_running_is_reported_not_requeued(
        tmp_path, monkeypatch):
    """
    A "processing" row is skipped, not cleared.

    Deleting it does nothing to the FFmpeg process already running — that is
    what worker.abort_job is for, and it is not called here. The running job
    would finish invisibly while the reprocess queued a second one against
    the same final path, so the stale pre-override output could land last
    and overwrite the corrected file.

    The choice is still saved: the override is committed before this point,
    the running job rewrites the file, and the next delta scan picks it up.
    So the file is reported rather than counted, and nothing is queued.
    """
    from app.api.routes._language_review import ApplyRequest
    from app.api.routes.audio_language import apply_language
    from app.database.models import AudioLanguageFlag, QueueItem

    db = _db()
    file = _flagged_file(db, tmp_path, [(1, "mismatch")])
    _queue_item(db, file.id, "processing")
    flag_id = db.query(AudioLanguageFlag).one().id

    called = []
    _real_run(monkeypatch)
    import app.api.routes._language_review as lr
    monkeypatch.setattr(lr, "_process_file",
                        lambda *a, **k: called.append(a))

    result = apply_language(
        ApplyRequest(flag_ids=[flag_id], target_language="eng"), db)

    db.expire_all()
    assert result["applied"] == 0
    assert [e["file_id"] for e in result["errors"]] == [file.id]
    assert called == [], "the file was re-queued while its job was running"
    assert [i.status for i in db.query(QueueItem).all()] == ["processing"], (
        "the running job's row was cleared"
    )
    # The point of skipping rather than refusing: the answer is kept.
    assert db.get(type(file), file.id).audio_language_overrides not in (None, "{}")


def test_the_reprocess_keeps_the_files_sonarr_and_radarr_linkage(
        tmp_path, monkeypatch):
    """
    The arr ids are read off the item being deleted and handed to the
    reprocess.

    A pending item can carry them — a webhook-queued file does — and they
    are what makes the finished job fire RescanSeries or RescanMovie. Lost
    here, the correction is written to disk and the library is never told,
    which looks like the whole thing not having worked.
    """
    from app.api.routes._language_review import ApplyRequest
    from app.api.routes.audio_language import apply_language
    from app.database.models import AudioLanguageFlag

    db = _db()
    file = _flagged_file(db, tmp_path, [(1, "mismatch")])
    _queue_item(db, file.id, "pending", sonarr_series_id=77, radarr_movie_id=12)
    flag_id = db.query(AudioLanguageFlag).one().id

    seen = {}
    _real_run(monkeypatch)
    import app.api.routes._language_review as lr
    monkeypatch.setattr(lr, "_process_file",
                        lambda *a, **kw: seen.update(kw))

    result = apply_language(
        ApplyRequest(flag_ids=[flag_id], target_language="eng"), db)

    assert result["applied"] == 1, result["errors"]
    assert seen.get("sonarr_series_id") == 77
    assert seen.get("radarr_movie_id") == 12


def test_the_newest_active_item_is_the_one_whose_linkage_is_kept(
        tmp_path, monkeypatch):
    """
    With more than one active item, the ids come off the most recent.

    Defensive rather than load-bearing: the guards elsewhere against
    double-queueing mean there is at most one in practice, which is why the
    query says so where it orders. Pinned anyway, because "whichever row
    the database happened to return" is not the same decision as "the
    newest", and only one of the two survives someone removing the
    order_by while tidying.

    Timestamps are set explicitly. Both rows would otherwise be created
    inside the same clock tick and the order would be arbitrary, so the
    test would pass or fail on how fast the machine is.
    """
    from datetime import datetime

    from app.api.routes._language_review import ApplyRequest
    from app.api.routes.audio_language import apply_language
    from app.database.models import AudioLanguageFlag, QueueItem

    db = _db()
    file = _flagged_file(db, tmp_path, [(1, "mismatch")])
    _queue_item(db, file.id, "pending", sonarr_series_id=11,
                created_at=datetime(2024, 1, 1, 0, 0, 0))
    _queue_item(db, file.id, "manual_review", sonarr_series_id=22,
                created_at=datetime(2024, 6, 1, 0, 0, 0))
    flag_id = db.query(AudioLanguageFlag).one().id

    seen = {}
    _real_run(monkeypatch)
    import app.api.routes._language_review as lr
    monkeypatch.setattr(lr, "_process_file", lambda *a, **kw: seen.update(kw))

    result = apply_language(
        ApplyRequest(flag_ids=[flag_id], target_language="eng"), db)

    assert result["applied"] == 1, result["errors"]
    assert seen.get("sonarr_series_id") == 22, (
        "the older item's linkage was carried over instead of the newer one's"
    )
    assert db.query(QueueItem).count() == 0, "both active items should go"
