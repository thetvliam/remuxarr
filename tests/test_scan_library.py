"""
scan_library: the walk, the cancel contract, and the cleanup pass
=================================================================

The function every scan goes through — manual, scheduled and startup — and
none of its 83 statements had ever run in a test. `test_scan_and_cancellation.py`
reads as though it covers this and does not: it pins three request-lifecycle
regressions in the route (offloading to the executor, cancelled-item
timestamps, the delta sentinels an abort failed to reset). Nothing called
`scan_library` at all. Its per-file worker `_process_file` is well covered;
the orchestration around it was not.

The line that makes this worth a file of its own is the cleanup pass:

    stats.removed = cleanup_deleted_files(db, [p for p in paths if os.path.isdir(p)])

`cleanup_deleted_files` does not check that the directories it is given
still exist. It takes the prefixes handed to it, finds every MediaFile
underneath them, and deletes any whose file `os.path.exists` says is gone.
That comprehension is the only thing standing between an unmounted share and
a wipe, and an unmounted share is ordinary: an array still spinning up, a
remote mount that has not come back, and a scheduler firing scans on a timer
regardless. Widen it and one scan removes every MediaFile row under the
missing path along with its queue items, planned actions, forge jobs, Plex
backlog rows and both language-flag tables. RevertPoints are detached rather
than deleted, so undo survives — every language override the user set by
hand does not. Nothing raises. The scan logs a cheerful removed=N and
returns.

The two tests covering that pull in opposite directions on purpose. One says
a row under an unmounted path must survive; the other says a row whose file
is genuinely gone must still be removed. Either alone can be satisfied by
breaking the other — "never delete anything" passes the first, and the
symptom of that is deleted files that never leave the UI.

Eighteen mutants, all confirmed surviving the full suite before this file
was written:

  cleanup     the path filter widened to every path given        killed
              the cleanup call replaced by removed = 0           killed
              the auto_cleanup_on_scan setting ignored           killed
              the pass no longer skipped on cancellation         killed
  cancelling  cancel_check's answer discarded                    killed
              no break out of the file loop                      killed
              no break out of the directory walk                 ALIVE
              no break between scan paths                        ALIVE
              stats.cancelled hardcoded False                    killed
  the walk    a missing path breaking instead of continuing      killed
              the pre-count skipping real directories            killed
              the pre-count guard removed entirely               ALIVE
              the is_media_file filter removed                   killed
  per file    the try/except around _process_file removed        killed
              stats.errors no longer incremented                 killed
              force_probe replaced by a literal False            killed
              the time.sleep(0) yield removed                    ALIVE
  progress    the total reported as the running count            killed

Each test names the one it closes. The four left alive, with reasoning
rather than a quiet omission:

  * The breaks out of the directory walk and out of the path loop.
    Cancellation is guarded four times over in this function, nested, and
    only the innermost — the break out of the file loop, killed above —
    changes any outcome. Remove either outer one and control still reaches
    the file loop, whose break fires on its first iteration, so nothing is
    processed either way. What is lost is iterations, not results: one
    directory listing per remaining directory or path. Both were mutated
    separately rather than assumed alike, and a test could catch them by
    asserting on what the walker was handed, but that pins an internal call
    where the outcome is already pinned.
  * Removing the pre-count's `if not os.path.isdir(scan_path): continue`.
    `_walk_media_dirs` on a path that is not a directory yields nothing at
    all (verified), so the guard saves a call and changes no count. The
    second copy of that guard, in the walk itself, is a different matter
    and is killed above — the two are only superficially the same line.
  * Removing `time.sleep(0)` at the end of each file. It exists to let HTTP
    handler threads run between files; nothing a test can assert on
    distinguishes it from its absence in-process, and a test that timed
    thread interleaving would fail on a loaded CI runner for reasons that
    have nothing to do with the scanner.

The cleanup pass is never substituted: those tests use a real settings
read, a real walk, the real cleanup_deleted_files and real rows, because
that is where the consequence lives. `_process_file` is substituted
throughout, since it shells out to ffprobe and has its own coverage.

Run from the project root:
    pytest tests/test_scan_library.py -v
"""
import os

import pytest

from app.core.scanner import scan_library


@pytest.fixture
def db():
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    from app.database.models import Base

    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)()


def _setting(db, key, value):
    """A real AppSetting row, so the real get_app_settings reads it."""
    from app.database.models import AppSetting

    db.add(AppSetting(key=key, value=value))
    db.commit()


def _media(db, path):
    from app.database.models import MediaFile

    mf = MediaFile(path=path, filename=os.path.basename(path),
                   directory=os.path.dirname(path), size=100, mtime=1.0)
    db.add(mf)
    db.commit()
    return mf


def _media_files(db):
    from app.database.models import MediaFile

    return {m.path for m in db.query(MediaFile).all()}


