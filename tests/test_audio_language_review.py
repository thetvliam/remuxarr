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
    """A real file on disk with audio flags given as (stream_index, origin)."""
    from app.database.models import AudioLanguageFlag, MediaFile

    path = tmp_path / "Show.mkv"
    path.write_bytes(b"video")
    file = MediaFile(path=str(path), filename="Show.mkv",
                     directory=str(tmp_path), size=5, mtime=1.0, **media)
    db.add(file)
    db.commit()
    for stream_index, origin in flags:
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
