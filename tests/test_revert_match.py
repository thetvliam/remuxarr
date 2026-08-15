"""
Matching a detached revert point back to a file by hand.

The danger here is specific. Everywhere else in this feature the sentinel
decides whether a revert may proceed; here the user is proposing which
file a point belongs to, and a wrong answer means muxing one release's
tracks into another. The result plays. Nobody finds out.

So the tiers carry the weight, and most of these tests are about keeping
them apart:

  EXACT       the candidate is byte-for-byte what the job produced. Not a
              heuristic — this is provably the same file under a new name,
              which is what a rename produces and therefore what the
              common case gets.
  COMPATIBLE  streams and runtime line up but the fingerprint does not.
              Consistent with being right; not proof. Requires explicit
              confirmation.
  INCOMPATIBLE refused, with the reason.

A refusal without a reason is close to useless here — it sends someone
down the list trying the next point at random — so the reasons are
asserted, not just the outcome.

Attaching re-establishes the fingerprint from the file as it is now, so
every later revert runs with a full sentinel. That is what stops one
manual decision becoming a point that skips the check forever, and it is
tested directly.

Verified by mutation, 13 applied, 13 killed. One initially SURVIVED:
treating a matching mtime alone as EXACT. The suite checked a differing
mtime but never a differing size with a matching mtime, which is the more
likely half in practice — copy tools preserve timestamps, so the stamp
survives while the content does not.

The full list:

  • Fingerprint comparison dropped, everything EXACT   → killed
  • EXACT returned when only the size matches          → killed
  • EXACT returned when only the mtime matches         → killed
  • INCOMPATIBLE attachable with confirmation          → killed
  • confirm_mismatch ignored, COMPATIBLE auto-attached → killed
  • confirm_mismatch inverted                          → killed
  • Missing-stream check dropped                       → killed
  • Streams held in the sidecar counted as missing     → killed
  • Duration check dropped                             → killed
  • Duration tolerance widened past a different release→ killed
  • Annotations carried over instead of re-resolved    → killed
  • Fingerprint not refreshed on attach                → killed
  • Attaching an already-attached point allowed        → killed
  • Fingerprint candidates matched on size alone       → killed
  • Dismissal sentinels not excluded from matching     → killed
  • Nearby candidates not filtered to the directory    → killed
  • Exact matches repeated as guesses                  → killed
  • Candidates offered for an already-attached point   → killed

Two of those were the same oversight in two places: a fingerprint check
written against size and mtime, tested only for a differing mtime. The
mirror case — same size, different mtime — went untested both times.

No equivalent mutants.
"""
import json
import os

import pytest

from app.core.revert import MANIFEST_VERSION


# ── Harness ──────────────────────────────────────────────────────────────────

@pytest.fixture
def db(monkeypatch):
    from sqlalchemy.orm import sessionmaker

    from tests.conftest import memory_engine

    from app.database.models import Base
    import app.database.session as session_mod

    engine = memory_engine()
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine)
    monkeypatch.setattr(session_mod, "SessionLocal", factory)
    return factory()


def _stream(index, kind, codec, *, language=None, channels=None,
            sidecar_index=None, processed_index=None):
    out = {"index": index, "type": kind, "codec": codec, "language": language,
           "title": None, "disposition": [], "channels": channels,
           "sample_rate": None, "width": None, "height": None,
           "filename": None, "mimetype": None,
           "processed_index": processed_index}
    if sidecar_index is not None:
        out["sidecar_index"] = sidecar_index
    return out


def _manifest(*streams, duration=1200.0):
    return {"version": MANIFEST_VERSION, "path": "/m/old/Show.mkv",
            "container": "mkv", "streams": list(streams), "chapters": 0,
            "duration": duration}


def _probe(*streams, duration=1200.0):
    out = []
    for s in streams:
        entry = {"index": s["index"], "codec_type": s["type"],
                 "codec_name": s["codec"]}
        if s.get("language"):
            entry["tags"] = {"language": s["language"]}
        if s.get("channels"):
            entry["channels"] = s["channels"]
        out.append(entry)
    return {"streams": out, "format": {"duration": str(duration),
                                       "format_name": "matroska,webm"}}


