"""
Schema migration on databases that already exist.

create_all() only creates missing TABLES. It never alters existing ones,
so every column added after a table first shipped is invisible on any
install that already has that table — and the failure is quiet and late.
Nothing goes wrong at startup. The first symptom is an OperationalError
from whatever background task happens to touch the column first, which on
a real install was the retention sweep, sixty seconds after boot, in a log
nobody was watching.

revert_points hit this twice at once, and only one half showed:

  • detached_at was added and never migrated. That is what appeared in
    the log — a crashing sweep.
  • file_id had to become nullable, and SQLite cannot ALTER a column's
    nullability. Nothing at all reported this. The first symptom would
    have been an IntegrityError inside cleanup_deleted_files, which runs
    unattended on every scan, on the first file the user renamed.

The second is the reason for test_the_live_schema_matches_the_models
below. A per-column test would have caught the missing detached_at and
sailed straight past the constraint, because a column can be present and
still wrong.

Verified by mutation, 6 applied, 6 killed:

  • detached_at migration removed              → killed
  • file_id rebuild never called               → killed
  • rebuild guard inverted (runs every startup)→ killed
  • rebuild drops rows instead of copying them → killed
  • stale indexes left on the renamed table    → killed
  • scratch table left behind                  → killed

The first initially SURVIVED, and the reason is worth keeping: the
rebuild recreates revert_points from the model, so it adds detached_at
too and the ADD COLUMN is currently unreachable. That makes it dead
code today and load-bearing the day the rebuild is deleted — which is
the intent, once every deployed install has started once.
test_the_column_migration_stands_on_its_own pins it against the shape
the world is in on that day.
"""
import importlib
import sqlite3

import pytest
from sqlalchemy import create_engine, inspect


# The revert_points table exactly as it first shipped: file_id NOT NULL,
# no detached_at. Written out rather than generated, because the point is
# to test against what is actually deployed, not against a shape derived
# from the models we are checking.
LEGACY_REVERT_POINTS = """
CREATE TABLE revert_points (
    id INTEGER NOT NULL PRIMARY KEY,
    file_id INTEGER NOT NULL,
    sidecar_path VARCHAR NOT NULL,
    sidecar_size BIGINT,
    manifest TEXT NOT NULL,
    original_path VARCHAR NOT NULL,
    original_container VARCHAR,
    processed_size BIGINT,
    processed_mtime FLOAT,
    created_at DATETIME,
    FOREIGN KEY(file_id) REFERENCES media_files (id) ON DELETE CASCADE
)
"""


@pytest.fixture
def upgraded(tmp_path, monkeypatch):
    """A database carrying the previously-released revert_points table."""

    path = tmp_path / "remuxarr.db"
    conn = sqlite3.connect(path)
    conn.execute(LEGACY_REVERT_POINTS)
    conn.execute("CREATE INDEX ix_revert_points_file_id ON revert_points (file_id)")
    conn.execute(
        "INSERT INTO revert_points "
        "(file_id, sidecar_path, sidecar_size, manifest, original_path) "
        "VALUES (7, '/recycle/7_1.remuxarr_revert', 4096, '{\"version\": 2}', "
        "'/m/Show.mkv')"
    )
    conn.commit()
    conn.close()

    session_mod = _point_the_engine_at(path, monkeypatch)
    session_mod.init_db()

    yield path, session_mod

    _restore_the_engine(session_mod)


def _point_the_engine_at(path, monkeypatch):
    """
    Rebuild session.engine against `path`, without replacing the settings
    OBJECT.

    The obvious version of this reloads app.config, and that quietly
    breaks other test files. Reloading a module rebinds its globals, so
    app.config.settings becomes a NEW object — while every module that did
    `from app.config import settings` still holds the old one. A later
    test patching app.config.settings then patches something app.core.
    recycle has never seen, and its assertions fail for reasons that have
    nothing to do with what it is testing.

    That is order-dependent, so the full suite passed and the breakage
    only appeared when these files ran together in a different order.

    Patching the attribute on the existing object and reloading only
    session — which reads settings.DATABASE_PATH at import to build the
    engine — keeps one settings object alive throughout.
    """
    import app.config
    import app.database.session as session_mod

    monkeypatch.setattr(app.config.settings, "DATABASE_PATH", str(path))
    importlib.reload(session_mod)
    return session_mod


