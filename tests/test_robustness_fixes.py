"""
Robustness fixes: symlink cycles, retry-all accounting, settings isolation.

Three unrelated defects that share a property — none of them raises. Each
produces a wrong number, a wrong duration, or a wrong shared object, and the
app carries on as if nothing happened.
"""
import os

import pytest

from app.core.scanner import _walk_media_dirs


# ── Symlink cycles ───────────────────────────────────────────────────────────

def test_symlink_cycle_terminates(tmp_path):
    """
    A symlink pointing at an ancestor makes os.walk(followlinks=True) descend
    forever. Nothing else catches it: the per-job timeout does not apply to a
    scan, and the cancel flag is only read once per yielded directory, so the
    scan appears to run indefinitely while re-processing the same files.

    followlinks stays ON — Unraid setups routinely point library paths at
    symlinked shares — so the guard has to be a cycle check, not a switch.
    """
    (tmp_path / "Show" / "S01").mkdir(parents=True)
    (tmp_path / "Show" / "S01" / "ep.mkv").touch()
    os.symlink(tmp_path, tmp_path / "Show" / "S01" / "loop")

    # Bounded so a regression fails the test instead of hanging the suite.
    yielded = 0
    for _root, _dirs, _files in _walk_media_dirs(str(tmp_path)):
        yielded += 1
        assert yielded < 200, "walk did not terminate — the cycle guard is gone"

    assert yielded < 20


def test_two_symlinks_to_one_directory_do_not_double_count(tmp_path):
    """
    The quieter half, present with no cycle at all: two links resolving to the
    same directory made every file beneath it counted twice in the progress
    total and processed twice.
    """
    real = tmp_path / "Show"
    (real / "S01").mkdir(parents=True)
    (real / "S01" / "ep.mkv").touch()
    os.symlink(real, tmp_path / "alias")

    files = [
        os.path.join(root, f)
        for root, _dirs, fs in _walk_media_dirs(str(tmp_path))
        for f in fs
    ]
    assert len(files) == 1, f"file seen {len(files)} times via aliased paths: {files}"


def test_hidden_directories_are_pruned(tmp_path):
    """Pruning moved into the helper; both walk sites depend on it."""
    (tmp_path / ".hidden").mkdir()
    (tmp_path / ".hidden" / "x.mkv").touch()
    (tmp_path / "Visible").mkdir()
    (tmp_path / "Visible" / "y.mkv").touch()

    names = [
        f
        for _root, _dirs, fs in _walk_media_dirs(str(tmp_path))
        for f in fs
    ]
    assert "y.mkv" in names
    assert "x.mkv" not in names


def test_broken_symlink_is_skipped_not_fatal(tmp_path):
    """A dangling link must not abort the scan of everything after it."""
    (tmp_path / "Show").mkdir()
    (tmp_path / "Show" / "ep.mkv").touch()
    os.symlink(tmp_path / "does-not-exist", tmp_path / "dangling")

    files = [f for _r, _d, fs in _walk_media_dirs(str(tmp_path)) for f in fs]
    assert "ep.mkv" in files


def test_missing_scan_path_yields_nothing(tmp_path):
    assert list(_walk_media_dirs(str(tmp_path / "nope"))) == []


# ── get_app_settings isolation ───────────────────────────────────────────────

@pytest.fixture
def db():
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    from app.database.models import Base

    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)()


def test_settings_defaults_are_not_shared_between_calls(db):
    """
    dict() is a shallow copy, so every caller received the SAME list object
    held in the module-level DEFAULT_APP_SETTINGS. One caller appending to
    cfg["keep_audio_languages"] would have corrupted the default for the
    lifetime of the process, for every later request and worker job.
    """
    from app.database.session import get_app_settings

    a = get_app_settings(db)
    b = get_app_settings(db)

    for key in ("keep_audio_languages", "scan_paths", "email_recipients",
                "plex_path_mappings", "scheduled_scan_times",
                "keep_subtitle_languages"):
        assert a[key] is not b[key], f"{key} is shared between calls"


def test_mutating_returned_settings_cannot_corrupt_the_defaults(db):
    from app.database.session import DEFAULT_APP_SETTINGS, get_app_settings

    before = list(DEFAULT_APP_SETTINGS["keep_audio_languages"])

    cfg = get_app_settings(db)
    cfg["keep_audio_languages"].append("fra")
    cfg["scan_paths"].append("/mnt/injected")

    assert DEFAULT_APP_SETTINGS["keep_audio_languages"] == before
    assert DEFAULT_APP_SETTINGS["scan_paths"] == []
    # And a later call is unaffected.
    assert get_app_settings(db)["keep_audio_languages"] == before


# ── Log timestamps ───────────────────────────────────────────────────────────