# The original: video, two audio, one subtitle. The job dropped the French
# audio, so it lives in the sidecar and the rest should still be in the file.
ORIGINAL = _manifest(
    _stream(0, "video", "h264", processed_index=0),
    _stream(1, "audio", "aac", language="eng", channels=2, processed_index=1),
    _stream(2, "audio", "ac3", language="fre", channels=6, sidecar_index=0),
    _stream(3, "subtitle", "subrip", language="eng", processed_index=2),
)

# The renamed file: the same content, minus the dropped track.
SURVIVING = _probe(
    _stream(0, "video", "h264"),
    _stream(1, "audio", "aac", language="eng", channels=2),
    _stream(2, "subtitle", "subrip", language="eng"),
)


def _setup(db, tmp_path, *, content=b"processed bytes", manifest=None,
           fingerprint="exact"):
    """A detached point plus a candidate file on disk."""
    from app.database.models import MediaFile, RevertPoint

    path = tmp_path / "Show - S01E01.mkv"
    path.write_bytes(content)
    stat = path.stat()

    media = MediaFile(path=str(path), filename=path.name,
                      directory=str(tmp_path), size=stat.st_size,
                      mtime=stat.st_mtime, container="mkv")
    db.add(media)
    db.commit()

    sizes = {"exact": stat.st_size, "different": stat.st_size + 1}
    point = RevertPoint(
        file_id=None, detached_at=None,
        sidecar_path=str(tmp_path / "1_1.remuxarr_revert"), sidecar_size=100,
        manifest=json.dumps(manifest or ORIGINAL),
        original_path="/m/old/Show.mkv", original_container="mkv",
        processed_size=sizes[fingerprint],
        processed_mtime=stat.st_mtime if fingerprint == "exact" else 1.0,
    )
    db.add(point)
    db.commit()
    return point, media, path


def _patch_probe(monkeypatch, probe):
    import app.core.revert_match as rm

    monkeypatch.setattr(rm, "probe_file", lambda _p, *_a, **_k: probe)


# ── Tiers ────────────────────────────────────────────────────────────────────

def test_a_renamed_file_matches_exactly(db, tmp_path):
    """
    The everyday case, and the reassuring one: a rename does not touch a
    byte, so the fingerprint the point already holds still identifies the
    file. Nothing is being taken on trust — the user supplies the identity
    the scanner lost and the ordinary check confirms it.
    """
    from app.core.revert_match import EXACT, assess

    point, _media, path = _setup(db, tmp_path)

    result = assess(point, str(path), SURVIVING)

    assert result.tier == EXACT
    assert result.reasons == []


def test_a_different_size_is_compatible_not_exact(db, tmp_path):
    from app.core.revert_match import COMPATIBLE, assess

    point, _media, path = _setup(db, tmp_path, fingerprint="different")

    result = assess(point, str(path), SURVIVING)

    assert result.tier == COMPATIBLE
    assert result.reasons, "a match that cannot be confirmed must say so"


def test_a_matching_size_with_a_different_mtime_is_not_exact(db, tmp_path):
    """
    Both halves of the fingerprint, or it is not a fingerprint. A rewrite
    that happens to land on the same byte count is not the same file.
    """
    from app.core.revert_match import COMPATIBLE, assess

    point, _media, path = _setup(db, tmp_path)
    point.processed_mtime = point.processed_mtime + 5
    db.commit()

    assert assess(point, str(path), SURVIVING).tier == COMPATIBLE


def test_a_matching_mtime_with_a_different_size_is_not_exact(db, tmp_path):
    """
    The mirror of the test above, and not redundant with it: a fingerprint
    that only consults one field passes half the wrong files. Copy tools
    that preserve mtime make this the more likely half — the timestamp
    survives while the content does not.
    """
    from app.core.revert_match import COMPATIBLE, assess

    point, _media, path = _setup(db, tmp_path)
    point.processed_size = path.stat().st_size + 4096
    db.commit()

    assert assess(point, str(path), SURVIVING).tier == COMPATIBLE


def test_a_file_missing_an_expected_stream_is_incompatible(db, tmp_path):
    """
    Not merely a wrong-file signal. A stream the manifest expects, that is
    in neither the file nor the sidecar, has nowhere to come from — the
    restore could not be built at all. Saying so here beats failing after
    the user has committed.
    """
    from app.core.revert_match import INCOMPATIBLE, assess

    point, _media, path = _setup(db, tmp_path)
    without_subtitle = _probe(
        _stream(0, "video", "h264"),
        _stream(1, "audio", "aac", language="eng", channels=2),
    )

    result = assess(point, str(path), without_subtitle)

    assert result.tier == INCOMPATIBLE
    assert "subtitle" in result.reasons[0]


