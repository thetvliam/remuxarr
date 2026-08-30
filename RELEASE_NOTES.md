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

- Adding a language code or path that is already in a list now says so instead of doing nothing. The box also accepts a comma to separate entries, and Escape to clear it, matching the schedule-times field.

- Pause/Resume and Scan now tell you when they fail. Both used to do nothing visible if the request was refused or the backend was unreachable — the button simply stayed as it was, which looked exactly like a click that had not registered.

- Clearing the database from Settings > Backup & Danger Zone now refreshes the rest of the app. Previously the queue, history, forge and revert panels kept showing rows the wipe had already deleted until you reloaded the page, and acting on one of those rows failed because the item no longer existed.

- Leaving Settings with unsaved changes by pressing Back — the browser button, or the Android system one — now asks first, the same way clicking away to another tab already did. Previously only tab clicks were caught, so Back discarded the edits with no prompt.

- A settings change typed while an earlier save was still going through is no longer discarded. Previously the page re-read the server's values when the save landed and wrote them over anything you had touched in the meantime: a text field silently reverted, and a number field kept showing what you typed while the page had already dropped it, so the next save sent nothing and the bar said there was nothing to save.
