"""
Timestamp storage and serialisation contract.

WHY THIS FILE EXISTS
--------------------
`datetime.utcnow()` is deprecated and scheduled for removal, so every write
site has to move to `datetime.now(timezone.utc)`. Before this file there was
no test anywhere covering how a timestamp is stored, read back, or serialised,
which made that swap unverifiable — the failure mode of getting it wrong is
not an exception but every timestamp in the history panel being silently
wrong by the local UTC offset.

WHAT THESE TESTS PIN
--------------------
The behaviour that makes the swap safe, established empirically rather than
assumed:

  SQLAlchemy's SQLite DATETIME type STRIPS tzinfo on write WITHOUT converting.

Two consequences, and both matter:

  1. A UTC-aware value stores byte-identically to the naive value it replaces,
     reads back naive, and therefore serialises identically. No column change,
     no backfill, no frontend change — the migration is mechanical.

  2. A non-UTC aware value stores its LOCAL wall clock as though it were UTC.
     So `datetime.now()` (or any non-UTC zone) is silently, permanently wrong,
     and the error is exactly the class of bug frontend/src/utils.js's
     toUtcDate() comment warns about.

The second point is why these tests are worth keeping after the migration
lands: they are the guard that stops someone typing `datetime.now()` and
shifting every timestamp by the server's offset with nothing failing loudly.
"""
import datetime as dt
import os
import time
from pathlib import Path

import pytest



def _db():
    """Fresh in-memory DB — same pattern as the other DB tests in this suite."""
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    from app.database.models import Base

    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)()


def _media(db, path, **kw):
    from app.database.models import MediaFile

    mf = MediaFile(path=path, filename=path.rsplit("/", 1)[-1], directory="/m",
                   size=1, mtime=1.0, **kw)
    db.add(mf)
    db.commit()
    return mf


def _raw(db, column="last_scanned"):
    """Read the column as SQLite actually stored it, bypassing SQLAlchemy typing."""
    from sqlalchemy import text

    return [r[0] for r in db.execute(
        text(f"SELECT {column} FROM media_files ORDER BY id")
    )]


# ── The core equivalence the migration depends on ────────────────────────────

def test_utc_aware_stores_identically_to_naive_utcnow():
    """
    The migration's whole premise: swapping utcnow() for now(timezone.utc)
    must not change a single stored byte.
    """
    db = _db()
    instant_naive = dt.datetime(2026, 8, 6, 12, 0, 0, 123456)
    instant_aware = instant_naive.replace(tzinfo=dt.timezone.utc)

    _media(db, "/m/naive.mkv", last_scanned=instant_naive)
    _media(db, "/m/aware.mkv", last_scanned=instant_aware)

    stored = _raw(db)
    assert stored[0] == stored[1], (
        f"aware and naive UTC stored differently: {stored[0]!r} vs {stored[1]!r} "
        "— the utcnow migration would NOT be storage-compatible"
    )


def test_aware_value_reads_back_naive():
    """
    Reads must stay naive, or serialisation gains a +00:00 suffix and the
    frontend contract changes.
    """
    from app.database.models import MediaFile

    db = _db()
    _media(db, "/m/a.mkv",
           last_scanned=dt.datetime(2026, 8, 6, 12, 0, tzinfo=dt.timezone.utc))

    got = db.query(MediaFile).filter_by(path="/m/a.mkv").first().last_scanned
    assert got.tzinfo is None, f"expected naive on read, got tzinfo={got.tzinfo}"
    assert got == dt.datetime(2026, 8, 6, 12, 0)


def test_serialised_form_has_no_offset_suffix():
    """
    _iso() feeds the frontend. toUtcDate() in utils.js appends 'Z' only when
    the string carries no timezone info, so an offset appearing here would
    change how every timestamp is parsed in the browser.
    """
    from app.api.routes.queue import _iso
    from app.database.models import MediaFile

    db = _db()
    _media(db, "/m/a.mkv",
           last_scanned=dt.datetime(2026, 8, 6, 12, 0, tzinfo=dt.timezone.utc))
    got = db.query(MediaFile).filter_by(path="/m/a.mkv").first().last_scanned

    s = _iso(got)
    assert "+" not in s and not s.endswith("Z"), f"unexpected tz suffix in {s!r}"
    assert s.startswith("2026-08-06T12:00:00")
    assert _iso(None) is None


# ── The trap this file exists to guard ───────────────────────────────────────

