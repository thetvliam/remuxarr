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
- Videos from a phone or a camera are now left as they are, whatever the settings, with the reason in History. Remuxarr is for downloaded films and series, and processing a recording cost it what a download does not have: an iPhone video lost its metadata tracks and a GoPro video its telemetry, with no Recycle Bin entry able to hold them, and an Android phone's video lost its device tag. An MP4, M4V or MOV file counts as a recording when it has a data track of the kind cameras write, a GPS location, a device tag or a camera's file brand, which every QuickTime `.mov` has. AC3 Forge no longer offers such a file, and refuses one already in its queue; removing the AC3 track it added to one before this update still works. A recording with none of these, such as one an editor or a messaging app has rewritten, looks like any download and is still processed. Recordings processed before this update stay as they are.
- A file is now left as it was when processing it, or AC3 Forge adding or removing a track, would lose any of the file's own tags, with the tags named in History. Remuxarr cannot write every tag an MP4 can hold: an MP4 tagged the way iTunes and tag editors tag one lost its content rating, cast and crew, purchase date and sort name whenever it was processed, and a Matroska converted to MP4 lost any tag an MP4 has no place for. No Recycle Bin entry could bring them back. Such a file is not queued again by later scans unless what Remuxarr would do to it changes, for instance with Prefer MP4 turned off, which keeps a Matroska as a Matroska with its tags. Tags the file format itself rewrites, such as the program that wrote the file, are not counted.

## Fixed

- Retry, Re-process and Process Now in a job's window now say what happened: queued again, waiting in Review, or needing no changes. They said "Re-queued" every time, including when nothing was queued. When a retry is refused, the message now gives the reason, such as the file no longer being on disk.
- Clear Database is now refused while a job, an AC3 Forge run, a revert or a scan is running, with the reason shown. Clearing during a job let it save its revert point afterwards, and the point then appeared in the Recycle Bin under an unrelated file.
- The AC3 Forge page shows the job that is running. When the oldest waiting job had to wait, because its file was being processed or reverted, the page showed that one as queued and "waiting for a free worker slot" while a newer job ran unseen, with no progress shown.
- An AC3 Forge job no longer stays at Processing after an unexpected error. An error before the file was touched left it there until the container restarted, unable to be cancelled and holding up that file's other processing; it now fails at once, with the error in the Forge page and the container log. An error after the track was added used to leave the job unfinished too, so that after a restart the file was offered again and a second add gave it two AC3 tracks; it is now recorded as done.
- Settings no longer accepts a negative Job Timeout, which stopped every job, AC3 Forge run and revert as soon as it started with a "timed out" error. Job Timeout and SMTP Port now take 0 or more, where 0 still means no timeout and the default port; Concurrent Jobs and Consecutive Failure Threshold take 1 or more, which is how anything lower was already treated. A negative Job Timeout saved before this update stays until it is changed: set it to 0 or more in Settings, Worker.
- Fix Undefined Subtitle Language Tags works again on installs that turned it on before it had three choices, in July, and have not changed it since. Since the last update it was treated as Always Leave and shown as Always Leave in Settings, so untagged subtitles stopped being tagged. It is now Always Fix again, as it was set. To tag the subtitles of files scanned in the meantime, run Re-check Scanned Files (Settings, Maintenance & Logs).
- AC3 Forge no longer overwrites a file that changes while it is adding or removing a track. If Sonarr or Radarr imported an upgrade at the same name during the run, Forge replaced the upgrade with the old file plus its AC3, and a file deleted during the run was written back. Such a run now fails with nothing written and says why in the Forge page. If the new file needs an AC3 too, add it again from AC3 Forge.
- AVI files converted to MP4 keep their frames in the right order. An AVI stores no display times, and for video with B-frames, as most Xvid and DivX files have, the converted file timed its frames in the order they are stored rather than the order they are shown, and FFmpeg dropped the last frame when reading it back. Files already converted keep the wrong timing, unless they have a Recycle Bin entry to revert and convert again. Xvid and DivX files with packed B-frames, which many encoders wrote, cannot be converted correctly, so they now stay AVI (see Changed).
- An AVI file kept as AVI is no longer queued again on every full scan. With Prefer MP4 Container off and Fix Undefined Audio Language Tags set to Always Fix, Remuxarr tried to tag its untagged audio, but AVI files cannot store language tags, so the tag never stuck. Such a file is now left as it is, with the reason in History, and AVI files no longer appear in Language Review, since an answer could not be written to them. Turn on Prefer MP4 Container to convert AVI files to MP4.
- AC3 Forge and reverting no longer add an extra data track to an MP4 with chapters. Each Forge run, undo or revert on such a file left one more. Files that already have them keep them; they are no longer multiplied.
- Reverting works on QuickTime `.mov` files with chapters again. Since the last update it failed on such a file and left it unchanged.
- Reverting an MP4 with a timecode track no longer fails, and neither does AC3 Forge on one. Every revert of such a file failed and left it as it was, and so did every Forge run. The timecode is kept, though in an MP4 where it was not the last track it now comes last.
- Cover art is kept when Remuxarr writes an MP4. Every job that left a file as MP4, or converted one to MP4, removed its cover, even a job that only changed a language tag. A JPEG, PNG or BMP cover now stays, including a Matroska file's when it is converted. A GIF or TIFF cover cannot be stored in an MP4 and is still removed in a conversion, as fonts are. Files processed before this update stay without their cover, unless they have a Recycle Bin entry: reverting that brings the cover back.
- Files keep their creation date when Remuxarr processes them or AC3 Forge adds or removes a track. Every job that wrote an MP4, a QuickTime `.mov` or an MKV, even one that only moved the index for fast start or changed a language tag, removed the date stored in the file, and so did every AC3 Forge add and undo. For a video from a phone or a camera that is the day it was recorded, which photo libraries sort by. Files processed before this update stay undated, unless they have a Recycle Bin entry to revert to.
