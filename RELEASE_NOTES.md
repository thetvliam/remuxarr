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

- Cancelling a job just as the worker picked it up could let it run anyway: it was either put back in progress, or Cancel said it had worked while the job carried on under a cancelled entry in History, where Retry or Dismiss would act on the running job. A job is now either cancelled before it starts, or the cancel fails because it has already started.
- Answering a question in Audio or Subtitle Language Review just as the worker picked up or started that file's job could log "Failed to claim next job" and, with auto-start off, pause the queue as if it had finished, or take the running job out of the queue, losing its result and, with more than one job at a time, starting a second job on the same file. The answer and the worker now take turns, and an answer given while a job runs is applied after it.
- For a file on a slow or sleeping disk, a Language Review answer, Retry and Retry All held up the queue while the file was read again. Past 30 seconds the worker could stop as if the queue were empty (with auto-start off), and a job finishing then could be recorded as failed with "database is locked". The file is now read before its job is replaced.
- A Language Review answer for a file Remuxarr could not read removed the file's job from the queue, and only a full scan queued it again. The job now stays and applies the answer when it runs; if the file still cannot be read then, it fails and can be retried.
- A Matroska file whose creation date has fractional seconds, as mkvmerge writes it, could not be converted to MP4: the job failed saying the file would lose creation_time, and the file was left as it was. MP4 keeps the date to the second, which now counts as keeping it (issue #61).
