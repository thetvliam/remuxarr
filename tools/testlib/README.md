# Generated test library and explorer

Two tools for finding behaviour nobody planned, outside the test suite:

- `make_test_library.sh` builds a small library of unusual media: 28 files
  across MKV, MP4, M4V, MOV, AVI, TS, M2TS, WMV, FLV and WebM, with PGS,
  VobSub, DVB, ASS, WebVTT and mov_text subtitles, fonts, cover art,
  chapters, untagged and two-letter language tags, non-UTF-8 subtitles,
  corrupt AAC packets, an audio-only file and a zero-byte one. A folder
  layout of copies covers the extras rules, an existing sidecar and an
  output name that is already taken. The comment above each `fNN` function
  says what that file is built to exercise.
- `explore.py` runs a library through Remuxarr's real scan, queue and worker
  under many settings profiles, answers every review the way the profile
  says, reverts every file it can, and reports anything that breaks a rule
  that should hold whatever the settings are.

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
16 seconds and all 21 profiles, review answers, Forge and reverts included, ran in
about three minutes at `-j 2`. It has
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

`default` is the app's own defaults. The other 20 vary the settings below
and the runner's three choices, so that every pair of values
of any two of them appears together in at least one profile. All
combinations would be about 5 million; the pairwise set is checked for
full coverage each time it is built.

`prefer_mp4_container`, `add_faststart_to_mp4`, `fix_undefined_language`,
`fix_undefined_language_audio`, `keep_default_audio`,
`keep_forced_subtitles`, `extract_text_subtitles_to_srt`,
`existing_srt_handling`, `image_subtitle_handling`,
`font_attachment_handling`, `keep_audio_languages`,
`keep_subtitle_languages`, `skip_extras`, `revert_enabled`.

The runner's choices, which are not app settings and are kept out of what
each profile writes: `_subtitle_answer` (keep, remove or extract), given to
every flagged track of a subtitle review, falling back to keep where the app
refuses it; and `_language_answer` (ignore, `eng` or `jpn`), given to every
audio and subtitle language question; and `_forge_undo` (yes or no), whether
to undo each AC3 Forge job after it runs. The default profile keeps,
ignores and does not undo.

Each profile is checked against the app's settings schema before it runs,
so a setting or value the app no longer has stops the run instead of being
stored and ignored.

Not covered yet: Original Language
Audio, which needs an original language from Sonarr or Radarr; more than one
job at a time; and the Plex, Sonarr, Radarr and email paths.

## The checks

1. Every job ends as success, review or skipped, and nothing logs an
   exception. A scan error is expected only on a file ffprobe cannot read.
2. Every output probes, and its video and audio decode without an error.
3. Every input stream is in the output, dropped by a planned action or
   extracted to a sidecar, and the chapters survive. Every stream the job
   kept also holds what it held: kept streams are paired with the output's
   by kind, in order, and a checksum of each one's packets (or an
   attachment's data), taken before any job, is compared. A job copies what
   it keeps, so a kept track with another's content, or a re-encoded one,
   shows here. Video or audio whose packets differ is decoded and compared
   with the library the run was given; the same frames pass, and the stream
   is listed as repackaged (below). Subtitles, covers and attachments must
   match as stored.
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

Questions are answered through the app's own route functions, called with
a database session: after the scan, so a file is processed once with its
answers, and again after each queue run until none is left. Then:

16. What the review preview refuses, apply refuses, and for an answer it
    accepts, the preview's outcome (will process, still in review) is what
    apply did.
17. A processed file's job plans what its subtitle answers said: keep is
    neither dropped nor extracted, remove is dropped, extract is extracted;
    and a file whose every question was answered is not still waiting.
