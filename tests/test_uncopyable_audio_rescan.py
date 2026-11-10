"""
A scan does not queue a file again whose last job failed because its audio
cannot be copied, while neither the file nor its plan has changed
(scanner._held_back).

A full scan or a re-check decides every file again, so it queued a failed
one again too, which is how a scan retries a failure. Since the audio retry
was removed (worker._explain_failure), this failure is no retry: the audio
is the same, nothing re-encodes it, and the job fails again, at the cost of
an FFmpeg run and another failed entry in History, on every such scan. The
testlib explorer's files 10 and 11 were queued again by every full scan.

Anything that changes is worth another try: a new file, or a plan that now
differs. Nothing but a scan holds a file back: Retry, Ask Again, a webhook
and the other callers of _process_file queue it as before.

These drive the real scan_library and _process_file against a real database
and real files on disk; ffprobe is replaced by a table, as in
test_recheck_scan.py. A job's failure is written as the worker records it.
"""
import json

import pytest

import app.core.scanner as scanner
import app.core.worker as worker
from app.core.scanner import ScanStats, scan_library
from app.database.models import AppSetting, QueueItem

CORRUPT_AUDIO = (
    "[aac @ 0x55d0c0a1b2c0] Invalid data found when processing input\n"
    "[aost#0:1/copy @ 0x55d0c0a1b3c0] Error submitting a packet to the muxer: "
    "Invalid data found when processing input"
)
DISK_FULL = "av_interleaved_write_frame(): No space left on device"


def _probe(*languages):
    streams = [{"index": 0, "codec_type": "video", "codec_name": "h264"}]
    streams += [{"index": i + 1, "codec_type": "audio", "codec_name": "aac",
                 "channels": 2, "tags": {"language": lang},
                 "disposition": {"default": 1 if i == 0 else 0}}
                for i, lang in enumerate(languages)]
    return {"format": {"format_name": "matroska,webm", "duration": "1.0"},
            "streams": streams}


@pytest.fixture
def lib(tmp_path, monkeypatch):
    from sqlalchemy.orm import sessionmaker

    from app.database.models import Base
    from tests.conftest import memory_engine

    engine = memory_engine()
    Base.metadata.create_all(engine)
    db = sessionmaker(bind=engine)()

    folder = tmp_path / "films"
    folder.mkdir()
    probes: dict[str, dict] = {}
    monkeypatch.setattr(scanner, "probe_file", lambda path, *_a, **_k: probes[path])

    def setting(key, value):
        db.merge(AppSetting(key=key, value=json.dumps(value)))
        db.commit()

    def media(probe, size=64):
        path = folder / "Film.mkv"
        path.write_bytes(b"\0" * size)
        probes[str(path)] = probe
        return str(path)

    def scan(**kwargs):
        return scan_library(db, [str(folder)], **kwargs)

    def fail_newest(error):
        """Record the newest job as failed with `error`, as _finish_job does."""
        job = db.query(QueueItem).order_by(QueueItem.id.desc()).first()
        job.status = "failed"
        job.error_message = worker._explain_failure(error)
        db.commit()
        return job

    def items():
        return [i.status for i in db.query(QueueItem).order_by(QueueItem.id)]

    setting("dry_run_mode", False)
    setting("prefer_mp4_container", False)
    setting("keep_audio_languages", ["eng"])

    # The film: English and French audio, the French to be dropped. Its job
    # fails because the English audio cannot be copied.
    path = media(_probe("eng", "fre"))
    scan()
    fail_newest(CORRUPT_AUDIO)
    assert items() == ["failed"], "fixture changed"

    yield type("Lib", (), dict(db=db, path=path, setting=staticmethod(setting),
                               media=staticmethod(media), scan=staticmethod(scan),
                               fail_newest=staticmethod(fail_newest),
                               items=staticmethod(items)))
    db.close()


# ── Held ─────────────────────────────────────────────────────────────────────

def test_a_full_scan_does_not_queue_it_again(lib):
    stats = lib.scan(force_probe=True)

    assert lib.items() == ["failed"]
    assert stats.held == 1 and stats.queued == 0


def test_a_recheck_does_not_queue_it_again(lib):
    stats = lib.scan(recheck=True)

    assert lib.items() == ["failed"]
    assert stats.held == 1


def test_a_routine_scan_leaves_it_as_before(lib):
    """The control: an unchanged file is not decided again at all."""
    stats = lib.scan()

    assert lib.items() == ["failed"]
    assert stats.held == 0 and stats.unchanged == 1


# ── Queued: something changed ────────────────────────────────────────────────

def test_a_changed_file_is_queued(lib):
    lib.media(_probe("eng", "fre"), size=128)

    lib.scan(force_probe=True)

    assert lib.items() == ["failed", "pending"]


def test_a_changed_plan_is_queued(lib):
    """
    Keeping French and dropping English instead: the track that would not
    copy may be the one now dropped, so the job may succeed.
    """
    lib.setting("keep_audio_languages", ["fre"])

    lib.scan(force_probe=True)

    assert lib.items() == ["failed", "pending"]


def test_another_failure_is_queued_by_a_full_scan_as_before(lib):
    lib.db.query(QueueItem).one().error_message = worker._explain_failure(DISK_FULL)
    lib.db.commit()

    lib.scan(force_probe=True)

    assert lib.items() == ["failed", "pending"]


def test_only_the_newest_job_counts(lib):
    """A later failure of another kind is what the file last did."""
    lib.setting("keep_audio_languages", ["fre"])
    lib.scan(force_probe=True)
    lib.fail_newest(DISK_FULL)
    lib.setting("keep_audio_languages", ["eng"])

    lib.scan(force_probe=True)

    assert lib.items() == ["failed", "failed", "pending"]


# ── Queued: not a scan ───────────────────────────────────────────────────────

def test_retry_queues_it(lib):
    from app.api.routes.queue import _retry_with_reprobe

    _retry_with_reprobe(lib.db, lib.db.query(QueueItem).one())

    assert lib.items() == ["pending"]


def test_ask_again_queues_it(lib):
    from app.api.routes.queue import ask_again

    ask_again(lib.db.query(QueueItem).one().id, lib.db)

    assert lib.items() == ["failed", "pending"]


def test_a_webhook_queues_it(lib):
    scanner.queue_single_file(lib.db, lib.path)

    assert lib.items() == ["failed", "pending"]


def test_any_other_caller_queues_it(lib):
    """
    _process_file held back nothing unless a scan says it is one: an
    applied language answer and the re-decision after a cancelled job call
    it this way.
    """
    from app.database.session import get_app_settings

    scanner._process_file(lib.db, lib.path, get_app_settings(lib.db),
                          force_probe=True, dry_run=False, stats=ScanStats())

    assert lib.items() == ["failed", "pending"]
