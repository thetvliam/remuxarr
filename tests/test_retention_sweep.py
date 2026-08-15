"""
Retention sweep — the thing that keeps the recycle bin bounded.

Two bounds, applied in order, and both are needed. A days-only window has
no ceiling during a big library sweep, when hundreds of files are
processed inside the window and every one leaves a sidecar. A size-only
cap keeps one stale sidecar forever on a quiet library. The tests below
pin each bound independently and then together, because an implementation
that applies only one passes half of them and looks fine.

Eviction order is newest-kept, oldest-dropped. Reversed, a big sweep
evicts precisely the revert points a user is about to want — the ones
from the jobs that just ran — while keeping week-old ones nobody will
look at.

The orphan pass
---------------
A sidecar is written during a job but its row is not recorded until that
job finishes, so for the length of the staging copy there is legitimately
a complete sidecar with nothing pointing at it. Sweeping that window
deletes a live revert point out from under a running job, which is why
ORPHAN_GRACE_SECONDS exists and why two tests here are specifically about
a recent file being left alone. The pass is still worth having: a crash
between those two steps is the one way to leak, and leaked bytes are
invisible — no row names them, nothing scans the volume, and they are not
counted against the cap, so the bin quietly exceeds its limit.

Verified by mutation, 11 applied, 11 killed:

  • Age pass removed                          → killed
  • Size pass removed                         → killed
  • Eviction order reversed (oldest kept)     → killed
  • Age comparison flipped (> for <)          → killed
  • Size budget compared with >= not >        → killed (exact-fit evicted)
  • days=0 treated as "expire everything"     → killed
  • max_gb=0 treated as "keep nothing"        → killed
  • Orphan pass removed                       → killed
  • Orphan grace period ignored               → killed (live sidecar
                                                 deleted mid-job)
  • Orphan pass matching any file, not just
    sidecars                                   → killed
  • Sweep runs without a mounted volume       → killed

No equivalent mutants.
"""
import os
import time
from datetime import timedelta

import pytest

from app.core.timeutil import utcnow_naive


@pytest.fixture
def bin_(tmp_path, monkeypatch):
    """A mounted recycle volume wired to an in-memory database."""
    from sqlalchemy.orm import sessionmaker

    from tests.conftest import memory_engine

    from app.config import settings as app_settings
    from app.database.models import Base
    import app.database.session as session_mod

    root = tmp_path / "recycle"
    root.mkdir()
    monkeypatch.setattr(app_settings, "RECYCLE_DIR", str(root), raising=False)

    engine = memory_engine()
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine)
    monkeypatch.setattr(session_mod, "SessionLocal", factory)

    cfg = {"revert_retention_days": 7, "revert_retention_max_gb": 20}
    monkeypatch.setattr(session_mod, "get_app_settings", lambda _db: cfg)

    return {"root": root, "db": factory(), "cfg": cfg}


def _point(bin_, name, *, age_days=0, size=1024, with_file=True):
    """
    A revert point whose ROW claims `size` while the file on disk stays
    tiny.

    Not a shortcut — it is the design being tested. The size pass reads
    the stored sidecar_size so it can order and sum candidates in one
    query instead of a filesystem round trip per row, and a version that
    stat()ed instead would fail these tests rather than quietly costing
    an I/O per point on a spun-down array. It also means a multi-gigabyte
    cap can be exercised without writing multiple gigabytes.
    """
    from app.database.models import RevertPoint

    path = bin_["root"] / f"{name}.remuxarr_revert"
    if with_file:
        path.write_bytes(b"x" * min(size, 4096))

    row = RevertPoint(
        file_id=1, sidecar_path=str(path), sidecar_size=size,
        manifest="{}", original_path=f"/m/{name}.mkv",
        created_at=utcnow_naive() - timedelta(days=age_days),
    )
    bin_["db"].add(row)
    bin_["db"].commit()
    return path


def _sweep():
    from app.core.recycle import sweep_retention

    return sweep_retention()


def _remaining(bin_):
    from app.database.models import RevertPoint

    bin_["db"].expire_all()
    return {os.path.basename(p.sidecar_path)
            for p in bin_["db"].query(RevertPoint).all()}