def test_log_timestamps_are_iso_utc(monkeypatch):
    """
    The log record must carry a full ISO-8601 UTC timestamp, not a
    pre-formatted clock string.

    This field was the one place that bypassed the app's store-UTC /
    display-local convention: the backend formatted "%H:%M:%S" and LogViewer
    rendered it raw, so the clock shown was the SERVER's local time. That
    happened to look right only because the container has no TZ set and runs
    UTC — it agreed with a UTC user by accident and was wrong for everyone
    else.

    Formatting it as UTC while still rendering raw made the disagreement
    visible instead of fixing it: log lines showed UTC while queue and history
    showed local, an hour apart on BST. Sending ISO lets the frontend apply the
    same toUtcDate() + toLocaleTimeString() path as every other timestamp, so
    the viewer agrees with the rest of the UI in every timezone.
    """
    import datetime as dt
    import logging
    import time

    if not hasattr(time, "tzset"):
        pytest.skip("time.tzset() unavailable on this platform")

    from app.core.log_handler import MemoryLogHandler

    original = os.environ.get("TZ")
    os.environ["TZ"] = "Asia/Tokyo"          # +9, never UTC
    time.tzset()
    try:
        handler = MemoryLogHandler()
        handler.setFormatter(logging.Formatter("%(message)s"))
        handler.emit(logging.LogRecord(
            "t", logging.INFO, __file__, 1, "hello", None, None,
        ))
        stamped = handler.get_records()[0]["ts"]

        # Parseable, and carrying an explicit offset so the frontend does not
        # have to guess. toUtcDate() keys off exactly this.
        parsed = dt.datetime.fromisoformat(stamped)
        assert parsed.tzinfo is not None, (
            f"{stamped!r} has no timezone information — toUtcDate() would have "
            "to assume one, which is how this bug started"
        )
        assert parsed.utcoffset() == dt.timedelta(0), (
            f"{stamped!r} is not UTC — the record is being stamped with the "
            "server's local clock"
        )

        # And it is genuinely now, not a fixed or shifted value.
        delta = abs((parsed - dt.datetime.now(dt.timezone.utc)).total_seconds())
        assert delta < 120, f"timestamp is {delta:.0f}s from now"

        # Explicitly NOT the old bare clock string.
        assert len(stamped) > 8, (
            f"{stamped!r} looks like a pre-formatted clock string; the frontend "
            "cannot convert that to the viewer's local time"
        )
    finally:
        if original is None:
            os.environ.pop("TZ", None)
        else:
            os.environ["TZ"] = original
        time.tzset()


# ── Retry-all accounting ─────────────────────────────────────────────────────

def test_retry_all_reports_queued_not_merely_processed(db, monkeypatch, tmp_path):
    """
    "retried" counted every item _process_file did not raise on, which is not
    the same as re-queued. force_probe=True re-runs the decision engine, which
    may now legitimately decide a file needs no work or needs a human — so a
    settings change that turned 3 of 4 failures into no-ops still reported all
    4 requeued, and the Queue then showed 1.
    """
    import app.api.routes.queue as queue_routes
    from app.database.models import MediaFile, QueueItem

    real = tmp_path / "a.mkv"
    real.write_bytes(b"x")

    ids = []
    for i in range(4):
        mf = MediaFile(path=str(real), filename=f"a{i}.mkv", directory=str(tmp_path),
                       size=1, mtime=1.0)
        # Same path for all four is fine — _process_file is stubbed below.
        mf.path = f"{real}{i}"
        (tmp_path / f"a.mkv{i}").write_bytes(b"x")
        db.add(mf)
        db.commit()
        qi = QueueItem(file_id=mf.id, status="failed", error_message="boom")
        db.add(qi)
        db.commit()
        ids.append(qi.id)

    # One queues, one skips, one goes to review, one queues.
    outcomes = iter(["queued", "skipped", "manual_review", "queued"])

    def fake_process(db_, path, cfg, force_probe, dry_run, stats, **kw):
        """Increment whichever ScanStats field this item's outcome maps to."""
        field = next(outcomes)
        setattr(stats, field, getattr(stats, field) + 1)

    monkeypatch.setattr(queue_routes, "_process_file", fake_process)
    monkeypatch.setattr(queue_routes, "get_app_settings", lambda _db: {})
    monkeypatch.setattr(queue_routes, "_current_dry_run_mode", lambda _db: False)

    result = queue_routes.retry_all_failed(db)

    assert result["retried"] == 2, (
        f"reported {result['retried']} requeued, but only 2 items actually "
        "became pending work"
    )
    assert result["skipped"] == 1
    assert result["manual_review"] == 1, (
        "items sent to Review are not reported, so a retry that moved work to "
        "the Review tab looks like it did nothing to them"
    )


def test_retry_all_counts_missing_files_as_skipped(db, monkeypatch):
    """The pre-existing skip reason must survive the new accounting."""
    import app.api.routes.queue as queue_routes
    from app.database.models import MediaFile, QueueItem

    mf = MediaFile(path="/does/not/exist.mkv", filename="x.mkv",
                   directory="/does/not", size=1, mtime=1.0)
    db.add(mf)
    db.commit()
    db.add(QueueItem(file_id=mf.id, status="failed"))
    db.commit()

    monkeypatch.setattr(queue_routes, "get_app_settings", lambda _db: {})
    monkeypatch.setattr(queue_routes, "_current_dry_run_mode", lambda _db: False)

    result = queue_routes.retry_all_failed(db)
    assert result == {"retried": 0, "skipped": 1, "manual_review": 0, "errors": []}
