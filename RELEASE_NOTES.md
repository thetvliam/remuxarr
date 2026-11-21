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

- A job cancelled just as the worker picked it up could run anyway: depending on the moment, it was put back in progress, or Cancel reported success while the job ran under a cancelled entry, and Retry or Dismiss on that entry in History then acted on the running job. Now the job is either cancelled before it starts, or the cancel fails because the job has already started. Answering a question in Audio or Subtitle Language Review while the worker was picking up that file's job logged "Failed to claim next job" and, with auto-start off, could pause the queue as if it had finished; it no longer does.
- Answering a question in Audio or Subtitle Language Review just as the worker started that file's job could take the job out of the queue while it ran: its result was never recorded and, with more than one job allowed at a time, the job queued for the answer could start on the same file beside it. The answer and the worker now take turns: a job that has started is left to finish and the answer is applied after it, as it already was for a job that was running when you answered.
- Answering a question in Audio or Subtitle Language Review for a file on a slow or sleeping disk held up the queue while the file was read again: past 30 seconds the worker could log "Failed to claim next job" and, with auto-start off, pause, and a job finishing at that moment could be recorded as failed with "database is locked". The file is now read before the answer replaces its job.
