"""
scanner.py deletion — removing a MediaFile and everything that references it.

_delete_media_file_and_related exists because the cascade everyone assumes is
there mostly isn't. tracks and queue_items (and queue_items' planned_actions)
do cascade via SQLAlchemy's cascade="all, delete-orphan". The other four —
Ac3ForgeJob, PlexAnalyzeBacklog, AudioLanguageFlag, SubtitleLanguageFlag —
have ondelete="CASCADE" on their foreign keys and that does nothing here,
because SQLite only enforces it when PRAGMA foreign_keys=ON is set
per-connection and this project never sets it.

Its docstring records the same mistake being made twice: the function was
written before PlexAnalyzeBacklog and AudioLanguageFlag existed and wasn't
updated when they were added, so both were silently orphaned in production.
SubtitleLanguageFlag was added alongside that comment specifically to avoid a
third repeat.

So the central test here is not "these seven tables get cleared" — that would
pass just as happily on the day someone adds an eighth table and forgets it.
test_every_table_referencing_media_files_is_cleared derives the list of
referencing tables from the model metadata at runtime and fails on any table
this function doesn't handle. It is designed to fail on a schema addition,
which is the only moment the historic bug was ever catchable.

Verified by mutation: 24 mutations, of which 21 are killed by at least one
test here. Deleting any of the four non-cascading tables' cleanup lines is
caught both by that table's own test and by the metadata-derived one.

The three survivors are equivalent mutants, confirmed against the models
rather than assumed. MediaFile.tracks and MediaFile.queue_items both declare
cascade="all, delete-orphan", so removing their explicit deletes changes
nothing — db.delete(media) reaches them anyway. (PlannedAction is NOT in that
category: the explicit bulk QueueItem delete bypasses ORM cascade, so its own
delete is load-bearing, and removing it does fail.) The third is
`if not scan_paths: return 0` in cleanup_deleted_files, a redundant early
exit — with no paths, the prefix filter matches nothing and the function
returns 0 regardless.

Two mutations initially survived for a real reason rather than an equivalent
one: dropping db.commit() from either caller. The in-memory single-session
fixture cannot see that, because uncommitted deletes are already invisible to
the session that made them. The db_factory fixture and the two commit tests
exist specifically to close that blind spot.
"""
import os

import pytest


# ── Harness ──────────────────────────────────────────────────────────────────

@pytest.fixture
def db():
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    from app.database.models import Base

    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)()


@pytest.fixture
def db_factory(tmp_path):
    """
    A file-backed database with a sessionmaker, so a SECOND session can be
    opened to check what was actually committed. The in-memory `db` fixture
    above cannot see the difference: uncommitted deletes are already invisible
    to the session that made them, so a missing db.commit() looks identical to
    a successful one until the session closes and the changes are lost.
    """
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    from app.database.models import Base

    engine = create_engine(f"sqlite:///{tmp_path / 'test.db'}")
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)


def _media(db, path="/media/tv/ep.mkv"):
    from app.database.models import MediaFile

    mf = MediaFile(path=path, filename=os.path.basename(path),
                   directory=os.path.dirname(path), size=100, mtime=1.0)
    db.add(mf)
    db.commit()
    return mf


def _fully_populate(db, media):
    """One row in every table that references media_files, plus a
    PlannedAction hanging off the queue item."""
    from app.database.models import (
        Ac3ForgeJob, AudioLanguageFlag, PlannedAction, PlexAnalyzeBacklog,
        QueueItem, RevertPoint, SubtitleLanguageFlag, Track,
    )

    qi = QueueItem(file_id=media.id, status="pending")
    db.add(qi)
    db.commit()

    db.add_all([
        PlannedAction(queue_item_id=qi.id, action_type="drop",
                      description="drop track 2"),
        Track(file_id=media.id, stream_index=0, track_type="video"),
        Ac3ForgeJob(file_id=media.id, status="pending"),
        PlexAnalyzeBacklog(file_id=media.id, expected_language="eng"),
        AudioLanguageFlag(file_id=media.id, stream_index=1,
                          detected_language="dut"),
        SubtitleLanguageFlag(file_id=media.id, stream_index=2,
                             detected_language="und"),
        RevertPoint(file_id=media.id,
                    sidecar_path="/recycle/1.remuxarr_revert",
                    manifest="{}",
                    original_path=media.path),
    ])
    db.commit()
    return qi


