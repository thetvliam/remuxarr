"""
worker.py — _run_job's pre-flight: everything before FFmpeg is spawned.

Three things happen between _run_job being called and it committing to a
subprocess: a job with nothing loaded returns, a dry run finishes without
touching the file, and a disk-space check runs against both directories
the job will write into. The disk check and the no-data return were
uncovered.

The disk check exists to turn a cryptic mid-encode failure into an
immediate, readable one. It is entirely advisory — nothing downstream
depends on it having run — which is exactly why losing it is invisible:
the job still goes ahead, gets most of the way through a remux, and dies
on a write with whatever error the filesystem happened to raise. It is
also the only place the job's two destinations are distinguished, since
_pick_temp_dir can land on a different volume from the output directory,
and a message naming the wrong one sends the reader to the wrong disk.

The dry-run outcome is deliberately not re-tested here.
test_source_file_preservation.py already covers it, and confirmed so
under mutation: recording a dry run as a failure, and letting a dry run
fall through into real processing, are both killed by tests already in
that file. Only the extract-subtitle logging line inside the dry-run
branch was uncovered, and the one dry-run test below exists for that line
rather than for the outcome.

Confirmed unprotected before this file was written: ten mutations applied
to the pre-flight, each run against the whole 1277-test suite, all ten
survived. Running the check for a file of unknown size; treating exactly
enough room as too little; checking only the temp dir; recording a
shortfall as a successful job; failing for space and then running anyway;
abandoning the job when a directory cannot be stated; always blaming the
temp dir in the message; formatting sizes with decimal divisors under
binary labels; never creating the directories before stating them; and
proceeding with a substituted payload when nothing loaded. All ten are
killed by the tests below.

Two further mutations were applied and are recorded as already covered
rather than as part of this file's contribution: a dry run recorded as a
failure, and a dry run falling through to real processing.
"""
import asyncio
import logging
import os
from types import SimpleNamespace

import pytest

import app.core.worker as worker
from app.core.decision import Action, ProcessingDecision


GIB = 1024 ** 3


class ReachedExecution(Exception):
    """
    Raised by the stand-in for the first step after the pre-flight.

    _files_the_job_will_create is called immediately after the check and
    outside _run_job's try block, so raising there propagates out and
    gives these tests a positive signal that the pre-flight let the job
    through — rather than inferring it from the absence of a failure.
    """


# ── Harness ──────────────────────────────────────────────────────────────────

class FakeWS:
    def __init__(self):
        self.sent = []

    async def broadcast_json(self, payload):
        self.sent.append(payload)


@pytest.fixture
def rig(monkeypatch, tmp_path):
    """
    _run_job with the pre-flight's collaborators replaced.

    shutil.disk_usage is patched on the shutil module itself, since that
    is how worker.py reaches it; monkeypatch restores it at the end of
    each test. The directories are real paths under tmp_path so that
    os.makedirs does real work and can be asserted on.
    """
    rig = SimpleNamespace(
        ws=FakeWS(),
        temp_dir=str(tmp_path / "temp"),
        output_path=str(tmp_path / "out" / "Show.mkv"),
        input_path=str(tmp_path / "Show.mkv"),
        size=2 * GIB,
        actions=[],
        is_dry_run=False,
        free={},                 # directory -> free bytes
        default_free=100 * GIB,
        oserror_dirs=set(),
        disk_calls=[],
        finish_calls=[],
    )

    def fake_load(job_id):
        if rig.job_data_missing:
            return None
        decision = ProcessingDecision(
            should_process=True, reason="remux",
            actions=list(rig.actions), target_container="mkv",
        )
        return (
            {"id": job_id, "is_dry_run": rig.is_dry_run},
            {"id": 1, "path": rig.input_path,
             "filename": "Show.mkv", "size": rig.size},
            [],
            {},
            decision,
        )

    rig.job_data_missing = False

    def fake_disk_usage(directory):
        rig.disk_calls.append(directory)
        if directory in rig.oserror_dirs:
            raise OSError("stat unavailable on this mount")
        return SimpleNamespace(free=rig.free.get(directory, rig.default_free))

    def reached_execution(extract_actions):
        raise ReachedExecution()

    monkeypatch.setattr(worker, "_load_job_data", fake_load)
    monkeypatch.setattr(worker, "determine_output_path",
                        lambda path, decision: rig.output_path)
    monkeypatch.setattr(worker, "_pick_temp_dir", lambda path: rig.temp_dir)
    monkeypatch.setattr(worker.shutil, "disk_usage", fake_disk_usage)
    monkeypatch.setattr(worker, "_files_the_job_will_create", reached_execution)
    monkeypatch.setattr(worker, "_finish_job",
                        lambda *args: rig.finish_calls.append(args))
    return rig


def run(rig, job_id=1):
    async def driver():
        loop = asyncio.get_running_loop()
        await worker._run_job(job_id, rig.ws, loop)

    asyncio.run(driver())


def run_expecting_execution(rig, job_id=1):
    """The job got past the pre-flight and started real work."""
    with pytest.raises(ReachedExecution):
        run(rig, job_id)


def output_dir(rig):
    return os.path.dirname(os.path.abspath(rig.output_path))


