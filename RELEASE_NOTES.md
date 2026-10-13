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

- Windows narrower than 860px now get the phone layout, which used to start at 640px. Between 860px and 1140px the header is more compact: the logo shows without its name, DRY RUN and PAUSE / RESUME show as their symbols, AUTO / MANUAL drops its symbol, and scan progress shows as "✕ 123/456". Hover over a control to see its full name.

## Fixed

- The selected tab in History, and the selected level in the log viewer, now show their full outline when the page loads. Before, the right edge was missing until you clicked another tab and back, and that click also doubled the lines between the tabs.
- Long entries in list settings, such as library paths, Exclude Patterns and Always Include, now wrap inside their box. Before, an entry with few spaces, like a release-style folder name, ran off the edge of the box and took the × that removes it with it.
- On a narrow phone, the buttons in Maintenance & Logs and in Backup & Danger Zone now move below their description. Before, they squeezed the description into a thin column, and on the smallest screens CHECK FOR ORPHANED FILES and REPLACE DATABASE ran off the edge.
- In narrower windows, the header no longer squeezes its buttons until their labels wrap onto two lines, or pushes PAUSE, SCAN and LIVE off the screen.
- The History panel's tabs now have a row of their own, under HISTORY and its count, with RETRY ALL or CLEAR ALL beside the title. Before, on a phone or in a narrower window, the tabs squeezed until their labels broke onto two lines, and DRY RUN and RETRY ALL or CLEAR ALL ran past the edge of the panel, out of reach.
