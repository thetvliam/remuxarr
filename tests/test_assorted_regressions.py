"""
Regression tests for several unrelated correctness edge cases.

Container detection for Matroska-family files (mkv AND webm):
    ffprobe reports format_name="matroska,webm" for EVERY Matroska file,
    not just webm — it's a demuxer property. _normalise_container must
    therefore check "matroska" before "webm" and treat both as "mkv"
    (format_name genuinely can't distinguish them; only the file
    extension or EBML DocType can). A prior change reordered "webm"
    first, which mislabelled every MKV as a webm container and routed
    normal H.264/HEVC/AC3/DTS MKVs to `-f webm` — FFmpeg rejects that,
    breaking every remux. These tests use the REAL "matroska,webm" input
    that ffprobe actually emits (an earlier test used the unrealistic
    ["matroska"], which is why the regression slipped through).

    Related (worker.py): _is_subtitle_encoding_failure must NOT classify
    a container-capability error (e.g. "...WebVTT subtitles are supported
    for WebM") as a subtitle-encoding failure — that misread turned the
    webm mux rejection into a misleading "non-UTF-8 subtitle" manual
    review. Pinned below.

Email breaker counted inconsistently between enabled/disabled:
    once tripped, the email-enabled path froze consecutive_failures but
    the email-disabled path kept incrementing, so toggling email off
    after a trip silently resumed counting. Fix freezes the counter once
    tripped regardless of email_enabled.

Retrying a success/skipped item destroyed its history record:
    _retry_with_reprobe deleted the item for ALL statuses, so
    "RE-PROCESS" on a success erased its bytes-saved stats (and, for an
    already-compliant file, created nothing to replace it). Fix deletes
    only stale attempts (failed/cancelled/dry_run) and preserves
    completed evaluations (success/skipped).

Single-item retry lacked the bulk sibling's exception guard:
    _retry_with_reprobe called _process_file bare, so a probe/decision
    exception surfaced as an unhandled 500 AND left a stale item already
    deleted. Fix wraps it in try/except with rollback + a 400.

Abort path relied on the finally running under cancellation:
    documented + made explicit. The async contract (a single
    task.cancel() lets the finally complete, so the "cancelled" broadcast
    still reaches every client) is pinned below.

Run from the project root:
    pytest tests/ -v
"""
import asyncio
import os
import shutil
import subprocess
import sys
import time

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.core.probe import _normalise_container
from app.core.worker import _is_subtitle_encoding_failure


# ═══════════════════════════════════════════════════════════════════════════
# _normalise_container (pure function)
# ═══════════════════════════════════════════════════════════════════════════

def test_matroska_webm_format_name_is_mkv():
    """
    THE regression guard. ffprobe emits format_name="matroska,webm" for
    EVERY Matroska file — plain MKVs included — because it names the
    shared demuxer, not the file. extract_format_info splits it to
    ["matroska", "webm"]. That MUST normalise to "mkv": treating it as
    "webm" (as a prior reorder did) sends ordinary H.264/HEVC/AC3/DTS
    MKVs to `-f webm`, which FFmpeg rejects, breaking every remux.
    """
    # exactly what extract_format_info passes in (fmt["format_name"].split(","))
    assert _normalise_container(["matroska", "webm"]) == "mkv"
    # and the bare-string spelling, for safety
    assert _normalise_container(["matroska,webm"]) == "mkv"


def test_real_webm_file_also_normalises_to_mkv():
    """
    A genuine .webm reports the SAME "matroska,webm" format_name, so it
    also normalises to "mkv" — and that's correct: `-f matroska` muxes
    webm content fine and the file keeps playing. format_name cannot
    distinguish the two; only the extension/DocType can, which this
    function intentionally does not attempt.
    """
    assert _normalise_container(["matroska", "webm"]) == "mkv"


def test_asf_maps_to_wmv():
    """ASF/WMV files report format_name 'asf' → 'wmv'."""
    assert _normalise_container(["asf"]) == "wmv"


def test_other_containers_unchanged():
    """The remaining mappings are untouched."""
    assert _normalise_container(["mov", "mp4", "m4a"]) == "mp4"
    assert _normalise_container(["mpegts"]) == "ts"
    assert _normalise_container(["avi"]) == "avi"
    # Unknown format falls through to the raw first name.
    assert _normalise_container(["flv"]) == "flv"
    assert _normalise_container([]) == "unknown"


