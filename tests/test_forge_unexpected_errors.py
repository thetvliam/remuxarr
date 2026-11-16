"""
An AC3 Forge run that raises where nothing expected it to.

A queue job has a net (_run_and_broadcast): an error nothing else caught is
logged and the job forced to failed. A forge run had none. Measured with the
real worker loop and an error injected at each point:

  • raised before the file was touched (loading the job's data): the row
    stayed at "processing" until the next start, the cancel route refused
    it, the file's queue job could not be claimed, and with one forge job
    nothing was logged until the app stopped;
  • raised in the bookkeeping after the file was rewritten: the row stayed
    at "processing" though the file had its AC3. The next start failed it,
    a failed add put the file back among the candidates, and adding again
    gave it a second AC3.

_process_next_forge now logs such an error at once and fails a job still at
"processing" (undo_failed for an undo), saying the file was not changed,
and sends the completion message. After the swap check has let the run go
on, the only way to the swap, the job is held in worker._forge_unsettled
until its outcome is written, and the net leaves its row alone: "not
changed" would be false. The outcome is written even if re-reading the
tracks raises, and the write is tried a second time.

These were planned for test_forge_orchestration.py. Its fixture stubs the
database out, and the net is a database write, so they run here on
test_forge_shutdown.py's fixture: a real file, real FFmpeg and a
file-backed database.

All 7 fail against the code before the net, each with the injected error
escaping _process_next_forge unhandled.

Verified by mutation, 11 applied, each run first against the suite as it
stood (2737 tests, the documented-count check left out):

  • the net removed                              survived → killed by the
                                                   three before-the-file
                                                   tests and both outcome
                                                   tests
  • the net not logging                          survived → killed by the
                                                   first and the last
  • the net failing a job already finished       survived → killed by the
                                                   last
  • an undo failed as "failed"                   survived → killed by the
                                                   undo
  • no completion message from the net           survived → killed by the
                                                   first
  • the outcome not tried a second time          survived → killed by the
                                                   second attempt
  • the net ignoring a job that may have
    rewritten its file                           survived → killed by the
                                                   outcome that cannot be
                                                   recorded
  • the swap check not recording the job         survived → killed by the
                                                   same
  • the track re-read able to stop the outcome   survived → killed by the
                                                   failed re-read
  • the job left marked after its outcome        survived → killed by the
                                                   last
  • the net catching a stop too                  already killed by
                                                   test_forge_shutdown.py
                                                   (a run stopped while
                                                   FFmpeg runs is requeued)

The proposal named eight; the swap check not recording the job, the track
re-read and the job left marked were added once the code made each a
separate line to break.
"""
import asyncio
import hashlib
import logging
import shutil

import pytest

from tests import test_forge_shutdown as _shutdown
from tests.test_forge_shutdown import _audio, _forged_then_undo, _job

pytestmark = pytest.mark.skipif(
    shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None,
    reason="ffmpeg/ffprobe not available",
)

# That file's fixture: Movie.mkv with one English AAC 5.1 track, scanned,
# a forge add waiting. Bound by assignment, not imported by name: pyflakes
# reads an imported fixture as unused and every test's parameter as a
# redefinition of it.
lib = _shutdown.lib

LOCKED = RuntimeError("database is locked")
NOT_CHANGED = ("Stopped by an unexpected error before the file was changed: "
               "RuntimeError: database is locked")


class _WS:
    def __init__(self):
        self.completed = []

    async def broadcast_json(self, data):
        if data.get("event") == "forge_job_completed":
            self.completed.append(data)


def _digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _run(lib):
    ws = _WS()
    assert asyncio.run(lib.worker._process_next_forge(ws)) is True
    return ws


def _raise_once(real):
    calls = []

    def wrapper(*args, **kwargs):
        calls.append(1)
        if len(calls) == 1:
            raise LOCKED
        return real(*args, **kwargs)
    return wrapper


def _always_raise(*args, **kwargs):
    raise LOCKED


