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

- Remuxarr no longer re-encodes audio it cannot copy. When a file had a corrupt audio track, or audio with no timestamps (some older AVI files), the job was quietly run again with every audio track it kept re-encoded to AAC, including tracks that were fine, while the job's plan still said "copy". Such a job now fails, says that the audio cannot be copied, and leaves the file exactly as it was. Nothing in processing re-encodes any more; AC3 Forge, which you run yourself, is the only place a track is re-encoded. Files processed before this update keep the audio they were given.

## Fixed

- With Fix Undefined Language for audio set to Always Fix, an untagged audio track is now judged by Keep Audio Languages as the language it is about to be tagged (Primary Language). It used to be kept for being untagged, tagged, and then removed by the next scan when Primary Language was not in the list, so the file was processed twice. Kept that way, it could also leave a file with only that track: when no tagged track was in the list and Keep Default Audio was off, every tagged track was dropped, a film's main audio included, and an untagged director's commentary was what remained. Now the first audio track is kept as a last resort instead, as it is for any file where nothing else would be.
- Reverting a file no longer loses a track when the job had removed one track and written the same language onto another of the same kind: an untagged subtitle kept and tagged English while an English subtitle was removed, for example, as happens when an untagged subtitle is answered in Subtitle Language Review. The revert point used to save the kept track rather than the removed one, so a revert gave back two copies of the kept track and the removed track was gone. Revert points recorded before this update may still have saved the wrong track, and cannot be repaired.
- A track you have answered in Audio or Subtitle Language Review is now always kept, and treated as the language you gave it. A track answered in a language not in Keep Audio Languages or Keep Subtitle Languages used to be kept and tagged, then removed by the next scan; and in a file that also had an untagged audio track, answering the language of a track kept only as a last resort could remove it straight away, keeping the untagged one in its place. Choosing Delete for a subtitle in Manual Review still removes it.
- The plan for an untagged subtitle that Fix Undefined Subtitle Language Tags took as Primary Language and then removed now says which language it was taken as. It said "und".