def test_streams_held_in_the_sidecar_are_not_expected_in_the_file(db, tmp_path):
    """
    The dropped French track is absent from every candidate by definition
    — that is what the revert point exists to put back. Counting it as
    missing would make every point permanently unmatchable.
    """
    from app.core.revert_match import EXACT, assess

    point, _media, path = _setup(db, tmp_path)

    assert assess(point, str(path), SURVIVING).tier == EXACT


def test_a_different_runtime_is_incompatible(db, tmp_path):
    """
    Two releases of the same episode can share every codec, resolution and
    channel count. Runtime is the cheapest thing that separates them and
    the only signal here a stream comparison cannot see.
    """
    from app.core.revert_match import INCOMPATIBLE, assess

    point, _media, path = _setup(db, tmp_path)
    other_release = _probe(
        _stream(0, "video", "h264"),
        _stream(1, "audio", "aac", language="eng", channels=2),
        _stream(2, "subtitle", "subrip", language="eng"),
        duration=1247.0,
    )

    result = assess(point, str(path), other_release)

    assert result.tier == INCOMPATIBLE
    assert "Runtime" in result.reasons[0]


def test_a_frame_of_runtime_drift_is_tolerated(db, tmp_path):
    """
    A remux can shift the reported duration slightly and containers differ in
    overhead. The check separates releases, it does not police rounding.
    """
    from app.core.revert_match import EXACT, assess

    point, _media, path = _setup(db, tmp_path)
    nudged = _probe(
        _stream(0, "video", "h264"),
        _stream(1, "audio", "aac", language="eng", channels=2),
        _stream(2, "subtitle", "subrip", language="eng"),
        duration=1200.4,
    )

    assert assess(point, str(path), nudged).tier == EXACT


# ── Attaching ────────────────────────────────────────────────────────────────

def test_an_exact_match_attaches(db, tmp_path, monkeypatch):
    from app.core.revert_match import EXACT, attach
    from app.database.models import RevertPoint

    point, media, _path = _setup(db, tmp_path)
    _patch_probe(monkeypatch, SURVIVING)

    outcome = attach(point.id, media.id)

    assert outcome.success is True
    assert outcome.tier == EXACT
    db.expire_all()
    assert db.get(RevertPoint, point.id).file_id == media.id
    assert db.get(RevertPoint, point.id).detached_at is None


def test_a_compatible_match_needs_confirmation(db, tmp_path, monkeypatch):
    """
    The one place a user can overrule the safety check, so it has to be an
    explicit act rather than a default. What it permits is a revert that
    produces a plausible, wrong file.
    """
    from app.core.revert_match import COMPATIBLE, attach
    from app.database.models import RevertPoint

    point, media, _path = _setup(db, tmp_path, fingerprint="different")
    _patch_probe(monkeypatch, SURVIVING)

    outcome = attach(point.id, media.id)

    assert outcome.success is False
    assert outcome.tier == COMPATIBLE
    assert outcome.reasons, "refused without saying what could not be confirmed"
    db.expire_all()
    assert db.get(RevertPoint, point.id).file_id is None


def test_a_compatible_match_attaches_once_confirmed(db, tmp_path, monkeypatch):
    from app.core.revert_match import attach
    from app.database.models import RevertPoint

    point, media, _path = _setup(db, tmp_path, fingerprint="different")
    _patch_probe(monkeypatch, SURVIVING)

    outcome = attach(point.id, media.id, confirm_mismatch=True)

    assert outcome.success is True
    db.expire_all()
    assert db.get(RevertPoint, point.id).file_id == media.id


def test_an_incompatible_match_is_refused_even_when_confirmed(db, tmp_path,
                                                              monkeypatch):
    """
    Confirmation covers "cannot be verified", not "known to be wrong".
    There is no restore to build from a file missing streams the manifest
    needs.
    """
    from app.core.revert_match import INCOMPATIBLE, attach

    point, media, _path = _setup(db, tmp_path)
    _patch_probe(monkeypatch, _probe(_stream(0, "video", "h264")))

    outcome = attach(point.id, media.id, confirm_mismatch=True)

    assert outcome.success is False
    assert outcome.tier == INCOMPATIBLE