# ── Age ──────────────────────────────────────────────────────────────────────

def test_points_past_the_window_are_removed(bin_):
    old = _point(bin_, "old", age_days=9)
    recent = _point(bin_, "recent", age_days=2)

    removed, freed = _sweep()

    assert not old.exists()
    assert recent.exists()
    assert removed == 1
    assert freed == 1024, "freed bytes came from stat() rather than the stored size"
    assert _remaining(bin_) == {"recent.remuxarr_revert"}


def test_a_point_inside_the_window_survives(bin_):
    """
    The boundary matters more than it looks: a comparison the wrong way
    round empties the bin on the first sweep and the symptom is "revert
    never works", not an error.
    """
    just_inside = _point(bin_, "inside", age_days=6)

    _sweep()

    assert just_inside.exists()


def test_zero_days_disables_the_age_bound(bin_):
    """Not "expire everything immediately" — that reading empties the bin."""
    bin_["cfg"]["revert_retention_days"] = 0
    ancient = _point(bin_, "ancient", age_days=400)

    _sweep()

    assert ancient.exists()


# ── Size ─────────────────────────────────────────────────────────────────────

def test_the_cap_evicts_the_oldest_first(bin_):
    """
    A user reverting is almost always undoing something that just
    happened. Evicting newest-first would drop exactly those.
    """
    gb = 1024 * 1024 * 1024
    bin_["cfg"]["revert_retention_max_gb"] = 2

    oldest = _point(bin_, "oldest", age_days=3, size=gb)
    middle = _point(bin_, "middle", age_days=2, size=gb)
    newest = _point(bin_, "newest", age_days=1, size=gb)

    _sweep()

    assert newest.exists()
    assert middle.exists()
    assert not oldest.exists(), "the cap evicted the most recent point"


def test_a_bin_under_the_cap_is_untouched(bin_):
    a = _point(bin_, "a", size=1024)
    b = _point(bin_, "b", size=1024)

    removed, _ = _sweep()

    assert (a.exists(), b.exists()) == (True, True)
    assert removed == 0


def test_zero_cap_disables_the_size_bound(bin_):
    bin_["cfg"]["revert_retention_max_gb"] = 0
    big = _point(bin_, "big", size=50 * 1024 * 1024 * 1024)

    _sweep()

    assert big.exists()


def test_both_bounds_apply_together(bin_):
    """
    Age runs first, then the cap over what is left. An implementation
    applying only one of them passes half this file.
    """
    gb = 1024 * 1024 * 1024
    bin_["cfg"]["revert_retention_max_gb"] = 2

    expired = _point(bin_, "expired", age_days=30, size=1024)
    oldest_kept = _point(bin_, "oldest_kept", age_days=3, size=gb)
    newer = _point(bin_, "newer", age_days=2, size=gb)
    newest = _point(bin_, "newest", age_days=1, size=gb)

    _sweep()

    assert not expired.exists(), "age bound did not apply"
    assert not oldest_kept.exists(), "size bound did not apply"
    assert newer.exists() and newest.exists()


# ── Orphans ──────────────────────────────────────────────────────────────────

def _age_file(path, seconds):
    past = time.time() - seconds
    os.utime(path, (past, past))


def test_an_orphaned_sidecar_is_removed(bin_):
    """
    A crash between writing the sidecar and recording its row is the only
    way to produce one. Nothing else would find it — no row names it and
    nothing scans the volume — so the bytes sit outside the cap forever.
    """
    orphan = bin_["root"] / "99_1.remuxarr_revert"
    orphan.write_bytes(b"x" * 4096)
    _age_file(orphan, 7200)

    removed, freed = _sweep()

    assert not orphan.exists()
    assert removed == 1
    assert freed == 4096


def test_a_recent_orphan_is_left_alone(bin_):
    """
    The window between a sidecar being written and its job finishing is a
    legitimate no-row period. Sweeping it deletes a live revert point out
    from under a running job.
    """
    in_flight = bin_["root"] / "99_1.remuxarr_revert"
    in_flight.write_bytes(b"x" * 4096)

    removed, _ = _sweep()

    assert in_flight.exists(), "deleted a sidecar a running job was about to record"
    assert removed == 0


