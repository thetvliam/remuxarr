"""
Excluded files through a scan, a webhook and the single-file endpoint.

test_exclude_rules.py pins what the rules match. This file pins what each
way into Remuxarr does with a match: a scan passes over it and counts it, a
record already held for it is removed at the end of a completed scan the
way a file gone from disk is, and neither a webhook nor the single-file
endpoint queues it. These run the real scan_library, queue_single_file and
scan route against a file-backed database, with only the probe stubbed.

The removal runs whether or not auto cleanup is on. That setting exists
for a share that is not mounted, where a missing file proves nothing; an
excluded file is one the user asked to have left alone.

Confirmed unprotected before this file and test_exclude_rules.py were
written: eighteen mutations, each run against the whole 1768-test suite
(the toast one against the frontend suite), all eighteen survived. The
switch ignored; an extras folder's no-video-subfolder test dropped; its
inside-a-movie-or-show test dropped; season folders not recognised; name
endings matched with case; Plex Versions only as the direct folder; the
library path's own folders tested by name patterns; path patterns not
covering a folder's contents; brackets in a pattern read as a character
class; the walk counting an excluded file and processing it anyway; the
progress total including excluded files; the removal gated on auto
cleanup; the removal taking a record whose job is running; the removal on
a cancelled scan; queue_single_file ignoring the rules; the single-file
endpoint not checking them; scan_completed without the count; and the
toast without it. All eighteen are killed.

The list of excluded files (GET /api/scan/excluded, over
scanner.find_excluded_files) came later, as new code that nothing called,
so every mutant of it survived by construction. Two were run against the
unchanged 1842-test suite only to show the harness reached it, and both
survived: the walk stopping after the first library, and the list left
unsorted. Eleven were run against the whole suite once the tests below
existed: each file given the first file's reason; the extras switch forced
on; the patterns ignored; the first library only; files nothing excludes
listed too; the 400 for no libraries removed; a plain os.walk in place of
the scan's own walk, which enters hidden folders; the list unsorted; a
file under two overlapping libraries listed twice; the folder given as
the filename; and the route served at another path. All eleven are
killed, each by the test written for it.

Always Include (include_patterns) came after that, with its rule tests in
test_exclude_rules.py and its flow tests here. Against the unchanged
1850-test suite: the include list ignored and its extension handling
removed, run only to show the harness reached new code, both survived; the
setting's default, its accepted-key entry and its schema entry each
removed, all three already killed by test_settings_schema.py, so not
claimed here. Moving the pattern loop into exclude._matches touched code
earlier mutants aimed at: the folder-contents mutant from the record above
was re-aimed there and is still killed by the existing tests, and a new one,
a path pattern applied to a file under no library path, survived them,
because their pattern never lined up with the start of the file's absolute
path. An absolute-path case was added to that test. Nine run against the
whole suite once the tests existed: the include list ignored; it beating
only the extras rule; only patterns; the extension handling removed; any
dotted ending accepted in place of a media extension; from_settings not
reading the setting; the schema entry moved away from Exclude Patterns;
that path-pattern guard removed; and (against the frontend suite) the
excluded-files list left under Exclude Patterns. All nine are killed, each
by the test written for it.

The list's include_as field (ExcludeRules.entry_for), which the Include
button adds to Always Include, came last. Two tests here compared whole
items, so they could not run against code that adds a field; for the
baseline both were deselected by node id, against which the entry written
as an absolute path survived as a harness check, and both then gained the
field. Four run against the whole suite once the tests existed: the entry
measured from the shallowest library path, written as the absolute path,
written without its leading "/", and the field left out. All four are
killed, each by the test written for it.

The reasons were then made specific: each starts with the label of the
setting responsible, and a name rule quotes the ending it matched in the
file's own case. Four tests asserted the old wording and were updated; with
those four deselected by node id, against the unchanged suite, the ending
reported lower-cased, the Exclude Patterns label swapped for the other, and
the Skip Trailers and Extras label renamed in the schema all survived. Eight
run against the whole suite once the Reasons tests in test_exclude_rules.py
existed: the ending lower-cased; the first ending in the list reported
instead of the one that matched; a file named only "Sample" reported as an
ending; each label swapped for the other; the Plex Versions reason and the
extras-folder reason each without its label; and the schema label renamed.
All eight are killed, each by the test written for it.

When the walk's visited-folder set was shared across library paths (see
test_scan_library.py), the list's own de-duplication by path could no
longer change anything, since no folder is walked twice, and was removed;
the mutant that dropped it is retired with it. The overlapping-libraries
test above now pins the shared walk: each library path given its own set in
the list was already killed by it and by the include_as round trip, and so
was removing the skip of an already-walked library path. The plain os.walk
mutant from the list's first record, re-aimed at the changed line, is still
killed. A symlinked library was added, where the two routes give the same
file different paths.
"""
import asyncio
import json
import os
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

