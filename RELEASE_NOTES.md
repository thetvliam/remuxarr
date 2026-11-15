# Release Notes

<!--
WHAT THIS FILE IS
=================
The pending release notes: what has changed since the last merge to main
that a USER would notice. Remuxarr serves it at /api/release-notes/ and the
UI shows it once, as a dialog, the first time someone loads the app after
the content changes.

It is not a changelog. A changelog accumulates; this file is emptied at the
start of each cycle, and only ever holds what is new since the last release.
Git history is the permanent record.

WHAT GOES IN IT
===============
One line per change a user could notice without reading the source. Written
for someone who runs the container, not for whoever reviewed the diff.

  Yes  a setting changed name, moved, or now defaults differently
  Yes  behaviour changed in a way that alters what ends up in their library
  Yes  a file naming convention changed
  Yes  something they reported is fixed
  Yes  an upgrade will silently reset or migrate part of their config

  No   refactors, renames, type hints, dead code removal
  No   test coverage, mutation testing, CI, lint
  No   internal fixes with no user-visible symptom
  No   anything whose honest description is "you would never know"

If a change needs the user to DO something, or will surprise them on
upgrade, say so plainly and say what to do. That is the whole reason this
is a dialog and not a file nobody opens.

FORMAT
======
`## Heading` starts a section, `- item` is an entry. Headings are free text
— use whatever describes the batch, e.g. Changed / Fixed / Added, or
something more specific. Everything above the first `##` is ignored, which
is why this comment is safe here. Keep entries to a sentence or two;
anything longer belongs in the docs, with a pointer from here.

THE CYCLE
=========
1. Work lands on `testing`. Whoever makes a user-visible change adds a line
   here in the same commit.
2. `testing` merges to `main` WITH those entries intact. Users pull main,
   the content hash changes, and the dialog shows once.
3. ONLY THEN are the released entries removed, on `testing`, in the same
   commit that adds the first entry of the next cycle. A cycle with nothing
   user-visible to add leaves the released entries in place until one
   arrives; main still holds them, so nothing is shown twice.

Step 3 is after step 2 and not part of it. Emptying the file in the merge
itself ships an empty file to main, and the release nobody was told about
is the one that renamed their settings. main keeps the last released set
until the next merge replaces it.

An empty file — no `##` sections — means no dialog. That is the correct
state for a cycle in which nothing user-visible has changed yet.
-->

## Changed

- AVI files whose Xvid or DivX video stores its B-frames packed, as many older encoders did, are no longer converted to MP4, even with Prefer MP4 Container on. Their frames cannot be timed correctly in an MP4 without altering the video, which would stop a revert giving back the original. They stay AVI, with the reason in History, and still get any other changes they need. Most Plex clients transcode this kind of video whatever it is stored in. Files already converted stay converted; reverting one gives back the AVI, which then stays AVI.
- Videos from a phone or a camera are now left as they are, whatever the settings, with the reason in History. Remuxarr is for downloaded films and series, and processing a recording cost it what a download does not have: an iPhone video lost its metadata tracks and a GoPro video its telemetry, with no Recycle Bin entry able to hold them, and an Android phone's video lost its device tag. An MP4, M4V or MOV file counts as a recording when it has a data track of the kind cameras write, a GPS location, a device tag or a camera's file brand, which every QuickTime `.mov` has. AC3 Forge no longer offers such a file, and refuses one already in its queue. A recording with none of these, such as one an editor or a messaging app has rewritten, looks like any download and is still processed. Recordings processed before this update stay as they are.

## Fixed

- AVI files converted to MP4 keep their frames in the right order. An AVI stores no display times, and for video with B-frames, as most Xvid and DivX files have, the converted file timed its frames in the order they are stored rather than the order they are shown, and FFmpeg dropped the last frame when reading it back. Files already converted keep the wrong timing, unless they have a Recycle Bin entry to revert and convert again. Xvid and DivX files with packed B-frames, which many encoders wrote, cannot be converted correctly, so they now stay AVI (see Changed).
- An AVI file kept as AVI is no longer queued again on every full scan. With Prefer MP4 Container off and Fix Undefined Audio Language Tags set to Always Fix, Remuxarr tried to tag its untagged audio, but AVI files cannot store language tags, so the tag never stuck. Such a file is now left as it is, with the reason in History, and AVI files no longer appear in Language Review, since an answer could not be written to them. Turn on Prefer MP4 Container to convert AVI files to MP4.
- AC3 Forge and reverting no longer add an extra data track to an MP4 with chapters. Each Forge run, undo or revert on such a file left one more. Files that already have them keep them; they are no longer multiplied.
- Reverting works on QuickTime `.mov` files with chapters again. Since the last update it failed on such a file and left it unchanged.
- Reverting an MP4 with a timecode track no longer fails, and neither does AC3 Forge on one. Every revert of such a file failed and left it as it was, and so did every Forge run. The timecode is kept, though in an MP4 where it was not the last track it now comes last.
- Cover art is kept when Remuxarr writes an MP4. Every job that left a file as MP4, or converted one to MP4, removed its cover, even a job that only changed a language tag. A JPEG, PNG or BMP cover now stays, including a Matroska file's when it is converted. A GIF or TIFF cover cannot be stored in an MP4 and is still removed in a conversion, as fonts are. Files processed before this update stay without their cover, unless they have a Recycle Bin entry: reverting that brings the cover back.
- Files keep their creation date when Remuxarr processes them or AC3 Forge adds or removes a track. Every job that wrote an MP4, a QuickTime `.mov` or an MKV, even one that only moved the index for fast start or changed a language tag, removed the date stored in the file, and so did every AC3 Forge add and undo. For a video from a phone or a camera that is the day it was recorded, which photo libraries sort by. Files processed before this update stay undated, unless they have a Recycle Bin entry to revert to.
