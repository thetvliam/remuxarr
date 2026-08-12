"""
_run_job's post-success file handling — the source-deletion guard.

WHY THIS FILE EXISTS
--------------------
After a successful remux, _run_job does:

    if output_path != input_path and os.path.exists(input_path):
        os.remove(input_path)

The guard is what makes that safe. run_staged_subprocess has already swapped
the finished file into position at input_path for every IN-PLACE job — which
is the overwhelming majority, since output_path only differs when the
container changes. Drop the `output_path != input_path` term and the line
deletes the file the job just successfully produced.

Deleting that term leaves the entire suite passing. It is silent, total data
loss on every remux that is not a container conversion, with a job row
reporting success. Nothing guarded it.

WHAT THESE TESTS PIN
--------------------
  • in-place success        -> source must SURVIVE (the regression)
  • container-change success -> source must be DELETED (the guard's real purpose)
  • failure                  -> source must SURVIVE regardless of paths

The third matters because a fix for the first that simply never deletes would
pass test one while breaking container conversions, leaving both the old and
new file on disk.
"""
import asyncio
from types import SimpleNamespace

import pytest


import app.core.worker as worker


class _FakeWS:
    async def broadcast_json(self, *_a, **_k):
        return None


def _run(job_id=1):
    """Drive _run_job on a real event loop, as the worker does."""
    async def driver():
        loop = asyncio.get_running_loop()
        await worker._run_job(job_id, _FakeWS(), loop)
    asyncio.run(driver())


@pytest.fixture
def scenario(tmp_path, monkeypatch):
    """
    Stubs everything around the guard so the assertion is about the guard and
    nothing else: no ffmpeg, no database, no staging.

    Returns a factory taking the output path and ffmpeg outcome, so each test
    varies only the thing it is actually testing.
    """
    source = tmp_path / "Movie.mkv"
    source.write_bytes(b"ORIGINAL-MEDIA-BYTES")

    finished = {}

    def build(output_path=None, success=True):
        # None means "in place" — determine_output_path returns the input
        # unchanged, which is what it really does when no container change is
        # planned. Resolved here so no test has to patch a module global
        # outside monkeypatch and leak it into the next test.
        resolved_out = str(source) if output_path is None else str(output_path)
        decision = SimpleNamespace(actions=[], target_container=None)

        def fake_load(_job_id):
            job_dict = {"is_dry_run": False}
            file_dict = {"path": str(source), "size": 0}
            return job_dict, file_dict, [], {"job_timeout_minutes": 0}, decision

        async def fake_execute(**kwargs):
            # Simulate the real thing: staging has already put the finished
            # file at output_path by the time _run_job inspects the result.
            out = kwargs["output_path"]
            if success:
                with open(out, "wb") as f:
                    f.write(b"REMUXED-MEDIA-BYTES")
            return SimpleNamespace(
                success=success,
                output_path=out if success else None,
                output_size=19 if success else None,
                error=None if success else "ffmpeg failed",
            )

        def fake_finish(job_id, ok, out_path, out_size, err):
            finished.update(job_id=job_id, ok=ok, output_path=out_path, error=err)

        monkeypatch.setattr(worker, "_load_job_data", fake_load)
        monkeypatch.setattr(worker, "execute_ffmpeg", fake_execute)
        monkeypatch.setattr(worker, "_finish_job", fake_finish)
        monkeypatch.setattr(worker, "determine_output_path",
                            lambda _in, _dec: resolved_out)
        # No subtitle extraction path in these scenarios.
        monkeypatch.setattr(worker, "execute_ffmpeg_combined", None, raising=False)
        return source, finished

    return build


# ── The regression ───────────────────────────────────────────────────────────

def test_in_place_success_does_not_delete_the_source(scenario):
    """
    THE regression. output_path == input_path, so staging has already replaced
    the file in position — deleting input_path here destroys the job's own
    output. The file must exist afterwards and must hold the REMUXED bytes,
    not the original ones.
    """
    source, finished = scenario(output_path=None, success=True)

    _run()

    assert source.exists(), (
        "SOURCE FILE DELETED after a successful in-place job — this is the "
        "output_path != input_path guard being lost. Silent total data loss."
    )
    assert source.read_bytes() == b"REMUXED-MEDIA-BYTES"
    assert finished["ok"] is True


def test_container_change_success_deletes_the_original(scenario, tmp_path):
    """
    The guard's actual purpose. When the container changes the new file is a
    DIFFERENT path, so the superseded original must go — otherwise every
    conversion leaves both copies and doubles library size.
    """
    new_path = tmp_path / "Movie.mp4"
    source, finished = scenario(output_path=str(new_path), success=True)

    _run()

    assert not source.exists(), "original was not removed after a container change"
    assert new_path.exists()
    assert new_path.read_bytes() == b"REMUXED-MEDIA-BYTES"
    assert finished["ok"] is True


# ── Failure paths must never delete ──────────────────────────────────────────

