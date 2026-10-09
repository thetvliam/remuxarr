"""
A clean stop with a job running puts the job back in the queue.

docker stop, an Unraid update, a restart from the dashboard: the container
gets SIGTERM, uvicorn runs the lifespan's shutdown, and main.lifespan calls
stop_worker(). That used to stop only the loop that claims jobs. A job
mid-FFmpeg was then cancelled by the event loop's own teardown, found
still at "processing" by _run_and_broadcast's emergency net, and recorded
as "Job did not complete cleanly (finalisation failed)" — counted towards
the failure email, and left in the Failed tab until someone pressed Retry.
Every update did that to whatever was running.

stop_worker now cancels each running job and waits for it. A job cut off
before it replaced anything goes back to "pending" and runs again on the
next start. One whose output was already swapped in finishes its
bookkeeping before the cancellation lands, so it is recorded as the
success it was — re-queuing that one would re-run a job against a file it
had already converted.

These run the real start_worker and stop_worker against a file-backed
database, in the order main.lifespan uses them. FFmpeg is replaced by a
`sleep` driven through the real run_staged_subprocess, so the subprocess
is killed by the production path; the probe is stubbed, since what the
file holds is not the question.

The worker keeps module-level state (its running flag, the active-job set,
the task registry), so every test resets it on the way in as well as out.

Confirmed unprotected before this file was written: six mutations, each run
against the whole 1754-test suite, all six survived. stop_worker not
cancelling running jobs; not waiting for them; _run_and_broadcast not
re-queuing a cancelled job; re-queuing whatever its status, which undoes
an abort; the post-swap bookkeeping left interruptible; and the re-queue
leaving the claimed job's start time and progress behind. All six are
killed here.
"""
import asyncio
import json
import os
import subprocess
import threading
import time
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

import app.core.worker as worker
import app.database.session as session_module
from app.api.ws_manager import ws_manager
from app.config import settings as app_settings
from app.core import scanner
from app.core.subprocess_runner import StagedOutput, run_staged_subprocess
from app.database.models import AppSetting, Base, NotificationState, QueueItem


SLEEP = "29.123"     # an argument no other process on the box will have


def _probe():
    """An MKV with a French track to drop: an in-place job under defaults."""
    return {"format": {"format_name": "matroska,webm", "duration": "1.0"},
            "streams": [
                {"index": 0, "codec_type": "video", "codec_name": "h264"},
                {"index": 1, "codec_type": "audio", "codec_name": "aac",
                 "channels": 2, "tags": {"language": "eng"},
                 "disposition": {"default": 1}},
                {"index": 2, "codec_type": "audio", "codec_name": "aac",
                 "channels": 2, "tags": {"language": "fre"}},
            ]}


@pytest.fixture
def env(tmp_path, monkeypatch):
    engine = create_engine(f"sqlite:///{tmp_path / 'remuxarr.db'}",
                           connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine)
    monkeypatch.setattr(worker, "SessionLocal", factory)
    monkeypatch.setattr(session_module, "SessionLocal", factory)
    monkeypatch.setattr(app_settings, "TEMP_DIR", str(tmp_path / "tmp"))
    monkeypatch.setattr(scanner, "probe_file", lambda *_a, **_kw: _probe())
    monkeypatch.setattr(worker, "probe_file", lambda *_a, **_kw: _probe())

    # Module state, fresh on the way in; monkeypatch puts the originals back.
    monkeypatch.setattr(worker, "_running", False)
    monkeypatch.setattr(worker, "_paused", False)
    monkeypatch.setattr(worker, "_worker_task", None)
    monkeypatch.setattr(worker, "_active_jobs", set())
    monkeypatch.setattr(worker, "_active_task_registry", {})

    sent = []

    async def record(payload):
        sent.append(payload)

    monkeypatch.setattr(ws_manager, "broadcast_json", record)

    with factory() as db:
        for key, value in {"dry_run_mode": False, "auto_start_jobs": True}.items():
            db.merge(AppSetting(key=key, value=json.dumps(value)))
        db.commit()

    media = tmp_path / "library" / "Show - S01E01.mkv"
    media.parent.mkdir()
    media.write_bytes(b"\0" * 64)

    def queue():
        with factory() as db:
            item = scanner.queue_single_file(db, str(media))
            assert item is not None and item.status == "pending"
            return item.id

    def row(job_id):
        with factory() as db:
            job = db.get(QueueItem, job_id)
            return SimpleNamespace(status=job.status, started_at=job.started_at,
                                   progress=job.progress,
                                   current_action=job.current_action,
                                   error=job.error_message)

    def failures_counted():
        with factory() as db:
            state = db.get(NotificationState, 1)
            return state.consecutive_failures if state else 0

    def sleeping():
        # Matched on the exact argument list rather than pgrep -f, which
        # also matches any process whose command line merely contains the
        # text — the shell that launched the test run, for one.
        out = subprocess.run(["ps", "-eo", "args="], capture_output=True,
                             text=True).stdout
        return any(line.split() == ["sleep", SLEEP] for line in out.splitlines())

    def slow_ffmpeg():
        async def run(**kwargs):
            temp = os.path.join(app_settings.TEMP_DIR, "job.remuxarr_tmp")
            os.makedirs(app_settings.TEMP_DIR, exist_ok=True)
            return await run_staged_subprocess(
                ["sleep", SLEEP],
                [StagedOutput(temp_path=temp, final_path=kwargs["output_path"])],
                before_swap=kwargs.get("before_swap"))
        monkeypatch.setattr(worker, "execute_ffmpeg", run)

    def fast_ffmpeg():
        async def run(**kwargs):
            return SimpleNamespace(success=True, output_path=kwargs["output_path"],
                                   output_size=64, error=None)
        monkeypatch.setattr(worker, "execute_ffmpeg", run)

    async def until(predicate, timeout=15.0):
        deadline = time.monotonic() + timeout
        while not predicate():
            assert time.monotonic() < deadline, "timed out waiting"
            await asyncio.sleep(0.05)

    yield SimpleNamespace(queue=queue, row=row, failures_counted=failures_counted,
                          sleeping=sleeping, slow_ffmpeg=slow_ffmpeg,
                          fast_ffmpeg=fast_ffmpeg, until=until, sent=sent,
                          tmp=tmp_path)
    engine.dispose()