def test_webm_container_error_is_not_an_encoding_failure():
    """
    The misclassification that turned the webm mux rejection into a bogus
    "non-UTF-8 subtitle" manual review. FFmpeg's container-capability
    error merely mentions the word "subtitles"; it is NOT an encoding
    failure and must not be routed into the subtitle-encoding review.
    """
    webm_err = (
        "[webm @ 0x5565a5017000] Only VP8 or VP9 or AV1 video and Vorbis "
        "or Opus audio and WebVTT subtitles are supported for WebM.\n"
        "[out#0/webm @ 0x5565a5126680] Could not write header "
        "(incorrect codec parameters ?): Invalid argument"
    )
    assert _is_subtitle_encoding_failure(webm_err) is False, (
        "a container-capability error was misclassified as a subtitle "
        "encoding failure — this is what produced the misleading "
        "'non-UTF-8 encoded characters' manual-review reason."
    )
    # The genuine encoding failure must STILL be detected.
    real = ("[srt @ 0x0] Invalid UTF-8 in decoded subtitles text; maybe "
            "missing -sub_charenc option")
    assert _is_subtitle_encoding_failure(real) is True


# ═══════════════════════════════════════════════════════════════════════════
# Email breaker counting consistency
# ═══════════════════════════════════════════════════════════════════════════

def _email_db():
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    from app.database.models import Base

    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)()


def _seed_setting(db, key, value):
    import json

    from app.database.models import AppSetting
    db.merge(AppSetting(key=key, value=json.dumps(value)))
    db.commit()


def _add_failed_job(db, job_id, file_id=1):
    from app.database.models import MediaFile, QueueItem
    if db.get(MediaFile, file_id) is None:
        db.add(MediaFile(id=file_id, path=f"/m/{file_id}.mkv",
                         filename=f"{file_id}.mkv", directory="/m",
                         size=1, mtime=1.0))
    db.add(QueueItem(id=job_id, file_id=file_id, status="failed",
                     error_message="boom"))
    db.commit()


def test_breaker_frozen_when_disabled_after_trip(monkeypatch):
    """
    Trip the breaker with email ENABLED
    (threshold 3), then DISABLE email and fail twice more. The counter
    must stay frozen at 3 — the old email-disabled branch kept
    incrementing (→ 5), disagreeing with the enabled path's freeze.
    """
    import app.core.worker as worker
    db = _email_db()
    monkeypatch.setattr(worker, "SessionLocal", lambda: db)

    _seed_setting(db, "email_enabled", True)
    _seed_setting(db, "email_failure_threshold", 3)

    from app.database.models import NotificationState

    # 3 failures with email on → trips on the 3rd.
    for i in range(1, 4):
        _add_failed_job(db, job_id=i)
        worker._load_email_notify_data(i)
    st = db.get(NotificationState, 1)
    assert st.breaker_tripped is True
    assert st.consecutive_failures == 3

    # Now disable email and fail twice more.
    _seed_setting(db, "email_enabled", False)
    for i in range(4, 6):
        _add_failed_job(db, job_id=i)
        result = worker._load_email_notify_data(i)
        assert result is None  # tripped → nothing to send

    st = db.get(NotificationState, 1)
    assert st.consecutive_failures == 3, (
        f"counter resumed to {st.consecutive_failures} while disabled — the "
        "email-disabled branch is still incrementing past a trip."
    )
    assert st.breaker_tripped is True


def test_success_resets_breaker(monkeypatch):
    """A success must un-trip and zero the counter (unchanged behavior,
    pinned so the restructure didn't disturb it)."""
    import app.core.worker as worker
    db = _email_db()
    monkeypatch.setattr(worker, "SessionLocal", lambda: db)
    _seed_setting(db, "email_enabled", True)
    _seed_setting(db, "email_failure_threshold", 2)

    from app.database.models import MediaFile, NotificationState, QueueItem

    for i in range(1, 3):
        _add_failed_job(db, job_id=i)
        worker._load_email_notify_data(i)
    assert db.get(NotificationState, 1).breaker_tripped is True

    db.add(MediaFile(id=9, path="/m/9.mkv", filename="9.mkv", directory="/m",
                     size=1, mtime=1.0))
    db.add(QueueItem(id=99, file_id=9, status="success"))
    db.commit()
    assert worker._load_email_notify_data(99) is None
    st = db.get(NotificationState, 1)
    assert st.consecutive_failures == 0 and st.breaker_tripped is False