import app.api.routes.scan as scan_routes
import app.database.session as session_module
from app.core import scanner
from app.database.models import (AppSetting, AudioLanguageFlag, Base,
                                 MediaFile, QueueItem, RevertPoint)


def _probe_mkv():
    """An MKV that needs a conversion under the defaults."""
    return {"format": {"format_name": "matroska,webm", "duration": "1.0"},
            "streams": [
                {"index": 0, "codec_type": "video", "codec_name": "h264"},
                {"index": 1, "codec_type": "audio", "codec_name": "aac",
                 "channels": 2, "tags": {"language": "eng"},
                 "disposition": {"default": 1}},
            ]}


@pytest.fixture
def lib(tmp_path, monkeypatch):
    engine = create_engine(f"sqlite:///{tmp_path / 'remuxarr.db'}",
                           connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine)
    monkeypatch.setattr(session_module, "SessionLocal", factory)
    monkeypatch.setattr(scan_routes, "SessionLocal", factory)
    probed = []

    def probe(path, *_a, **_kw):
        probed.append(path)
        return _probe_mkv()

    monkeypatch.setattr(scanner, "probe_file", probe)

    root = tmp_path / "movies"
    folder = root / "Avatar (2009)"
    (folder / "Trailers").mkdir(parents=True)
    paths = SimpleNamespace(
        movie=folder / "Avatar (2009).mkv",
        trailer=folder / "Trailers" / "Teaser.mkv",
        featurette=folder / "Avatar (2009)-featurette.mkv",
    )
    for p in vars(paths).values():
        p.write_bytes(b"\0" * 64)

    def settings(**values):
        with factory() as db:
            for key, value in values.items():
                db.merge(AppSetting(key=key, value=json.dumps(value)))
            db.commit()

    settings(scan_paths=[str(root)], dry_run_mode=False)

    def scan(**kwargs):
        with factory() as db:
            return scanner.scan_library(db, [str(root)], force_probe=True, **kwargs)

    def records():
        with factory() as db:
            return sorted(os.path.basename(m.path) for m in db.query(MediaFile))

    def media_id(path):
        with factory() as db:
            return db.query(MediaFile).filter(MediaFile.path == str(path)).one().id

    yield SimpleNamespace(Session=factory, root=root, paths=paths, probed=probed,
                          settings=settings, scan=scan, records=records,
                          media_id=media_id)
    engine.dispose()


# ── A scan ───────────────────────────────────────────────────────────────────

def test_a_scan_passes_over_excluded_files_and_counts_them(lib):
    totals = []
    stats = lib.scan(progress_callback=lambda scanned, total: totals.append(total))

    assert lib.probed == [str(lib.paths.movie)]
    assert lib.records() == ["Avatar (2009).mkv"]
    assert (stats.total, stats.excluded, stats.queued) == (1, 2, 1)
    assert totals == [1]


def test_the_scan_summary_carries_the_count(lib, monkeypatch):
    sent = []
    monkeypatch.setattr(scan_routes, "broadcast_threadsafe",
                        lambda data, _loop: sent.append(data))

    scan_routes._run_scan([str(lib.root)], True, object())

    completed = [m for m in sent if m.get("event") == "scan_completed"]
    assert len(completed) == 1
    assert completed[0]["excluded"] == 2


