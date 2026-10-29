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
- Sonarr and Radarr imports are now ignored while no Media Library Paths are set. They were processed anyway, so a webhook set up before the library paths could change files Remuxarr had not been pointed at. An import from outside every library path is still processed, and the container log now warns about it and says to add its folder under Settings, Library & Processing, Library.
- Your review answers, and Confirm correct in Language Review, no longer carry over to a new release that replaces a file at the same path. When Sonarr or Radarr upgraded a file, the new release was decided by the answers given for the old one without asking: a Keep given for English subtitles kept the replacement's German ones, and Confirm correct on Japanese audio stopped a German release being flagged. When a scan or an import finds that a file has changed on disk and its tracks are different, Remuxarr now forgets everything answered for it, so its settings and any questions apply as they would to a new file. Remuxarr's own processing, reverts and AC3 Forge runs are not treated as a replacement, and a replacement whose tracks match the old file's exactly keeps its answers.
- Language codes now match whichever way they are written: two-letter (en) or either three-letter form (ger or deu, fre or fra, chi or zho). They were compared letter for letter, so with Keep Audio Languages set to eng and ger, a German track tagged deu was removed, an English track tagged en was removed while a Japanese default track was kept, and a list holding en removed every track tagged eng. Keep Audio Languages, Keep Subtitle Languages, Primary Language and Set language in Language Review now save the three-letter form (en is saved as eng). MP4 cannot hold a two-letter code: a track tagged en lost its language when the file was converted, and a Set language of en never took effect and was queued again on every Force Full Rescan. When a file is processed, a track tagged with two letters now gets its three-letter code written; a file is not queued for that alone. Extracted subtitles are still named with the two-letter code (Movie.en.srt), now for every language that has one.

## Added

- Re-check Scanned Files (Settings, Maintenance & Logs) applies your current settings to every file Remuxarr has already scanned. Each file is decided again from the tracks stored when it was last scanned, so only new or changed files are run through ffprobe, where Force Full Rescan probes every file. Files that now need processing are queued as a scan would queue them, so Dry Run and Auto-Start apply. After you save a change to a setting a routine scan does not re-apply, such as Keep Audio Languages, the Settings page offers the same re-check under the save bar.
- Ask again, in the details window of a Queue or History item, forgets your review answers and Confirm correct choices for that file and decides it again straight away, so each question it raises is asked afresh. An answer could not be taken back before: after you reverted a file, the next scan applied the answers for the tracks the revert kept. The button appears only when forgetting would change what happens to the file, so not for a language fix that has already been processed, for example. It takes two clicks and is refused while the file is being processed.
