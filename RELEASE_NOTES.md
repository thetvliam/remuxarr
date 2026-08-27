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

## Added

- Two new themes in Settings > Appearance: **Midnight**, a cool dark theme, and **Dawn**, the first light theme. Your current theme is unchanged and neither is selected for you.

## Changed

- Undefined-language handling is now set separately for audio and subtitles, so you can tag undefined audio automatically while holding undefined subtitles for review. **On upgrade the audio side starts at "Always leave" regardless of your previous setting** — if you had it on "Always fix", set the new "Fix Undefined Audio Language Tags" back to that. Your subtitle setting carries over untouched.
- Subtitles renamed through Subtitle Language Review now use the two-letter code (`Show.en.srt`) instead of the three-letter one (`Show.eng.srt`), matching what automatic extraction already produced. If you have scripts or Plex agents keyed on the old name, they will need updating.

## Fixed

- Ignoring a file in Audio or Subtitle Language Review now clears every flagged track on it. Previously it cleared one, so the file stayed on the review page it had just been ignored from.
- Applying a language to several tracks of one file now reprocesses that file once instead of once per track, and the confirmation counts files rather than tracks.
- The queue no longer starts a job on a file while a revert is rewriting it. The two could previously both write the same file, leaving whichever finished last.
- Emptying the recycle bin, or discarding a revert point, is now refused while a revert is running rather than deleting the sidecar being restored from.
- When a candidate file is rejected while matching a revert point, the reason is now shown next to the list instead of a generic failure message.
