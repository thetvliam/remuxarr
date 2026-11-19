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

- In Settings → Recycle Bin, Match now opens under the entry you clicked it for. With a long list of unmatched entries it opened below the whole list, off the screen, so clicking Match seemed to do nothing.
- On the Review page, the tracks you have ticked in Audio Language Review and Subtitle Language Review stay ticked when a job finishes, a file is queued or a scan ends. Every one of those cleared them, so on a busy queue a selection could vanish while you were making it.
- On the Review page, Confirmed Undefined-Audio Tracks keeps the files you have ticked, and keeps its list on screen, when a job finishes, a file is queued or a scan ends. Each of those unticked everything and blanked the list to "Loading…".
- Audio Language Review, Subtitle Language Review and the AC3 Forge candidates list keep their rows on screen when they refresh, instead of emptying and jumping back to the top. A finished job, a queued file or a finished scan refreshed the review lists, and every ADD refreshed the candidates, each time keeping only the first page of rows.
- On the Review page, cards you have scrolled down to stay on the page when a job finishes or a file is queued. Each of those reloaded only the first 25 cards, so the card you were answering could disappear from under you.
- The History panel on the dashboard keeps its rows on screen when a job finishes, instead of emptying and jumping back to the top. On the Success tab every successful job did that, each time keeping only the first 50 entries.

## Removed

- The `DELETE /api/history/clear` API endpoint, which deleted finished History entries in bulk and which nothing in Remuxarr used, has been removed: a script calling it now gets an error and nothing is deleted. Dismiss in a job's window still removes one entry.
