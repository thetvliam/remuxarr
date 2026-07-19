"""
Regression tests for the forge-undo track resolution fixes
(review items M3 and M8).

M3 — undo removed tracks by a positional index captured at ADD time:
    the forge rewrite changes the file's size/mtime, the next delta scan
    re-evaluates it, and the main pipeline can drop audio tracks before
    the user ever clicks undo. The forge AC3 was appended LAST, so any
    drop pushes the stored index past the end of the surviving audio —
    and FFmpeg silently ignores a negative map that matches nothing
    (rc=0, verified live during the fix). The old undo therefore rewrote
    the whole file with every track kept and recorded a false "undone",
    leaving the AC3 embedded forever while the UI reported it removed.
    The fix resolves the AC3's position from a fresh probe at undo time
    via resolve_forge_ac3_for_undo, whose "last audio track" invariant
    these tests pin down — including both refusal outcomes ("absent" →
    already removed, mark undone without touching the file; "mismatch" →
    layout violates the invariant, refuse rather than guess).

    The same stale index also fed analyze_file's und-threshold
    exclusion, which previously indexed into audio_tracks blindly — a
    stale in-range index silently excluded whatever unrelated track sat
    there. The exclusion is now property-validated with the same
    last-track fallback; tests below cover both directions (stale index
    still excludes the real forge track; a forge track that's genuinely
    gone excludes nothing).

M8 — _get_forged_ac3_audio_index couldn't see an undo mid-flight:
    claiming an undo job flips undo_pending → "processing", a status the
    old filter excluded even though the AC3 remains in the file for the
    whole rewrite. The query now matches processing+is_undo; tested
    against a real (in-memory) database since the fix IS the query.

Run from the project root:
    pytest tests/ -v
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.core.decision import analyze_file
from app.core.forge import build_undo_command, resolve_forge_ac3_for_undo
from conftest import BASE_SETTINGS, make_track, make_file_info


def _aac51(idx, lang="eng", **kw):
    return make_track(stream_index=idx, track_type="audio", codec="aac",
                      language=lang, channels=6, channel_layout="5.1", **kw)


def _ac3_forge(idx, lang="eng"):
    """The shape build_add_ac3_command produces: ac3, 6ch, appended last."""
    return make_track(stream_index=idx, track_type="audio", codec="ac3",
                      language=lang, channels=6, channel_layout="5.1")


def _video(idx=0):
    return make_track(stream_index=idx, track_type="video", codec="h264")


# ═══════════════════════════════════════════════════════════════════════════
# M3 — resolve_forge_ac3_for_undo
# ═══════════════════════════════════════════════════════════════════════════

def test_unchanged_file_resolves_to_stored_position():
    """
    Baseline: nothing touched the file between add and undo — the
    resolved index must equal the add-time position (audio_track_count),
    proving the resolver is a strict generalisation, not a behavior
    change for the common case.
    """
    tracks = [_video(0), _aac51(1), _ac3_forge(2)]
    outcome, idx = resolve_forge_ac3_for_undo(tracks)
    assert outcome == "found"
    assert idx == 1  # 0-based among AUDIO tracks; add-time count was also 1


def test_pipeline_drop_shifts_index_and_resolver_follows():
    """
    The exact M3 incident shape. Original file: eng AAC 5.1 + fre AAC
    5.1; forge appended its AC3 at audio index 2 (audio_track_count=2).
    The main pipeline then dropped the non-kept French track, shifting
    the AC3 to audio index 1. The stored index (2) now matches nothing —
    FFmpeg silently ignores an unmatched negative map, so the old undo
    "succeeded" while removing no track at all.
    """
    stored_add_time_index = 2
    tracks_after_drop = [_video(0), _aac51(1, "eng"), _ac3_forge(2)]

    outcome, idx = resolve_forge_ac3_for_undo(tracks_after_drop)
    assert outcome == "found"
    assert idx == 1
    assert idx != stored_add_time_index, (
        "If these ever match, this test's premise (a drop occurred) has "
        "been broken — the point is that the resolver diverges from the "
        "stale stored value."
    )
    # The command surface must carry the resolved index verbatim.
    cmd = build_undo_command("in.mkv", "tmp.mkv", idx, container="mkv")
    assert "-0:a:1" in cmd
    assert "-0:a:2" not in cmd


def test_ac3_gone_entirely_resolves_absent():
    """
    The pipeline legitimately drops the forge AC3 when its inherited
    language isn't in the keep list (it inherits the source AAC's tag).
    Undo must recognise 'nothing to remove' — load_forge_job_data marks
    the job undone without touching the file — rather than failing or,
    worse, stripping something else.
    """
    tracks = [_video(0), _aac51(1)]
    assert resolve_forge_ac3_for_undo(tracks) == ("absent", None)


def test_ac3_51_not_last_is_a_refusal():
    """
    The invariant: a surviving forge AC3 is ALWAYS the last audio track
    (appended at add time; the pipeline never reorders or appends). An
    ac3/6ch that exists but is NOT last means this cannot be the file
    forge modified — most plausibly a replacement landed at the same
    path. Refuse rather than guess.
    """
    tracks = [_video(0), _ac3_forge(1), _aac51(2)]
    assert resolve_forge_ac3_for_undo(tracks) == ("mismatch", None)


def test_wrong_shaped_last_track_does_not_match():
    """
    Channel count is part of the identity: an AC3 STEREO commentary
    track sitting last must not be mistaken for the forge AC3 5.1. With
    no ac3/6ch anywhere, the correct outcome is 'absent'.
    """
    ac3_stereo = make_track(stream_index=2, track_type="audio", codec="ac3",
                            language="eng", channels=2, channel_layout="stereo")
    tracks = [_video(0), _aac51(1), ac3_stereo]
    assert resolve_forge_ac3_for_undo(tracks) == ("absent", None)


def test_preexisting_ac3_51_before_forge_track_still_resolves_last():
    """
    Candidates only require an AAC 5.1 — the source can carry its own
    AC3 5.1 too. The pre-existing one sits BEFORE the appended forge
    track, so 'last' must pick the forge one, never the original.
    """
    tracks = [_video(0), _ac3_forge(1), _aac51(2), _ac3_forge(3)]
    outcome, idx = resolve_forge_ac3_for_undo(tracks)
    assert outcome == "found"
    assert idx == 2  # the appended (last) one, not the pre-existing at 0


# ═══════════════════════════════════════════════════════════════════════════
# M3 — analyze_file's und-threshold exclusion validates the index
# ═══════════════════════════════════════════════════════════════════════════

def test_stale_forge_index_still_excludes_the_real_forge_track():
    """
    und-language AAC 5.1 + its und forge AC3, threshold 2. After a drop
    elsewhere the stored index (here: 5, past the end) no longer lands
    on anything — the OLD code then excluded nothing and tripped manual
    review on a file whose second und track is a known, intentional
    duplicate. The validated fallback must exclude the last-track AC3.
    """
    settings = dict(BASE_SETTINGS)
    tracks = [_video(0), _aac51(1, "und"), _ac3_forge(2, "und")]
    decision = analyze_file(make_file_info(), tracks, settings,
                            forged_ac3_audio_index=5)
    assert not decision.is_manual_review, (
        "Stale forge index defeated the exclusion — the file was sent to "
        "manual review even though its second und track is the forge AC3."
    )


def test_stale_index_landing_on_wrong_track_does_not_exclude_it():
    """
    The inverse hazard the validation exists for: two GENUINE und AAC
    tracks and a stored index that happens to land in range. Blind
    indexing excluded whichever unrelated track sat there, silently
    lowering the und count below threshold. With no ac3/6ch anywhere,
    nothing may be excluded — the file must reach manual review.
    """
    settings = dict(BASE_SETTINGS)
    tracks = [_video(0), _aac51(1, "und"), _aac51(2, "und")]
    decision = analyze_file(make_file_info(), tracks, settings,
                            forged_ac3_audio_index=1)
    assert decision.is_manual_review, (
        "An in-range stale index excluded a genuine und track that is "
        "not the forge AC3 — the threshold gate was silently bypassed."
    )


# ═══════════════════════════════════════════════════════════════════════════
# M8 — mid-undo "processing" jobs are matched by the exclusion query
# ═══════════════════════════════════════════════════════════════════════════

def _make_forge_db():
    """In-memory database with one MediaFile — the fix IS the query, so
    it's tested against real SQLAlchemy filtering, not a re-implementation."""
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    from app.database.models import Base, MediaFile

    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    db = sessionmaker(bind=engine)()
    db.add(MediaFile(id=1, path="/media/x.mkv", filename="x.mkv",
                     directory="/media", size=100, mtime=1.0))
    db.commit()
    return db