def _record_processed(monkeypatch):
    """
    Substitute _process_file and record what it was handed.

    The real one shells out to ffprobe and is covered elsewhere; what is
    being checked here is the orchestration around it. The parameter names
    mirror the real signature so a change to it fails here rather than
    silently recording the wrong argument.
    """
    from app.core import scanner as sc

    calls: list[dict] = []

    def fake(db, path, app_cfg, force_probe, dry_run, stats,
             sonarr_series_id=None, radarr_movie_id=None):
        calls.append({"path": path, "force_probe": force_probe,
                      "dry_run": dry_run})

    monkeypatch.setattr(sc, "_process_file", fake)
    return calls


def _library(tmp_path, name, *filenames):
    d = tmp_path / name
    d.mkdir()
    for f in filenames:
        (d / f).write_text("not really a video")
    return str(d)


# ── The cleanup pass ──────────────────────────────────────────────────────────

def test_a_scan_path_that_is_not_mounted_keeps_its_library(db, tmp_path):
    """
    Closes: the cleanup filter widened from the paths that are directories
    to every path configured.

    The second library here is configured but not mounted, which is what a
    spun-down array or a share that has not come back looks like from
    inside the container. Its rows must be exactly as they were.

    The queue item is in the assertion deliberately: MediaFile surviving is
    not the whole claim, since the delete takes every related row with it,
    and history is not something a rescan can rebuild.
    """
    from app.database.models import QueueItem

    mounted = _library(tmp_path, "tv")
    unmounted = str(tmp_path / "movies")          # never created
    assert not os.path.isdir(unmounted)

    stranded = _media(db, os.path.join(unmounted, "Film.mkv"))
    db.add(QueueItem(file_id=stranded.id, status="pending"))
    db.commit()

    stats = scan_library(db, [mounted, unmounted])

    assert _media_files(db) == {os.path.join(unmounted, "Film.mkv")}
    assert db.query(QueueItem).count() == 1, "the row's history went with it"
    assert stats.removed == 0


def test_a_file_that_is_really_gone_is_still_removed(db, tmp_path,
                                                    monkeypatch):
    """
    Closes: the cleanup call replaced by removed = 0.

    The other half of the test above. Without this one, "never delete
    anything" passes that test, and the symptom of that is a library where
    files deleted on disk never leave the UI.
    """
    _record_processed(monkeypatch)
    mounted = _library(tmp_path, "tv")
    _media(db, os.path.join(mounted, "Deleted.mkv"))
    kept = _library(tmp_path, "kept", "Here.mkv")
    _media(db, os.path.join(kept, "Here.mkv"))

    stats = scan_library(db, [mounted, kept])

    assert _media_files(db) == {os.path.join(kept, "Here.mkv")}
    assert stats.removed == 1


def test_cleanup_is_skipped_when_the_setting_is_off(db, tmp_path):
    """
    Closes: the auto_cleanup_on_scan check dropped, so the pass runs
    whatever the user set. Someone who turned it off did so because their
    library lives on storage that goes away, which is the case where
    running it anyway costs the most.
    """
    _setting(db, "auto_cleanup_on_scan", "false")
    mounted = _library(tmp_path, "tv")
    _media(db, os.path.join(mounted, "Deleted.mkv"))

    stats = scan_library(db, [mounted])

    assert _media_files(db) == {os.path.join(mounted, "Deleted.mkv")}
    assert stats.removed == 0


def test_a_cancelled_scan_does_not_run_the_cleanup_pass(db, tmp_path,
                                                        monkeypatch):
    """
    Closes: the cleanup pass no longer skipped on cancellation.

    Not a correctness argument — the pass checks each file's existence
    directly and would be right either way. It is that the user asked it to
    stop, and a library-wide delete is not what stopping looks like.
    """
    _record_processed(monkeypatch)
    mounted = _library(tmp_path, "tv", "One.mkv")
    _media(db, os.path.join(mounted, "Deleted.mkv"))

    stats = scan_library(db, [mounted], cancel_check=lambda: True)

    assert _media_files(db) == {os.path.join(mounted, "Deleted.mkv")}
    assert stats.removed == 0


# ── Cancelling ────────────────────────────────────────────────────────────────

def test_cancelling_stops_after_the_current_file(db, tmp_path, monkeypatch):
    """
    Closes: cancel_check's answer discarded, and the break out of the file
    loop.

    Cancellation is guarded four times over, nested, and the four were
    mutated separately rather than treated as one. Only this innermost
    break changes an outcome; the two outer ones are redundant while it
    stands, and the module docstring records why they are left alive.

    The season subdirectory and the second library are the shapes that
    would tell the outer two apart if anything could. They are kept because
    they are what a real library looks like — a scan cancelled in the first
    directory of the first show must not go on to the next season, or the
    next library — and the assertion is on the whole scan's outcome rather
    than on which break produced it.
    """
    calls = _record_processed(monkeypatch)
    first = _library(tmp_path, "tv", "A.mkv", "B.mkv")
    season = tmp_path / "tv" / "Season 02"
    season.mkdir()
    (season / "C.mkv").write_text("not really a video")
    second = _library(tmp_path, "movies", "D.mkv")

    stats = scan_library(db, [first, second], cancel_check=lambda: True)

    assert len(calls) == 1, "the scan kept going after it was cancelled"
    assert stats.total == 1


