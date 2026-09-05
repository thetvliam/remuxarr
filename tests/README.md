# Remuxarr test suite

1176 tests across 56 test files, plus 436 frontend tests under
`frontend/src/**/__tests__/`. Backend line coverage is around 78%, though the
number below matters more than that one.

The file count is the modules `pytest` collects. There are 56 `.py` files under
`tests/`; `conftest.py` and `sample_library/parse_ffprobe_dump.py` are fixtures
and a helper script, contain no tests, and are not counted. Both readings were
in use at once until this was written down.

## What's here

**The decision engine** — `test_decision.py`, `test_subtitle_language_path.py`,
`test_command_and_pure_functions.py`. `analyze_file()` decides what happens to
every file; these take plain dicts in and assert on a plain dataclass out, so
they need no ffmpeg, no database and no real files, and run in well under a
second. Most are tied to a specific incident from this project's development —
see each docstring.

**Scanning and the database** — `test_scan_and_cancellation.py`,
`test_scan_stats_and_subtitle_classifier.py`, `test_media_file_deletion.py`,
`test_settings_persistence.py`, `test_backup_restore.py`. Real SQLite, real
temp files. `test_media_file_deletion.py` derives the list of tables
referencing `media_files` from the model metadata at runtime rather than
hardcoding it, so adding a table and forgetting to delete from it fails there
by name.

**Queue and job lifecycle** — `test_queue_lifecycle.py`, `test_queue_routes.py`,
`test_job_finalisation.py`, `test_history_routes.py`,
`test_manual_review_refresh.py`, `test_startup_recovery.py`,
`test_background_tasks.py`.

**FFmpeg and staging** — `test_ffmpeg_command.py`,
`test_source_file_preservation.py`, `test_forge_and_staging.py`,
`test_subtitle_extraction_failures.py`. Some run a real subprocess against real
temp files; a few need real ffmpeg/ffprobe and skip when the binaries are
absent (CI installs them, so they always run there).

**AC3 Forge** — `test_forge_candidates.py`, `test_forge_orchestration.py`,
`test_forge_selection_and_counts.py`, `test_forge_undo_resolution.py`.

**Integrations** — `test_webhook_paths.py`, `test_webhook_enable_scope.py`,
`test_arr_notifications.py`, `test_plex_client.py`, `test_scheduler.py`.

**Language review** — `test_audio_language_review.py`,
`test_subtitle_language_review.py`, `test_language_review_isolation.py`.

**Revert to original** — `test_revert_manifest.py`, `test_revert_capture.py`,
`test_revert_restore.py`, `test_revert_execution.py`,
`test_revert_multiple_jobs.py`, `test_revert_match.py`, `test_revert_routes.py`,
`test_recycle_bin.py`, `test_retention_sweep.py`, `test_staging_hook.py`,
`test_schema_migration.py`, `test_deployment_config.py`. The failure this
feature has to avoid is not a crash: it is a revert that succeeds and quietly
rebuilds the file wrong. Several of these therefore run real ffmpeg end to end —
capture from a real file, revert from the result, compare stream by stream —
because the bugs found here (the wrong audio track stored, stream indices off by
one past the first attachment, MP4 metadata residue) all produced valid,
playable files and argv-level assertions saw nothing amiss.

**Sample library** — `test_sample_library.py` runs the real pipeline against a
fixed set of probed media files (`tests/sample_library/`) and compares against
recorded golden decisions.

**Cross-cutting regressions** — `test_assorted_regressions.py`,
`test_robustness_fixes.py`, `test_timestamp_roundtrip.py`,
`test_spa_fallback_security.py`, and `test_health_build_identity.py`. These are
grouped by the incident that prompted them rather than by the module they touch,
so they span several areas each.

## How these tests are written

Coverage percentage is not the measure used here. Several modules in this
project once sat at high coverage while the code underneath was completely
unprotected — one module reported 100% branch coverage on 41% of its lines
because only a single pure helper was exercised, and the hook body it lived
beside had no tests at all.

So the standard is **mutation testing**: a deliberate change is made to the
production code, and the suite must fail. Tests here are added only once a
specific mutation kills them, and the mutations that no other test catches are
recorded in the file docstrings. Where a mutation cannot be killed because it
genuinely changes nothing observable, that is written down as an equivalent
mutant rather than papered over with a test that only appears to guard it.

Two failure modes this has caught, both of which read as coverage:

- A test that re-implemented the logic it was checking inside its own body and
  never called the production function — it passed regardless of what the app
  did.
- Tests asserting only on a spy, never on the resulting DOM or database state,
  so a value could be committed correctly while the user was shown something
  else entirely.

## Running it — two options, same suite either way

**Option A — locally.**

```bash
pip install -r requirements.txt -r tests/requirements-test.txt
pytest

cd frontend && npm install && npm test
```

**Option B — inside the deployed container**, against the real production
environment (real ffmpeg, real file paths), as an independent check after a
deploy:

```bash
docker exec -it remuxarr bash
cd /app
pip install -r tests/requirements-test.txt --break-system-packages
pytest tests/ -v
```

`pytest` and its dependencies aren't part of the production `requirements.txt`
on purpose — they only get installed if you actually run this, so the deployed
image doesn't carry test tooling it never uses day to day.

## Release notes are part of the change, not a step after it

`RELEASE_NOTES.md` in the repo root holds what has changed since the last
merge to `main` that a **user** would notice. The app serves it at
`/api/release-notes/` and shows it once, as a dialog, when the content
changes.

If a change alters what a user sees, add a line **in the same commit**. If
it does not — refactors, tests, lint, internal fixes with no visible
symptom — add nothing. The file's own header comment has the full test for
what qualifies; the short version is that it is written for someone running
the container, not for whoever reviewed the diff.

The cycle has one ordering rule that is easy to get backwards:

1. Entries accumulate on `testing`.
2. `testing` merges to `main` **with the entries intact** — that is what
   users pull, and what triggers the dialog.
3. **Only then** is the file emptied, on `testing`, as the first commit of
   the next cycle.

Emptying it as part of the merge ships an empty file to `main`, and the
release nobody was told about is the one that renamed their settings.

## Conventions

- No inter-file ordering dependence: any file can be run alone.
- Module-level state (caches, refresh keys, worker globals) is reset by an
  autouse fixture wherever it exists, since a leaked entry can make a broken
  lookup look like it works.
- `pytest.ini` sets `filterwarnings = default`, so new warnings are visible
  rather than swallowed. **A clean run now prints no warnings at all**, which
  is what makes that setting worth having: anything in the summary is new and
  worth reading. The three that used to be permanent are gone rather than
  filtered — Pydantic's class-based `Config` (now `SettingsConfigDict`),
  Starlette's `httpx` deprecation (the test extra now installs `httpx2`,
  which is the backend it asks for), and an un-awaited `broadcast_json`
  coroutine that a test double in `test_revert_routes.py` was dropping on the
  floor. Suppressing any of them in `pytest.ini` would have been the wrong
  fix: two were real deprecations with a real end date, and the third was a
  mock that did not behave like the function it replaced.
- Frontend tests wait on rendered state rather than on a spy's call count. The
  counter increments when a request is *issued*; the state lands later, and
  waiting on the counter produces a race that passes locally and fails on
  slower CI.
