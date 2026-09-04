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

- The AC3 Forge page no longer shows a job that is not running. When the backend returned an error while the page was refreshing, the error was read as a job: the panel showed FORGING against "Unknown file" with the bar stuck at 0.0%, and nothing polls it, so it stayed that way until you navigated away or a real forge job finished. The completed-jobs list below it was emptied by the same failure. Both now keep showing whatever they last loaded successfully.

- The Settings page no longer takes the whole app down when the backend answers with an error. An error reply was being read as though it were your settings, and the page then failed on it hard enough to blank the entire interface until you reloaded. It now shows the same "couldn't load settings" message it already showed for a backend it could not reach. The same fix covers a quieter version of this, where every setting on the page rendered a default it had never been given and saving would have written those defaults back.

- A list that scrolls to load more no longer gets stuck reloading the same page. If the server reported more results than the page it sent back — which could happen when rows were removed while you were scrolling — the list kept asking for the same page indefinitely, spinning without ever growing and putting steady load on the server until you navigated away. Affects History, Audio and Subtitle Language Review, and the AC3 Forge candidate list.
