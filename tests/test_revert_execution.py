"""
Executing a revert.

This is the only part of the feature that overwrites the user's media, so
the tests are weighted towards refusing rather than succeeding. A revert
that runs when it should not have is unrecoverable: the tracks it muxes
in belong to a file that no longer exists, and the result plays fine.

The sentinel is the guard that matters. processed_size/processed_mtime
record the file as the job left it, so a mismatch means something else
has written to it since — Sonarr upgrading the episode being the obvious
case. Four tests below are about that check alone, including one that
pins the error message identifying WHAT changed, because "revert failed"
on a file the user can see is right there is not actionable.

Everything else follows from "the bytes are what matter":

  • Every precondition is checked before FFmpeg starts, so a refusal
    leaves the file byte-for-byte as it was.
  • The write is staged, so a crash mid-restore cannot leave a truncated
    file where a working one was.
  • The database is updated only afterwards, and a bookkeeping failure
    never turns a successful restore into a reported failure.

Verified by mutation, 13 applied, 13 killed. Two initially SURVIVED, and
both had the same root cause: no test drove a FAILING FFmpeg run, only
failing validation. Removing the missing-sidecar check survived because
FFmpeg's own failure on a missing input surfaces as "temp file(s) missing
after command completed", so an assertion looking for "missing" passed
either way. Removing the early return on a failed run survived because
nothing ever reached it. test_a_failed_restore_changes_nothing closes
both: a sidecar that exists, passes validation, and is unreadable.

The full list:

  • Size sentinel check removed                  → killed
  • Mtime sentinel check removed                 → killed
  • Sentinel compared with != inverted to ==     → killed
  • Missing sidecar not checked                  → killed
  • Missing current file not checked             → killed
  • Database updated even when FFmpeg failed     → killed
  • Revert point kept after a successful revert  → killed
  • Only the used point cleared, siblings kept   → killed
  • Sidecars not deleted when points are cleared → killed
  • status left as "processed" after a revert    → killed
  • Track rows not refreshed                     → killed
  • Processed file not removed after a container
    change                                        → killed
  • Bookkeeping exception propagated as a failed
    revert                                        → killed

No equivalent mutants.
"""
import asyncio
import json
import os
import shutil
import subprocess

import pytest


ffmpeg_required = pytest.mark.skipif(
    shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None,
    reason="ffmpeg/ffprobe not available",
)


# ── Harness ──────────────────────────────────────────────────────────────────