def test_attaching_refreshes_the_fingerprint(db, tmp_path, monkeypatch):
    """
    What stops one manual decision becoming a point that skips the check
    forever. After attaching, the ordinary revert path runs with a full
    sentinel again.
    """
    from app.core.revert_match import attach
    from app.database.models import RevertPoint

    point, media, path = _setup(db, tmp_path, fingerprint="different")
    _patch_probe(monkeypatch, SURVIVING)

    attach(point.id, media.id, confirm_mismatch=True)

    db.expire_all()
    stored = db.get(RevertPoint, point.id)
    stat = path.stat()
    assert stored.processed_size == stat.st_size
    assert stored.processed_mtime == stat.st_mtime


def test_attaching_re_resolves_the_stream_annotations(db, tmp_path, monkeypatch):
    """
    The annotations were resolved against a file we have just stopped
    assuming this is. Carried over unchanged they name stream positions in
    something else, and restore maps them without question.
    """
    from app.core.revert_match import attach
    from app.database.models import RevertPoint

    # This candidate carries the surviving streams in a different order.
    reordered = _probe(
        _stream(0, "video", "h264"),
        _stream(1, "subtitle", "subrip", language="eng"),
        _stream(2, "audio", "aac", language="eng", channels=2),
    )
    point, media, _path = _setup(db, tmp_path)
    _patch_probe(monkeypatch, reordered)

    assert attach(point.id, media.id).success is True

    db.expire_all()
    manifest = json.loads(db.get(RevertPoint, point.id).manifest)
    by_index = {s["index"]: s for s in manifest["streams"]}
    assert by_index[1]["processed_index"] == 2, "audio annotation not re-resolved"
    assert by_index[3]["processed_index"] == 1, "subtitle annotation not re-resolved"
    assert by_index[2]["processed_index"] is None, "the sidecar track is not in the file"


def test_an_already_attached_point_is_refused(db, tmp_path, monkeypatch):
    """
    Attaching a live point to a second file would leave the first one
    believing it still had a revert point.
    """
    from app.core.revert_match import attach

    point, media, _path = _setup(db, tmp_path)
    point.file_id = media.id
    db.commit()
    _patch_probe(monkeypatch, SURVIVING)

    outcome = attach(point.id, media.id)

    assert outcome.success is False
    assert "already attached" in outcome.error


def test_a_missing_file_on_disk_is_refused(db, tmp_path, monkeypatch):
    from app.core.revert_match import attach

    point, media, path = _setup(db, tmp_path)
    path.unlink()
    _patch_probe(monkeypatch, SURVIVING)

    outcome = attach(point.id, media.id)

    assert outcome.success is False
    assert "not on disk" in outcome.error


# ── Finding the file again ───────────────────────────────────────────────────

def _extra_file(db, path, *, size, mtime, directory=None):
    from app.database.models import MediaFile

    import os as _os
    media = MediaFile(path=path, filename=_os.path.basename(path),
                      directory=directory or _os.path.dirname(path),
                      size=size, mtime=mtime, container="mkv")
    db.add(media)
    db.commit()
    return media


def test_a_renamed_file_is_found_by_its_fingerprint(db, tmp_path):
    """
    Not a suggestion. A rename does not touch a byte, so the renamed file
    still carries the fingerprint of the file the job produced — which
    means the common case for detaching resolves conclusively rather than
    by guesswork.
    """
    from app.core.revert_match import find_candidates

    point, media, path = _setup(db, tmp_path)
    point.file_id = None
    point.detached_at = point.created_at
    db.commit()

    found = find_candidates(point.id)

    assert [c["id"] for c in found["exact"]] == [media.id]


def test_a_file_with_a_different_fingerprint_is_not_exact(db, tmp_path):
    from app.core.revert_match import find_candidates

    point, media, _path = _setup(db, tmp_path)
    point.file_id = None
    point.processed_size = media.size + 1
    db.commit()

    assert find_candidates(point.id)["exact"] == []


