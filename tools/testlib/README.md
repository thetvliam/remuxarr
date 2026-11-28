# Generated test library and explorer

Two tools for finding behaviour nobody planned, outside the test suite:

- `make_test_library.sh` builds a small library of unusual media: 41 files
  across MKV, MP4, M4V, MOV, AVI, TS, M2TS, WMV, FLV and WebM, with PGS,
  VobSub, DVB, ASS, WebVTT and mov_text subtitles, fonts, cover art,
  chapters, untagged and two-letter language tags, non-UTF-8 subtitles,
  corrupt AAC packets, creation dates (a Matroska's to the microsecond, as
  mkvmerge writes it, and an MP4's), Dolby Vision (Profile 5 and 8.1 in
  Matroska, Profile 5 in MP4), tags FFmpeg cannot write into an MP4 (an IMDB
  and a TMDB ID in a Matroska, a key of its own in an MP4), regional
  languages (en-GB beside en-US, es-419 beside es-ES), two chapter editions
  with a nested chapter and names in two languages, tags at the collection
  level and aimed at a chapter, cover art ahead of a font, an audio-only
  file and a zero-byte one.
  Files 39 and 40 are put together with mkvmerge, as FFmpeg cannot write
  what they hold, so building the library needs MKVToolNix as well. A folder
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
16 seconds and all 23 profiles, review answers, Forge and reverts included, ran in
about seven minutes at `-j 2`, check 29 adding about 15%. It has
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

`default` is the app's own defaults. Twenty-one more vary the settings below
and the runner's three choices, so that every pair of values
of any two of them appears together in at least one profile. All
combinations would be about 10 million; the pairwise set is checked for
full coverage each time it is built. Only pairs are covered, so the two
Dolby Vision Matroska files are converted to MP4 in two profiles and
reverted in both, and the anime Matroska with fonts is converted to MP4 in
none: it was in one profile (Prefer MP4 Container on, fonts to be removed,
its PGS track answered Remove) before Tags an MP4 Can't Hold was varied.

`prefer_mp4_container`, `convert_dolby_vision_to_mp4`, `mp4_lost_tag_handling`,
`add_faststart_to_mp4`,
`fix_undefined_language`, `fix_undefined_language_audio`, `keep_default_audio`,
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

`r01` is a fixed profile run beside the pairwise ones: Keep Audio
Languages `en-GB`, Keep Subtitle Languages `en-GB` and `es-419`, Matroska
kept, subtitles left in the file and revert on, for check 35. A regional
value added to the varied lists instead would rebuild every profile.

Each profile is checked against the app's settings schema before it runs,
so a setting or value the app no longer has stops the run instead of being
stored and ignored.

Not covered yet: Original Language
Audio, which needs an original language from Sonarr or Radarr; more than one
job at a time; and the Plex, Sonarr, Radarr and email paths.

## The checks