def _counts(db):
    from app.database.models import (
        Ac3ForgeJob, AudioLanguageFlag, MediaFile, PlannedAction,
        PlexAnalyzeBacklog, QueueItem, RevertPoint, SubtitleLanguageFlag, Track,
    )

    return {m.__name__: db.query(m).count() for m in (
        MediaFile, QueueItem, PlannedAction, Track, Ac3ForgeJob,
        PlexAnalyzeBacklog, AudioLanguageFlag, SubtitleLanguageFlag,
        RevertPoint,
    )}


# ── The cascade itself ───────────────────────────────────────────────────────

def test_every_table_referencing_media_files_is_cleared(db):
    """
    The regression test that actually generalises.

    Rather than listing the tables by hand, this reads every table with a
    foreign key to media_files straight out of the model metadata, seeds one
    row in each, and asserts all of them are gone. Adding a new referencing
    table without updating _delete_media_file_and_related fails this test at
    the moment the model is added — which is the only point at which the two
    historic misses (PlexAnalyzeBacklog, AudioLanguageFlag) were catchable.

    If this fails after a schema change, the fix is a new delete in
    _delete_media_file_and_related, not a change here.
    """
    from sqlalchemy import text

    from app.core.scanner import _delete_media_file_and_related
    from app.database.models import Base, MediaFile

    referencing = {
        table.name
        for table in Base.metadata.tables.values()
        for fk in table.foreign_keys
        if fk.column.table.name == "media_files"
    }
    assert referencing, "metadata introspection found nothing — test is broken"

    media = _media(db)
    _fully_populate(db, media)

    # Every referencing table must actually have been seeded, or a table
    # could pass by never having had a row in the first place.
    unseeded = [
        name for name in referencing
        if db.execute(text(f"SELECT COUNT(*) FROM {name}")).scalar() == 0
    ]
    assert not unseeded, (
        f"tables reference media_files but this test never seeds them: "
        f"{sorted(unseeded)} — add them to _fully_populate"
    )

    _delete_media_file_and_related(db, media)
    db.commit()

    leftovers = {
        name: db.execute(text(f"SELECT COUNT(*) FROM {name}")).scalar()
        for name in referencing
    }
    orphaned = {n: c for n, c in leftovers.items() if c}
    assert not orphaned, (
        f"orphaned rows left behind after deletion: {orphaned} — "
        f"_delete_media_file_and_related needs a delete for each"
    )
    assert db.query(MediaFile).count() == 0


def test_the_media_file_row_itself_is_removed(db):
    from app.core.scanner import _delete_media_file_and_related

    media = _media(db)
    _fully_populate(db, media)

    _delete_media_file_and_related(db, media)
    db.commit()

    assert _counts(db)["MediaFile"] == 0


@pytest.mark.parametrize("table", [
    "QueueItem", "PlannedAction", "Track", "Ac3ForgeJob",
    "PlexAnalyzeBacklog", "AudioLanguageFlag", "SubtitleLanguageFlag",
])
def test_each_related_table_is_cleared(db, table):
    """
    Named per-table so a failure says which one leaked, rather than only
    that something did. The four non-cascading ones are the point:
    ondelete="CASCADE" is inert without PRAGMA foreign_keys=ON.
    """
    from app.core.scanner import _delete_media_file_and_related

    media = _media(db)
    _fully_populate(db, media)
    assert _counts(db)[table] == 1, "seed did not create the row"

    _delete_media_file_and_related(db, media)
    db.commit()

    assert _counts(db)[table] == 0