@pytest.fixture
def env(tmp_path, monkeypatch):
    """
    A real original, a real processed file, and a real sidecar cut from
    the original — the whole capture side, run for real, so the restore
    under test is working from genuine inputs.
    """
    from sqlalchemy.orm import sessionmaker

    from tests.conftest import memory_engine

    from app.config import settings as app_settings
    from app.core.ffmpeg import build_sidecar_command
    from app.core.revert import build_manifest, match_streams
    from app.core.timeutil import utcnow_naive
    from app.database.models import Base, MediaFile, RevertPoint
    import app.database.session as session_mod

    if shutil.which("ffmpeg") is None:
        pytest.skip("ffmpeg not available")

    media_dir = tmp_path / "media"
    media_dir.mkdir()
    recycle = tmp_path / "recycle"
    recycle.mkdir()
    monkeypatch.setattr(app_settings, "RECYCLE_DIR", str(recycle), raising=False)
    monkeypatch.setattr(app_settings, "TEMP_DIR", str(tmp_path / "tmp"),
                        raising=False)

    original = media_dir / "Show.mkv"
    subprocess.run(
        ["ffmpeg", "-v", "error", "-y",
         "-f", "lavfi", "-i", "testsrc=size=160x120:rate=10:duration=1",
         "-f", "lavfi", "-i", "sine=frequency=440:duration=1",
         "-f", "lavfi", "-i", "sine=frequency=880:duration=1",
         "-map", "0:v", "-map", "1:a", "-map", "2:a",
         "-metadata:s:a:0", "language=eng", "-metadata:s:a:1", "language=fre",
         "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac",
         "-f", "matroska", str(original)], check=True)

    def probe(path):
        out = subprocess.run(
            ["ffprobe", "-v", "error", "-show_streams", "-show_format",
             "-of", "json", str(path)], capture_output=True, text=True)
        return json.loads(out.stdout)

    original_probe = probe(original)
    original_streams = _summarise(original)

    # The job: drop the French track, in place.
    processed = media_dir / "Show.processed.mkv"
    subprocess.run(
        ["ffmpeg", "-v", "error", "-y", "-i", str(original),
         "-map", "0:0", "-map", "0:1", "-c", "copy",
         "-f", "matroska", str(processed)], check=True)
    os.replace(processed, original)

    manifest = build_manifest(original_probe, original_path=str(original),
                              original_container="mkv")
    matches = match_streams(manifest, probe(original))
    lost = [s for s, i in matches if i is None]
    assert len(lost) == 1

    sidecar = recycle / "1_1.remuxarr_revert"
    # Cut from a pristine copy, since the original has already been
    # overwritten in place above — capture does this before the swap.
    pristine = tmp_path / "pristine.mkv"
    subprocess.run(
        ["ffmpeg", "-v", "error", "-y",
         "-f", "lavfi", "-i", "testsrc=size=160x120:rate=10:duration=1",
         "-f", "lavfi", "-i", "sine=frequency=440:duration=1",
         "-f", "lavfi", "-i", "sine=frequency=880:duration=1",
         "-map", "0:v", "-map", "1:a", "-map", "2:a",
         "-metadata:s:a:0", "language=eng", "-metadata:s:a:1", "language=fre",
         "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac",
         "-f", "matroska", str(pristine)], check=True)
    subprocess.run(
        build_sidecar_command([str(pristine)], str(sidecar),
                              [(s, 0, s["index"]) for s in lost]),
        check=True)

    sidecar_indices = {id(s): n for n, s in enumerate(lost)}
    for stream, produced_index in matches:
        stream["processed_index"] = produced_index
        if id(stream) in sidecar_indices:
            stream["sidecar_index"] = sidecar_indices[id(stream)]

    engine = memory_engine()
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine)
    monkeypatch.setattr(session_mod, "SessionLocal", factory)

    db = factory()
    stat = original.stat()
    # last_processed is set because _finish_job sets it on every success,
    # so a file with a revert point always carries one. Left at the column
    # default of None the "cleared on revert" assertion could not fail:
    # verified by mutation, deleting _apply's assignment kept the suite
    # green.
    media = MediaFile(path=str(original), filename="Show.mkv",
                      directory=str(media_dir), size=stat.st_size,
                      mtime=stat.st_mtime, container="mkv", status="processed",
                      last_processed=utcnow_naive())
    db.add(media)
    db.commit()

    point = RevertPoint(
        file_id=media.id, sidecar_path=str(sidecar),
        sidecar_size=sidecar.stat().st_size,
        manifest=json.dumps(manifest), original_path=str(original),
        original_container="mkv",
        processed_size=stat.st_size, processed_mtime=stat.st_mtime,
    )
    db.add(point)
    db.commit()

    return {"db": db, "media": media, "point": point, "path": original,
            "recycle": recycle, "sidecar": sidecar,
            "original_streams": original_streams, "probe": probe,
            "media_dir": media_dir}


def _summarise(path):
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-show_streams", "-of", "json", str(path)],
        capture_output=True, text=True)
    streams = json.loads(out.stdout)["streams"]
    return [(s["codec_type"], s["codec_name"], (s.get("tags") or {}).get("language"))
            for s in streams]


def _revert(point_id):
    from app.core.revert_restore import restore_revert_point

    return asyncio.run(restore_revert_point(point_id))


# ── The happy path ───────────────────────────────────────────────────────────

@ffmpeg_required
def test_revert_puts_the_dropped_track_back(env):
    assert len(_summarise(env["path"])) == 2, "fixture is not in the processed state"

    outcome = _revert(env["point"].id)

    assert outcome.success is True, outcome.error
    assert _summarise(env["path"]) == env["original_streams"]


@ffmpeg_required
def test_a_successful_revert_clears_the_revert_point(env):
    from app.database.models import RevertPoint

    sidecar = env["sidecar"]
    _revert(env["point"].id)

    env["db"].expire_all()
    assert env["db"].query(RevertPoint).count() == 0
    assert not sidecar.exists(), "the sidecar outlived its revert point"