def _completed(sent, job_id):
    return [p["status"] for p in sent
            if p.get("event") == "job_completed" and p.get("job_id") == job_id]


# ── Cut off before it replaced anything ──────────────────────────────────────

def test_a_job_cut_off_by_a_clean_stop_goes_back_in_the_queue(env):
    env.slow_ffmpeg()
    job_id = env.queue()

    async def run_then_stop():
        await worker.start_worker()
        await env.until(lambda: env.row(job_id).status == "processing" and env.sleeping())
        await worker.stop_worker()
        # Read before returning to the event loop: stop_worker has to have
        # waited, not left the job for the loop's teardown to cancel.
        return (env.row(job_id),
                all(t.done() for t in worker._active_task_registry.values()))

    row, all_done = asyncio.run(run_then_stop())

    assert row.status == "pending", (row.status, row.error)
    assert (row.started_at, row.progress, row.current_action, row.error) == \
        (None, 0.0, None, None)
    assert all_done
    assert not env.sleeping(), "FFmpeg was left running"
    assert not os.listdir(app_settings.TEMP_DIR), "temp output left behind"
    assert env.failures_counted() == 0


def test_the_next_start_runs_the_job_it_put_back(env):
    env.slow_ffmpeg()
    job_id = env.queue()

    async def run_then_stop():
        await worker.start_worker()
        await env.until(lambda: env.row(job_id).status == "processing" and env.sleeping())
        await worker.stop_worker()

    asyncio.run(run_then_stop())
    assert env.row(job_id).status == "pending"

    env.fast_ffmpeg()

    async def restart():
        await worker.start_worker()
        await env.until(lambda: env.row(job_id).status == "success")
        await worker.stop_worker()

    asyncio.run(restart())
    assert env.row(job_id).status == "success"


def test_an_aborted_job_stays_cancelled_through_a_stop(env):
    """Abort marks the row first; the re-queue must leave that alone."""
    env.slow_ffmpeg()
    job_id = env.queue()

    async def abort_then_stop():
        await worker.start_worker()
        await env.until(lambda: env.row(job_id).status == "processing" and env.sleeping())
        assert worker.abort_job(job_id) is True
        await worker.stop_worker()

    asyncio.run(abort_then_stop())

    row = env.row(job_id)
    assert (row.status, row.error) == ("cancelled", "Aborted by user")


# ── Cut off after the swap ───────────────────────────────────────────────────

def test_a_stop_during_the_final_bookkeeping_still_records_the_success(env, monkeypatch):
    """
    The output is already in place when _finish_job runs. A cancellation
    landing there must not leave the job at "processing" to be re-queued
    and re-run against a file it already converted.
    """
    env.fast_ffmpeg()
    job_id = env.queue()
    entered = threading.Event()
    real_finish = worker._finish_job

    def slow_finish(*args):
        entered.set()
        time.sleep(0.5)
        real_finish(*args)

    monkeypatch.setattr(worker, "_finish_job", slow_finish)

    async def stop_during_finish():
        await worker.start_worker()
        await env.until(entered.is_set)
        await worker.stop_worker()
        return env.row(job_id)

    row = asyncio.run(stop_during_finish())

    assert row.status == "success", (row.status, row.error)
    assert _completed(env.sent, job_id) == ["success"]


# ── Nothing running ──────────────────────────────────────────────────────────

def test_a_stop_with_nothing_running_returns_at_once(env):
    async def start_then_stop():
        await worker.start_worker()
        await asyncio.sleep(0.2)
        started = time.monotonic()
        await worker.stop_worker()
        return time.monotonic() - started

    assert asyncio.run(start_then_stop()) < 2.0
