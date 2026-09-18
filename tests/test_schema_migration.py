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

Verified by mutation, 11 applied, 11 killed:

  • detached_at migration removed              → killed
  • file_id rebuild never called               → killed
  • rebuild guard inverted (runs every startup)→ killed
  • rebuild drops rows instead of copying them → killed
  • stale indexes left on the renamed table    → killed
  • scratch table left behind                  → killed
  • font_attachments migration removed         → killed
  • font_attachments migrated with DEFAULT 0   → killed
  • encoding relabel never run                 → killed
  • encoding relabel labelling image reviews   → killed
  • encoding relabel touching rows not in review → killed

The first initially SURVIVED, and the reason is worth keeping: the
rebuild recreates revert_points from the model, so it adds detached_at
too and the ADD COLUMN is currently unreachable. That makes it dead
code today and load-bearing the day the rebuild is deleted — which is
the intent, once every deployed install has started once.
test_the_column_migration_stands_on_its_own pins it against the shape
the world is in on that day.

Six more for the per-track reason labels, each run against the full
1532-test suite before its test existed, and all six survived:

  • per-track labels never run                 → killed
  • a font review's tracks labelled image      → killed
  • a null-reason review's tracks labelled styled → killed
  • a track labelled whatever its codec        → killed
  • the labels running before the encoding relabel → killed
  • the labels reaching rows not in review     → killed

A seventh is equivalent, checked rather than assumed: labelling a track that
already has a reason. It survives these tests too, because no writer
produces a row where that differs. Every track that already has a reason
either carries the label its review implies, or is a styled track on a
mixed image review, and the codec check skips that one first.

Five more for moving audio flags to one row per track. Each was run against
the full suite before the tests for it existed (1539 tests: the 1540 then,
less the test that pinned the old shape), and all five survived:

  • the audio rebuild never run                → killed
  • its uniqueness left on the file alone      → killed
  • its uniqueness dropped altogether          → killed
  • the rebuild repeated on every startup      → killed
  • the carried-over rows labelled "threshold" → killed

Three for releasing the files the undefined-audio threshold was holding,
each run against the full suite before the tests at the end of this file
existed, and all three survived:

  • the release never run                      → killed
  • it reaching subtitle reviews too           → killed
  • it re-stamping files already stamped       → killed

Three for re-keying the stored answers from stream numbers to descriptors,
each run against the full suite before the tests at the end of this file
existed, and all three survived:

  • the re-keying never run                    → killed
  • answers it could not match kept anyway     → killed
  • writing and logging on every startup       → killed
