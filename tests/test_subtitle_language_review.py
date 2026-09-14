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


# Mutation on the und widening, 3 applied 3 killed: the widening removed, the
# literal-"und" arm dropped so only nulls matched, and the widening applied to
# every language. The third is also caught by five pre-existing filter tests —
# test_another_language_does_not_sweep_up_untagged_rows is the one that names
# the reason rather than noticing the damage.


def test_an_untagged_flag_can_be_selected_by_the_option_it_is_offered_under():
    """
    The other half of the test above, which had none.

    A null detected_language is REPORTED as "und" in the facets, so the
    dropdown offers "und (1)". The filter compared the column to the string
    "und", and in SQL a null is not equal to anything — so picking the option
    the endpoint had just advertised returned nothing, with the count beside
    it still saying 1.

    Latent, and expected to stay latent: the scanner writes "und" literally
    for subtitles and `t["language"] or "und"` for audio, so production has no
    null rows to find. It is the facet and the filter disagreeing about the
    same row that is worth closing, since the facet is what tells the user the
    option is worth clicking.
    """
    db = _db()
    _flag(db, "Mystery.mkv", None, 9)

    offered = {e["language"]: e["count"] for e in _list(db)["languages"]}
    picked  = _list(db, language="und")

    assert offered == {"und": 1}
    assert picked["total"] == 1, "the option the dropdown offered found nothing"
    assert [i["filename"] for i in picked["items"]] == ["Mystery.mkv"]


def test_a_real_und_is_still_matched_on_its_own():
    """
    The positive control. Widening the und case must not turn it into a filter
    that matches everything — the literal rows are the ones production has.
    """
    db = _db()
    _flag(db, "Tagged.mkv", "und", 10)
    _flag(db, "Dutch.mkv", "dut", 11)

    picked = _list(db, language="und")

    assert [i["filename"] for i in picked["items"]] == ["Tagged.mkv"]


def test_another_language_does_not_sweep_up_untagged_rows():
    """Only "und" widens; "dut" must not pick up a null."""
    db = _db()
    _flag(db, "Mystery.mkv", None, 9)
    _flag(db, "Dutch.mkv", "dut", 11)

    picked = _list(db, language="dut")

    assert [i["filename"] for i in picked["items"]] == ["Dutch.mkv"]


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


def test_the_unfiltered_total_survives_a_search():
    """
    `total` is the filtered figure — pagination needs it, and the "n of m" on
    the select-all row is built from it. It is also what the section heading's
    badge showed, so typing a show name took the badge from the size of the
    backlog to the size of the match, and the backlog figure then appeared
    nowhere at all.

    The facets cannot stand in for it: they honour `search` on purpose, so the
    dropdown only ever offers tags the current search actually has. Summing
    them gives the filtered total back again.
    """
    db = _db()
    _seed(db)

    wide   = _list(db)
    narrow = _list(db, search="king of the hill")

    assert wide["total"] == 5
    assert narrow["total"] == 3
    assert narrow["total_unfiltered"] == 5, (
        "the overall figure did not survive the search"
    )


def test_the_unfiltered_total_survives_a_language_filter():
    """The other filter, which narrows `total` the same way."""
    db = _db()
    _seed(db)

    filtered = _list(db, language="jpn")

    assert filtered["total"] == 2
    assert filtered["total_unfiltered"] == 5


def test_the_unfiltered_total_still_moves_when_rows_do():
    """
    Not a constant. It ignores the filters, not the table — a row cleared by
    an apply or an ignore has to come off it, or the badge becomes a number
    that only ever goes stale.
    """
    from app.database.models import SubtitleLanguageFlag

    db = _db()
    _seed(db)
    assert _list(db)["total_unfiltered"] == 5

    db.delete(db.query(SubtitleLanguageFlag).first())
    db.commit()

    assert _list(db)["total_unfiltered"] == 4


# ── Pagination bounds ───────────────────────────────────────────────────────
#
# Mutation, 3 applied 3 killed: the limit floor, the limit ceiling and the
# offset floor each removed in turn. Each dies to exactly one test, which is
# what three independent bounds should look like.
#
# These go through TestClient rather than calling list_flags directly, as
# every other test in this file does. FastAPI applies Query() constraints when
# it parses a request; a direct Python call bypasses them entirely, so a test
# that called the function would pass no matter what the signature said.


def _threadsafe_db():
    """
    _db() above builds a plain sqlite:// engine, which TestClient cannot use:
    the request runs on another thread, gets its own connection, and sees an
    empty database — "no such table", as though the schema were never created.
    conftest.memory_engine exists for exactly this and says so.
    """
    from sqlalchemy.orm import sessionmaker

    from app.database.models import Base
    from tests.conftest import memory_engine

    engine = memory_engine()
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)()


def _client(db):
    from fastapi import FastAPI
    from starlette.testclient import TestClient

    from app.api.routes import _language_review, subtitle_language

    app = FastAPI()
    app.include_router(subtitle_language.router)
    # Keyed on the object the route actually closed over. Importing get_db
    # from app.database.session here looks equivalent and is not: something
    # earlier in the suite rebinds that name, so the override lands under a
    # different key, silently does nothing, and the request runs against the
    # real database — which reports total 0 and looks like a seeding bug.
    # Alone the file passed; only the full run showed it.
    app.dependency_overrides[_language_review.get_db] = lambda: db
    return TestClient(app)


def test_a_negative_limit_is_rejected():
    """
    Unbounded, `limit=-1` reached SQLAlchemy's .limit(), which treats a
    negative as no limit at all — so the endpoint quietly returned every
    flagged row in the library in one response. history, forge and logs all
    bound theirs; this pair was the exception.
    """
    db = _threadsafe_db()
    _seed(db)

    assert _client(db).get("/api/subtitle-language-review/?limit=-1").status_code == 422


def test_a_negative_offset_is_rejected():
    db = _threadsafe_db()
    _seed(db)

    assert _client(db).get("/api/subtitle-language-review/?offset=-5").status_code == 422


def test_an_unbounded_limit_is_rejected():
    """The ceiling matters as much as the floor: the page size is the only
    thing standing between one request and the whole table."""
    db = _threadsafe_db()
    _seed(db)

    assert _client(db).get("/api/subtitle-language-review/?limit=999999").status_code == 422


def test_the_ordinary_range_still_works():
    """The positive control, and the bound the UI actually sends."""
    db = _threadsafe_db()
    _seed(db)

    r = _client(db).get("/api/subtitle-language-review/?limit=100&offset=0")

    assert r.status_code == 200
    assert r.json()["total"] == 5