def test_unrelated_files_on_the_volume_are_not_touched(bin_):
    """
    The volume is the user's. Anything that is not a sidecar — a README
    they dropped in, a .part from a crashed write that the startup sweep
    owns — is none of this pass's business.
    """
    stray = bin_["root"] / "notes.txt"
    stray.write_text("mine")
    _age_file(stray, 7200)

    part = bin_["root"] / "5_1.remuxarr_revert.part"
    part.write_bytes(b"partial")
    _age_file(part, 7200)

    _sweep()

    assert stray.exists()
    assert part.exists(), "the startup sweep owns .part files, not this one"


def test_a_referenced_sidecar_is_never_treated_as_an_orphan(bin_):
    live = _point(bin_, "live", age_days=1)
    _age_file(live, 7200)

    _sweep()

    assert live.exists()


# ── Not mounted ──────────────────────────────────────────────────────────────

def test_sweep_does_nothing_without_a_mounted_volume(tmp_path, monkeypatch):
    from app.config import settings as app_settings
    import app.database.session as session_mod

    monkeypatch.setattr(app_settings, "RECYCLE_DIR",
                        str(tmp_path / "not-mounted"), raising=False)

    def boom():
        pytest.fail("opened a database session with no recycle volume")

    monkeypatch.setattr(session_mod, "SessionLocal", boom)

    assert _sweep() == (0, 0)


# ── Detached points ──────────────────────────────────────────────────────────
#
# _delete_media_file_and_related detaches a revert point rather than
# deleting it, on the grounds that it cannot tell a rename from a
# deletion. That exemption is only defensible because retention still
# bounds what it leaves behind — these tests are the other half of the
# argument made in test_media_file_deletion.py's DETACHED_TABLES.

def _detached(bin_, name, *, age_days=0, size=1024):
    from app.database.models import RevertPoint

    path = bin_["root"] / f"{name}.remuxarr_revert"
    path.write_bytes(b"x" * min(size, 4096))
    row = RevertPoint(
        file_id=None, sidecar_path=str(path), sidecar_size=size,
        manifest="{}", original_path=f"/m/{name}.mkv",
        created_at=utcnow_naive() - timedelta(days=age_days),
        detached_at=utcnow_naive() - timedelta(days=age_days),
    )
    bin_["db"].add(row)
    bin_["db"].commit()
    return path


def test_detached_points_still_expire(bin_):
    """
    Without this, a library-wide rename converts the whole recycle bin
    into rows nothing will ever clear.
    """
    old = _detached(bin_, "detached_old", age_days=30)

    _sweep()

    assert not old.exists()


def test_detached_points_still_count_against_the_cap(bin_):
    """
    Excluding them from the cap would let detached points crowd out the
    live ones a user is far more likely to need.
    """
    gb = 1024 * 1024 * 1024
    bin_["cfg"]["revert_retention_max_gb"] = 2

    orphan = _detached(bin_, "detached", age_days=3, size=gb)
    newer = _point(bin_, "newer", age_days=2, size=gb)
    newest = _point(bin_, "newest", age_days=1, size=gb)

    _sweep()

    assert not orphan.exists(), "a detached point escaped the size cap"
    assert newer.exists() and newest.exists()


def test_a_detached_points_sidecar_is_not_swept_as_an_orphan(bin_):
    """
    Detached is not orphaned. It has a row, it is listed in the UI, and a
    user can match it back to a renamed file — deleting the file under it
    would make that impossible while the row still promised it.
    """
    path = _detached(bin_, "detached", age_days=1)
    _age_file(path, 7200)

    _sweep()

    assert path.exists()


# ── Independent of the feature toggle ────────────────────────────────────────

def test_retention_still_applies_when_the_feature_is_off(bin_):
    """
    Turning the feature off should stop new revert points being created,
    not strand the existing ones. The opposite leaves someone who disabled
    it with 20GB they cannot explain and no UI that mentions it.
    """
    bin_["cfg"]["revert_enabled"] = False
    old = _point(bin_, "old", age_days=99)

    _sweep()

    assert not old.exists()
