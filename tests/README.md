# Remuxarr test suite

1382 tests across 69 test files, plus 472 frontend tests under
`frontend/src/**/__tests__/`. Backend line coverage is around 87%, though it is
not the measure used here — see How these tests are written below.

The file count is the modules `pytest` collects, not the `.py` files under
`tests/`: `conftest.py` and `sample_library/parse_ffprobe_dump.py` are fixtures
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
`test_clear_database.py`, `test_scan_library.py`, `test_settings_persistence.py`,
`test_settings_schema.py`, `test_backup_restore.py`. Real SQLite, real
temp files. `test_media_file_deletion.py` derives the list of tables
referencing `media_files` from the model metadata at runtime rather than
hardcoding it, so adding a table and forgetting to delete from it fails there
by name; `test_clear_database.py` guards the same omission at the other end,
where three tables have been left out of the wipe so far and SQLite's
unenforced foreign keys mean nothing complains. `test_settings_schema.py` asks
whether a setting is reachable at all — default, accepted key, schema entry and
a UI category that renders its group — after four Recycle Bin settings shipped
with the first two and stayed invisible for a dozen commits.
`test_scan_library.py` covers the walk itself, and mostly one line of it: the
cleanup pass is scoped to the scan paths that are directories right now, which
is the only thing standing between a share that has not mounted and every row
under it being deleted.

**Queue and job lifecycle** — `test_queue_lifecycle.py`, `test_queue_routes.py`,
`test_job_finalisation.py`, `test_history_routes.py`,
`test_manual_review_refresh.py`, `test_startup_recovery.py`,
`test_background_tasks.py`. Four more follow one job through `worker.py` in
order: `test_worker_loop.py` on the pool that claims it and the three places a
running job is tracked at once, `test_job_preflight.py` on the re-decision
against current settings between queueing and execution,
`test_run_job_preflight.py` on the dry-run and disk-space checks made before
FFmpeg is spawned, and `test_post_job_broadcast.py` on the dispatch afterwards.
The last sits directly above `test_post_job_notify.py` under Integrations: that
one pins what each notification loader returns, this one whether the caller
acts on it.

**FFmpeg and staging** — `test_ffmpeg_command.py`,
`test_source_file_preservation.py`, `test_forge_and_staging.py`,
`test_subtitle_extraction_failures.py`, `test_attachment_preservation.py`,
`test_audio_transcode_retry.py`, `test_subtitle_extraction_routing.py`,
`test_subtitle_cascade_review.py`. Some run a real subprocess against real
temp files; a few need real ffmpeg/ffprobe and skip when the binaries are
absent (CI installs them, so they always run there).
`test_attachment_preservation.py` pins the `-map` arguments against the bug
that let every remux, including a pure language re-tag, silently destroy a
file's fonts and cover art while reporting success.
`test_audio_transcode_retry.py` covers the retry for audio that cannot be
stream-copied, in both execution branches — the same guard twice over, so
killing it in one says nothing about the other. The last two are the two-pass
and combined-pass halves of one question: which failed extraction is a decision
for a human, and so goes to manual review, rather than a plain failure.

**AC3 Forge** — `test_forge_candidates.py`, `test_forge_orchestration.py`,
`test_forge_selection_and_counts.py`, `test_forge_undo_resolution.py`.

**Integrations** — `test_webhook_paths.py`, `test_webhook_enable_scope.py`,
`test_arr_client.py`, `test_arr_notifications.py`,
`test_arr_quality_restore.py`, `test_plex_client.py`, `test_scheduler.py`,
`test_email_notify.py`, `test_post_job_notify.py`.
`test_arr_quality_restore.py` covers putting a file's quality back after a
container change replaces it, and its fixture is the real history of one
movie that this happened to — two deletion records newer than the import
that matters, a grab, and a three-week-old import at the same path the file
returns to. An invented fixture would have agreed with every wrong answer.
`test_arr_client.py` covers only the *arr HTTP client's GET and PUT surface
and the per-verb wiring: the rules all three verbs share — the base_url
rstrip, the API key header, the timeout, errors propagating — are pinned
through `arr_post` next door, and sharing one request builder means they are
pinned once rather than three times. The last two are the two
halves of a notification: `test_email_notify.py` covers the SMTP send path,
and `test_post_job_notify.py` covers the layer in `worker.py` that decides
whether Sonarr, Radarr or Plex are told anything at all and with which URL
and key. The email circuit breaker's own decision lives in
`test_assorted_regressions.py`.

