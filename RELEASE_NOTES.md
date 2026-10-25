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

- With Auto-Start off (MANUAL), files Sonarr or Radarr import now wait in the queue. Turning Auto-Start off while processing was running stopped nothing, and after a Resume every later import was processed as it arrived. Turning it off now pauses processing; a job already running finishes. Resume processes what is in the queue and pauses again once it is empty. Turning Auto-Start back on does not resume a paused queue: press Resume. The setting is now called Auto-Start Processing (Settings, Worker), where it was Auto-Start Processing After Scan, and keeps its value.
- Exclude Patterns and Skip Trailers and Extras now also apply to files already waiting in the queue. A pattern you added after a file was queued did not stop it, and Retry, Retry All and Process Now processed a file the settings excluded. Such a job is now skipped when its turn comes, and the Skipped tab says which setting excluded it. Always Include still brings a file back.
- Sonarr and Radarr are now told about files Remuxarr found by scanning your library, not only about files their webhooks sent. They were never asked to rescan after such a file was processed, so after a file was converted from MKV to MP4 they went on recording the old .mkv until their own next refresh. Remuxarr now finds which series or movie the file belongs to from the folder it is in, using the Container Paths under Settings, Integrations, Sonarr and Radarr, and asks for a rescan as it does for webhook files. Once found, the series or movie is remembered for that file and not looked up again.
