"""
Subtitle Language Review — filtering and facet counts.

Mirrors test_audio_language_review; the two endpoints are the same shape
and the same rules apply, so the same cases are asserted against both.

The list is server-paginated, so both filters have to run on the server.
Narrowing only the loaded page would report fewer matches than exist, and
the section's "select all" acts on what the server returned — so an
under-reported list means a bulk action silently skips files the user
believes are included.
"""



def _db():
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    from app.database.models import Base

    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)()


def _flag(db, filename, language, file_id):
    """One flagged file: a MediaFile plus the SubtitleLanguageFlag pointing at it."""
    from app.database.models import SubtitleLanguageFlag, MediaFile

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
    db.add(SubtitleLanguageFlag(
        file_id=file_id,
        stream_index=1,
        detected_language=language,
    ))
    db.commit()
    return media


def _seed(db):
    # Same shape as the audio fixture: one show carrying two different tags,
    # so filtering to one must not sweep up the other.
    _flag(db, "King of the Hill S01E01.mkv", "dut", 1)
    _flag(db, "King of the Hill S01E02.mkv", "dut", 2)
    _flag(db, "King of the Hill S01E03.mkv", "dan", 3)
    _flag(db, "Cowboy Bebop S01E01.mkv",     "jpn", 4)
    _flag(db, "Cowboy Bebop S01E02.mkv",     "jpn", 5)


def _list(db, **kwargs):
    from app.api.routes.subtitle_language import list_flags

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
