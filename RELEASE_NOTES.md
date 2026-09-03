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

- Searching by filename now treats `_` and `%` as ordinary characters. Both were being passed through to the database as wildcards, so `_` matched any single character and `%` matched everything — a search for `The_Movie` also returned `TheXMovie`, and one containing a `%` returned rows that matched nothing you typed. Affects History, Audio and Subtitle Language Review, and the AC3 Forge candidate list; on that last one the wrong row could be the one you clicked ADD AC3 on.

- Reverting a file now lets the next ordinary scan re-evaluate it. The revert rewrote the file's stored size and timestamp to match the restored file, so every delta scan — including every scheduled one — saw nothing had changed and skipped it, leaving the file reachable only through a forced full rescan. Note the consequence: if you revert without changing the settings that produced the job, the next scan will queue it again.

- Retry All now refreshes every History tab, not just Failed. A retry that turned failures into skips or review items left the Skipped tab showing rows that had moved, and left Failed showing rows that were gone when nothing was requeued at all.

- Removing orphaned entries under Maintenance now refreshes the rest of the app. The queue, history, review and recycle bin carried on showing the rows it had just deleted until you reloaded the page, and opening one of those rows failed.

- The History panel no longer goes blank when a job finishes while a tab is still loading. The tab settled on "No success items" with the real count still shown in the badge beside it, and stayed that way until you switched tabs or another job of that same kind completed.

- The planned action for an extracted subtitle no longer shows the wrong language. With automatic tagging of undefined tracks turned on, the row named the corrected file but kept the old `[und]` tag beside it, so the tag and the filename disagreed. The file itself was always correct.

## Changed

- The Sonarr and Radarr "Enable Integration" descriptions now say what those switches actually do. They control the rescan Remuxarr sends after a job finishes; they have never controlled whether incoming webhooks are acted on. If you want Remuxarr to stop processing webhooks, remove the webhook in Sonarr or Radarr itself. Nothing has changed about how your setup behaves.
