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

- Answers you give in Review now stay with the track you gave them for when a job removes another track that looks the same. After a job removed one of two similar tracks, a later full scan or Re-process could apply the removed track's answer to the one left behind: a subtitle you chose to keep could be deleted, or an audio track given the other one's language. Files an earlier job already affected this way are not detected or corrected.
- After you revert a file, a track the job had removed is no longer removed again at the next scan by the answer you gave the first time. If it needs a decision, Review asks again.
- Switching on Dry Run now also covers jobs already waiting in the queue: they run as previews instead of changing files. A job that is already running when you switch it on still finishes.
- Remuxarr no longer deletes other programs' unfinished downloads (`.part` files) from your library folders when it starts. It now removes only its own leftover files there.
- Extracting a subtitle no longer overwrites a `.srt` already beside the video, such as one Bazarr downloaded, including one that arrives while the job is running. A new setting, Existing Subtitle Files (Settings, Library & Processing, Subtitles), decides what happens instead. The default, Keep both, saves the extracted one under the next free number, such as `Movie.en.2.srt`. Replace brings back the old behaviour, and a revert cannot restore a file it replaced. Embedded first gives the name to the extracted subtitle and moves the existing file to the next free number; a revert leaves it there. A job stopped because its file changed while it ran also no longer deletes a subtitle another program saved beside it in the meantime.
- A job waiting for a file while the AC3 Forge added or removed its AC3 track now works from the file as the forge left it. It could leave out an AC3 track the forge had just added, or fail after an undo.
- An AC3 Forge add now builds the AC3 from the AAC 5.1 track the file has when the add starts. If a job had removed an audio track after the add was queued, the forge could copy a different track, such as a commentary, build no AC3, and still report success. If the file no longer has an AAC 5.1 track, the add now fails and leaves the file unchanged.
- Cleanup after a scan, and Run Cleanup Now, no longer remove a library's records while its network share is not mounted. An unmounted share looks like an empty folder, so every file in it was treated as deleted, and your Review answers, ignores, job history and AC3 Forge records for those files were lost. Files missing from a folder that is now empty are kept. The same applies if you delete the last file in a folder and leave the empty folder behind: its entry stays until the folder is removed or something new is added to it.
- A language you choose in Audio or Subtitle Language Review while the file's job is running is now applied once the job finishes. The question left Review, but the choice was only applied by a Full Scan. After a successful job it is applied straight away; if the job fails or you abort it, the next scan applies it. Review now says the choice was saved, where it showed a red "not updated" message.
- Retry and Retry All no longer remove a failed item when its file cannot be read. It stays in Failed with its original error, and the retry says it could not read the file, where Retry All reported nothing and Retry said no further action was needed.
- A revert no longer overwrites or deletes a file that Sonarr or Radarr replaced while the revert was running. It stops, says the file changed, and leaves everything as it was.
- Importing a full database backup now takes effect when you restart the container. It used to replace the database under the running app, and until the restart some changes went to the old database and some to the new one. The restart screen now says that anything changed before the restart is discarded.
- Scheduled scans no longer sometimes skip their time. Each check ran a little over a minute after the last, so now and then a minute was never checked, and a scan set for it did not run that day. A scan whose time passed while the server was asleep now runs once when it wakes.
- With the Plex Analyze backlog on, a file whose audio language was fixed is now analyzed unless Plex already shows every audio track's language as the file has it. One English track already there was enough for Plex to be passed over, leaving the corrected track's old tag in place. A subtitle language fix no longer stands in for the audio's.