def test_a_cancelled_scan_reports_that_it_was_cancelled(db, tmp_path,
                                                        monkeypatch):
    """
    Closes: stats.cancelled hardcoded to False. It is the only way the
    caller can tell a scan that stopped early from one that finished, and
    the route broadcasts and logs on the strength of it — so the UI would
    report a complete scan of a library that was half walked.
    """
    _record_processed(monkeypatch)
    lib = _library(tmp_path, "tv", "A.mkv", "B.mkv")

    cancelled = scan_library(db, [lib], cancel_check=lambda: True)
    completed = scan_library(db, [lib], cancel_check=lambda: False)

    assert cancelled.cancelled is True
    assert completed.cancelled is False


# ── The walk ──────────────────────────────────────────────────────────────────

def test_a_missing_path_does_not_stop_the_paths_after_it(db, tmp_path,
                                                         monkeypatch):
    """
    Closes: the missing-path branch breaking out of the loop instead of
    continuing to the next path.

    Anyone with two libraries has this: one mount is late, and the scan
    silently covers none of the libraries configured after it. Ordering is
    the whole test, so the missing path goes first.
    """
    calls = _record_processed(monkeypatch)
    missing = str(tmp_path / "not-mounted")
    present = _library(tmp_path, "tv", "A.mkv")

    scan_library(db, [missing, present])

    assert [c["path"] for c in calls] == [os.path.join(present, "A.mkv")]


def test_only_media_files_are_processed(db, tmp_path, monkeypatch):
    """
    Closes: the is_media_file filter removed from the walk. Every artwork
    file, .nfo and sidecar subtitle in the library would be handed to
    _process_file, which stats it and hands it to ffprobe.
    """
    calls = _record_processed(monkeypatch)
    lib = _library(tmp_path, "tv", "Ep.mkv", "Ep.nfo", "Ep.eng.srt",
                   "poster.jpg")

    scan_library(db, [lib])

    assert [os.path.basename(c["path"]) for c in calls] == ["Ep.mkv"]


def test_progress_is_reported_against_the_real_total(db, tmp_path,
                                                     monkeypatch):
    """
    Closes: the pre-count skipping the directories it should count, and the
    total reported as the running count.

    Both produce a progress bar that is wrong rather than absent — X of 0,
    or a bar pinned at 100% for the whole scan — which is the kind of
    breakage that gets lived with rather than reported.
    """
    _record_processed(monkeypatch)
    lib = _library(tmp_path, "tv", "A.mkv", "B.mkv", "C.mkv", "notes.txt")
    seen: list[tuple[int, int]] = []

    scan_library(db, [lib], progress_callback=lambda s, t: seen.append((s, t)))

    assert seen == [(1, 3), (2, 3), (3, 3)]


# ── Per file ──────────────────────────────────────────────────────────────────

def test_one_file_that_raises_does_not_end_the_scan(db, tmp_path,
                                                    monkeypatch):
    """
    Closes: the try/except around _process_file removed, and stats.errors
    left un-incremented.

    A library scan that dies on its first unreadable file leaves everything
    after it unscanned, and the failure surfaces as a scan that finished
    suspiciously early rather than as an error against the file that caused
    it. The count is the other half: it is what the summary reports, and a
    scan reporting zero errors is a scan nobody looks into.
    """
    from app.core import scanner as sc

    seen: list[str] = []

    def fake(db, path, app_cfg, force_probe, dry_run, stats,
             sonarr_series_id=None, radarr_movie_id=None):
        seen.append(path)
        if os.path.basename(path) == "B.mkv":
            raise OSError("unreadable")

    monkeypatch.setattr(sc, "_process_file", fake)
    lib = _library(tmp_path, "tv", "A.mkv", "B.mkv", "C.mkv")

    stats = scan_library(db, [lib])

    assert len(seen) == 3, "the scan stopped at the file that raised"
    assert stats.errors == 1


def test_the_scan_mode_reaches_every_file(db, tmp_path, monkeypatch):
    """
    Closes: force_probe replaced by a literal False, which turns every full
    scan into a delta scan. Nothing fails — files that changed without
    changing size or mtime are simply never re-probed, so the decisions the
    queue is built from are made against stale track data indefinitely.

    Both flags are asserted, with different values, because they are
    adjacent booleans in a positional call: asserting only force_probe
    would let the two swap places unnoticed.
    """
    _setting(db, "dry_run_mode", "false")
    calls = _record_processed(monkeypatch)
    lib = _library(tmp_path, "tv", "A.mkv")

    scan_library(db, [lib], force_probe=True)
    scan_library(db, [lib], force_probe=False)

    assert [(c["force_probe"], c["dry_run"]) for c in calls] == [
        (True, False), (False, False),
    ]