def failure_message(rig):
    assert len(rig.finish_calls) == 1, rig.finish_calls
    job_id, success, out, size, error = rig.finish_calls[0]
    assert success is False
    return error


# ── Nothing to run ───────────────────────────────────────────────────────────

def test_a_job_with_nothing_loaded_does_nothing_at_all(rig):
    """
    _load_job_data returning None means it already settled the item —
    failed it, skipped it or gated it for review — so there is nothing
    left to do and nothing to report. Continuing from here would process
    a job whose row has already moved on.
    """
    rig.job_data_missing = True

    run(rig)

    assert rig.finish_calls == []
    assert rig.disk_calls == []


# ── Dry run ──────────────────────────────────────────────────────────────────

def test_a_dry_run_lists_the_subtitles_it_would_extract(rig, caplog):
    """
    The outcome of a dry run is covered by test_source_file_preservation.
    This is here for the extraction listing specifically, which no dry-run
    test reaches because none of them carries extract actions — and which
    reads a field, action.external_path, that only extract actions have.
    """
    rig.is_dry_run = True
    rig.actions = [
        Action(action_type="extract_subtitle", description="extract eng",
               track_type="subtitle", stream_index=2,
               external_path="/media/Show.eng.srt"),
    ]

    with caplog.at_level(logging.INFO, logger="app.core.worker"):
        run(rig)

    assert any("/media/Show.eng.srt" in r.getMessage() for r in caplog.records)
    assert rig.disk_calls == []


# ── The disk-space check: letting a job through ──────────────────────────────

def test_a_job_with_room_proceeds_to_execution(rig):
    rig.default_free = 100 * GIB

    run_expecting_execution(rig)

    assert rig.finish_calls == []


def test_exactly_enough_room_is_enough(rig):
    """
    The boundary is deliberate: free == need passes. Rejecting it would
    fail jobs on a volume sized exactly to the file, and the check is a
    guard against an obvious shortfall rather than a headroom policy.
    """
    rig.size = 2 * GIB
    rig.default_free = 2 * GIB

    run_expecting_execution(rig)

    assert rig.finish_calls == []


def test_a_file_of_unknown_size_skips_the_check(rig):
    """
    size can be missing or zero for a row written before the file was
    stated. There is nothing to compare against, so the check is skipped
    rather than run against a need of zero — which would pass on a full
    disk.
    """
    rig.size = 0

    run_expecting_execution(rig)

    assert rig.disk_calls == []


def test_a_directory_that_cannot_be_stated_does_not_block_the_job(rig, caplog):
    """
    disk_usage fails on some network mounts. The check is advisory, so an
    unanswerable question must not become a failed job — it warns and the
    job goes ahead.
    """
    rig.oserror_dirs = {rig.temp_dir, output_dir(rig)}

    with caplog.at_level(logging.WARNING, logger="app.core.worker"):
        run_expecting_execution(rig)

    assert rig.finish_calls == []
    assert any("disk space check failed" in r.getMessage() for r in caplog.records)


# ── The disk-space check: stopping a job ─────────────────────────────────────

def test_a_short_temp_dir_fails_the_job_before_ffmpeg(rig):
    """
    Failing here rather than mid-encode is the entire point: the job is
    settled as failed and execution is never reached.
    """
    rig.size = 2 * GIB
    rig.free = {rig.temp_dir: 1 * GIB}

    run(rig)      # no ReachedExecution — the job stops here

    assert "temp dir" in failure_message(rig)
    assert rig.temp_dir in failure_message(rig)


def test_a_short_output_dir_fails_the_job_before_ffmpeg(rig):
    """
    _pick_temp_dir can land on a different volume from the output
    directory, so room in one says nothing about the other. Naming the
    wrong one in the message sends whoever reads it to the wrong disk.
    """
    rig.size = 2 * GIB
    rig.free = {output_dir(rig): 1 * GIB}

    run(rig)

    assert "output dir" in failure_message(rig)
    assert output_dir(rig) in failure_message(rig)


def test_both_destinations_are_checked(rig):
    rig.default_free = 100 * GIB

    run_expecting_execution(rig)

    assert rig.disk_calls == [rig.temp_dir, output_dir(rig)]


def test_the_shortfall_message_reports_both_sizes_in_binary_units(rig):
    """
    The units are powers of 1024 and the labels say so. Formatting 2 GiB
    against decimal divisors prints 2.1 GB under a "GB" label that means
    GiB — small, but this string is the whole diagnosis the user gets.
    """
    rig.size = 2 * GIB
    rig.free = {rig.temp_dir: 1 * GIB}

    run(rig)

    assert failure_message(rig) == (
        f"Insufficient disk space in temp dir ({rig.temp_dir}) — "
        f"need 2.0 GB, have 1.0 GB free"
    )


def test_the_destinations_are_created_before_they_are_measured(rig):
    """
    The output directory need not exist yet — a container change can send
    the result somewhere new. Measuring free space on a path that is not
    there is not a meaningful answer, so both are created first.
    """
    assert not os.path.isdir(output_dir(rig))

    run_expecting_execution(rig)

    assert os.path.isdir(rig.temp_dir)
    assert os.path.isdir(output_dir(rig))