def test_failed_container_change_keeps_the_source(scenario, tmp_path):
    """
    A failed job must leave the library exactly as it found it, even when the
    paths differ — the deletion is gated on result.success as well.
    """
    new_path = tmp_path / "Movie.mp4"
    source, finished = scenario(output_path=str(new_path), success=False)

    _run()

    assert source.exists(), "source deleted after a FAILED job"
    assert source.read_bytes() == b"ORIGINAL-MEDIA-BYTES"
    assert finished["ok"] is False


def test_failed_in_place_keeps_the_source(scenario):
    source, finished = scenario(output_path=None, success=False)

    _run()

    assert source.exists()
    assert source.read_bytes() == b"ORIGINAL-MEDIA-BYTES"
    assert finished["ok"] is False


# ── Dry run must not touch the disk ──────────────────────────────────────────
#
# Found by an independent mutation audit (Phase 1). Neutering the dry-run
# short-circuit in _run_job survived the entire 662-test suite. The existing
# dry-run tests all exercise _finish_job, the bookkeeping function — the
# dry-run STATUS was pinned, the dry-run BEHAVIOUR was not.
#
# This file is the right home: the guard being removed sends a dry run through
# disk-space pre-flight, subtitle extraction, FFmpeg, and the source-deletion
# branch immediately above. A "preview" would destructively remux and could
# delete the original.

@pytest.fixture
def dry_run_scenario(tmp_path, monkeypatch):
    """
    As `scenario`, but the loaded job is a dry run and execute_ffmpeg records
    any call rather than performing one — so the assertion is that the guard
    returned before reaching it.
    """
    source = tmp_path / "Movie.mkv"
    source.write_bytes(b"ORIGINAL-MEDIA-BYTES")

    calls = {"ffmpeg": [], "finished": {}}

    def build(output_path=None):
        resolved_out = str(source) if output_path is None else str(output_path)
        decision = SimpleNamespace(actions=[], target_container=None)

        def fake_load(_job_id):
            job_dict  = {"is_dry_run": True}
            file_dict = {"path": str(source), "size": 0}
            return job_dict, file_dict, [], {"job_timeout_minutes": 0}, decision

        async def fake_execute(**kwargs):
            calls["ffmpeg"].append(kwargs)
            out = kwargs["output_path"]
            with open(out, "wb") as f:
                f.write(b"REMUXED-MEDIA-BYTES")
            return SimpleNamespace(success=True, output_path=out,
                                   output_size=19, error=None)

        def fake_finish(job_id, ok, out_path, out_size, err):
            calls["finished"].update(job_id=job_id, ok=ok, error=err)

        monkeypatch.setattr(worker, "_load_job_data", fake_load)
        monkeypatch.setattr(worker, "execute_ffmpeg", fake_execute)
        monkeypatch.setattr(worker, "_finish_job", fake_finish)
        monkeypatch.setattr(worker, "determine_output_path",
                            lambda _in, _dec: resolved_out)
        monkeypatch.setattr(worker, "execute_ffmpeg_combined", None, raising=False)
        return source, calls

    return build


def test_a_dry_run_never_invokes_ffmpeg(dry_run_scenario):
    """
    The guard that makes a dry run dry. Without it the job proceeds to real
    execution — a preview would rewrite the user's file.
    """
    _source, calls = dry_run_scenario()

    _run()

    assert calls["ffmpeg"] == [], (
        "FFmpeg was invoked for a DRY RUN — the short-circuit in _run_job is "
        "gone and previews now destructively remux"
    )


def test_a_dry_run_leaves_the_source_byte_identical(dry_run_scenario):
    """The user's file must be untouched, not merely 'still present'."""
    source, _calls = dry_run_scenario()

    _run()

    assert source.read_bytes() == b"ORIGINAL-MEDIA-BYTES", (
        "a dry run modified the source file"
    )


def test_a_dry_run_with_a_container_change_does_not_delete_the_original(
        dry_run_scenario, tmp_path):
    """
    The worst case. With the guard gone, a dry run of an MKV→MP4 conversion
    reaches the source-deletion branch immediately above: output_path !=
    input_path, so the original is removed. A preview would delete the file it
    was previewing.
    """
    new_path = tmp_path / "Movie.mp4"
    source, calls = dry_run_scenario(output_path=str(new_path))

    _run()

    assert source.exists(), (
        "a DRY RUN deleted the source file — the container-change deletion "
        "branch was reached because the dry-run short-circuit is gone"
    )
    assert source.read_bytes() == b"ORIGINAL-MEDIA-BYTES"
    assert not new_path.exists(), "a dry run produced a real output file"


def test_a_dry_run_still_finishes_its_job(dry_run_scenario):
    """
    Short-circuiting must not mean abandoning the row — the job still has to
    reach a terminal state, or it sits at 'processing' until the next restart.
    """
    _source, calls = dry_run_scenario()

    _run()

    assert calls["finished"].get("ok") is True
    assert calls["finished"].get("job_id") == 1