def test_planned_actions_are_removed_via_their_queue_item(db):
    """
    PlannedAction has no file_id — it is reached through queue_items, so its
    delete has to run BEFORE the queue items it depends on are gone.
    """
    from app.core.scanner import _delete_media_file_and_related
    from app.database.models import PlannedAction

    media = _media(db)
    qi = _fully_populate(db, media)
    db.add(PlannedAction(queue_item_id=qi.id, action_type="convert",
                         description="to mkv"))
    db.commit()
    assert db.query(PlannedAction).count() == 2

    _delete_media_file_and_related(db, media)
    db.commit()

    assert db.query(PlannedAction).count() == 0


def test_another_files_rows_are_left_completely_alone(db):
    """
    Every delete is filtered by file_id. A missing filter would wipe the
    whole table on the first cleanup pass — and cleanup runs unattended on
    every scan.
    """
    from app.core.scanner import _delete_media_file_and_related

    doomed   = _media(db, "/media/tv/gone.mkv")
    survivor = _media(db, "/media/tv/keep.mkv")
    _fully_populate(db, doomed)
    _fully_populate(db, survivor)

    _delete_media_file_and_related(db, doomed)
    db.commit()

    for name, count in _counts(db).items():
        assert count == 1, f"{name}: deleting one file destroyed another's rows"


def test_deletion_does_not_commit_on_its_own(db):
    """
    The caller owns the transaction — cleanup_deleted_files batches many
    deletions into one commit. Committing here would make a partial pass
    permanent if a later file raised.
    """
    from app.core.scanner import _delete_media_file_and_related
    from app.database.models import MediaFile

    media = _media(db)
    _fully_populate(db, media)

    _delete_media_file_and_related(db, media)
    db.rollback()

    assert db.query(MediaFile).count() == 1, (
        "deletion committed itself — a rollback could not undo it"
    )


# ── cleanup_deleted_files ────────────────────────────────────────────────────

def test_files_still_on_disk_are_never_removed(db, tmp_path):
    from app.core.scanner import cleanup_deleted_files
    from app.database.models import MediaFile

    real = tmp_path / "ep.mkv"
    real.write_bytes(b"x")
    _media(db, str(real))

    assert cleanup_deleted_files(db, [str(tmp_path)]) == 0
    assert db.query(MediaFile).count() == 1


def test_files_gone_from_disk_are_removed(db, tmp_path):
    from app.core.scanner import cleanup_deleted_files
    from app.database.models import MediaFile

    media = _media(db, str(tmp_path / "gone.mkv"))
    _fully_populate(db, media)

    assert cleanup_deleted_files(db, [str(tmp_path)]) == 1
    assert db.query(MediaFile).count() == 0
    assert all(c == 0 for c in _counts(db).values())


def test_no_scan_paths_removes_nothing(db, tmp_path):
    """
    Fails closed. An empty scan_paths list means "not configured", and
    treating it as "match everything" would delete the entire library the
    first time settings were cleared.
    """
    from app.core.scanner import cleanup_deleted_files
    from app.database.models import MediaFile

    _media(db, str(tmp_path / "gone.mkv"))

    assert cleanup_deleted_files(db, []) == 0
    assert db.query(MediaFile).count() == 1


def test_files_outside_the_scan_paths_are_untouched(db, tmp_path):
    """
    Deliberate scoping — cleanup never reaches outside the configured
    library, even for a row whose file is genuinely gone.
    """
    from app.core.scanner import cleanup_deleted_files
    from app.database.models import MediaFile

    inside  = tmp_path / "lib"
    outside = tmp_path / "elsewhere"
    inside.mkdir()
    outside.mkdir()
    _media(db, str(outside / "gone.mkv"))

    assert cleanup_deleted_files(db, [str(inside)]) == 0
    assert db.query(MediaFile).count() == 1