def test_non_utc_aware_value_is_silently_misstored():
    """
    Pins the dangerous behaviour explicitly so it can never be a surprise:
    tzinfo is stripped WITHOUT conversion, so a +05:00 value stores its local
    wall clock as though it were UTC — five hours wrong, silently.

    This is not desirable behaviour being blessed; it is a landmine being
    marked. If SQLAlchemy ever starts converting instead, this test fails and
    whoever sees it should read the migration note above rather than just
    updating the expectation.
    """
    db = _db()
    instant = dt.datetime(2026, 8, 6, 12, 0, tzinfo=dt.timezone.utc)
    plus5 = instant.astimezone(dt.timezone(dt.timedelta(hours=5)))
    assert plus5.hour == 17          # same instant, different wall clock

    _media(db, "/m/plus5.mkv", last_scanned=plus5)

    stored = _raw(db)[0]
    assert stored.startswith("2026-08-06 17:00"), (
        f"stored {stored!r}: SQLAlchemy's behaviour changed — it now appears to "
        "convert to UTC rather than strip. Re-read the module docstring before "
        "editing this assertion."
    )


@pytest.fixture
def non_utc_host():
    """
    Force a non-UTC local timezone for the duration of a test.

    Necessary, not cosmetic. These tests detect "a naive local clock was used
    where UTC was required", and on a UTC host the two are identical — so the
    bug is undetectable there. The Dockerfile sets no TZ, so the shipped
    container IS UTC, and so is the CI runner: without this the checks were
    inert in exactly the two environments that matter.

    Verified by injecting `return datetime.now()` into timeutil.utcnow():
    TZ=UTC gave 7 passed / 1 skipped, TZ=America/New_York gave 1 failed. The
    original guard skipped rather than forced, so the skip line was the only
    hint anything had gone quiet.
    """
    if not hasattr(time, "tzset"):          # Windows
        pytest.skip("time.tzset() unavailable on this platform")

    original = os.environ.get("TZ")
    os.environ["TZ"] = "America/New_York"   # DST-observing, never UTC
    time.tzset()
    try:
        yield
    finally:
        if original is None:
            os.environ.pop("TZ", None)
        else:
            os.environ["TZ"] = original
        time.tzset()


def test_naive_local_now_would_shift_timestamps(non_utc_host):
    """
    The concrete regression the migration must avoid: datetime.now() and
    datetime.now(timezone.utc) differ by the host offset, and NEITHER raises.

    No longer skipped on a UTC host — the fixture forces an offset so this
    runs everywhere, which is the whole point.
    """
    local = dt.datetime.now().replace(microsecond=0)
    utc = dt.datetime.now(dt.timezone.utc).replace(microsecond=0, tzinfo=None)
    offset = abs((local - utc).total_seconds())

    assert offset > 60, (
        f"expected a non-zero host offset under the forced timezone, got "
        f"{offset}s — the fixture is not taking effect and the checks below "
        "would be inert"
    )


# ── The invariant the TZ documentation rests on ──────────────────────────────

def test_naive_local_time_is_confined_to_the_scheduler():
    """
    The README tells users that TZ decides when scheduled work runs and
    does not affect any timestamp they read. Both halves rest on one
    invariant: a bare `datetime.now()` appears in the scheduler, where
    matching the user's wall clock is the entire point, and nowhere else.

    timeutil.py states that rule in prose already - never call
    `datetime.now()` for a value that lands in a DateTime column - and
    nothing enforced it. The failure is silent by construction: a naive
    local value stores its wall clock as though it were UTC, so the row
    is wrong by the host offset with nothing raising, and on a UTC host
    every other test still passes.

    Scanning source rather than behaviour because source is where the
    rule gets broken. A new write site is one line, and the value it
    stores looks entirely plausible until someone outside UTC reads it -
    which is exactly the drift this guards, in both directions: adding a
    call elsewhere makes the "your browser decides" half wrong, removing
    the scheduler's makes the "TZ decides when scans run" half wrong.

    timeutil.py is exempt because it names `datetime.now()` in its own
    docstring while calling only the timezone-aware form.

    The pattern matches any no-argument `.now()` rather than the literal
    `datetime.now()`, because `from datetime import datetime as dt`
    defeats the narrower form - a surviving mutant when this was first
    written. Today that broader pattern matches nothing in app/ except
    the two scheduler lines, so it costs no false positives.
    """
    import re

    root = Path(__file__).resolve().parent.parent
    exempt = {"app/core/timeutil.py"}
    bare_now = re.compile(r"\.now\(\s*\)")

    found = {}
    for path in sorted((root / "app").rglob("*.py")):
        rel = path.relative_to(root).as_posix()
        if rel in exempt:
            continue
        hits = [
            n for n, line in enumerate(path.read_text().splitlines(), 1)
            if bare_now.search(line)
        ]
        if hits:
            found[rel] = hits

    assert set(found) <= {"app/core/scheduler.py"}, (
        f"bare datetime.now() outside the scheduler: "
        f"{ {k: v for k, v in found.items() if k != 'app/core/scheduler.py'} } - "
        f"that stores local wall-clock time into a UTC column silently. Use "
        f"app.core.timeutil.utcnow()."
    )
    assert "app/core/scheduler.py" in found, (
        "the scheduler no longer matches on local time, so TZ no longer "
        "decides when scheduled scans run - the README says it does"
    )