def _is_candidate(lib):
    with lib.Session() as db:
        return [i["filename"] for i in lib.forge.get_candidates(db)["items"]] == ["Movie.mkv"]


def _net_logs(caplog):
    return [r for r in caplog.records
            if r.name == "app.core.worker" and r.levelno >= logging.ERROR]


# ── Before the file is touched ───────────────────────────────────────────────

def test_an_error_loading_the_job_fails_it_and_says_so(lib, monkeypatch, caplog):
    before = _digest(lib.video)
    monkeypatch.setattr(lib.forge, "load_forge_job_data", _always_raise)

    with caplog.at_level(logging.ERROR, logger="app.core.worker"):
        ws = _run(lib)

    job = _job(lib)
    assert (job.status, job.error) == ("failed", NOT_CHANGED)
    assert [m["status"] for m in ws.completed] == ["failed"]
    logged = _net_logs(caplog)
    assert logged and logged[0].exc_info and logged[0].exc_info[1] is LOCKED
    assert _digest(lib.video) == before


def test_an_undo_that_errors_before_starting_is_undo_failed(lib, monkeypatch):
    _forged_then_undo(lib)
    monkeypatch.setattr(lib.forge, "load_forge_job_data", _always_raise)

    _run(lib)

    job = _job(lib)
    assert (job.status, job.error) == ("undo_failed", NOT_CHANGED)
    assert _audio(lib.video) == ["aac", "ac3"]


def test_the_files_queue_job_can_be_claimed_afterwards(lib, monkeypatch):
    from app.database.models import MediaFile, QueueItem

    with lib.Session() as db:
        db.add(QueueItem(file_id=db.query(MediaFile).one().id, status="pending"))
        db.commit()
    monkeypatch.setattr(lib.forge, "load_forge_job_data", _always_raise)

    _run(lib)

    assert lib.worker._claim_next() is not None


# ── After the file is rewritten ──────────────────────────────────────────────

def test_a_failed_track_re_read_still_records_the_add(lib, monkeypatch):
    monkeypatch.setattr(lib.forge, "probe_tracks_after_forge", _always_raise)

    ws = _run(lib)

    assert _job(lib).status == "success"
    assert [m["status"] for m in ws.completed] == ["success"]
    assert _audio(lib.video) == ["aac", "ac3"]
    assert not _is_candidate(lib)


def test_the_outcome_is_recorded_on_a_second_attempt(lib, monkeypatch):
    monkeypatch.setattr(lib.forge, "finish_forge_job",
                        _raise_once(lib.forge.finish_forge_job))

    _run(lib)

    assert _job(lib).status == "success"
    assert not _is_candidate(lib)


def test_an_outcome_that_cannot_be_recorded_is_not_called_unchanged(lib, monkeypatch, caplog):
    # The file has its AC3 and the row cannot be written. The net must not
    # fail it as "not changed"; left at "processing", the next start marks
    # it interrupted, saying the file may have changed.
    monkeypatch.setattr(lib.forge, "finish_forge_job", _always_raise)

    with caplog.at_level(logging.ERROR, logger="app.core.worker"):
        _run(lib)

    job = _job(lib)
    assert (job.status, job.error) == ("processing", None)
    assert _audio(lib.video) == ["aac", "ac3"]
    assert any("may have rewritten its file" in r.getMessage() for r in _net_logs(caplog))
    assert lib.worker._forge_unsettled == set()


def test_an_error_after_the_outcome_is_recorded_leaves_it_as_recorded(lib, monkeypatch, caplog):
    # The status is already written, and an error in what follows it does
    # not turn a success into a failure, nor is it reported as an outcome
    # that could not be recorded.
    monkeypatch.setattr(lib.forge, "load_forge_final_state", _always_raise)

    with caplog.at_level(logging.ERROR, logger="app.core.worker"):
        _run(lib)

    assert (_job(lib).status, _job(lib).error) == ("success", None)
    assert _audio(lib.video) == ["aac", "ac3"]
    assert _net_logs(caplog)
    assert not any("may have rewritten" in r.getMessage() for r in _net_logs(caplog))
