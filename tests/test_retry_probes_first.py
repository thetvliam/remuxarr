"""
Retry and Retry All read the file before they start writing.

Both delete the failed row and have _process_file decide the file again
from a fresh probe. The probe ran with that delete flushed, so each retry
held SQLite's write lock for as long as ffprobe took, once per item for
Retry All. Every other write in the app waited behind it, and past the
engine's 30 s busy timeout failed, as behind a language answer
(test_language_answer_during_job, where that was measured). Measured here
before this file existed: with the probe held, a claim and another job's
finish both waited until the retry committed, for Retry and Retry All.

Both now probe first (scanner.probe_ahead) and hand the probe to
_process_file, as the answer does. What a retry does with a file it cannot
read is pinned in test_retry_unreadable_file. That the probe is used only
while the file is unchanged, and that a failed one is not repeated, is
pinned in test_language_answer_during_job.

Verified by mutation, 4 applied, 4 killed, each surviving first the 6 test
files that reach either retry, as they stood. Each retry has its own copy of
the change, mutated separately:

  • Retry's probe not handed over            → no write open, for Retry
  • Retry All's probe not handed over        → no write open, for Retry All
  • Retry's probe made after its delete      → both tests, for Retry
  • Retry All's probe made after its delete  → both tests, for Retry All
"""
import logging
import shutil
import sqlite3
import threading

import pytest

from tests import test_language_answer_during_job as answers
from tests.test_language_answer_during_job import _like_a_real_queue, needs_ffmpeg

pytestmark = needs_ffmpeg

# The answer tests' fixtures: a file-backed database the worker and the
# routes share, and Movie.mkv with its waiting job. Bound here, not imported
# by name, so pytest finds them and pyflakes does not report the names as
# unused imports that the tests' parameters then redefine.
database = answers.database
lib = answers.lib

RETRIES = pytest.mark.parametrize("which", ["Retry", "Retry All"])


def _fail(lib, job_id):
    """Turn a waiting job into a failed one, as a retry finds it."""
    from app.database.models import QueueItem

    with lib.Session() as db:
        job = db.get(QueueItem, job_id)
        job.status, job.error_message = "failed", "failed for the test"
        db.commit()


def _retry(lib, which, job_id):
    from app.api.routes.history import retry_history_item
    from app.api.routes.queue import retry_all_failed

    with lib.Session() as db:
        if which == "Retry":
            return retry_history_item(job_id, db)
        return retry_all_failed(db)


def _watch_probes(monkeypatch, on_probe):
    """
    Send every ffprobe through on_probe(path, probe), where probe() runs the
    real one. scanner.probe_file is the name probe_ahead and _process_file
    both call.
    """
    import app.core.scanner as scanner

    real = scanner.probe_file

    def probe_file(path, *args, **kwargs):
        return on_probe(path, lambda: real(path, *args, **kwargs))
    monkeypatch.setattr(scanner, "probe_file", probe_file)


@RETRIES
def test_other_writes_go_ahead_while_a_retry_probes_the_file(lib, monkeypatch, caplog, which):
    """
    The retry's probe of Movie.mkv is held while a claim and another job's
    finish are given five seconds: both land while it is still probing.
    """
    from app.database.models import MediaFile, QueueItem

    failed = _like_a_real_queue(lib)
    _fail(lib, failed)
    ids = {}
    with lib.Session() as db:
        for name, status, priority in (("Next.mkv", "pending", 1),
                                       ("Running.mkv", "processing", 5)):
            media = MediaFile(path=str(lib.library / name), filename=name,
                              directory=str(lib.library), size=1, mtime=1.0, status="queued")
            db.add(media)
            db.flush()
            job = QueueItem(file_id=media.id, status=status, priority=priority)
            db.add(job)
            db.flush()
            ids[name] = job.id
        db.commit()

    probing, go_on = threading.Event(), threading.Event()

    def held(path, probe):
        if path.endswith("Movie.mkv") and not go_on.is_set():
            probing.set()
            go_on.wait(60)
        return probe()
    _watch_probes(monkeypatch, held)
    caplog.set_level(logging.INFO)

    replies, claims = [], []
    retrying = threading.Thread(target=lambda: replies.append(_retry(lib, which, failed)))
    claiming = threading.Thread(target=lambda: claims.append(lib.worker._claim_next()))
    finishing = threading.Thread(target=lambda: lib.worker._finish_job(
        ids["Running.mkv"], False, None, None, "stopped for the test"))
    try:
        retrying.start()
        assert probing.wait(10), "the retry never probed Movie.mkv"
        claiming.start()
        finishing.start()
        claiming.join(5)
        finishing.join(5)
        waited = [what for what, thread in (("the claim", claiming), ("the finish", finishing))
                  if thread.is_alive()]
    finally:
        go_on.set()
        for thread in (retrying, claiming, finishing):
            if thread.ident:
                thread.join(60)

    assert waited == [], f"waited for the retry's probe: {waited}"
    assert claims == [ids["Next.mkv"]]
    assert len(replies) == 1, "the retry did not reply"
    with lib.Session() as db:
        finished = db.get(QueueItem, ids["Running.mkv"])
        assert (finished.status, finished.error_message) == ("failed", "stopped for the test")
        movie = db.query(MediaFile).filter(MediaFile.filename == "Movie.mkv").one()
        assert [j.status for j in db.query(QueueItem).filter(QueueItem.file_id == movie.id)] \
            == ["pending"], "the retry did not queue the file again"
    assert not [r for r in caplog.records if r.levelno >= logging.ERROR]


@RETRIES
def test_a_retry_probes_with_no_write_of_its_own_open(lib, monkeypatch, which):
    """
    At every probe a retry makes, another connection can take SQLite's write
    lock without waiting. Retry All is given two failed files, so the probe
    of the second, made after the first has been retried, is checked too.
    """
    from app.database.models import MediaFile, QueueItem

    with lib.Session() as db:
        failed = db.query(QueueItem).one().id
    _fail(lib, failed)
    files = ["Movie.mkv"]
    if which == "Retry All":
        second = lib.library / "Second.mkv"
        shutil.copy(lib.library / "Movie.mkv", second)
        with lib.Session() as db:
            media = MediaFile(path=str(second), filename=second.name,
                              directory=str(lib.library), size=1, mtime=1.0, status="error")
            db.add(media)
            db.flush()
            db.add(QueueItem(file_id=media.id, status="failed", error_message="failed for the test"))
            db.commit()
        files.append(second.name)

    database_path = lib.Session.kw["bind"].url.database
    seen = []

    def check_then_probe(path, probe):
        other = sqlite3.connect(database_path, timeout=0, isolation_level=None)
        try:
            other.execute("BEGIN IMMEDIATE")
            other.execute("ROLLBACK")
            seen.append((path.rsplit("/", 1)[-1], "free"))
        except sqlite3.OperationalError as exc:
            seen.append((path.rsplit("/", 1)[-1], f"held ({exc})"))
        finally:
            other.close()
        return probe()
    _watch_probes(monkeypatch, check_then_probe)

    reply = _retry(lib, which, failed)

    if which == "Retry":
        assert reply.get("status") == "pending", reply
    else:
        assert (reply["retried"], reply["errors"]) == (2, []), reply
    assert seen == [(name, "free") for name in files], f"the write lock at each probe: {seen}"