@ffmpeg_required
def test_a_stray_second_point_is_cleared_too(env):
    """
    Capture keeps one point per file, so a second row should not exist —
    this pins what happens if one ever does. Its sidecar describes a state
    the file no longer matches and its fingerprint could never match
    again, so leaving it behind means a dead entry and a sidecar nothing
    can reach.
    """
    from app.database.models import RevertPoint

    sibling_sidecar = env["recycle"] / "1_2.remuxarr_revert"
    sibling_sidecar.write_bytes(b"older tracks")
    env["db"].add(RevertPoint(
        file_id=env["media"].id, sidecar_path=str(sibling_sidecar),
        sidecar_size=12, manifest="{}", original_path=str(env["path"]),
    ))
    env["db"].commit()

    _revert(env["point"].id)

    env["db"].expire_all()
    assert env["db"].query(RevertPoint).count() == 0
    assert not sibling_sidecar.exists()


@ffmpeg_required
def test_the_file_is_queued_for_re_evaluation(env):
    """
    The file now looks the way it did before the job, so the next scan
    should judge it on its merits. Left as "processed" a reverted file is
    hidden from the very analysis that would say what it needs.

    The size/mtime sentinels are the half that actually delivers that,
    and they are asserted here rather than left to the end-to-end test
    below so that breaking either one is caught on its own terms. This
    test used to assert status alone and pass while the file was never
    re-evaluated at all.
    """
    _revert(env["point"].id)

    env["db"].expire_all()
    assert env["media"].status == "unprocessed"
    assert env["media"].last_processed is None
    assert env["media"].size == -1
    assert env["media"].mtime == -1.0


@ffmpeg_required
def test_a_delta_scan_actually_re_evaluates_the_reverted_file(env):
    """
    The property the test above is named for, driven through the real
    scanner rather than inferred from the columns it leaves behind.

    Both are needed. The sibling pins the values _apply writes; this pins
    what the scanner does with them, which is the thing the user cares
    about and the thing that was broken. Asserting the columns alone is
    how a reverted file went a whole release being skipped by every delta
    scan while a test named for re-evaluation passed.
    """
    from tests.conftest import BASE_SETTINGS
    from app.core.scanner import ScanStats, _process_file

    _revert(env["point"].id)

    env["db"].expire_all()
    stats = ScanStats()
    _process_file(env["db"], str(env["path"]), BASE_SETTINGS,
                  force_probe=False, dry_run=False, stats=stats)

    assert stats.unchanged == 0, (
        "the reverted file was skipped by a delta scan — it is only "
        "reachable via a forced full rescan"
    )


@ffmpeg_required
def test_track_rows_are_refreshed_to_the_restored_file(env):
    """
    The rows must describe the file that is on disk now, not the one the
    job produced.

    This used to be the only thing standing between a reverted file and
    permanently stale rows, because _apply wrote the restored file's real
    size and mtime and every delta scan therefore skipped it. It now
    writes the sentinels instead, so the next scan re-probes anyway —
    but "the next scan" can be hours away on a schedule, and until then
    anything reading Track directly is served from here. AC3 Forge's
    candidate query is the one that matters: it selects on codec and
    channels, so rows describing the pre-revert file offer the wrong
    files and hide the right ones.
    """
    from app.database.models import Track

    _revert(env["point"].id)

    env["db"].expire_all()
    tracks = env["db"].query(Track).filter(
        Track.file_id == env["media"].id).all()
    languages = sorted(t.language for t in tracks if t.track_type == "audio")
    assert languages == ["eng", "fre"], "Track rows still describe the processed file"


# ── Refusing ─────────────────────────────────────────────────────────────────

@ffmpeg_required
def test_a_resized_file_is_refused(env):
    """
    Sonarr upgrading the episode is the everyday case. The sidecar's
    tracks belong to the previous release, and muxing them into the new
    one produces a file that plays and is quietly wrong.
    """
    before = _summarise(env["path"])
    env["point"].processed_size = env["point"].processed_size + 1
    env["db"].commit()

    outcome = _revert(env["point"].id)

    assert outcome.success is False
    assert "size" in outcome.error
    assert _summarise(env["path"]) == before, "the file was modified anyway"


@ffmpeg_required
def test_a_touched_file_is_refused(env):
    before = _summarise(env["path"])
    env["point"].processed_mtime = env["point"].processed_mtime - 500
    env["db"].commit()

    outcome = _revert(env["point"].id)

    assert outcome.success is False
    assert "modified" in outcome.error
    assert _summarise(env["path"]) == before


