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

- A new **Embedded Font Handling** setting under Subtitles, for files that carry embedded fonts — common on anime releases with typeset signs and songs. Only MKV can hold those fonts, and the file's styled ASS subtitles reference them by name, so converting to MP4 dropped every font and flattened the subtitles to SRT: text that was positioned over a sign in the picture ended up as a line at the bottom of the screen, with the sign still visible behind it. Nothing failed, so the only symptom was subtitles rendering wrongly whenever someone next watched the episode. The setting defaults to asking, sending affected files to manual review. Always Keep leaves the styled tracks embedded, which keeps the file as MKV and preserves the fonts, while still removing unwanted audio tracks. Always Remove is the previous behaviour.

## Fixed

- The orphaned-files tool in Maintenance now refuses to run when no library paths are configured, instead of reporting every file you have as orphaned. A row counts as orphaned when it sits outside every configured scan path, so with none configured that was the whole library — listed under a heading saying these entries are orphaned, next to a button that removes them and everything referencing them. If you do want to start over, Clear Database in the danger zone is the action for it, and it cleans up revert points and their sidecars properly where this route only detaches them.

- Jobs on files with very long names no longer fail at the last step. The staged copy Remuxarr writes next to the destination before swapping it into place was named after the final file with `.part` added, which made the temporary name longer than the name it was standing in for — so a file already close to the filesystem's 255-byte limit could pass every earlier step and then fail with "File name too long", after the conversion had already been done. Reported on a show whose episode titles concatenate several segments: 23 files converted and one failed on its second subtitle, which needed two bytes more than the first. Originals were never touched, and re-running an affected job now works.

- Sonarr and Radarr no longer lose a file's quality when Remuxarr changes its container. Converting an MKV to an MP4 replaces the file, and the rescan reads the quality back off the new filename, so a WEBDL-1080p download reappeared as HDTV-1080p. That sits below most quality profiles' cutoff, so the service then treated the file as upgradable and would grab a replacement over the top of the converted one. Remuxarr now reads the original quality from the service's own import history after the rescan and writes it back to the new file. Where Sonarr or Radarr sees your library at a different path than Remuxarr does, this uses the Path Prefix (Remote) and Path Prefix (Local) settings you already have configured; if those are blank and the paths do differ, the quality is left alone and a warning is logged naming the file. Files converted before this release are not corrected; this applies from here on.