def _restore_the_engine(session_mod):
    """
    Rebuild the engine against the suite-wide database.

    Runs before monkeypatch undoes DATABASE_PATH, so it is reloaded once
    more by the caller's own teardown ordering — hence the explicit second
    reload rather than relying on it.
    """
    importlib.reload(session_mod)


def _columns(path):
    conn = sqlite3.connect(path)
    try:
        return {r[1]: {"notnull": r[3]}
                for r in conn.execute("PRAGMA table_info(revert_points)")}
    finally:
        conn.close()


# ── The two failures ─────────────────────────────────────────────────────────

def test_the_missing_column_is_added(upgraded):
    """The half that showed: a sweep crashing on every tick."""
    path, _ = upgraded

    assert "detached_at" in _columns(path)


def test_file_id_becomes_nullable(upgraded):
    """
    The half that showed nothing. Detaching sets file_id to NULL, so on an
    upgraded install the first rename would raise inside
    cleanup_deleted_files — unattended, on every scan.
    """
    path, _ = upgraded

    assert _columns(path)["file_id"]["notnull"] == 0


def test_detaching_actually_works_after_migrating(upgraded):
    """
    The column being nullable is the mechanism; this is the behaviour. A
    rebuild that produced the right PRAGMA output and a broken table would
    pass the test above.
    """
    path, session_mod = upgraded
    from app.database.models import RevertPoint

    with session_mod.SessionLocal() as db:
        point = db.query(RevertPoint).one()
        point.file_id = None
        db.commit()

        assert db.query(RevertPoint).one().file_id is None


def test_existing_revert_points_survive_the_rebuild(upgraded):
    """
    The table is recreated, not altered. Losing its contents would empty a
    user's recycle bin on upgrade — the one thing it exists to protect.
    """
    path, session_mod = upgraded
    from app.database.models import RevertPoint

    with session_mod.SessionLocal() as db:
        point = db.query(RevertPoint).one()
        assert point.file_id == 7
        assert point.original_path == "/m/Show.mkv"
        assert point.sidecar_size == 4096
        assert point.detached_at is None


def test_the_rebuild_restores_the_indexes(upgraded):
    """
    Indexes follow a renamed table. Left behind they collide with the ones
    the model creates; dropped and not recreated, the queries that
    motivated them quietly table-scan.
    """
    path, _ = upgraded
    conn = sqlite3.connect(path)
    try:
        names = {r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='index' "
            "AND tbl_name='revert_points'")}
    finally:
        conn.close()

    assert "ix_revert_points_file_id" in names
    assert "ix_revert_points_detached_at" in names
    assert not any(n.endswith("_old") for n in names)