def test_a_sibling_directory_sharing_a_name_prefix_is_not_matched(db, tmp_path):
    """
    Prefixes are normalised to end with a separator, so scan path "/media/tv"
    must not match "/media/tv2/...". Without it, configuring one library
    silently brings a differently-named sibling into scope for deletion.
    """
    from app.core.scanner import cleanup_deleted_files
    from app.database.models import MediaFile

    tv  = tmp_path / "tv"
    tv2 = tmp_path / "tv2"
    tv.mkdir()
    tv2.mkdir()
    _media(db, str(tv2 / "gone.mkv"))

    assert cleanup_deleted_files(db, [str(tv)]) == 0
    assert db.query(MediaFile).count() == 1


def test_a_file_with_a_running_job_is_left_for_the_worker(db, tmp_path):
    """
    Deleting mid-job produces confusing worker errors. The job fails
    naturally when it can't open the file, which is the cleaner failure.
    """
    from app.core.scanner import cleanup_deleted_files
    from app.database.models import MediaFile, QueueItem

    media = _media(db, str(tmp_path / "gone.mkv"))
    db.add(QueueItem(file_id=media.id, status="processing"))
    db.commit()

    assert cleanup_deleted_files(db, [str(tmp_path)]) == 0
    assert db.query(MediaFile).count() == 1


@pytest.mark.parametrize("status", ["pending", "failed", "success", "manual_review"])
def test_only_a_processing_job_defers_cleanup(db, tmp_path, status):
    """Any other status is finished business and must not block removal."""
    from app.core.scanner import cleanup_deleted_files
    from app.database.models import MediaFile, QueueItem

    media = _media(db, str(tmp_path / "gone.mkv"))
    db.add(QueueItem(file_id=media.id, status=status))
    db.commit()

    assert cleanup_deleted_files(db, [str(tmp_path)]) == 1
    assert db.query(MediaFile).count() == 0


def test_cleanup_reports_how_many_rows_it_removed(db, tmp_path):
    from app.core.scanner import cleanup_deleted_files

    for name in ("a.mkv", "b.mkv", "c.mkv"):
        _media(db, str(tmp_path / name))
    kept = tmp_path / "d.mkv"
    kept.write_bytes(b"x")
    _media(db, str(kept))

    assert cleanup_deleted_files(db, [str(tmp_path)]) == 3


def test_cleanup_commits_its_deletions(db_factory, tmp_path):
    """
    Checked from a second session, because the deleting session cannot tell
    the difference — uncommitted deletes are already invisible to it. Without
    the commit the rows come straight back when the session closes, and the
    next scan re-queues every file it just cleaned up.
    """
    from app.core.scanner import cleanup_deleted_files
    from app.database.models import MediaFile

    with db_factory() as seed:
        media = _media(seed, str(tmp_path / "gone.mkv"))
        _fully_populate(seed, media)

    with db_factory() as work:
        assert cleanup_deleted_files(work, [str(tmp_path)]) == 1

    with db_factory() as check:
        assert check.query(MediaFile).count() == 0, (
            "cleanup did not commit — the deletions were rolled back"
        )


def test_removing_orphans_commits_its_deletions(db_factory, tmp_path):
    """Same contract on the orphan-removal path, which is a user action."""
    from app.core.scanner import remove_orphaned_media_files
    from app.database.models import MediaFile

    with db_factory() as seed:
        media = _media(seed, str(tmp_path / "stray.mkv"))
        _fully_populate(seed, media)
        media_id = media.id

    with db_factory() as work:
        assert remove_orphaned_media_files(work, [media_id]) == 1

    with db_factory() as check:
        assert check.query(MediaFile).count() == 0, (
            "orphan removal did not commit — the deletions were rolled back"
        )


# ── find_orphaned_media_files / remove_orphaned_media_files ──────────────────

