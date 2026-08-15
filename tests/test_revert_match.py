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

No equivalent mutants.
"""
import json
import os

import pytest

from app.core.revert import MANIFEST_VERSION


# ── Harness ──────────────────────────────────────────────────────────────────

@pytest.fixture
def db(monkeypatch):
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    from app.database.models import Base
    import app.database.session as session_mod

    engine = create_engine("sqlite://")
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