# ── Records already held ─────────────────────────────────────────────────────

@pytest.fixture
def held(lib):
    """All three files recorded and queued before the switch is turned on,
    the trailer with a language review and a revert point."""
    lib.settings(skip_extras=False)
    lib.scan()
    assert len(lib.records()) == 3
    trailer_id = lib.media_id(lib.paths.trailer)
    with lib.Session() as db:
        db.add(AudioLanguageFlag(file_id=trailer_id, stream_index=1,
                                 detected_language="und"))
        db.add(RevertPoint(file_id=trailer_id, sidecar_path="/recycle/x.mkv",
                           original_path=str(lib.paths.trailer), manifest="{}"))
        db.commit()
    lib.settings(skip_extras=True)
    return trailer_id


def test_records_of_newly_excluded_files_are_removed_at_the_next_scan(lib, held):
    stats = lib.scan()

    assert lib.records() == ["Avatar (2009).mkv"]
    assert stats.removed == 2
    with lib.Session() as db:
        assert db.query(QueueItem).filter(QueueItem.file_id == held).count() == 0
        assert db.query(AudioLanguageFlag).count() == 0
        point = db.query(RevertPoint).one()
        assert (point.file_id, point.detached_at is not None) == (None, True)
    assert lib.paths.trailer.exists() and lib.paths.featurette.exists()


def test_they_are_removed_with_auto_cleanup_off(lib, held):
    lib.settings(auto_cleanup_on_scan=False)

    lib.scan()

    assert lib.records() == ["Avatar (2009).mkv"]


def test_a_record_whose_job_is_running_is_left_for_a_later_scan(lib, held):
    with lib.Session() as db:
        db.query(QueueItem).filter(QueueItem.file_id == held).update(
            {"status": "processing"})
        db.commit()

    lib.scan()

    assert lib.records() == ["Avatar (2009).mkv", "Teaser.mkv"]


def test_a_cancelled_scan_removes_nothing(lib, held):
    lib.scan(cancel_check=lambda: True)

    assert len(lib.records()) == 3


# ── A webhook and the single-file endpoint ───────────────────────────────────

def test_a_webhook_for_an_excluded_file_is_not_queued(lib):
    with lib.Session() as db:
        item = scanner.queue_single_file(db, str(lib.paths.trailer),
                                         sonarr_series_id=7)

    assert item is None
    assert lib.records() == []
    assert lib.probed == []


def test_the_single_file_endpoint_says_it_is_excluded(lib):
    body = scan_routes.FileScanRequest(path=str(lib.paths.trailer))

    answer = asyncio.run(scan_routes.scan_file(body))

    assert answer["queued"] is False
    assert answer["reason"].startswith("Excluded by your exclude settings")
    assert lib.records() == []


# ── Listing them ─────────────────────────────────────────────────────────────
#
# A scan only counts excluded files and nothing about them is stored, so
# GET /api/scan/excluded walks the libraries again to say which they are.
# The fixture's two excluded files are excluded for different reasons, so
# a reason that belongs to one file shown against both is visible.

def _listed(lib):
    with lib.Session() as db:
        return scan_routes.list_excluded(db=db)


def _paths(answer):
    return [item["path"] for item in answer["items"]]


def test_each_excluded_file_is_listed_with_its_own_reason(lib):
    answer = _listed(lib)

    assert answer == {"total": 2, "items": [
        {"path": str(lib.paths.featurette),
         "filename": "Avatar (2009)-featurette.mkv",
         "reason": 'Skip Trailers and Extras: name ends in "-featurette"',
         "include_as": "/Avatar (2009)/Avatar (2009)-featurette.mkv"},
        {"path": str(lib.paths.trailer),
         "filename": "Teaser.mkv",
         "reason": "Skip Trailers and Extras: inside an extras folder (Trailers)",
         "include_as": "/Avatar (2009)/Trailers/Teaser.mkv"},
    ]}