def test_the_scratch_table_is_cleaned_up(upgraded):
    path, _ = upgraded
    conn = sqlite3.connect(path)
    try:
        tables = {r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'")}
    finally:
        conn.close()

    assert "_revert_points_old" not in tables


def test_the_column_migration_stands_on_its_own(tmp_path, monkeypatch):
    """
    detached_at must be added by _migrate_schema, not merely as a
    side-effect of the file_id rebuild recreating the table from the
    model.

    The distinction is not academic. The rebuild is a one-off that exists
    only to carry already-deployed installs across the nullability change,
    and it is meant to be deleted once they have all started once. Delete
    it while the ADD COLUMN is only nominally there, and detached_at
    silently stops being migrated.

    So this fixture is a table that has ALREADY been made nullable but
    still lacks the column — the exact shape the world is in the day the
    rebuild is removed.
    """

    path = tmp_path / "half-migrated.db"
    conn = sqlite3.connect(path)
    conn.execute(LEGACY_REVERT_POINTS.replace("file_id INTEGER NOT NULL",
                                              "file_id INTEGER"))
    conn.commit()
    conn.close()

    session_mod = _point_the_engine_at(path, monkeypatch)
    try:
        session_mod.init_db()
        assert "detached_at" in _columns(path)
    finally:
        _restore_the_engine(session_mod)


def test_the_added_column_gets_its_index_on_every_path(tmp_path, monkeypatch):
    """
    ALTER TABLE ADD COLUMN never brings an index with it.

    An install whose revert_points.file_id was ALREADY nullable skips the
    table rebuild and takes the ADD COLUMN path, so detached_at arrived
    without ix_revert_points_detached_at — and list_detached orders by
    that column. The rebuild path got the index for free, which is exactly
    why this was missed: the path that was tested was the path that
    worked.

    index_migrations exists for this and the new column was not added
    to it.
    """
    path = tmp_path / "half-migrated.db"
    conn = sqlite3.connect(path)
    conn.execute(LEGACY_REVERT_POINTS.replace("file_id INTEGER NOT NULL",
                                              "file_id INTEGER"))
    conn.commit()
    conn.close()

    session_mod = _point_the_engine_at(path, monkeypatch)
    try:
        session_mod.init_db()

        conn = sqlite3.connect(path)
        try:
            indexes = {r[0] for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='index' "
                "AND tbl_name='revert_points'")}
        finally:
            conn.close()
    finally:
        _restore_the_engine(session_mod)

    assert "ix_revert_points_detached_at" in indexes, (
        "detached_at was added without its index; list_detached will "
        "table-scan"
    )


# ── The general check ────────────────────────────────────────────────────────

def test_the_live_schema_matches_the_models(upgraded):
    """
    The test that generalises, and the one that would have caught the
    silent half.

    Every column of every model must exist on the migrated database AND
    agree on nullability. A per-column existence check finds a forgotten
    ADD COLUMN and sails straight past a constraint that can no longer be
    satisfied — which is exactly how file_id got through.

    If this fails after a model change, the fix is a migration, not a
    change here.
    """
    path, session_mod = upgraded
    from app.database.models import Base

    engine = create_engine(f"sqlite:///{path}")
    inspector = inspect(engine)
    live_tables = set(inspector.get_table_names())

    problems = []
    for table in Base.metadata.tables.values():
        if table.name not in live_tables:
            problems.append(f"{table.name}: table missing entirely")
            continue

        live = {c["name"]: c for c in inspector.get_columns(table.name)}
        for column in table.columns:
            if column.name not in live:
                problems.append(f"{table.name}.{column.name}: missing — "
                                f"needs an ADD COLUMN migration")
                continue
            # Primary keys report nullable inconsistently across SQLite
            # versions and are never NULL either way.
            if column.primary_key:
                continue
            if bool(live[column.name]["nullable"]) != bool(column.nullable):
                problems.append(
                    f"{table.name}.{column.name}: nullable="
                    f"{live[column.name]['nullable']} on disk, "
                    f"{column.nullable} in the model — SQLite cannot ALTER "
                    f"this, it needs a table rebuild"
                )

    assert not problems, "migrated schema does not match the models:\n  " + \
                         "\n  ".join(problems)


def test_a_fresh_database_needs_no_migration(tmp_path, monkeypatch):
    """
    The rebuild must be a no-op when create_all already produced the right
    shape, or it runs on every startup and rewrites the table each time.
    """

    path = tmp_path / "fresh.db"
    session_mod = _point_the_engine_at(path, monkeypatch)

    try:
        session_mod.init_db()
        before = _columns(path)
        session_mod.init_db()   # second startup

        assert _columns(path) == before
        assert before["file_id"]["notnull"] == 0
    finally:
        _restore_the_engine(session_mod)
