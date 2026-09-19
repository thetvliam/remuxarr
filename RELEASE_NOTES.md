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

## Changed

- Manual Review now asks once per question instead of once per file. Files with the same subtitle tracks in the same folder are grouped into one card, so a season answered the same way is one decision rather than twelve. Each track offers Keep, Extract SRT or Delete, with Extract writing an external .srt and taking the track out of the file; image subtitles and tracks whose extraction failed offer Keep and Delete only. A single file can be set on its own, a card can be skipped, and the line under each card says what will happen to the files - whether they convert to MP4 or stay MKV - worked out by the same engine that will do the work. Nothing is written until you press Apply, which reports one summary. The two "resolve all" buttons are gone: they answered every file in the library of one kind at once, which is what a card now does deliberately and in sight of what it affects.

- Retry All in History's Failed tab now retries failed items only. Cancelled items (ones you skipped in Review, removed from the queue or aborted) stay where they are instead of all coming back at once; open one and press Retry to re-queue it. They still return on the next scan, as before.
- A file with both image-based subtitles and embedded fonts now shows all of its flagged subtitle tracks in one review, instead of asking about the image subtitles first and the styled ones only after those were answered.
- Undefined audio language tags no longer hold a file for manual review. Every undefined track is flagged in Audio Language Review instead, where you can set each one's language or confirm they are correct as they are, and the file keeps moving through the queue in the meantime. Files already waiting on this move across on the next scan. "Fix Undefined Audio Language Tags" set to Always Fix no longer guesses at a file with several undefined tracks. The list of confirmed files on the Review page is now Confirmed Undefined-Audio Tracks, and its button reads Clear Confirmation.

## Fixed

- An answer given in Review stopped applying to the track it was given for once the file had been processed. Processing renumbers the streams it keeps, and answers were stored against the old numbers, so a file you had answered came back asking the same question on the next full scan. Answers are now stored against the track itself, and existing ones are converted when Remuxarr starts.

- Approving, skipping or resolving a file in Review now reports what happened, including when dry run means a preview is written rather than the file being changed. Only failures were reported before, so a card just vanished from the list and a failed Approve looked exactly like a successful one.
- Approving a file held only by the undefined-audio threshold now tells you when the file will not be processed. If nothing else needs doing it is marked Skipped rather than converted, which the page previously described the wrong way round.
- The Review page's IGNORE button is now CONFIRM CORRECT, and says what it does. It asserts the existing language tags are right, which is permanent — those files are never flagged for language again. Both buttons now name their units, since one counts tracks and the other counts files.

## Added

- The Review page now lists files you approved past the undefined audio track threshold, and lets you take an approval back. Approving exempted a file from that check permanently, with nothing showing which files were affected; cleared files return to review on the next scan and are not otherwise touched.
