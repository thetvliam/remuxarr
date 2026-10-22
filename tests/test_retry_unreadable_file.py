"""
Retrying a failed item whose file cannot be read.

Both retries delete the failed row and then re-run the scanner's per-file
evaluation. A file that cannot be stat'ed or probed is logged and returned
from there, before anything is written, with no exception. Reproduced with
a real ffprobe before this file existed:

  • Retry All, with that file first: the next item's commit made the delete
    permanent, so the failure left Failed with nothing re-queued, and the
    reply listed no errors. With it last, the delete was never committed and
    the row survived, still unreported.
  • Retry from the item's window: the delete was committed and the reply
    said "File re-evaluated — no further action needed".

Both now roll the delete back, keep the row with its original error, and
say the file could not be read: Retry All among its errors, which the page
shows as "N errored", and Retry as a 400, which the page shows as "Could
not re-queue".

Verified by mutation, 5 applied, 4 killed, 1 equivalent. All five survived
the suite as it stood (2001 tests):

  • Retry All without the check              → both orders
  • Retry All reporting it, not rolling back → unreadable first (last, the
                                               delete is never committed)
  • Retry All rolling back, not reporting it → both orders
  • Retry without the check                  → the Retry test

Equivalent: Retry checking but not rolling back. The request's session is
closed without a commit after the reply, which undoes the delete anyway.
The rollback is kept so the code says so, as the exception path beside it
does. That is why rows are read back through a session of their own here:
read through the request's session, the uncommitted delete shows, and the
mutant looked killed.
"""
import json
import shutil
import subprocess

import pytest
from fastapi import HTTPException


pytestmark = pytest.mark.skipif(
    shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None,
    reason="ffmpeg/ffprobe not available",
)


@pytest.fixture
def lib(tmp_path):
    """A database holding a failed item for Good.mkv, a real file, and one
    for Corrupt.mkv, which ffprobe cannot read."""
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    from app.database.models import AppSetting, Base

    good = tmp_path / "Good.mkv"
    subprocess.run([
        "ffmpeg", "-v", "error", "-y",
        "-f", "lavfi", "-i", "testsrc=size=64x36:rate=5:duration=1",
        "-f", "lavfi", "-i", "sine=duration=1",
        "-metadata:s:a:0", "language=eng",
        "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-f", "matroska", str(good)], check=True)
    corrupt = tmp_path / "Corrupt.mkv"
    corrupt.write_bytes(b"this is not a matroska file" * 100)

    engine = create_engine(f"sqlite:///{tmp_path / 'remuxarr.db'}")
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine)
    db = factory()
    db.add(AppSetting(key="dry_run_mode", value=json.dumps(False)))
    db.commit()
    yield db, {"good": good, "corrupt": corrupt, "Session": factory}
    db.close()
    engine.dispose()


def _failed(db, *paths):
    """A failed item for each path, in this order. Returns their file ids."""
    from app.database.models import MediaFile, QueueItem

    ids = []
    for path in paths:
        media = MediaFile(path=str(path), filename=path.name, directory=str(path.parent),
                          size=1, mtime=1.0, container="mkv", status="error")
        db.add(media)
        db.flush()
        db.add(QueueItem(file_id=media.id, status="failed", error_message="exit 1: earlier failure"))
        db.flush()
        ids.append(media.id)
    db.commit()
    return ids


def _rows(files, file_id):
    """What is committed, read from a session of its own: the request's
    session is closed after the reply, and anything it left uncommitted
    goes with it."""
    from app.database.models import QueueItem

    with files["Session"]() as fresh:
        return [(q.status, q.error_message)
                for q in fresh.query(QueueItem).filter(QueueItem.file_id == file_id)]


@pytest.mark.parametrize("corrupt_first", [True, False], ids=["unreadable first", "unreadable last"])
def test_retry_all_keeps_and_reports_a_file_it_cannot_read(lib, corrupt_first):
    from app.api.routes.queue import retry_all_failed

    db, files = lib
    order = ["corrupt", "good"] if corrupt_first else ["good", "corrupt"]
    ids = dict(zip(order, _failed(db, *(files[name] for name in order))))

    result = retry_all_failed(db)

    assert result["retried"] == 1
    assert result["errors"] == [{
        "path": str(files["corrupt"]),
        "error": "Could not re-read the file — see the log for details",
    }]
    assert _rows(files, ids["corrupt"]) == [("failed", "exit 1: earlier failure")], (
        "the failed item is gone although nothing was re-queued for it")
    assert _rows(files, ids["good"]) == [("pending", None)]


def test_retry_keeps_a_file_it_cannot_read_and_says_so(lib):
    from app.api.routes.history import retry_history_item
    from app.database.models import QueueItem

    db, files = lib
    (file_id,) = _failed(db, files["corrupt"])
    item_id = db.query(QueueItem).one().id

    with pytest.raises(HTTPException) as refused:
        retry_history_item(item_id, db)

    assert refused.value.status_code == 400
    assert "could not re-read the file" in refused.value.detail
    assert _rows(files, file_id) == [("failed", "exit 1: earlier failure")]
