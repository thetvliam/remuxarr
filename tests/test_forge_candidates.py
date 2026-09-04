"""
Regression test for get_candidates query batching.

get_candidates issued two extra queries PER FILE (the AAC 5.1 lookup and
the audio-track count) — 2N+1 for a page of N. They're now collapsed
into a single batched, stream-index-ordered query over the page's
file_ids, grouped in Python. This test pins the observable output:
correct candidate set (exclusions honored), correct per-file audio
count, and a DETERMINISTIC "first AAC 5.1" = lowest stream_index (which
is what the previous per-file .first() returned, since tracks are
inserted in stream order).

Folding _has_pending_forge into claim_next_forge_job was deliberately
NOT done: the fold saves a query only on the hit path, doesn't reduce
the idle per-tick polling, and would decouple the claim from execution
in the worker's hottest loop for negligible gain. No test for a
non-change.

Run from the project root:
    pytest tests/test_forge_candidates.py -v
"""


from app.core.forge import get_candidates


def _db():
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    from app.database.models import Base
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)()


def _seed(db):
    from app.database.models import Ac3ForgeJob, MediaFile, Track

    def mf(i, name):
        db.add(MediaFile(id=i, path=f"/m/{name}", filename=name,
                         directory="/m", size=100, mtime=1.0))

    def tr(fid, si, codec, ch):
        db.add(Track(file_id=fid, stream_index=si, track_type="audio",
                     codec=codec, channels=ch, channel_layout=f"{ch}ch",
                     language="eng", is_default=(si == 1)))

    # A: aac 5.1 (si=2) + ac3 (si=1) → candidate, audio_count=2
    mf(1, "A.mkv"); tr(1, 1, "ac3", 6); tr(1, 2, "aac", 6)
    # B: only stereo aac → NOT a candidate (no AAC 5.1)
    mf(2, "B.mkv"); tr(2, 1, "aac", 2)
    # C: single aac 5.1 → candidate, audio_count=1
    mf(3, "C.mkv"); tr(3, 1, "aac", 6)
    # D: aac 5.1 but a completed forge job → excluded
    mf(4, "D.mkv"); tr(4, 1, "aac", 6)
    db.add(Ac3ForgeJob(file_id=4, status="success", aac_stream_index=1,
                       audio_track_count=1))
    # E: two aac 5.1 tracks (si=3, si=1) + a dts → candidate, must pick si=1
    mf(5, "E.mkv"); tr(5, 3, "aac", 6); tr(5, 1, "aac", 6); tr(5, 2, "dts", 6)
    db.commit()


def test_candidates_set_and_exclusions():
    db = _db(); _seed(db)
    items = {i["filename"]: i for i in get_candidates(db)["items"]}
    assert get_candidates(db)["total"] == 3
    assert set(items) == {"A.mkv", "C.mkv", "E.mkv"}, (
        "B (stereo-only) and D (already forged) must be excluded"
    )


def test_audio_count_and_aac_stream_index():
    db = _db(); _seed(db)
    items = {i["filename"]: i for i in get_candidates(db)["items"]}
    assert items["A.mkv"]["audio_track_count"] == 2
    assert items["A.mkv"]["aac_stream_index"] == 2
    assert items["C.mkv"]["audio_track_count"] == 1
    assert items["C.mkv"]["aac_stream_index"] == 1


def test_first_aac51_is_lowest_stream_index():
    """Deterministic selection: E has AAC 5.1 at si=3 and si=1 — the
    batched, stream-index-ordered query must pick si=1, matching the
    previous per-file .first() (rowid ≈ stream order)."""
    db = _db(); _seed(db)
    e = {i["filename"]: i for i in get_candidates(db)["items"]}["E.mkv"]
    assert e["aac_stream_index"] == 1
    assert e["audio_track_count"] == 3
    assert e["aac_track"]["channels"] == 6


def test_empty_page_is_safe():
    """No candidates → the batched query is skipped (empty file_ids) and
    the result is well-formed."""
    db = _db()  # nothing seeded
    res = get_candidates(db)
    assert res == {"total": 0, "items": []}


def test_search_treats_underscore_and_percent_as_literal_characters():
    """
    The candidate search had no test at all, which is how it kept the
    same unescaped-wildcard bug as history and language review: `_`
    matched any single character and `%` matched everything.

    On this endpoint the consequence is worse than a bad result list.
    The next thing the user does is click ADD AC3 on a row, so a search
    that quietly matches the wrong file is a search that offers to
    rewrite the wrong file's audio.
    """
    from app.database.models import MediaFile, Track

    db = _db()
    for i, name in ((1, "The_Movie.mkv"), (2, "TheXMovie.mkv"),
                    (3, "Show 100% Real.mkv"), (4, "Show 100Z Real.mkv")):
        db.add(MediaFile(id=i, path=f"/m/{name}", filename=name,
                         directory="/m", size=100, mtime=1.0))
        db.add(Track(file_id=i, stream_index=1, track_type="audio",
                     codec="aac", channels=6, channel_layout="6ch",
                     language="eng", is_default=True))
    db.commit()

    underscore = get_candidates(db, search="The_Movie")
    assert [i["filename"] for i in underscore["items"]] == ["The_Movie.mkv"]
    assert underscore["total"] == 1

    percent = get_candidates(db, search="100%")
    assert [i["filename"] for i in percent["items"]] == ["Show 100% Real.mkv"]
    assert percent["total"] == 1


def test_search_relevance_ranking_also_treats_wildcards_literally():
    """
    The ranking builds two more patterns from the same term, and both
    need the same escaping the filter got. See the matching test in
    test_history_routes.py for the full reasoning — this endpoint builds
    the identical two patterns, so it can drift in the identical way.

    Every name below contains a literal "The_Movie" and so survives any
    filter; what differs is the rank each earns:

      name                          escaped  unescaped
      The_Movie Quest.mkv           0        0
      TheXMovie The_Movie.mkv       1        0   ← "_" matches "X"
      Zzz TheXMovie-The_Movie.mkv   2        1   ← "_" matches "X"
      Aaa-The_Movie.mkv             2        2

    Within a rank the tie-break is filename, so the two rank-2 names are
    chosen to order Aaa before Zzz.
    """
    from app.database.models import MediaFile, Track

    db = _db()
    for i, name in enumerate((
        "The_Movie Quest.mkv",
        "TheXMovie The_Movie.mkv",
        "Zzz TheXMovie-The_Movie.mkv",
        "Aaa-The_Movie.mkv",
    ), 1):
        db.add(MediaFile(id=i, path=f"/m/{name}", filename=name,
                         directory="/m", size=100, mtime=1.0))
        db.add(Track(file_id=i, stream_index=1, track_type="audio",
                     codec="aac", channels=6, channel_layout="6ch",
                     language="eng", is_default=True))
    db.commit()

    out = get_candidates(db, search="The_Movie")

    assert [i["filename"] for i in out["items"]] == [
        "The_Movie Quest.mkv",
        "TheXMovie The_Movie.mkv",
        "Aaa-The_Movie.mkv",
        "Zzz TheXMovie-The_Movie.mkv",
    ]
