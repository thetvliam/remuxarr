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

## Fixed

- AC3 Forge undo no longer removes an AC3 5.1 track the file already had. Once a job had removed the forged track, for instance after its language left Keep Audio Languages, undo removed the file's own AC3 instead, and could leave the file with no audio. Tracks forged in MKV and MP4 files from now on carry the handler name AC3 5.1 (Remuxarr), which undo goes by; for tracks forged before this update, and in AVI, TS or WMV files, undo now refuses to remove a file's only audio track. When a job removes the forged track, the file no longer counts as forged, so the Forge page offers it again if it still has an AAC 5.1, without an undo that has nothing to remove. This does not happen for a file forged before this update that has an AC3 5.1 of its own.
- A scan or Re-check Scanned Files that reaches a file while its job is running now leaves it alone, and the file is looked at again when the job ends (after a failed job, at the next scan). It used to decide from the file as it was before the job: a review raised then could name the wrong track, so your answer was applied to a different subtitle; the file showed as skipped while it was being processed; and work the job was not doing was forgotten. A review raised this way before this update is corrected by running Re-check Scanned Files; until then, answering it is refused rather than applied to another track. A file whose queued job is no longer needed, after a settings change and a Re-check, is now also taken out of the queue, and History no longer lists a file twice when its job is found to need nothing or a review as it starts, or when your Review answer settles it.
