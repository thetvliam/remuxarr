"""
UTC time helpers.

Exists because `datetime.utcnow()` is deprecated and scheduled for removal,
and because the replacement is not quite a drop-in — see below. One helper
means one place to reason about it rather than twenty.

STORAGE SEMANTICS (verified in tests/test_timestamp_roundtrip.py)
----------------------------------------------------------------
SQLAlchemy's SQLite DATETIME type strips tzinfo on write WITHOUT converting.
That has two consequences worth stating explicitly:

  • A UTC-aware value stores byte-identically to the naive value it replaces
    and reads back naive, so swapping utcnow() for utcnow_aware() at a write
    site changes nothing on disk, nothing in the API payload, and nothing in
    the browser. That is what made this migration mechanical rather than a
    data migration.

  • A NON-UTC aware value stores its local wall clock as though it were UTC.
    `datetime.now()` is therefore silently wrong by the host's offset, with
    nothing raising. Always go through this module; never call
    `datetime.now()` for a value that lands in a DateTime column.

The columns remain naive-UTC by convention, and the frontend's toUtcDate()
in utils.js depends on that (it appends 'Z' only when the serialised string
carries no timezone info). Do not switch the columns to
DateTime(timezone=True) without revisiting that function and the tests.
"""
from datetime import datetime, timezone


def utcnow() -> datetime:
    """
    Current UTC time, timezone-aware.

    The direct replacement for the deprecated `datetime.utcnow()` at any site
    whose value is written to a DateTime column. Passed to SQLAlchemy the
    tzinfo is stripped, so the stored value is identical to what utcnow()
    produced.
    """
    return datetime.now(timezone.utc)


def utcnow_iso_z() -> str:
    """
    Current UTC time as an ISO-8601 string with a 'Z' suffix.

    For export manifests, which are read by other tooling and have always used
    the 'Z' spelling. Note this is NOT `utcnow().isoformat() + "Z"`: an
    aware value already serialises its offset, so appending 'Z' yields
    '...+00:00Z', which is not a valid timestamp. The offset is rewritten
    rather than appended.
    """
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
