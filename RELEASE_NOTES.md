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

- A scan no longer stops at a file it could not record. One database error, such as a Sonarr or Radarr import of the same file arriving mid-scan, left every file after it unscanned and the scan reported as failed with 0 files. A file that fails during a scan is now tried again by the next scan, where it could be left unprocessed until a Force Full Rescan.
- A language you set in Audio or Subtitle Language Review after a file was processed, reverted or had its AC3 Forge track removed now goes to the track you were asked about. When the job, revert or removal had moved the tracks, the choice went to the track that took its place, and that track was retagged. Audio questions on files processed before this update are put right by Force Full Rescan (Settings, Maintenance & Logs). A question about a subtitle extracted before this update is not, and answering it can still retag the subtitle that moved into its place.
- Set language on an extracted subtitle whose new name is already taken now renames it by Existing Subtitle Files (Settings, Library & Processing, Subtitles). With Keep both it takes the next free number, such as Movie.en.2.srt, and with Embedded first it takes the name and the file already there moves to the next free number. With Replace it takes the next free number as well, as a rename never replaces a file. Such a subtitle used to keep its old name, and the question was removed. One that still cannot be renamed, such as a subtitle whose name does not contain its language, now keeps its question in Review, which reports the file as not updated, with the subtitle's name in the browser console.
- A file queued again, by a scan, Re-check Scanned Files or an import, no longer shows planned actions left over from an earlier entry for the same file beside its own. Leftover actions already in your database are removed when Remuxarr starts.
- A queued job whose file no longer matches the tracks Remuxarr has on record for it is now decided again from the file, instead of running on tracks the file may not have. This happened to files processed before 6 July 2026 and re-checked since, and to a file Sonarr or Radarr replaced while its job waited. Such a job is cancelled, and shows in History's Failed tab saying the file was probed and decided again; nothing was done to the file, and anything the file still needs is queued again or sent to Review.

## Added

- Original Language Audio (Settings, Library & Processing, Audio) keeps the audio a film or series was made in, as Sonarr or Radarr records it, even when that language is not in Keep Audio Languages. Keep both keeps it as well as those languages, and a default-flagged dub is then no longer kept just because none of them is in the file. Keep original only keeps just the original language when the file has a track in it, and Keep Audio Languages otherwise. It is off by default and needs the Sonarr or Radarr integration. Files imported from now on bring their original language with them; for files already in your library, run Re-check Scanned Files after turning it on, which the Settings page offers when you save. Audio a job has already removed is not brought back, except by a revert if Keep Removed Tracks (Settings, Recycle Bin) was on when it was processed.