def test_the_list_is_the_files_a_scan_passes_over(lib):
    """Including one a pattern matches, so both kinds of rule are compared."""
    three_d = lib.paths.movie.parent / "Avatar (2009) - 3D.mkv"
    three_d.write_bytes(b"\0" * 64)
    lib.settings(exclude_patterns=["* - 3D.mkv"])

    listed = _paths(_listed(lib))
    stats = lib.scan()

    on_disk = {str(p) for p in (*vars(lib.paths).values(), three_d)}
    assert set(listed) == on_disk - set(lib.probed)
    assert len(listed) == stats.excluded == 3


def test_the_list_follows_both_settings(lib):
    """With the extras switch off, only the pattern's match is listed, under
    the pattern's name."""
    lib.settings(skip_extras=False, exclude_patterns=["*-featurette.mkv"])

    answer = _listed(lib)

    assert answer["items"] == [
        {"path": str(lib.paths.featurette),
         "filename": "Avatar (2009)-featurette.mkv",
         "reason": "Exclude Patterns: '*-featurette.mkv'",
         "include_as": "/Avatar (2009)/Avatar (2009)-featurette.mkv"},
    ]


def test_every_library_is_listed_in_path_order(lib):
    """The second library is named first in the settings' list but sorts
    first by path, so walk order and path order disagree."""
    docs = lib.root.parent / "documentaries"
    film = docs / "Planet (2020)"
    (film / "Trailers").mkdir(parents=True)
    (film / "Planet (2020).mkv").write_bytes(b"\0" * 64)
    (film / "Trailers" / "Teaser.mkv").write_bytes(b"\0" * 64)
    lib.settings(scan_paths=[str(lib.root), str(docs)])

    assert _paths(_listed(lib)) == [
        str(film / "Trailers" / "Teaser.mkv"),
        str(lib.paths.featurette),
        str(lib.paths.trailer),
    ]


def test_a_file_under_two_overlapping_libraries_is_listed_once(lib):
    lib.settings(scan_paths=[str(lib.root), str(lib.paths.movie.parent)])

    assert _paths(_listed(lib)) == [str(lib.paths.featurette),
                                    str(lib.paths.trailer)]


def test_a_folder_a_symlink_brings_in_from_another_library_is_listed_once(lib):
    """The overlapping-libraries case above, reached through a symlink, where
    the two routes give the same file two different paths."""
    other = lib.root.parent / "elsewhere"
    other.mkdir()
    os.symlink(lib.paths.movie.parent, other / "Avatar (2009)")
    lib.settings(scan_paths=[str(lib.root), str(other)])

    assert _paths(_listed(lib)) == [str(lib.paths.featurette),
                                    str(lib.paths.trailer)]


def test_a_hidden_folder_is_not_walked_here_either(lib):
    """A scan never enters a folder whose name starts with a dot, so a file
    in one is not excluded by anything: it is never reached at all."""
    hidden = lib.paths.movie.parent / ".grab" / "Avatar (2009)-trailer.mkv"
    hidden.parent.mkdir()
    hidden.write_bytes(b"\0" * 64)

    listed = _paths(_listed(lib))
    lib.scan()

    assert str(hidden) not in listed
    assert str(hidden) not in lib.probed


def test_no_library_paths_is_refused(lib):
    """As GET /orphaned does: with no libraries there is nothing to list."""
    lib.settings(scan_paths=[])

    with pytest.raises(HTTPException) as refused:
        _listed(lib)

    assert refused.value.status_code == 400


def test_the_list_is_served_where_the_settings_page_will_ask(lib):
    """
    Through the scan router, so its prefix and this route's path are what
    is tested. A bare FastAPI app rather than app.main: importing app.main
    here, under the fixture's patched SessionLocal, would bind that factory
    into the modules app.main imports first (see test_clear_database.py).
    """
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    def db():
        session = lib.Session()
        try:
            yield session
        finally:
            session.close()

    app = FastAPI()
    app.include_router(scan_routes.router)
    app.dependency_overrides[scan_routes.get_db] = db

    response = TestClient(app).get("/api/scan/excluded")

    assert response.status_code == 200
    assert response.json()["total"] == 2