def test_rows_outside_the_configured_paths_are_reported_as_orphans(db, tmp_path):
    """
    The inverse of cleanup's scoping. Removing a scan path from settings
    leaves its rows permanently invisible to cleanup, which will never again
    consider a path outside the current configuration — this is how they are
    found again.
    """
    from app.core.scanner import find_orphaned_media_files

    inside  = tmp_path / "lib"
    outside = tmp_path / "old"
    inside.mkdir()
    outside.mkdir()
    _media(db, str(inside / "keep.mkv"))
    stray = _media(db, str(outside / "stray.mkv"))

    found = find_orphaned_media_files(db, [str(inside)])

    assert [m.id for m in found] == [stray.id]


def test_an_orphan_is_reported_even_if_the_file_still_exists(db, tmp_path):
    """
    Membership outside the configured library is the criterion here, not disk
    presence — that is what makes this distinct from cleanup_deleted_files.
    """
    from app.core.scanner import find_orphaned_media_files

    inside  = tmp_path / "lib"
    outside = tmp_path / "old"
    inside.mkdir()
    outside.mkdir()
    real = outside / "still-here.mkv"
    real.write_bytes(b"x")
    _media(db, str(real))

    assert len(find_orphaned_media_files(db, [str(inside)])) == 1


def test_with_no_scan_paths_configured_everything_is_an_orphan(db, tmp_path):
    """
    The mirror of cleanup's behaviour, and safe for the opposite reason: this
    function only reports, and removal is a separate explicit user action.
    """
    from app.core.scanner import find_orphaned_media_files

    _media(db, str(tmp_path / "a.mkv"))
    _media(db, str(tmp_path / "b.mkv"))

    assert len(find_orphaned_media_files(db, [])) == 2


def test_a_sibling_directory_sharing_a_prefix_is_not_reported_as_inside(db, tmp_path):
    """
    The separator-boundary guard on the orphan side. Scan path "/media/tv"
    must not be treated as covering "/media/tv2/...", or files in a
    differently-named sibling library look configured and are never reported
    as orphans — so a library removed from settings stays invisible forever,
    which is the exact condition this function exists to surface.

    cleanup_deleted_files has the identical idiom and is pinned by
    test_a_sibling_directory_sharing_a_name_prefix_is_not_matched above; this
    is the second copy. Found by an independent mutation audit (Phase 1),
    which had to skip it because the two copies made the anchor ambiguous.
    """
    from app.core.scanner import find_orphaned_media_files

    tv  = tmp_path / "tv"
    tv2 = tmp_path / "tv2"
    tv.mkdir()
    tv2.mkdir()
    _media(db, str(tv / "inside.mkv"))
    stray = _media(db, str(tv2 / "stray.mkv"))

    found = find_orphaned_media_files(db, [str(tv)])

    assert [m.id for m in found] == [stray.id], (
        "a file under a name-prefix sibling of the scan path was treated as "
        "inside the configured library"
    )


def test_removing_orphans_clears_their_related_rows_too(db, tmp_path):
    """Uses the same complete deletion helper, not a bare MediaFile delete."""
    from app.core.scanner import remove_orphaned_media_files

    media = _media(db, str(tmp_path / "stray.mkv"))
    _fully_populate(db, media)

    assert remove_orphaned_media_files(db, [media.id]) == 1
    assert all(c == 0 for c in _counts(db).values())


def test_removing_orphans_ignores_ids_that_no_longer_exist(db, tmp_path):
    """
    The id list comes from an earlier find call, so rows can disappear in
    between — a stale id must not abort the whole batch.
    """
    from app.core.scanner import remove_orphaned_media_files
    from app.database.models import MediaFile

    media = _media(db, str(tmp_path / "stray.mkv"))

    assert remove_orphaned_media_files(db, [999999, media.id, 888888]) == 1
    assert db.query(MediaFile).count() == 0


