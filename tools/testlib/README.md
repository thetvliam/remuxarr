# Generated test library and explorer

Two tools for finding behaviour nobody planned, outside the test suite:

- `make_test_library.sh` builds a small library of unusual media: 26 files
  across MKV, MP4, M4V, MOV, AVI, TS, M2TS, WMV, FLV and WebM, with PGS,
  VobSub, DVB, ASS, WebVTT and mov_text subtitles, fonts, cover art,
  chapters, untagged and two-letter language tags, non-UTF-8 subtitles,
  corrupt AAC packets, an audio-only file and a zero-byte one. A folder
  layout of copies covers the extras rules, an existing sidecar and an
  output name that is already taken. The comment above each `fNN` function
  says what that file is built to exercise.
- `explore.py` runs a library through Remuxarr's real scan, queue and worker
  under many settings profiles and reports anything that breaks a rule that
  should hold whatever the settings are.

`tests/sample_library` is a different thing: an ffprobe snapshot of real
files that the suite checks decisions against on every run. Nothing here
replaces it.

## Running it

From the repository root, with the app's Python dependencies installed and
the FFmpeg the Dockerfile pins first on `PATH` (other versions read some of
these files differently):

```
tools/testlib/make_test_library.sh /tmp/testlib
python tools/testlib/explore.py /tmp/testlib -j 2
```

`--list-profiles` prints every profile's settings, `--profile NAME` runs
only that one (repeatable), and `--keep` keeps every profile's processed
copy rather than only those with findings. Results go to
`remuxarr-testlib-runs` in the system temp folder unless `--out` says
otherwise, so a run from the repository root leaves the checkout alone.

In the sandbox this was written in (two CPUs), the library built in 5 to
16 seconds and all 18 profiles ran in about 80 seconds at `-j 2`. It has
not been tried inside the Remuxarr container, and the image does not
include `tools/`.

The exit status is 0 when nothing was found, 1 when something was, and 2
when a profile could not run at all (its `run.log` says why).

## What a run touches

Each profile runs in its own process on its own copy of the library, with
its own database, temp folder and recycle folder under `--out`. The child
process points the app's `DATABASE_PATH`, `TEMP_DIR` and `RECYCLE_DIR` there
before importing it, and stops if any of them is not the scratch copy. Dry
run is turned off in that copy, so the worker really rewrites the copied
files. The library you pass in is only read.

## Profiles

`default` is the app's own defaults. The other 17 vary the settings below so
that every pair of values of any two of them appears together in at least
one profile. All combinations would be about 280,000; the pairwise set is
checked for full coverage each time it is built.

`prefer_mp4_container`, `add_faststart_to_mp4`, `fix_undefined_language`,
`fix_undefined_language_audio`, `keep_default_audio`,
`keep_forced_subtitles`, `extract_text_subtitles_to_srt`,
`existing_srt_handling`, `image_subtitle_handling`,
`font_attachment_handling`, `keep_audio_languages`,
`keep_subtitle_languages`, `skip_extras`, `revert_enabled`.

Each profile is checked against the app's settings schema before it runs,
so a setting or value the app no longer has stops the run instead of being
stored and ignored.

Not covered yet: answering reviews, revert and AC3 Forge; Original Language
Audio, which needs an original language from Sonarr or Radarr; more than one
job at a time; and the Plex, Sonarr, Radarr and email paths.

## The checks

1. Every job ends as success, review or skipped, and nothing logs an
   exception. A scan error is expected only on a file ffprobe cannot read.
2. Every output probes, and its video and audio decode without an error.
3. Every input stream is in the output, dropped by a planned action or
   extracted to a sidecar, and the chapters survive.
4. Every kept audio and subtitle track carries the language the plan gave
   it.
5. Each extraction adds exactly one sidecar, and an existing sidecar's text
   survives somewhere unless Existing Subtitle Files is Replace.
6. Nothing outside a processed file and its sidecars changes.
7. A full rescan queues nothing for a file that was processed or had
   nothing to do. A failed job is left out, since a full scan queues it
   again as its retry.
8. No temp file is left behind, and the source is gone only when the
   container changed.

Findings are grouped across profiles, so one cause reads as one line with
the profiles it appeared in. `summary.json` holds every profile's settings,
scan counts, findings and per-file outcome.

A finding is a lead, not a verdict. Reproduce it with the real code first,
and turn a real bug into a normal test in `tests/`.

## Accepted losses

Some outcomes are decisions the project has already made: attachments and
cover art do not go into MP4, and a conversion onto a name another file
already holds is refused. These are listed under "Accepted losses" with
where each decision is recorded, and do not count as findings. Add to
`KNOWN_LOSSES` in `explore.py` only when the decision is written down
somewhere a reader can check.
