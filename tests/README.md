# Remuxarr test suite

## What's here so far

`test_decision.py` — regression tests for `app/core/decision.py`'s
`analyze_file()`, the pure decision engine that decides what happens to
every file. Every test is tied to a real incident from this project's
development — see each test's docstring for exactly what it guards
against. No ffmpeg, no database, no real files needed — `analyze_file()`
takes plain dicts in and returns a plain dataclass out, so these run in
well under a second.

## Running it — two options, same suite either way

**Option A — I run it myself**, as part of verifying any change to
`decision.py` before handing you the file. This is already happening
going forward: any time I touch this function, running this suite first
is now part of how I check the change before it reaches you.

**Option B — you run it yourself**, inside the actual deployed container,
against the real production environment (real ffmpeg 8.1, real file
paths) as an independent check after any deploy:

```bash
docker exec -it remuxarr bash
cd /app
pip install -r tests/requirements-test.txt --break-system-packages
pytest tests/ -v
```

`pytest` and its dependency aren't part of the production `requirements.txt`
on purpose — they only get installed if you actually run this, so the
deployed image doesn't carry test tooling it never uses day to day.

## What's not here yet

This only covers the pure decision logic — the part of the codebase with
the highest concentration of real bugs found this session (the silent-audio
fallback, the language-override pass, the container-detection ValueError,
the threshold clamp), and the cheapest to test since it needs nothing but
Python.

Not yet covered: anything that actually runs ffmpeg, touches the database,
or calls Sonarr/Radarr/Plex. That's a natural next phase — a small library
of synthetic test video files (generated with ffmpeg's own test-source
generators, not real copyrighted media) that could be run through the
actual scan → decide → process pipeline end to end, then re-probed to
confirm the real output matches expectations. Worth building once this
layer has proven itself useful in practice.
