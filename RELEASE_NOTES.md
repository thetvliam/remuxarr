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

- Subtitle tracks with no language tag are no longer removed. They used to be removed unless the track was forced or Always Keep Undefined-Language Subtitles was on; that setting is gone, as every untagged subtitle is now kept. With Fix Undefined Subtitle Language Tags on Always Ask, every untagged subtitle now reaches Subtitle Language Review, not only forced ones. A file with an untagged image subtitle (PGS or VobSub), or an untagged styled subtitle beside embedded fonts, while subtitles are extracted to SRT, now goes to Review to ask what to do with it, where the subtitle used to be removed without asking. Subtitles already removed from processed files are not brought back.
- Fix Undefined Language Tags set to Always Fix now tags an untagged track with Primary Language only when it is the file's only untagged track of its type, and for audio only when the film or series was made in Primary Language, as Sonarr or Radarr records it, or that is not known. Every other untagged track goes to Audio or Subtitle Language Review, where Always Fix used to tag it or leave it untagged without asking. Always Ask now asks about every untagged track. The two Apply To settings are gone. Files that used to be tagged automatically may now come to Review, and the answers you give there are counted, so a file whose tracks you have started to name is not guessed at afterwards. The Undefined Audio Track Threshold setting is gone as well: with Fix Undefined Audio Language Tags on Always Leave, a file's untagged audio tracks are no longer flagged for Audio Language Review, however many it has. Set it to Always Ask to be asked about them. Untagged tracks you have already confirmed correct are not asked about again.

## Fixed

- AC3 Forge undo no longer removes an AC3 5.1 track the file already had. Once a job had removed the forged track, for instance after its language left Keep Audio Languages, undo removed the file's own AC3 instead, and could leave the file with no audio. Tracks forged in MKV and MP4 files from now on carry the handler name AC3 5.1 (Remuxarr), which undo goes by; for tracks forged before this update, and in AVI, TS or WMV files, undo now refuses to remove a file's only audio track. When a job removes the forged track, the file no longer counts as forged, so the Forge page offers it again if it still has an AAC 5.1, without an undo that has nothing to remove. This does not happen for a file forged before this update that has an AC3 5.1 of its own.
- A scan or Re-check Scanned Files that reaches a file while its job is running now leaves it alone, and the file is looked at again when the job ends (after a failed job, at the next scan). It used to decide from the file as it was before the job: a review raised then could name the wrong track, so your answer was applied to a different subtitle; the file showed as skipped while it was being processed; and work the job was not doing was forgotten. A review raised this way before this update is corrected by running Re-check Scanned Files; until then, answering it is refused rather than applied to another track. A file whose queued job is no longer needed, after a settings change and a Re-check, is now also taken out of the queue, and History no longer lists a file twice when its job is found to need nothing or a review as it starts, or when your Review answer settles it.
- A file Remuxarr could not read straight after its job is now looked at again shortly after, and by the next scan if it still cannot be read. Its stored tracks used to keep describing the file as it was before the job until a Force Full Rescan, so AC3 Forge's list and Re-check Scanned Files went by tracks the file no longer had.
- Files with embedded fonts are no longer sent to Review when Extract Subtitles to External SRT is off. Their styled subtitles stay in the file, which keeps it MKV, and the fonts with them, so there was nothing to decide. With extraction on, the Review explanation now says what is at risk: extracting the styled subtitles to SRT flattens their styling, and the fonts are lost if the file then converts to MP4. It used to talk only about converting to MP4, even when Prefer MP4 Container was off or the file's audio meant it could not convert. On the Review page, a card whose answers would leave a file still needing review now says so, rather than describing a change that would not happen.
- Reverting a file that Sonarr or Radarr had renamed no longer leaves its extracted subtitles behind. A revert puts those subtitles back inside the file and removes the .srt files the job made, but it looked for them under the old names, so when the service renamed them along with the file, they stayed, and each subtitle showed up twice. The renamed .srt files are now followed when the name in front of the language code is the only part that changes.
- A file in Review because a subtitle could not be extracted to SRT (its text is not UTF-8) now stays there until you answer. Re-check Scanned Files used to send it back to the queue, where the same extraction failed again and returned it to Review, and a different review for the same file could replace the question, so answering that one tried the extraction again. The question is replaced only if the file changes or your settings mean the subtitle would no longer be extracted.
- If Remuxarr stops (a crash, a forced restart, the container killed) in the moment after a job has replaced a file and before it is recorded, the job is now finished when Remuxarr starts again, instead of being marked failed. Before, the next scan treated the processed file as a new release and forgot its answers, a file converted to MP4 was left beside the original MKV, and the saved original was not kept for Revert. If the job had not yet replaced the file, anything it had already put in place is removed and the job is marked failed, as before. An extracted subtitle can no longer be lost if Remuxarr stops while the finished files are being moved into place.
- A setting saved with the wrong kind of value is no longer acted on as if it were right. For example, Keep Subtitle Languages saved as the text "eng" instead of a list used to drop English subtitles, and a switch saved as the text "false" counted as on. The Settings page always sends the right kind of value, but an imported settings file, a full backup or a script calling the API might not. Saving a wrong value through the API is now refused with a message naming the setting. Importing a settings file skips wrong values, applies the rest, and lists what it skipped. A wrong value already stored, for example from a restored backup, is ignored in favour of the default and noted in the log.
- MP4 files keep their track names (such as "English SDH" or "Commentary") when Remuxarr processes them or AC3 Forge adds or removes a track. Every track name in an MP4 used to be lost, even by a job that only removed one track. MKV files and conversions from MKV to MP4 were not affected.
- MKV files keep their cover art when a job leaves them as MKV. Every such job used to remove it, even one that only removed a track or fixed a language tag. If the cover art cannot be copied, the job now fails with a message naming it and leaves the file untouched. A file converted to MP4 still loses its cover art, as before. Cover art already removed is not put back by this update; where a file can be reverted, Revert still restores it, as a still-image video track (see Two limitations worth knowing in the README).
- AC3 Forge can add an AC3 track to an MKV that has attachments, such as the fonts for styled subtitles. Every such add used to fail, with FFmpeg reporting "Received a packet for an attachment stream"; the file was left untouched. Removing a forged track was not affected.
- Adding or removing an AC3 track with AC3 Forge keeps an MKV's cover art as cover art. It used to turn it into a second, still-image video track that a player could offer as video. Files forged before this update keep that extra track.
- Reverting an MP4 that was fast-start (its index at the front, so it can start playing while it downloads) gives it back fast-start. A revert used to put the index at the end, and with Add Fast Start to MP4 Files on, the next scan then wanted to process the file again. Revert points recorded before this update don't know the original's layout, so they still come back without it.
- Processing an MKV, or adding or removing a track in one with AC3 Forge, no longer makes a track the default. With two or more subtitles and none of them on by default, the first used to be turned on, so a player showed it without being asked; the same happened to the first of two or more audio tracks when none was the default. Every track now keeps the flags it had, so if a job removes the default audio track, no other track becomes the default in its place. MP4 files were not affected. Files processed before this update keep the flag they were given.