"""
import importlib
import json
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


# ── One subtitle flag per track ──────────────────────────────────────────────

LEGACY_SUBTITLE_FLAGS = """
CREATE TABLE subtitle_language_flags (
    id INTEGER NOT NULL PRIMARY KEY,
    file_id INTEGER NOT NULL,
    stream_index INTEGER NOT NULL,
    detected_language VARCHAR,
    created_at DATETIME,
    UNIQUE (file_id),
    FOREIGN KEY(file_id) REFERENCES media_files (id) ON DELETE CASCADE
)
"""


@pytest.fixture
def legacy_flags(tmp_path, monkeypatch):
    """A database whose subtitle flags are still one-per-file."""
    path = tmp_path / "remuxarr.db"
    conn = sqlite3.connect(path)
    conn.execute(LEGACY_SUBTITLE_FLAGS)
    conn.execute(
        "INSERT INTO subtitle_language_flags "
        "(file_id, stream_index, detected_language) VALUES (7, 2, 'und')")
    conn.commit()
    conn.close()

    session_mod = _point_the_engine_at(path, monkeypatch)
    session_mod.init_db()
    yield path
    _restore_the_engine(session_mod)


def test_a_second_track_of_the_same_file_can_be_flagged(legacy_flags):
    """
    The constraint that made the bug structural. One row per file meant a
    file with three undefined subtitles could only ever offer one of them
    for review, while extraction wrote a separate .srt for each — so the
    other two kept "und" in their filenames with no way to correct them.
    """
    conn = sqlite3.connect(legacy_flags)
    try:
        conn.execute(
            "INSERT INTO subtitle_language_flags "
            "(file_id, stream_index, detected_language) VALUES (7, 3, 'und')")
        conn.commit()
        count = conn.execute(
            "SELECT COUNT(*) FROM subtitle_language_flags "
            "WHERE file_id = 7").fetchone()[0]
    finally:
        conn.close()

    assert count == 2


def test_the_same_track_still_cannot_be_flagged_twice(legacy_flags):
    """
    Relaxing the constraint is not removing it. Without uniqueness per
    (file, stream) every rescan would add another row for the same track.
    """
    conn = sqlite3.connect(legacy_flags)
    try:
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                "INSERT INTO subtitle_language_flags "
                "(file_id, stream_index) VALUES (7, 2)")
    finally:
        conn.close()


def test_existing_flags_survive_the_rebuild(legacy_flags):
    """
    The table is recreated, not altered. Losing its rows would drop
    everything currently waiting in review.
    """
    conn = sqlite3.connect(legacy_flags)
    try:
        rows = list(conn.execute(
            "SELECT file_id, stream_index, detected_language "
            "FROM subtitle_language_flags"))
    finally:
        conn.close()

    assert rows == [(7, 2, "und")]


def test_the_rebuild_does_not_repeat_on_later_startups(legacy_flags,
                                                       monkeypatch, caplog):
    """
    Rebuilding a table means recreating and re-copying it. Done on every
    startup that is pure waste, and on a large review backlog it is waste
    that grows — and it is invisible, because the result is correct every
    time.
    """
    import logging

    session_mod = _point_the_engine_at(legacy_flags, monkeypatch)
    try:
        with caplog.at_level(logging.INFO):
            session_mod.init_db()
    finally:
        _restore_the_engine(session_mod)

    assert not [r for r in caplog.records
                if "subtitle language flag" in r.getMessage()], (
        "the table was rebuilt again on a database that was already migrated"
    )


# ── One audio flag per track ─────────────────────────────────────────────────

LEGACY_AUDIO_FLAGS = [
    """
    CREATE TABLE audio_language_flags (
        id INTEGER NOT NULL PRIMARY KEY,
        file_id INTEGER NOT NULL,
        stream_index INTEGER NOT NULL,
        detected_language VARCHAR,
        created_at DATETIME,
        UNIQUE (file_id),
        FOREIGN KEY(file_id) REFERENCES media_files (id) ON DELETE CASCADE
    )
    """,
    # The indexes the old model created, which the rebuild has to clear out
    # of the way before recreating them under the same names.
    "CREATE INDEX ix_audio_language_flags_id ON audio_language_flags (id)",
    "CREATE INDEX ix_audio_language_flags_detected_language "
    "ON audio_language_flags (detected_language)",
]


@pytest.fixture
def legacy_audio_flags(tmp_path, monkeypatch):
    """A database whose audio flags are still one-per-file, with no origin."""
    path = tmp_path / "remuxarr.db"
    conn = sqlite3.connect(path)
    for statement in LEGACY_AUDIO_FLAGS:
        conn.execute(statement)
    conn.execute(
        "INSERT INTO audio_language_flags "
        "(file_id, stream_index, detected_language) VALUES (7, 2, 'dut')")
    conn.commit()
    conn.close()

    session_mod = _point_the_engine_at(path, monkeypatch)
    session_mod.init_db()
    yield path
    _restore_the_engine(session_mod)


def test_a_second_audio_track_of_the_same_file_can_be_flagged(legacy_audio_flags):
    """
    Replaces a test that pinned the opposite, on the grounds that only one
    representative audio track was ever flagged. A file with several
    undefined audio tracks, often in different languages, needs each one
    answered on its own.
    """
    conn = sqlite3.connect(legacy_audio_flags)
    try:
        conn.execute(
            "INSERT INTO audio_language_flags "
            "(file_id, stream_index, detected_language) VALUES (7, 3, 'und')")
        conn.commit()
        count = conn.execute(
            "SELECT COUNT(*) FROM audio_language_flags "
            "WHERE file_id = 7").fetchone()[0]
    finally:
        conn.close()

    assert count == 2


def test_the_same_audio_track_still_cannot_be_flagged_twice(legacy_audio_flags):
    """Without uniqueness per (file, stream) every rescan would add another row."""
    conn = sqlite3.connect(legacy_audio_flags)
    try:
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                "INSERT INTO audio_language_flags "
                "(file_id, stream_index) VALUES (7, 2)")
    finally:
        conn.close()


def test_existing_audio_flags_survive_the_rebuild_as_mismatches(legacy_audio_flags):
    """
    Every row written before the origin column came from
    audio_language_mismatch — the threshold held files instead of flagging
    them — so "mismatch" is the true origin of all of them, and the one that
    keeps them under the switch they were confirmed through.
    """
    conn = sqlite3.connect(legacy_audio_flags)
    try:
        rows = list(conn.execute(
            "SELECT file_id, stream_index, detected_language, origin "
            "FROM audio_language_flags"))
    finally:
        conn.close()

    assert rows == [(7, 2, "dut", "mismatch")]


def test_the_audio_rebuild_does_not_repeat_on_later_startups(legacy_audio_flags,
                                                             monkeypatch, caplog):
    import logging

    session_mod = _point_the_engine_at(legacy_audio_flags, monkeypatch)
    try:
        with caplog.at_level(logging.INFO):
            session_mod.init_db()
    finally:
        _restore_the_engine(session_mod)

    assert not [r for r in caplog.records
                if "audio language flag" in r.getMessage()], (
        "the table was rebuilt again on a database that was already migrated"
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


# ── media_files.font_attachments ─────────────────────────────────────────────

@pytest.fixture
def uncounted(tmp_path, monkeypatch):
    """
    A database whose media_files predates font_attachments, holding one file.

    Built from the model with the one column dropped, rather than written
    out like LEGACY_REVERT_POINTS: the deployed shape is exactly that, and
    two dozen columns typed by hand would drift from it. The live-schema
    test above covers everything else about the table.
    """
    from app.database.models import Base

    path = tmp_path / "remuxarr.db"
    engine = create_engine(f"sqlite:///{path}")
    Base.metadata.tables["media_files"].create(engine)
    engine.dispose()

    conn = sqlite3.connect(path)
    conn.execute("ALTER TABLE media_files DROP COLUMN font_attachments")
    conn.execute(
        "INSERT INTO media_files (path, filename, directory, size, mtime, status) "
        "VALUES ('/m/Show.mkv', 'Show.mkv', '/m', 1, 1.0, 'processed')"
    )
    conn.commit()
    conn.close()

    session_mod = _point_the_engine_at(path, monkeypatch)
    session_mod.init_db()

    yield path

    _restore_the_engine(session_mod)


def test_existing_files_come_out_uncounted_not_font_free(uncounted):
    """
    Null is what tells the worker a file's fonts were never counted, so it
    counts them at pickup. A migration that filled in 0 would record every
    file already in the library as probed and font-free, and the worker
    would never look — the font gate would stay blind to exactly the files
    that were there before it could see.
    """
    conn = sqlite3.connect(uncounted)
    try:
        columns = {r[1] for r in conn.execute("PRAGMA table_info(media_files)")}
        value = conn.execute(
            "SELECT font_attachments FROM media_files WHERE path = '/m/Show.mkv'"
        ).fetchone()
    finally:
        conn.close()

    assert "font_attachments" in columns
    assert value == (None,)


# ── Subtitle-encoding reviews written unlabelled ─────────────────────────────

_OLD_REVIEWS = {
    # name: (status, review_reason, flagged codecs or None)
    "encoding":      ("manual_review", None, ["subrip"]),
    "image":         ("manual_review", None, ["hdmv_pgs_subtitle"]),
    "mixed":         ("manual_review", None, ["subrip", "dvd_subtitle"]),
    "threshold":     ("manual_review", None, None),
    "font":          ("manual_review", "font_attachments", ["ass"]),
    "not_in_review": ("pending",       None, ["subrip"]),
}


@pytest.fixture
def old_reviews(tmp_path, monkeypatch):
    """
    One row of every kind an existing install can hold, then a real startup.
    Returns each row's review_reason afterwards, by name.
    """
    from sqlalchemy.orm import Session

    from app.database.models import Base, MediaFile, QueueItem

    path = tmp_path / "remuxarr.db"
    engine = create_engine(f"sqlite:///{path}")
    Base.metadata.create_all(engine)
    ids = {}
    with Session(engine) as db:
        media = MediaFile(path="/m/Movie.mkv", filename="Movie.mkv",
                          directory="/m", size=1, mtime=1.0)
        db.add(media)
        db.flush()
        for name, (status, reason, codecs) in _OLD_REVIEWS.items():
            item = QueueItem(
                file_id=media.id, status=status, review_reason=reason,
                review_subtitles=(
                    json.dumps([{"stream_index": 2 + n, "codec": c}
                                for n, c in enumerate(codecs)])
                    if codecs is not None else None
                ),
            )
            db.add(item)
            db.flush()
            ids[item.id] = name
        db.commit()
    engine.dispose()

    session_mod = _point_the_engine_at(path, monkeypatch)
    session_mod.init_db()

    conn = sqlite3.connect(path)
    try:
        labels = {ids[i]: r for i, r in
                  conn.execute("SELECT id, review_reason FROM queue_items")}
    finally:
        conn.close()

    yield labels

    _restore_the_engine(session_mod)


def test_an_unlabelled_encoding_review_is_labelled_and_nothing_else_is(old_reviews):
    """
    Closes: the relabel never running, and it labelling image reviews.

    The worker wrote these unlabelled, which the image resolver reads as
    its own, so resolving subtitle items in bulk re-queued each one's
    failing extraction. They are told apart by what they flag: an image
    review flags only image-based codecs, an encoding review only text
    codecs. A row flagging any image codec is an image review, and a row
    flagging nothing is the undefined-audio threshold.
    """
    assert old_reviews["encoding"] == "subtitle_encoding"
    assert old_reviews["image"] is None
    assert old_reviews["mixed"] is None
    assert old_reviews["threshold"] is None
    assert old_reviews["font"] == "font_attachments"


def test_the_relabel_leaves_rows_that_are_no_longer_in_review(old_reviews):
    """
    Closes: the relabel reaching past the review tab.

    A pending row can still carry the review_subtitles of a review it has
    left. It is not in any bulk resolver's scope, so there is nothing to
    protect it from, and a label on it would say something untrue.
    """
    assert old_reviews["not_in_review"] is None


# ── Per-track reasons on reviews written before them ─────────────────────────

_UNREASONED_REVIEWS = {
    # name: (status, review_reason, flagged codecs)
    "image":           ("manual_review", "image_subtitles",   ["hdmv_pgs_subtitle"]),
    "font":            ("manual_review", "font_attachments",  ["ass"]),
    "encoding":        ("manual_review", "subtitle_encoding", ["subrip"]),
    "unlabelled":      ("manual_review", None,                ["dvd_subtitle"]),
    "unlabelled_text": ("manual_review", None,                ["subrip"]),
    "mixed":           ("manual_review", None,                ["subrip", "dvd_subtitle"]),
    "not_in_review":   ("pending",       "image_subtitles",   ["hdmv_pgs_subtitle"]),
}


@pytest.fixture
def unreasoned(tmp_path, monkeypatch):
    """
    Reviews written before flagged tracks carried a reason, then a real
    startup. Returns each row's per-track reasons afterwards, by name.
    """
    from sqlalchemy.orm import Session

    from app.database.models import Base, MediaFile, QueueItem

    path = tmp_path / "remuxarr.db"
    engine = create_engine(f"sqlite:///{path}")
    Base.metadata.create_all(engine)
    ids = {}
    with Session(engine) as db:
        media = MediaFile(path="/m/Show.mkv", filename="Show.mkv",
                          directory="/m", size=1, mtime=1.0)
        db.add(media)
        db.flush()
        for name, (status, reason, codecs) in _UNREASONED_REVIEWS.items():
            item = QueueItem(
                file_id=media.id, status=status, review_reason=reason,
                review_subtitles=json.dumps(
                    [{"stream_index": 2 + n, "codec": c}
                     for n, c in enumerate(codecs)]
                ),
            )
            db.add(item)
            db.flush()
            ids[item.id] = name
        db.commit()
    engine.dispose()

    session_mod = _point_the_engine_at(path, monkeypatch)
    session_mod.init_db()

    conn = sqlite3.connect(path)
    try:
        reasons = {
            ids[i]: [entry.get("reason") for entry in json.loads(flagged)]
            for i, flagged in conn.execute(
                "SELECT id, review_subtitles FROM queue_items")
        }
    finally:
        conn.close()

    yield reasons

    _restore_the_engine(session_mod)


def test_each_track_takes_the_reason_its_review_was_raised_for(unreasoned):
    """
    Closes: the labels never running, and a font or null-reason review
    mislabelled.

    Before one review could hold both subtitle gates' tracks, every review
    had one cause, so the item's reason is each track's. A null reason with
    a bitmap track is an image review — QueueItem.review_reason lists where
    those rows come from.
    """
    assert unreasoned["image"] == ["image"]
    assert unreasoned["font"] == ["styled"]
    assert unreasoned["encoding"] == ["encoding"]
    assert unreasoned["unlabelled"] == ["image"]


def test_an_unlabelled_encoding_review_is_named_before_its_tracks(unreasoned):
    """
    Closes: the labels running before the encoding relabel.

    A null reason on a text track is an encoding review, but only the
    encoding relabel says so. Labelling first reads the null as an image
    review, finds no bitmap to label, and the track stays unlabelled.
    """
    assert unreasoned["unlabelled_text"] == ["encoding"]


def test_a_track_its_review_could_not_have_flagged_is_left_unlabelled(unreasoned):
    """
    Closes: a track labelled whatever its codec.

    The image gate never flags a text track, so a null-reason review holding
    one alongside a bitmap is not something the labels can explain. Calling
    the subrip track an image would claim a text track has no text to
    extract.
    """
    assert unreasoned["mixed"] == [None, "image"]


def test_the_labels_leave_rows_that_are_no_longer_in_review(unreasoned):
    """
    Closes: the labels reaching past the review tab, the same boundary the
    encoding relabel keeps.
    """
    assert unreasoned["not_in_review"] == [None]


# ── Files the undefined-audio threshold was holding ──────────────────────────

@pytest.fixture
def held_files(tmp_path, monkeypatch):
    """
    A threshold hold and a subtitle review, then a real startup. Returns each
    file's scan stamp afterwards, by name.
    """
    from sqlalchemy.orm import Session

    from app.database.models import Base, MediaFile, QueueItem

    path = tmp_path / "remuxarr.db"
    engine = create_engine(f"sqlite:///{path}")
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        for name, flagged in (("threshold", None),
                              ("subtitles", '[{"stream_index": 2}]')):
            media = MediaFile(path=f"/m/{name}.mkv", filename=f"{name}.mkv",
                              directory="/m", size=1234, mtime=99.0)
            db.add(media)
            db.flush()
            db.add(QueueItem(file_id=media.id, status="manual_review",
                             review_subtitles=flagged))
        db.commit()
    engine.dispose()

    session_mod = _point_the_engine_at(path, monkeypatch)
    session_mod.init_db()

    conn = sqlite3.connect(path)
    try:
        stamps = {
            filename.removesuffix(".mkv"): (size, mtime)
            for filename, size, mtime in conn.execute(
                "SELECT filename, size, mtime FROM media_files")
        }
    finally:
        conn.close()

    yield stamps, path, session_mod

    _restore_the_engine(session_mod)


def test_a_file_held_by_the_threshold_is_stamped_for_a_rescan(held_files):
    """
    Closes: the release never running.

    The threshold no longer holds files, but the ones it held sit in review
    until something re-decides them, and a scan skips them while their size
    and mtime match. Clearing the stamp is what brings them back — and with
    them, their undefined tracks, in Audio Language Review.
    """
    stamps, _, _ = held_files

    assert stamps["threshold"] == (-1, -1.0)


def test_a_subtitle_review_is_left_where_it_is(held_files):
    """
    Closes: the release reaching past the threshold's own holds.

    A subtitle review still holds its file on purpose and has its own way
    out. Stamping it would re-probe every waiting review on the next scan.
    Its flagged tracks are what tell the two apart, the same test Approve
    relies on.
    """
    stamps, _, _ = held_files

    assert stamps["subtitles"] == (1234, 99.0)


def test_a_file_already_stamped_is_not_stamped_again(held_files, monkeypatch, caplog):
    """
    Closes: the release rewriting rows on every startup.

    The hold stays until a scan runs, so this runs again on the next start
    and every start after. Writing and logging each time would report work
    that had already been done, on a line a user reads as something new.
    """
    import logging

    _, path, _ = held_files
    session_mod = _point_the_engine_at(path, monkeypatch)
    try:
        with caplog.at_level(logging.INFO):
            session_mod.init_db()
    finally:
        _restore_the_engine(session_mod)

    assert not [r for r in caplog.records
                if "undefined-audio threshold" in r.getMessage()]


# ── Answers re-keyed from stream numbers to descriptors ──────────────────────

@pytest.fixture
def indexed_answers(tmp_path, monkeypatch):
    """
    A file whose stored answers are still keyed by stream_index, then a real
    startup. Returns the answers afterwards, by column.
    """
    from sqlalchemy.orm import Session

    from app.database.models import Base, MediaFile, Track

    path = tmp_path / "remuxarr.db"
    engine = create_engine(f"sqlite:///{path}")
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        media = MediaFile(path="/m/Show.mkv", filename="Show.mkv",
                          directory="/m", size=1, mtime=1.0,
                          subtitle_overrides=json.dumps({"2": "keep", "9": "remove"}),
                          audio_language_overrides=json.dumps({"1": "eng"}))
        db.add(media)
        db.flush()
        db.add(Track(file_id=media.id, stream_index=1, track_type="audio",
                     codec="aac", language="und"))
        db.add(Track(file_id=media.id, stream_index=2, track_type="subtitle",
                     codec="ass", language="eng", title="Signs"))
        db.commit()
    engine.dispose()

    session_mod = _point_the_engine_at(path, monkeypatch)
    session_mod.init_db()

    conn = sqlite3.connect(path)
    try:
        row = conn.execute(
            "SELECT subtitle_overrides, audio_language_overrides "
            "FROM media_files").fetchone()
    finally:
        conn.close()

    yield {"subtitle_overrides": json.loads(row[0]),
           "audio_language_overrides": json.loads(row[1])}, path, session_mod

    _restore_the_engine(session_mod)


def test_stored_answers_are_re_keyed_by_descriptor(indexed_answers):
    """
    Closes: the re-keying never running.

    Left as stream numbers, every answer a user has given is read against
    keys nothing matches, so each one is dropped and every file comes back
    to review asking what it already asked.
    """
    from app.core.scanner import descriptors_by_stream

    answers, _, _ = indexed_answers
    keys = descriptors_by_stream([
        {"stream_index": 1, "track_type": "audio", "codec": "aac"},
        {"stream_index": 2, "track_type": "subtitle", "codec": "ass",
         "title": "Signs"},
    ])

    assert answers["subtitle_overrides"] == {keys[2]: "keep"}
    assert answers["audio_language_overrides"] == {keys[1]: "eng"}


def test_an_answer_whose_stream_has_no_track_is_dropped(indexed_answers):
    """
    Closes: keeping answers that could not be matched.

    Stream 9 is not a track of this file, so nothing can be said about what
    that answer was for. Kept under its old key it would sit there forever,
    matching nothing; kept under some other track's key it would answer a
    question nobody asked.
    """
    answers, _, _ = indexed_answers

    assert len(answers["subtitle_overrides"]) == 1


def test_the_re_keying_does_not_repeat_on_later_startups(indexed_answers,
                                                         monkeypatch, caplog):
    """
    Closes: writing and logging on every startup.

    Descriptor keys are not stream numbers, so a second pass finds nothing
    to convert. Reporting work that was not done is a line a user reads as
    something new happening to their library.
    """
    import logging

    _, path, _ = indexed_answers
    session_mod = _point_the_engine_at(path, monkeypatch)
    try:
        with caplog.at_level(logging.INFO):
            session_mod.init_db()
    finally:
        _restore_the_engine(session_mod)

    assert not [r for r in caplog.records
                if "track answer" in r.getMessage()]