def test_removing_orphans_does_not_check_scan_paths(db, tmp_path):
    """
    Deliberately unscoped — the caller already decided. Re-checking here
    would make the function unable to remove the very rows it exists for,
    since they are by definition outside the configured library.
    """
    from app.core.scanner import remove_orphaned_media_files
    from app.database.models import MediaFile

    real = tmp_path / "still-here.mkv"
    real.write_bytes(b"x")
    media = _media(db, str(real))

    assert remove_orphaned_media_files(db, [media.id]) == 1
    assert db.query(MediaFile).count() == 0


# ── JSON override loading ────────────────────────────────────────────────────
#
# Found by an independent mutation audit (Phase 1). Dropping the int(k)
# conversion survived the entire 662-test suite, as did corrupting the
# degradation contract. One shared helper backs all three override features
# (subtitle, audio-language, subtitle-language), so one gap covered three.

def _media_with(db, **cols):
    from app.database.models import MediaFile

    mf = MediaFile(path="/media/tv/ep.mkv", filename="ep.mkv",
                   directory="/media/tv", size=1, mtime=1.0, **cols)
    db.add(mf)
    db.commit()
    return mf


def test_override_keys_are_converted_to_integers(db):
    """
    JSON object keys are ALWAYS strings; analyze_file looks these up by
    integer stream_index (subtitle_overrides.get(si) where si comes from
    track["stream_index"]). Drop the conversion and every lookup silently
    misses — no exception, no log line. Every decision a user made in manual
    review, Audio Language Review and Subtitle Language Review is discarded,
    and the file returns to manual review on the next scan.

    Asserting equality is sufficient: {2: "keep"} != {"2": "keep"}.

    Audit ref: SCN-05.
    """
    from app.core.scanner import _load_int_keyed_json_overrides

    media = _media_with(db, subtitle_overrides='{"2": "keep", "3": "drop"}')

    result = _load_int_keyed_json_overrides(media, "subtitle_overrides")

    assert result == {2: "keep", 3: "drop"}
    assert all(isinstance(k, int) for k in result), (
        f"override keys left as strings: {list(result)} — every stream_index "
        f"lookup in analyze_file will silently miss"
    )


@pytest.mark.parametrize("attr", [
    "subtitle_overrides",
    "audio_language_overrides",
    "subtitle_language_overrides",
])
def test_every_override_column_shares_the_conversion(db, attr):
    """All three features route through the one helper — none may drift."""
    from app.core.scanner import _load_int_keyed_json_overrides

    media = _media_with(db, **{attr: '{"5": "eng"}'})

    assert _load_int_keyed_json_overrides(media, attr) == {5: "eng"}


def test_corrupt_override_json_degrades_to_an_empty_dict(db):
    """
    The degradation contract: corrupt bookkeeping must not raise. This is
    called during scanning and job pickup, so an exception here would fail
    the file rather than simply ignoring an unreadable override.

    Audit ref: SCN-06.
    """
    from app.core.scanner import _load_int_keyed_json_overrides

    media = _media_with(db, subtitle_overrides="{not valid json")

    assert _load_int_keyed_json_overrides(media, "subtitle_overrides") == {}


@pytest.mark.parametrize("stored", [
    '["not", "a", "dict"]',      # valid JSON, wrong shape → .items() missing
    '{"notanint": "keep"}',      # valid dict, key not int-convertible
    '"a bare string"',
    "42",
])
def test_structurally_wrong_override_json_also_degrades(db, stored):
    """
    The except clause catches ValueError, AttributeError and TypeError
    specifically — each corresponds to one of these shapes, and all three
    have to be caught for the contract to hold.
    """
    from app.core.scanner import _load_int_keyed_json_overrides

    media = _media_with(db, subtitle_overrides=stored)

    assert _load_int_keyed_json_overrides(media, "subtitle_overrides") == {}


def test_an_empty_override_column_is_an_empty_dict(db):
    from app.core.scanner import _load_int_keyed_json_overrides

    assert _load_int_keyed_json_overrides(
        _media_with(db, subtitle_overrides=None), "subtitle_overrides") == {}