18. An applied language is the planned language of its track (an extracted
    subtitle's is the one its file name gets); an ignored one plans none.
19. After a rescan, nothing answered is asked again.

Then every file AC3 Forge lists as a candidate is queued through its own
route function, and the worker runs the Forge jobs:

20. Every add succeeds, and nothing logs an exception.
21. The file gains exactly one AC3 5.1 track carrying Forge's marker, placed
    after the last audio track, and every other stream is as it was (the
    same comparison as check 11, with every packet checksum equal: an add
    copies the streams it keeps).
22. The forged file probes and decodes cleanly.
23. A full rescan queues nothing for a forged file that had nothing left to
    do before the forge.
24. With `_forge_undo`, every add is undone through the route function, and
    the file is back to exactly the streams it had before the add, packet
    checksums included.
25. No Forge temp file is left, no Forge job is still pending, processing
    or waiting to undo, and nothing in the library changed but the forged
    files.

Then, in each profile with revert on (15 of the 21; pairwise, so every value
of every other setting meets revert on at least once), every revert point is
restored through the app's own `restore_revert_point`, and:

9. A file has a revert point exactly when its job lost a track a point can
   hold, which is anything but an attachment. With revert off, no file has
   one.
10. Every restore succeeds, onto the original name and extension, and a
    converted output goes away.
11. The restored file matches the original stream for stream (type, codec,
    language and title tags, disposition flags, attachment filename and
    mimetype, order), and an MP4's fast-start layout matches too. Each
    stream also holds what it held: a checksum of its packets, or of an
    attachment's data, is taken before any job and compared. Tags and flags
    are written back from the revert point, so they can all be right while
    a stream carries another's content. Video or audio whose packets differ
    is decoded and compared with the library the run was given; the same
    frames pass, and the stream is listed as repackaged (below).
12. The restored file probes and decodes cleanly.
13. The job's other files are back as they were: extracted subtitle files
    gone, everything else byte for byte, and nothing else touched.
14. A full rescan plans the restored file exactly as it planned the original.
15. The point is spent: no row left, its sidecar gone from the recycle
    folder, and no temp file left behind. No Forge job on the file is
    still marked a success, since the AC3 it added is gone.

A question raised by a job and answered after it starts a second job on
the processed file. Checks 3, 4, 5, 8 and 14, and checks 17 and 18 where
they compare a job's plan with the answers, judge one job against the
original, so they leave such a file out; `summary.json` lists it under
`several_jobs`.

A stream a job or a revert leaves with different packets that decode to the
same frames is repackaged, not changed, and is not a finding: the report and
`summary.json` list it under `repackaged`, with which of the two did it. The
generated library's two MPEG-TS files do this whenever a job converts them
to MP4. Going in, the MP4 muxer stores H.264 with length prefixes where
MPEG-TS has start codes (`ff_nal_parse_units`, from `mov_write_packet`) and
AAC without its ADTS headers (`aac_adtstoasc`, added by
`mov_check_bitstream`; both in `libavformat/movenc.c`). Coming back, a
revert gives the H.264 every NAL unit the same and start codes of other
lengths, which FFmpeg writes going back to MPEG-TS (`count_or_copy` in
`libavcodec/bsf/h264_mp4toannexb.c`). Where the restored file's streams are
not the original's in number or order (a Matroska cover come back as a video
track, or a check 11 finding of its own), positions cannot be compared, and
the check is that what each original stream held is somewhere in the file.

A file the rescan in check 7 queued again is not reverted, since the revert
route refuses a queued file; `summary.json` marks it `revert_skipped`. Each
profile's `run.log` holds the app's own log, and `summary.json` each file's
answers.

Findings are grouped across profiles, so one cause reads as one line with
the profiles it appeared in. `summary.json` holds every profile's settings,
scan counts, findings and per-file outcome.

A finding is a lead, not a verdict. Reproduce it with the real code first,
and turn a real bug into a normal test in `tests/`.

## Accepted losses

Some outcomes are decisions the project has already made: attachments and
cover art do not go into MP4, a conversion onto a name another file already
holds is refused, a revert gives Matroska cover art back as a video track,
an existing subtitle file a job replaced or moved stays that way after a
revert, a job goes ahead without a revert point when capture reports one
unavailable and Require Revert Point is off, an answer for a track a
revert brings back from the sidecar is asked again, and a job whose audio
cannot be copied fails and leaves the file as it was, since nothing is
re-encoded to get past it. These are listed under "Accepted losses" with
where each decision is recorded, and do not count as findings. Add to
`KNOWN_LOSSES` in `explore.py` only when the decision is written down
somewhere a reader can check.
