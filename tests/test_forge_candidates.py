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
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

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