**Live updates** — `test_ws_manager.py`, `test_ws_broadcast_threadsafe.py`. The
WebSocket hub every UI event leaves through: connection bookkeeping, the
fan-out and its pruning of sockets whose tab has gone, the `/ws` endpoint that
registers one, and the hand-off scan and revert use to broadcast from a worker
thread onto the loop that started them.

**Release notes** — `test_release_notes.py`. The endpoint the update dialog
reads and the version hash that decides whether it is shown at all. The failure
it guards is silence: a release that renames someone's settings and says
nothing.

**Language review** — `test_audio_language_review.py`,
`test_subtitle_language_review.py`, `test_language_review_isolation.py`,
`test_subtitle_rename.py`. The last covers what the others cannot reach: an
extracted subtitle is no longer in the mux, so a language chosen in review has
to rename the file on disk rather than re-extract the track, and the filename
is what Plex reads.

**Revert to original** — `test_revert_manifest.py`, `test_revert_capture.py`,
`test_revert_restore.py`, `test_revert_execution.py`,
`test_revert_multiple_jobs.py`, `test_revert_match.py`, `test_revert_routes.py`,
`test_revert_created_files.py`, `test_recycle_bin.py`,
`test_retention_sweep.py`, `test_staging_hook.py`, `test_schema_migration.py`,
`test_deployment_config.py`. The failure this feature has to avoid is not a
crash: it is a revert that succeeds and quietly rebuilds the file wrong.
Several of these therefore run real ffmpeg end to end —
capture from a real file, revert from the result, compare stream by stream —
because the bugs found here (the wrong audio track stored, stream indices off by
one past the first attachment, MP4 metadata residue) all produced valid,
playable files and argv-level assertions saw nothing amiss.
`test_revert_created_files.py` is the same failure one step out from the mux: a
revert re-embeds the subtitles extraction wrote beside the media, so leaving
those files gives every subtitle twice, while deleting every `.srt` next to the
media would take out the ones that predate Remuxarr.

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

### Lines left untested on purpose

It follows from the above that some lines should stay uncovered, and a
coverage report will keep pointing at them. A line is left alone when no
mutation of it produces a failure a test could meaningfully assert on —
which is a different question from whether the line is important.

The recurring shapes, so the next reader does not re-derive them:

- **One-line reads of module state**, like the worker's paused flag and active
  job count. A test can only restate the implementation, and any mutation is
  either equivalent or absurd.
- **Log-only branches**, where the code catches something, writes a warning and
  carries on — removing the original after a container change, the
  emergency-cleanup handler of last resort. The only assertion available is
  "it did not crash", which the surrounding tests already establish.
- **Guards whose real caller is covered elsewhere.** The worker loop's
  `CancelledError` break is reached through `stop_worker`, which has its own
  tests; asserting it directly means cancelling a task mid-iteration to check
  that nothing escaped.
- **Short-circuits whose only observable difference is on input nothing
  sends.** `broadcast_json` returns early when no client is connected. With an
  empty connection list the send loop never runs and the prune list is empty
  either way, so removing the return differs only on a payload `json.dumps`
  cannot serialise — and every caller passes a plain dict. A test would pin
  the accident that broadcasting garbage to nobody happens to be silent.

Two things worth keeping straight. This is not a licence to skip anything
awkward to reach — every one of these was checked by asking what a wrong
version would look like, and the answer was "nothing a test can see". And
coverage is still useful as a *finder*: a block sitting at zero is a reliable
signal that nothing exercises it, which is how most of the units above were
picked. It is the target that is wrong, not the tool.

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