def _forge_job(db, status, is_undo):
    from app.database.models import Ac3ForgeJob
    job = Ac3ForgeJob(file_id=1, status=status, is_undo=is_undo,
                      aac_stream_index=1, audio_track_count=1)
    db.add(job)
    db.commit()
    return job


def test_processing_undo_is_matched():
    """
    The M8 window: the worker claims an undo job, flipping undo_pending
    → "processing" while the AC3 is still physically in the file for the
    entire rewrite. A scan landing in that window must still get the
    exclusion index — the old status list returned None here.
    """
    from app.core.scanner import _get_forged_ac3_audio_index
    db = _make_forge_db()
    _forge_job(db, status="processing", is_undo=True)
    assert _get_forged_ac3_audio_index(db, 1) == 1


def test_processing_add_is_not_matched():
    """
    "processing" with is_undo=False is an ADD in flight — the AC3 does
    NOT exist yet, so excluding a track would wrongly lower the und
    count. Must stay unmatched, exactly as before M8.
    """
    from app.core.scanner import _get_forged_ac3_audio_index
    db = _make_forge_db()
    _forge_job(db, status="processing", is_undo=False)
    assert _get_forged_ac3_audio_index(db, 1) is None


def test_original_status_matrix_unchanged():
    """The pre-M8 matches and non-matches must all behave identically."""
    from app.core.scanner import _get_forged_ac3_audio_index
    expectations = {
        "success":      True,
        "undo_pending": True,
        "undo_failed":  True,
        "pending":      False,
        "failed":       False,
        "undone":       False,
        "cancelled":    False,
    }
    for status, should_match in expectations.items():
        db = _make_forge_db()
        _forge_job(db, status=status, is_undo=status.startswith("undo"))
        got = _get_forged_ac3_audio_index(db, 1)
        assert (got is not None) == should_match, (
            f"status={status!r}: expected match={should_match}, got {got!r}"
        )
        db.close()