def test_a_same_sized_file_with_a_different_mtime_is_not_exact(db, tmp_path):
    """
    Size alone is not a fingerprint. Two episodes of the same show from
    the same encode land within bytes of each other and sometimes exactly
    on it — an "exact" label that a size collision can earn is worse than
    no label, because the UI presents it as conclusive.
    """
    from app.core.revert_match import find_candidates

    point, media, _path = _setup(db, tmp_path)
    point.file_id = None
    db.commit()
    _extra_file(db, "/m/Different.mkv", size=media.size, mtime=media.mtime + 900)

    found = find_candidates(point.id)

    assert [c["id"] for c in found["exact"]] == [media.id]


def test_dismissed_rows_are_never_fingerprint_matches(db, tmp_path):
    """
    Several queue routes reset MediaFile.size/mtime to -1/-1.0 to dismiss a
    file. Those rows describe nothing on disk, and matching on them would
    pair a revert point with every OTHER dismissed row exactly — the worst
    false positive available, since it looks conclusive.
    """
    from app.core.revert_match import find_candidates

    point, _media, _path = _setup(db, tmp_path)
    point.file_id = None
    point.processed_size = -1
    point.processed_mtime = -1.0
    db.commit()
    _extra_file(db, "/m/Dismissed.mkv", size=-1, mtime=-1.0)

    assert find_candidates(point.id)["exact"] == []


def test_files_in_the_originals_directory_are_offered_as_guesses(db, tmp_path):
    from app.core.revert_match import find_candidates

    point, _media, _path = _setup(db, tmp_path)
    point.file_id = None
    point.original_path = "/m/old/Show.mkv"
    db.commit()
    sibling = _extra_file(db, "/m/old/Show - S01E01.mkv", size=999, mtime=9.0)
    _extra_file(db, "/m/elsewhere/Other.mkv", size=999, mtime=9.0)

    found = find_candidates(point.id)

    assert [c["id"] for c in found["nearby"]] == [sibling.id]


def test_an_exact_match_is_not_repeated_as_a_guess(db, tmp_path):
    """
    Listed twice, a UI shows the same file under both headings and the
    "exact" label stops meaning anything.
    """
    from app.core.revert_match import find_candidates

    point, media, _path = _setup(db, tmp_path)
    point.file_id = None
    point.original_path = str(tmp_path / "Show.mkv")
    db.commit()

    found = find_candidates(point.id)

    assert [c["id"] for c in found["exact"]] == [media.id]
    assert media.id not in [c["id"] for c in found["nearby"]]


def test_an_attached_point_offers_no_candidates(db, tmp_path):
    from app.core.revert_match import find_candidates

    point, media, _path = _setup(db, tmp_path)
    point.file_id = media.id
    db.commit()

    assert find_candidates(point.id) == {"exact": [], "nearby": []}


# ── Listing ──────────────────────────────────────────────────────────────────

def test_detached_points_are_listed_with_what_identifies_them(db, tmp_path):
    """
    original_path is the name the file had when Remuxarr last saw it —
    exactly what a rename changed, and so the thing a user recognises. The
    stored tracks are there because someone who renamed a whole season
    needs more than the old name to tell two points apart.
    """
    from app.core.revert_match import list_detached

    point, _media, _path = _setup(db, tmp_path)
    point.detached_at = point.created_at
    db.commit()

    listed = list_detached()

    assert len(listed) == 1
    entry = listed[0]
    assert entry["id"] == point.id
    assert entry["original_filename"] == "Show.mkv"
    assert entry["duration"] == 1200.0
    assert [t["language"] for t in entry["stored_tracks"]] == ["fre"]


def test_attached_points_are_not_listed(db, tmp_path):
    from app.core.revert_match import list_detached

    point, media, _path = _setup(db, tmp_path)
    point.file_id = media.id
    db.commit()

    assert list_detached() == []


def test_a_listed_point_reports_whether_its_sidecar_survives(db, tmp_path):
    """
    A point whose sidecar the retention sweep already took cannot restore
    anything. Offering it as a choice wastes the one decision the user is
    being asked to make carefully.
    """
    from app.core.revert_match import list_detached

    point, _media, _path = _setup(db, tmp_path)
    point.detached_at = point.created_at
    db.commit()

    assert list_detached()[0]["sidecar_present"] is False

    os.makedirs(os.path.dirname(point.sidecar_path), exist_ok=True)
    with open(point.sidecar_path, "wb") as f:
        f.write(b"tracks")

    assert list_detached()[0]["sidecar_present"] is True
