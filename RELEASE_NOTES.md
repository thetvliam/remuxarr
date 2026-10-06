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
3. ONLY THEN is this file emptied, on `testing`, as the first commit of the
   next cycle.

Step 3 is after step 2 and not part of it. Emptying the file in the merge
itself ships an empty file to main, and the release nobody was told about
is the one that renamed their settings. main keeps the last released set
until the next merge replaces it.

An empty file — no `##` sections — means no dialog. That is the correct
state for a cycle in which nothing user-visible has changed yet.
-->

## Fixed

- In the Audio and Subtitle Language Review lists, a track could appear twice after more of the list loaded, and "SELECT ALL LOADED" counted it twice.
- On the Review page, a card answered file by file, with every file's tracks set on their own, was not applied. It is now, as long as every file has an answer for every track, set on the file or on the card.
- On the Review page, a card could say its files would convert when some of them could not be answered as chosen, and you found out only after pressing Apply. The line under the card now says how many cannot, and why.
- Recycle bin (beta): a revert could bring back the wrong track when the job had removed one track and kept a similar-looking one, such as two audio tracks with the same codec and channels, or two subtitles with identical labels. You got two copies of the kept track and lost the removed one. Revert points made from now on keep both; points made before this update cannot be repaired.
- Recycle bin (beta): reverting a file that had been renamed and matched back put it back under its old name and deleted the renamed file, or failed if its folder had been renamed. It now keeps the file's current name and folder and restores only the original extension. A revert also no longer overwrites a different file that already has the name it would write; it refuses and says which file is in the way.
- Recycle bin (beta): a reverted file could have some of its tracks written out of order. Subtitles showed from the start but disappeared after seeking, most often with styled subtitles and fonts. Reverts now write files in order. Files reverted before this update keep the problem.
- With more than one concurrent job allowed, a normal job and an AC3 Forge run could rewrite the same file at the same time, and the one that finished last silently replaced the other's work. They now take turns on a file, and a recycle bin revert waits for the forge too.
- Recycle bin (beta): using AC3 Forge on a processed file made its recycle bin entry unusable, saying the file had been modified. Forge runs now keep the entry up to date, and undoing a forged AC3 track that was there before processing keeps that track restorable. Reverting a file forged after processing returns it to the original, without the forged track.
- Recycle bin (beta): a revert now tells Sonarr, Radarr and Plex about the restored file, as a finished job does, so it shows up without a manual refresh — including when the revert changed it back from MP4 to MKV. Sonarr and Radarr are told about files that reached Remuxarr through their webhook, as for jobs.
- AC3 Forge: after a recycle bin revert removes a forged AC3 track, the Forge page no longer lists the file as forged, and it can be forged again straight away. A track forged before processing comes back with the revert and stays listed.
- Recycle bin (beta): a revert now keeps the file's own title and other file-level tags, including ones an MP4 conversion could not carry. Files reverted before this update stay as they are.
- Recycle bin (beta): when a job had to re-encode the audio because the source was damaged, a revert brought back the re-encoded audio instead of the original. Revert points made from now on keep the original audio. If the original cannot be stored, the job carries on without a revert point, or with "require a revert point" on, stops and says why.
- Recycle bin (beta): a revert point that failed to save part-way, or was stopped by cancelling the job, no longer leaves a partial file on the recycle volume until the next restart.
- Recycle bin (beta): a revert now stops after the Job Timeout setting, as jobs do, instead of waiting forever on a stalled FFmpeg and keeping the file locked until a restart. The file and its recycle bin entry are left as they were, so the revert can be tried again.