# ── Defaults and ordering ────────────────────────────────────────────────────

def test_column_defaults_populate_and_are_naive(non_utc_host):
    """
    created_at/last_scanned defaults must produce naive UTC, like explicit
    writes.

    Runs under a forced non-UTC timezone: this is THE test that catches
    `default=lambda: datetime.now()` slipping into models.py, and on a UTC host
    local and UTC coincide so it would pass against that exact bug.
    """
    from app.database.models import MediaFile

    db = _db()
    _media(db, "/m/default.mkv")          # no timestamps passed

    row = db.query(MediaFile).filter_by(path="/m/default.mkv").first()
    assert row.created_at is not None
    assert row.last_scanned is not None
    assert row.created_at.tzinfo is None
    assert row.last_scanned.tzinfo is None

    # Default must be UTC, not local. Under the forced timezone the two differ
    # by hours, so a local-clock default fails loudly instead of coinciding.
    utc_now = dt.datetime.now(dt.timezone.utc).replace(tzinfo=None)
    delta = abs((row.created_at - utc_now).total_seconds())
    assert delta < 120, (
        f"created_at is {delta:.0f}s from UTC now — the column default is "
        f"writing local time. Host offset is "
        f"{abs((dt.datetime.now() - utc_now).total_seconds()):.0f}s."
    )


def test_ordering_is_chronological_across_mixed_rows():
    """
    An interrupted migration leaves rows written both ways. Ordering must stay
    correct, since the history panel sorts on these columns.
    """
    from app.database.models import MediaFile

    db = _db()
    base = dt.datetime(2026, 8, 6, 12, 0)
    _media(db, "/m/2nd.mkv", last_scanned=(base + dt.timedelta(hours=1)))          # naive
    _media(db, "/m/1st.mkv", last_scanned=base.replace(tzinfo=dt.timezone.utc))    # aware
    _media(db, "/m/3rd.mkv", last_scanned=(base + dt.timedelta(hours=2)))          # naive

    paths = [r.path for r in
             db.query(MediaFile).order_by(MediaFile.last_scanned).all()]
    assert paths == ["/m/1st.mkv", "/m/2nd.mkv", "/m/3rd.mkv"], paths


def test_all_datetime_columns_round_trip():
    """
    Every DateTime column, not just the one convenient to test — the migration
    touches completed_at/started_at on queue and forge rows too.
    """
    from app.database.models import Ac3ForgeJob, MediaFile, QueueItem

    db = _db()
    t = dt.datetime(2026, 8, 6, 12, 0, tzinfo=dt.timezone.utc)
    expected = dt.datetime(2026, 8, 6, 12, 0)

    mf = _media(db, "/m/x.mkv", last_scanned=t, last_processed=t, created_at=t)
    db.add(QueueItem(file_id=mf.id, status="success",
                     created_at=t, started_at=t, completed_at=t))
    db.add(Ac3ForgeJob(file_id=mf.id, status="success",
                       created_at=t, started_at=t, completed_at=t))
    db.commit()

    row = db.query(MediaFile).filter_by(path="/m/x.mkv").first()
    for f in ("last_scanned", "last_processed", "created_at"):
        assert getattr(row, f) == expected, f"MediaFile.{f}"

    q = db.query(QueueItem).first()
    for f in ("created_at", "started_at", "completed_at"):
        assert getattr(q, f) == expected, f"QueueItem.{f}"

    j = db.query(Ac3ForgeJob).first()
    for f in ("created_at", "started_at", "completed_at"):
        assert getattr(j, f) == expected, f"Ac3ForgeJob.{f}"