def test_one_tripped_email_then_silence(monkeypatch):
    """Crossing the threshold yields exactly one 'tripped' email; the
    next failure (still enabled) yields nothing."""
    import app.core.worker as worker
    db = _email_db()
    monkeypatch.setattr(worker, "SessionLocal", lambda: db)
    _seed_setting(db, "email_enabled", True)
    _seed_setting(db, "email_failure_threshold", 2)

    _add_failed_job(db, job_id=1)
    assert worker._load_email_notify_data(1)["kind"] == "failure"
    _add_failed_job(db, job_id=2)
    assert worker._load_email_notify_data(2)["kind"] == "tripped"
    _add_failed_job(db, job_id=3)
    assert worker._load_email_notify_data(3) is None


# ═══════════════════════════════════════════════════════════════════════════
# _retry_with_reprobe (DB-backed; needs a probeable file)
# ═══════════════════════════════════════════════════════════════════════════

def _retry_db():
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    from app.database.models import Base

    engine = create_engine("sqlite://", connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)()


def _make_probeable_mp4(path):
    """A minimal valid MP4 so _process_file can probe it. Any decision
    outcome is fine here — the assertion is only about row survival."""
    subprocess.run(
        ["ffmpeg", "-v", "error", "-y",
         "-f", "lavfi", "-i", "testsrc=duration=1:size=128x72:rate=10",
         "-f", "lavfi", "-i", "sine=duration=1",
         "-c:v", "libx264", "-c:a", "aac", "-metadata:s:a:0", "language=eng",
         "-movflags", "+faststart", "-t", "1", str(path)],
        check=True, capture_output=True,
    )


ffmpeg_missing = shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None


@pytest.mark.skipif(ffmpeg_missing, reason="ffmpeg/ffprobe not available")
def test_retry_success_preserves_history_row(tmp_path):
    """
    RE-PROCESS on a success must NOT delete the success row or
    its bytes-saved stats. _process_file's queue path clears only
    skipped/manual_review, never success, so the row survives whatever
    the re-probe decides.
    """
    from app.api.routes.queue import _retry_with_reprobe
    from app.database.models import MediaFile, QueueItem

    f = tmp_path / "Movie (2020).mp4"
    _make_probeable_mp4(f)

    db = _retry_db()
    db.add(MediaFile(id=1, path=str(f), filename=f.name, directory=str(tmp_path),
                     size=os.path.getsize(f), mtime=os.path.getmtime(f),
                     status="success", container="mp4"))
    db.add(QueueItem(id=10, file_id=1, status="success",
                     original_size=1000, output_size=600))
    db.commit()

    _retry_with_reprobe(db, db.get(QueueItem, 10))

    kept = db.get(QueueItem, 10)
    assert kept is not None, "the success history row was deleted by RE-PROCESS"
    assert kept.status == "success"
    assert kept.original_size == 1000 and kept.output_size == 600, (
        "bytes-saved stats were lost"
    )


@pytest.mark.skipif(ffmpeg_missing, reason="ffmpeg/ffprobe not available")
def test_retry_failed_still_deletes_the_stale_item(tmp_path):
    """
    Contrast: a failed item is a stale ATTEMPT and must still be deleted
    (replaced by the re-probe), confirming the guard preserves only
    completed evaluations, not everything.
    """
    from app.api.routes.queue import _retry_with_reprobe
    from app.database.models import MediaFile, QueueItem

    f = tmp_path / "Movie (2021).mp4"
    _make_probeable_mp4(f)

    db = _retry_db()
    db.add(MediaFile(id=1, path=str(f), filename=f.name, directory=str(tmp_path),
                     size=os.path.getsize(f), mtime=os.path.getmtime(f),
                     status="failed", container="mp4"))
    db.add(QueueItem(id=20, file_id=1, status="failed", error_message="x"))
    db.commit()

    _retry_with_reprobe(db, db.get(QueueItem, 20))
    assert db.get(QueueItem, 20) is None, "stale failed item should be deleted"