@ffmpeg_required
def test_the_refusal_says_what_changed(env):
    """
    "Revert failed" on a file the user can see is right there is not
    actionable. The message has to name the file and the reason.
    """
    env["point"].processed_size = 999999
    env["db"].commit()

    outcome = _revert(env["point"].id)

    assert "Show.mkv" in outcome.error
    assert "999999" in outcome.error


@ffmpeg_required
def test_a_missing_sidecar_is_refused_before_ffmpeg_runs(env):
    """
    Caught in validation, not by FFmpeg failing on a missing input.

    The message is asserted specifically because both routes produce an
    error containing the word "missing" — FFmpeg's own failure surfaces
    as "temp file(s) missing after command completed" — so a looser
    assertion here passes whether or not the check exists at all.
    """
    before = _summarise(env["path"])
    env["sidecar"].unlink()

    outcome = _revert(env["point"].id)

    assert outcome.success is False
    assert "recycle volume" in outcome.error
    assert _summarise(env["path"]) == before


@ffmpeg_required
def test_a_failed_restore_changes_nothing(env):
    """
    The sidecar exists and passes validation but is unreadable, so FFmpeg
    itself fails. Everything must survive: the file, the revert point, and
    the file's status — a failed revert the user can retry is fine, one
    that consumed its own revert point is not.
    """
    from app.database.models import RevertPoint

    before = _summarise(env["path"])
    env["sidecar"].write_bytes(b"not a matroska file")

    outcome = _revert(env["point"].id)

    assert outcome.success is False
    assert _summarise(env["path"]) == before, "the media file was modified"

    env["db"].expire_all()
    assert env["db"].query(RevertPoint).count() == 1, "the revert point was consumed"
    assert env["sidecar"].exists()
    assert env["media"].status == "processed"


@ffmpeg_required
def test_a_missing_media_file_is_refused(env):
    env["path"].unlink()

    outcome = _revert(env["point"].id)

    assert outcome.success is False
    assert "no longer on disk" in outcome.error


def test_an_unknown_revert_point_is_refused(tmp_path, monkeypatch):
    from sqlalchemy.orm import sessionmaker

    from tests.conftest import memory_engine

    from app.database.models import Base
    import app.database.session as session_mod

    engine = memory_engine()
    Base.metadata.create_all(engine)
    monkeypatch.setattr(session_mod, "SessionLocal", sessionmaker(bind=engine))

    outcome = _revert(999)

    assert outcome.success is False
    assert "no longer exists" in outcome.error


# ── Container change ─────────────────────────────────────────────────────────

@ffmpeg_required
def test_reverting_a_container_change_removes_the_processed_file(env):
    """
    A MKV → MP4 job leaves the file under a different name. The restored
    original goes back to the original name, and the processed copy beside
    it is dead weight the user never asked to keep.
    """
    import json as _json

    from app.database.models import RevertPoint

    processed_mp4 = env["media_dir"] / "Show.mp4"
    env["path"].rename(processed_mp4)

    point = env["db"].get(RevertPoint, env["point"].id)
    manifest = _json.loads(point.manifest)
    manifest["path"] = str(env["path"])
    point.manifest = _json.dumps(manifest)
    stat = processed_mp4.stat()
    point.processed_size, point.processed_mtime = stat.st_size, stat.st_mtime
    env["media"].path = str(processed_mp4)
    env["media"].filename = "Show.mp4"
    env["db"].commit()

    outcome = _revert(point.id)

    assert outcome.success is True, outcome.error
    assert env["path"].exists(), "the original was not restored"
    assert not processed_mp4.exists(), "the processed file was left behind"

    env["db"].expire_all()
    assert env["media"].path == str(env["path"])


# ── Bookkeeping is not the restore ───────────────────────────────────────────

@ffmpeg_required
def test_a_database_failure_does_not_report_a_failed_revert(env, monkeypatch):
    """
    The bytes on disk are already correct at this point. Reporting failure
    would send the user to try again against a file that has already been
    reverted, and the sentinel would then refuse them.
    """
    import app.core.revert_restore as mod

    def boom(*_a, **_k):
        raise RuntimeError("database went away")

    monkeypatch.setattr(mod, "_apply", boom)

    outcome = _revert(env["point"].id)

    assert outcome.success is True
    assert _summarise(env["path"]) == env["original_streams"]