1. Every job ends as success, review or skipped, and nothing logs an
   exception. A scan error is expected only on a file ffprobe cannot read.
   A job refused because its output would lose some of the file's own tags
   is an accepted loss (below), unless one of them is `creation_time`, the
   date the file says it was made: a job writes it back, so losing it is a
   finding. A Matroska dated to the microsecond was refused that way by
   every conversion to MP4 (issue #61). It is a finding too when the job
   wrote an MP4 and the tags an MP4 would lose were known before it ran
   (the file's `mp4_lost_tags` column holds a list, not null): Tags an MP4
   Can't Hold decides about those, so a job should never meet them, and
   the refusal is left to stop only what could not be found out.
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
   again as its retry; except one that failed because its audio cannot be
   copied, which would only fail again, so a scan holds the file back
   until the file or its plan changes.
8. No temp file is left behind, and the source is gone only when the
   container changed.
26. Every kept video, audio and subtitle track keeps its own tags, its title
    above all (an MP4 track's title is the tag FFmpeg shows as `name`). The
    language is check 4's, and `DURATION` and `ENCODER` are the muxer's own.
    A Matroska track's other tags going in a conversion to MP4, which has no
    place for them, is listed as an accepted loss, not a finding: Remuxarr
    does not refuse a conversion over one. Only this tool checks track tags;
    the worker checks the file's own (see the main README), and check 30
    shows that it did.
27. Every kept video stream keeps its Dolby Vision configuration record,
    field for field, and so does the file after an AC3 Forge add, its undo
    and a revert, each reported under this number. The record (an MP4's
    `dvcC`, `dvvC` or `dvwC` box, a Matroska's BlockAdditionMapping) is the
    container's word that a stream is Dolby Vision, beside an MP4's sample
    entry (`dvh1`, `dvhe`). A copy without it keeps every RPU, as the packets
    are the same, and passes checks 3 and 11, while a player can show
    Profile 5 as plain HEVC in the wrong colours (issue #62). The library's
    three Dolby Vision files carry a record and no RPUs (FFmpeg's `dovi_rpu`
    filter writes one from the colour properties), so they test the record
    and nothing about playback.
28. With Convert Dolby Vision Files to MP4 off, which is the default, a file
    whose video has a Dolby Vision record is not converted to MP4, after any
    number of jobs.
29. Every kept video, audio and subtitle track starts where it did against
    the file's first video stream (its first audio stream in a file with
    none), to within 2 ms, kept streams paired as for check 3; and so does
    every stream after a revert, reported under this number too. Video and
    audio start at their first decoded frame, which is what a player shows
    or plays, and subtitles at their first packet. Checks 3 and 11 compare
    what a stream holds, not when it plays, and packet times alone miss two
    cases: the first packets of an AVI's MPEG-4 video with B-frames carry no
    time, and a decoder leaves out an audio track's start-up samples only
    when the container records how many there are. A revert that placed an
    AVI by its first timed packet brought the video back 80 ms late with
    every packet the same. No AVI here loses a track, as FFmpeg cannot tag
    an AVI track's language and Remuxarr drops no audio from a file whose
    tracks carry none (tried with a two-track AVI in all 22 profiles), so
    that case is covered by `tests/test_revert_timing.py` instead.
30. Every processed file keeps its own tags, as opposed to its streams'
    (check 26): each name, matched ignoring case as a Matroska stores names
    in capitals and an MP4 in lower case, with the same value. An MP4's
    brand (`major_brand`, `minor_version`, `compatible_brands`) and
    `encoder` and `software`, which muxers write for themselves, are left
    out, and `creation_time` counts as kept in the same second in an MP4,
    which holds whole seconds. A conversion to MP4 under Tags an MP4 Can't
    Hold's `drop_tags` may lose the tags the worker found an MP4 would lose
    when it took the job, listed as an accepted loss, and no others, never
    `creation_time`. The worker makes the same comparison before a file is
    replaced, and this check is what shows it held.
31. Every kept track of a Matroska original whose language has a region or
    a script keeps exactly that tag. ffprobe reports only a track's old
    three-letter code, so checks 4 and 26 cannot see this: these read
    `mkvmerge -J`, whose track numbering matched ffprobe's for every
    Matroska file of the library. A track the plan gives a language is left
    to check 4. Losing the region (none, or only the language) is the
    accepted loss below; any other tag in its place is a finding.
32. A Matroska original's chapters survive, as `mkvextract` reads them:
    every edition with its flags, nested chapters, each chapter's names
    with their languages, and the default edition's top-level chapters at
    the same starts under names the original gave them. ffprobe shows one
    edition with one name per chapter, which is all check 3 counts.
33. A Matroska original's tags that checks 26 and 30 cannot see survive:
    tags at a level other than 50 (the collection's at 70, a chapter's at
    30), tags aimed at a chapter, an edition or an attachment, and nested
    tags, compared in any order, as FFmpeg reorders a file's tags (file 37)
    and order means nothing to a player.

    For 31 to 33, measured with FFmpeg n8.1.3: a Matroska file FFmpeg
    writes keeps each track's old code and no BCP 47 one, the first
    edition's top-level chapters with one name each (the last, or the TITLE
    of a tag aimed at that chapter, which it shows as the chapter's name)
    and no chapter language, and no tag at another level. A job whose
    output FFmpeg wrote (check 34 says when that is right) lists each of
    those losses as an accepted loss, as does a conversion to MP4, which
    holds a flat list of chapters and no tag levels. Anything else is a
    finding: a chapter that moved, one shown under a name it never had, a
    tag or a region that appeared, and any loss at all in a file mkvmerge
    wrote. The same checks run after each revert, where every loss is a
    finding: a revert writes the regions, the chapters and the tags back
    with mkvpropedit from its point, unless mkvmerge numbers the original's
    tracks differently from ffprobe, where it writes none.
34. A Matroska output is written by mkvmerge exactly when it should be: when
    it keeps something FFmpeg drops (any of 31 to 33's losses, but a
    chapter's language alone) and nothing stops it. What stops it, as the
    app was measured to need: a WebM; tracks `mkvmerge -J` numbers
    differently from ffprobe; a kept track whose old language code mkvmerge
    reads differently (`en` as `eng`, `undefined` as `und`), as it would
    write its own reading back; and a kept text subtitle that is not UTF-8,
    whose bytes mkvmerge would replace and on which it exits 1, sending the
    job back to FFmpeg. The tool is read from the output's writing
    application. Worked out here rather than taken from
    `app/core/mkvtoolnix.py`, so the check does not take its answer from the
    code it checks.
35. In a profile whose Keep Subtitle Languages holds an entry with a region
    or script (`r01`), a subtitle whose language, as `mkvmerge -J` reads it,
    is that entry or a narrower tag under it is kept, and one of the same
    language matching no entry is dropped, unless its language is also
    listed plainly. Forced subtitles (by flag or title), answered ones and
    ones the plan gives a language are left out, since other rules decide
    those.
36. After a revert of a Matroska original, as `mkvmerge -J` reads both, the
    track UIDs, the attachments (UID, name, size and type, in any order),
    the segment's title and its UIDs are the original's. FFmpeg gives every
    track and attachment a new UID, which a tag aimed at one names, and
    makes a film-level TITLE tag the segment's title; the revert sets them
    back (`mkvtoolnix.write_matroska_record`). Unless the tracks are
    numbered unalike, as for 31 to 33.

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

20. Every add succeeds, and nothing logs an exception. An add refused over
    the file's own tags is an accepted loss, as in check 1, unless one of
    them is `creation_time`, which Forge writes back too.
21. The file gains exactly one AC3 5.1 track carrying Forge's marker, placed
    after the last audio track, and every other stream is as it was (the
    same comparison as check 11, with every packet checksum equal: an add
    copies the streams it keeps). An MP4 or QuickTime file keeps its major
    brand and minor version; its compatible brands may gain `dby1`, which
    the MP4 muxer lists for any file with an AC3 track.
22. The forged file probes and decodes cleanly.
23. A full rescan queues nothing for a forged file that had nothing left to
    do before the forge.
24. With `_forge_undo`, every add is undone through the route function, and
    the file is back to exactly the streams it had before the add, packet
    checksums included, and an MP4 or QuickTime file to its brand.
25. No Forge temp file is left, no Forge job is still pending, processing
    or waiting to undo, and nothing in the library changed but the forged
    files.

Then, in each profile with revert on (16 of the 23; pairwise, so every value
of every other setting meets revert on at least once), every revert point is
restored through the app's own `restore_revert_point`, and:

9. A file has a revert point exactly when its job lost a track a point can
   hold, which is anything but an attachment. With revert off, no file has
   one.
10. Every restore succeeds, onto the original name and extension, and a
    converted output goes away.
11. The restored file matches the original stream for stream (type, codec
    and codec tag, language and title tags, disposition flags, attachment
    filename and mimetype, order), and an MP4's fast-start layout and an
    MP4 or QuickTime file's brand match too, as does the file's
    `creation_time`, exactly: a revert writes back the original's, so a
    Matroska's fraction comes back even where the MP4 between held whole
    seconds. So do the file's other own tags, as check 30 compares them,
    exactly, with none added: a conversion under `drop_tags` lost the ones
    an MP4 cannot hold, and the revert is where they come back. The codec tag (an MP4 sample
    entry, an AVI fourcc) and the brand are what tell a QuickTime movie
    from an MP4 of the same streams. Each
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
holds is refused, a revert gives Matroska cover art back after the file's
other attachments (attached again, as a job does; back as a video track it
is a finding, check 11), an existing subtitle file a job replaced or moved stays that way after a
revert, a job goes ahead without a revert point when capture reports one
unavailable and Require Revert Point is off, an answer for a track a
revert brings back from the sidecar is asked again, a job whose audio
cannot be copied fails and leaves the file as it was, since nothing is
re-encoded to get past it, a revert of a Matroska file converted to MP4
gives an audio track back without the encoder delay it recorded (check 29
accepts that only when playing those samples, as many as the restored
track's `initial_padding` lost, is all that moved), and a job or a Forge
run whose output would lose some of the file's own tags is refused and
leaves the file as it was, except over its creation date, which both write
back. A job finds the tags an MP4 would lose before it runs and does what
Tags an MP4 Can't Hold says, so the refusal is accepted only where they
could not be found out, or where the job kept a container other than MP4
(check 1), and a conversion under `drop_tags` that loses tags found that
way, and no others, is listed too (check 30). What FFmpeg drops from a
Matroska file and an MP4 cannot hold (checks 31 to 33) is listed too, as
the root README says for a conversion and a revert. These are listed
under "Accepted losses" with where each decision is recorded, and do not
count as findings. Add to
`KNOWN_LOSSES` in `explore.py` only when the decision is written down
somewhere a reader can check.