def test_every_listed_entry_brings_its_own_file_back(lib):
    """
    Each item's include_as is what the list's Include button puts in Always
    Include, so read back by the rules it has to match the file it names.
    Two libraries, one of them inside the other, and a file at a library's
    top level: an entry measured from the wrong library path, or written as
    the absolute path, would leave its file on the list.
    """
    docs = lib.root.parent / "documentaries"
    docs.mkdir()
    (docs / "Planet (2020)-trailer.mkv").write_bytes(b"\0" * 64)
    inner = lib.paths.movie.parent
    lib.settings(scan_paths=[str(lib.root), str(inner), str(docs)])

    items = _listed(lib)["items"]
    # In path order, so the documentaries library comes first. The other two
    # are measured from the inner library path, the deeper of the two.
    assert [item["include_as"] for item in items] == [
        "/Planet (2020)-trailer.mkv",
        "/Avatar (2009)-featurette.mkv",
        "/Trailers/Teaser.mkv",
    ]

    lib.settings(include_patterns=[item["include_as"] for item in items])

    assert _listed(lib) == {"total": 0, "items": []}


def test_an_entry_for_a_file_at_a_librarys_top_level_covers_only_that_file(lib):
    """Starting with "/" makes it a path from the library path. A bare name
    would be a name pattern, matching the same name in any folder."""
    top = lib.root / "Teaser-trailer.mkv"
    top.write_bytes(b"\0" * 64)
    deeper = lib.paths.movie.parent / "Teaser-trailer.mkv"
    deeper.write_bytes(b"\0" * 64)
    entry = next(item["include_as"] for item in _listed(lib)["items"]
                 if item["path"] == str(top))

    lib.settings(include_patterns=[entry])

    assert entry == "/Teaser-trailer.mkv"
    listed = _paths(_listed(lib))
    assert str(top) not in listed
    assert str(deeper) in listed


# ── Always Include ───────────────────────────────────────────────────────────
#

#
# The rule itself is in test_exclude_rules.py. These follow an included file
# through each way in, with the setting read from the database as it is in
# production, so the setting reaching the rules is tested too.

def test_a_scan_probes_and_queues_an_included_file(lib):
    lib.settings(include_patterns=["Teaser.mkv"])

    stats = lib.scan()

    assert str(lib.paths.trailer) in lib.probed
    assert lib.records() == ["Avatar (2009).mkv", "Teaser.mkv"]
    assert stats.excluded == 1


def test_an_included_files_record_survives_the_next_scan(lib, held):
    lib.settings(include_patterns=["Teaser.mkv"])

    lib.scan()

    assert lib.records() == ["Avatar (2009).mkv", "Teaser.mkv"]


def test_a_webhook_queues_an_included_file(lib):
    lib.settings(include_patterns=["Teaser.mkv"])

    with lib.Session() as db:
        item = scanner.queue_single_file(db, str(lib.paths.trailer))

    assert item is not None
    assert lib.records() == ["Teaser.mkv"]


def test_the_list_leaves_out_an_included_file(lib):
    lib.settings(include_patterns=["Teaser.mkv"])

    assert _paths(_listed(lib)) == [str(lib.paths.featurette)]


# ── Defaults ─────────────────────────────────────────────────────────────────

def test_an_upgrading_install_gets_the_switch_on(lib):
    """_seed_defaults writes a missing key with its default."""
    with lib.Session() as db:
        assert db.get(AppSetting, "skip_extras") is None
        session_module._seed_defaults(db)
        assert json.loads(db.get(AppSetting, "skip_extras").value) is True
        assert json.loads(db.get(AppSetting, "exclude_patterns").value) == []
        assert json.loads(db.get(AppSetting, "include_patterns").value) == []


def test_always_include_sits_directly_under_exclude_patterns():
    """The settings page renders the schema in order, and the excluded-files
    list after Always Include, so this order puts the two settings that
    decide the list directly above it."""
    from app.api.routes.settings import SETTINGS_SCHEMA

    keys = [field["key"] for field in SETTINGS_SCHEMA]
    assert keys[keys.index("exclude_patterns") + 1] == "include_patterns"