def test_retry_reprobe_failure_raises_400_and_restores_item(tmp_path, monkeypatch):
    """
    When _process_file raises (the cited case is the
    ValueError decision.py throws for unknown container info; a probe
    error is caught inside _process_file, so we force the raise directly
    to exercise the guard itself), the single-item retry must surface a
    400 — not an unhandled 500 — AND roll back so the failed item it
    deleted is restored, never silently destroyed.
    """
    from fastapi import HTTPException

    import app.api.routes.queue as queue
    from app.database.models import MediaFile, QueueItem

    f = tmp_path / "Movie (2022).mp4"
    f.write_text("placeholder — _process_file is monkeypatched to raise")

    db = _retry_db()
    db.add(MediaFile(id=1, path=str(f), filename=f.name, directory=str(tmp_path),
                     size=os.path.getsize(f), mtime=os.path.getmtime(f),
                     status="failed", container="mp4"))
    db.add(QueueItem(id=30, file_id=1, status="failed", error_message="orig"))
    db.commit()

    def _boom(*a, **k):
        raise ValueError("Unsupported output container 'flv'")
    monkeypatch.setattr(queue, "_process_file", _boom)

    with pytest.raises(HTTPException) as ei:
        queue._retry_with_reprobe(db, db.get(QueueItem, 30))
    assert ei.value.status_code == 400
    assert "Unsupported output container" in ei.value.detail

    restored = db.get(QueueItem, 30)
    assert restored is not None, (
        "a failed retry destroyed the item it was meant to re-queue — "
        "rollback did not restore it."
    )
    assert restored.status == "failed"


# ═══════════════════════════════════════════════════════════════════════════
# Abort: the finally broadcast runs under cancellation
# ═══════════════════════════════════════════════════════════════════════════

def test_finally_broadcast_survives_single_cancel():
    """
    Pins the async contract the abort fix relies on: a single
    task.cancel() (what abort_job issues) raises CancelledError once, at
    the inner await, so the finally block — where the "cancelled"
    job_completed broadcast lives — still runs to completion, and the
    task still ends cancelled. Mirrors _run_and_broadcast's
    try/except CancelledError (re-raise) / except Exception / finally
    structure exactly. If a future runtime change broke this, aborted
    jobs would stop notifying other connected clients.
    """
    events = []

    def blocking_load():          # stands in for _load_post_job_data (executor call)
        time.sleep(0.01)
        return {"status": "cancelled"}

    async def inner():
        await asyncio.sleep(10)   # stands in for _run_job awaiting the subprocess

    async def run_and_broadcast(loop):
        try:
            await inner()
        except asyncio.CancelledError:
            events.append("logged-abort")   # explicit branch for the abort path
            raise
        except Exception:
            events.append("caught-exception")
        finally:
            post = await loop.run_in_executor(None, blocking_load)
            events.append(f"broadcast:{post['status']}")

    async def driver():
        loop = asyncio.get_running_loop()
        t = asyncio.create_task(run_and_broadcast(loop))
        await asyncio.sleep(0.03)
        t.cancel()
        with pytest.raises(asyncio.CancelledError):
            await t

    asyncio.run(driver())

    assert events == ["logged-abort", "broadcast:cancelled"], (
        f"abort finalisation did not complete as expected: {events}"
    )


def test_normal_completion_unaffected_by_abort_branch():
    """The added except-CancelledError branch must not disturb the
    normal (no-cancel) completion path."""
    events = []

    async def run_and_broadcast(loop):
        try:
            pass
        except asyncio.CancelledError:
            events.append("logged-abort"); raise
        except Exception:
            events.append("caught-exception")
        finally:
            post = await loop.run_in_executor(None, lambda: {"status": "success"})
            events.append(f"broadcast:{post['status']}")

    async def driver():
        await run_and_broadcast(asyncio.get_running_loop())

    asyncio.run(driver())
    assert events == ["broadcast:success"]
