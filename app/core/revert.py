"""
Revert manifests — describing a file's original layout, and working out
what a job actually destroyed.

Three functions — build_manifest, match_streams and find_lost_streams — and
the interesting decisions are about faithfulness rather than cleverness.
match_streams is public rather than a helper of find_lost_streams because
revert_capture and revert_match both need the pairing without the
"what is missing" view layered on top.

build_manifest reads raw ffprobe output rather than probe.extract_tracks().
That is not a shortcut around an existing helper, it is the opposite:
extract_tracks normalises for the DECISION layer, and every one of those
normalisations is wrong to persist here.

  • It filters to video/audio/subtitle. Attachments — fonts, posters —
    never appear, so a manifest built from it cannot record that they
    existed. This is not hypothetical: the remux path drops attachments
    today, so they are among the most commonly destroyed streams there
    are.
  • It infers is_forced from a regex over the track TITLE. A track named
    "English (Forced)" with no forced disposition reads as forced. Write
    that back on revert and the restored file gains a disposition flag
    the original never had.
  • It collapses a missing language tag to "und". Restoring an explicit
    "und" where there was no tag at all is a metadata change, in the one
    operation whose entire purpose is to not change anything.

So the manifest stores what ffprobe reported: set disposition flags, raw
tags, nothing derived.

find_lost_streams compares the original against the file the job produced
and returns what is no longer there. It deliberately does NOT read the
job's planned actions, because the plan is a statement of intent and the
sidecar has to be built from what happened — the attachment loss above is
exactly a case where the two disagree and nothing in the plan mentions it.

match_streams can be told one thing by the job: where each of its kept
tracks went (positional_pairs, as `pinned`). That is not the plan standing
in for what happened. It is used only for a track type whose count in the
output is what the plan said it would be, a kept track still has to be
found in the output with the same payload, and everything it says nothing
about (attachments, covers, data streams, a type whose count is off) is
matched from the files alone, as before. What it replaces is a guess:
matching by metadata cannot tell look-alike tracks apart once the job has
rewritten one's language (see match_streams).
"""

# 2: manifests describe the PRISTINE original and are extended in place by
# later jobs, rather than each job writing a fresh manifest describing
# whatever it was handed. processed_index/sidecar_index are re-resolved on
# every capture and are only meaningful against the sidecar and processed
# file recorded alongside them.
MANIFEST_VERSION = 2


def build_manifest(probe_data: dict, *, original_path: str,
                   original_container: str | None,
                   faststart: bool | None = None) -> dict:
    """
    Describe a file's full stream layout, for restoring it later.

    Every stream is recorded, including attachments and the attached_pic
    cover art that extract_tracks skips — this is an inventory, not a
    processing plan, and something absent from the inventory can never be
    put back.
    """
    streams = []
    for stream in probe_data.get("streams", []):
        tags = stream.get("tags") or {}
        disposition = stream.get("disposition") or {}

        streams.append({
            # Index in the ORIGINAL file. This is what -map uses when the
            # sidecar is cut, and it is only meaningful against that file.
            "index": stream.get("index"),
            "type":  stream.get("codec_type"),
            "codec": stream.get("codec_name"),

            # Raw, un-normalised. A missing tag stays missing — see the
            # module docstring on why "und" is not a safe stand-in.
            "language": tags.get("language") or tags.get("LANGUAGE"),
            "title":    tags.get("title") or tags.get("TITLE")
                        or tags.get("name") or tags.get("NAME"),

            # Only the flags actually set, and only as ffprobe reported
            # them. No title-regex inference: see the module docstring.
            "disposition": sorted(
                flag for flag, value in disposition.items() if value == 1
            ),

            # Payload shape. Used to re-identify a stream after a job has
            # rewritten its metadata, so only immutable-under-remux
            # properties belong here.
            "channels":    stream.get("channels"),
            "sample_rate": stream.get("sample_rate"),
            "width":       stream.get("width"),
            "height":      stream.get("height"),

            # Attachments carry their identity in tags rather than in any
            # stream property.
            "filename": tags.get("filename") or tags.get("FILENAME"),
            "mimetype": tags.get("mimetype") or tags.get("MIMETYPE"),

            # Every tag, verbatim, including the ones above. Language and
            # title stay as their own fields because matching and display
            # need them; this is what makes a RESTORE faithful.
            #
            # Without it a revert quietly changes metadata on exactly the
            # streams it did not have to touch. A track that survived the
            # job comes back through the processed container and arrives
            # carrying whatever that container left on it — an MP4 round
            # trip strips mkvmerge's BPS and NUMBER_OF_BYTES statistics and
            # adds a handler_name that means nothing in Matroska. Streams
            # restored from the sidecar keep theirs, so the result is a
            # file whose streams disagree about which tags they carry,
            # which the original never did.
            "tags": dict(tags),
        })

    return {
        "version":   MANIFEST_VERSION,
        "path":      original_path,
        "container": original_container,
        "streams":   streams,
        # Recorded, not stored in the sidecar. Chapters survive a remux
        # (verified against the real pipeline), so a revert that rebuilds
        # from the processed file keeps them for free; this is here so a
        # future check can notice if that ever stops being true. Points
        # recorded before probe_file asked for chapters hold 0 here,
        # whatever the file had.
        "chapters": len(probe_data.get("chapters") or []),
        # Runtime, for identifying the file later. Two different releases
        # of the same episode can share every codec, resolution and
        # channel count and still differ by a few seconds — duration is
        # the cheapest signal that separates them, and the only one in
        # this manifest that a stream-by-stream comparison cannot see.
        "duration": _as_float(probe_data.get("format", {}).get("duration")),
        # The file's own tags — title, comments, whatever the release
        # carried — verbatim. Recorded rather than read back from the
        # processed file at restore time, because a conversion loses them
        # there: an MP4 keeps only a handful of standard keys. Optional:
        # manifests written before it lack it, and build_restore_command
        # falls back for those. Not a version bump, which would make capture
        # replace — and so discard — every existing point.
        "format_tags": dict(probe_data.get("format", {}).get("tags") or {}),
        # Whether an MP4 original had its index (moov) before the media, as
        # capture read it from the file (is_faststart_mp4): a layout, so no
        # probe shows it. Restore writes it back; without it a fast-start
        # original came back with the index at the end. None for anything
        # else, and absent from manifests written before it, which restore
        # treats alike. Not a version bump, for the reason format_tags gives.
        "faststart": faststart,
    }


def _as_float(value) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _payload_key(stream: dict) -> tuple:
    """
    Identify a stream by properties a remux cannot change.

    Excludes language, title and dispositions on purpose: those are
    precisely what a re-tagging job rewrites, and a stream that was
    re-tagged is still present, not lost.
    """
    kind = stream.get("type")
    if kind == "video":
        return (kind, stream.get("codec"), stream.get("width"), stream.get("height"))
    if kind == "audio":
        return (kind, stream.get("codec"), stream.get("channels"),
                stream.get("sample_rate"))
    if kind == "attachment":
        return (kind, stream.get("codec"), stream.get("filename"))
    return (kind, stream.get("codec"))


def _language_key(stream: dict) -> tuple:
    """
    Payload identity plus language, and nothing else.

    Dispositions are excluded because a remux rewrites them as a side
    effect of what it removed: drop the default audio track and FFmpeg
    promotes whatever is left, so a surviving track's disposition differs
    from the original's through no decision of ours. Title is excluded for
    the same reason a re-tag changes it.

    Language survives both, which makes this the strongest key that is
    still stable across an ordinary job.
    """
    return (_payload_key(stream), stream.get("language"))


def _full_key(stream: dict) -> tuple:
    """Payload identity plus every piece of metadata."""
    return (
        _payload_key(stream),
        stream.get("language"),
        stream.get("title"),
        tuple(stream.get("disposition") or ()),
    )


def _pair_pass(key_fn, unmatched: list[dict], remaining: list[dict],
               matched: dict[int, int | None]) -> tuple[list[dict], list[dict]]:
    """
    One matching pass. Records what it decides in `matched` and returns
    (still_unmatched, still_remaining) for the next, looser pass.

    Streams are grouped by key, and each group is settled by comparing how
    many the original had with how many the processed file has:

      • At least as many in the processed file — paired in order, so the
        nth original look-alike takes the nth survivor. The order is the
        only evidence left once the key is equal, and this is what the
        matching always did; it rests on the job not reordering the tracks
        of a type, which build_ffmpeg_command does not.
      • Fewer, but some — the group is settled as LOST, every member of
        it, and its survivors are consumed without being paired. Which of
        them survived is exactly what this key cannot say, and a wrong
        guess stores a surviving track while the destroyed one is gone
        for good. Over-capturing only costs disk, and restore takes a
        stream from the sidecar when it has one, so the result is the
        pristine original either way.

        Consuming the survivors matters as much as giving up on the
        originals: left in the pool, a later and looser pass would hand
        them to some other original that merely shares a codec.
      • None — nothing to decide here; the group goes on to the next pass.

    still_unmatched keeps manifest order, because the next pass pairs in
    order within its own groups, and those can mix members of several
    groups from this one.
    """
    groups: dict[tuple, list[dict]] = {}
    for original in unmatched:
        groups.setdefault(key_fn(original), []).append(original)

    settled: set[int] = set()
    consumed: set[int] = set()
    for key, originals in groups.items():
        candidates = [c for c in remaining if key_fn(c) == key]
        if not candidates:
            continue
        if len(candidates) >= len(originals):
            for original, candidate in zip(originals, candidates):
                matched[id(original)] = candidate["index"]
                settled.add(id(original))
                consumed.add(id(candidate))
        else:
            for original in originals:
                matched[id(original)] = None
                settled.add(id(original))
            for candidate in candidates:
                consumed.add(id(candidate))

    return ([o for o in unmatched if id(o) not in settled],
            [c for c in remaining if id(c) not in consumed])


def positional_pairs(
    before: list[dict], after: list[dict], removed: set[int] | frozenset[int],
) -> tuple[dict[int, int], list[tuple[str, int, int]]]:
    """
    Where each track a job kept now sits: (before stream_index -> after
    stream_index, the types left out as (type, kept, in output)).

    before and after are probe.extract_tracks() lists of the file the job
    was handed and the file it wrote; removed is the stream indices the job
    dropped or extracted. Paired by position within each track type,
    because that is how build_ffmpeg_command writes the output: every video
    track, then the kept audio, then the kept subtitles, each type in
    source order. Interleaved sources come out grouped by type, so counting
    across types would pair a subtitle with an audio track.

    A type whose kept count does not match what the output holds is left
    out: some step this does not know about added or lost a track of it
    (an AC3 Forge add, say), and pairing by position would be a guess.

    Shared by the two places that need to follow a job's tracks: the stored
    answers (scanner.job_stream_mapping) and revert capture
    (revert_capture.capture, as match_streams' pins).
    """
    mapping: dict[int, int] = {}
    left_out: list[tuple[str, int, int]] = []
    for track_type in sorted({t["track_type"] for t in before + after}):
        kept = sorted(
            (t for t in before
             if t["track_type"] == track_type and t["stream_index"] not in removed),
            key=lambda t: t["stream_index"],
        )
        out = sorted((t for t in after if t["track_type"] == track_type),
                     key=lambda t: t["stream_index"])
        if len(kept) != len(out):
            left_out.append((track_type, len(kept), len(out)))
            continue
        for src, dst in zip(kept, out):
            mapping[src["stream_index"]] = dst["stream_index"]
    return mapping, left_out


def match_streams(
    manifest: dict,
    processed_probe: dict,
    pinned: dict[int, int | None] | None = None,
) -> list[tuple[dict, int | None]]:
    """
    Pair every manifest entry with its index in the processed file, or None
    if it is no longer there.

    Restore needs the whole mapping, not just the gaps: for each original
    stream it has to know whether to pull that stream out of the processed
    file or out of the sidecar, and at which index. find_lost_streams is
    the capture-side view of the same answer.

    Matching runs in three passes, each looser than the last, and the
    order is the whole design — a looser pass must never get to claim a
    stream a stricter one could have placed correctly:

      1. Exact — payload plus language, title and dispositions. Pairs off
         every stream the job left completely alone, first, so they cannot
         be consumed as loose matches for something else.
      2. Payload plus language. Pairs off streams whose dispositions or
         title the job rewrote. This pass is not optional and its absence
         was a real bug: dropping the default audio track makes FFmpeg
         promote the survivor to default, so a kept track fails pass 1
         through no decision of ours. Without this pass it fell to pass 3,
         where a DIFFERENT track of the same codec and channel count
         claimed the match first purely by being earlier in the file — and
         the sidecar then stored the track that survived while the one
         actually destroyed was lost for good.
      3. Payload only. What remains is re-tagged streams and genuinely
         destroyed ones; matching on payload alone pairs the re-tagged
         ones up, because a re-tag changes metadata without touching a
         byte of the stream.

    Anything still unmatched on the original side was destroyed.

    Every pass pairs a group of look-alikes only when the pairing cannot be
    wrong — see _pair_pass. When fewer of them survived than the original
    had, a key that cannot tell them apart cannot say WHICH survived, and
    the whole group is reported lost. Guessing here was the bug: a job
    that drops a French track and re-tags an untagged English one of the
    same codec and layout left both looking alike to pass 3, which paired
    the French original with the surviving English track. The sidecar then
    stored the English track a second time and the French one was never
    stored at all, and the revert reported success with two English tracks.
    Identical metadata does not make two streams interchangeable either —
    a full subtitle and a forced-only one can share every field this
    compares, and a review answer can remove either of them.

    Where it is uncertain, it errs towards "lost". The two failure modes
    are not symmetric: capturing a stream that actually survived costs
    disk, while missing one that did not makes the revert point silently
    wrong. A container change is the common trigger — MKV to MP4 rewrites
    subrip subtitles as mov_text, the codec no longer matches, and the
    subtitle is captured. That is the right outcome anyway, since the
    subrip original is the better thing to restore from.

    pinned, when the caller knows where its job put each track, settles
    those tracks before any pass runs. Keyed by a manifest stream's own
    "index", a value is the processed stream it became, or None for one
    the job removed. Pinned to a processed stream, a track is paired with
    it only if their payloads match, and otherwise lost (a subrip track an
    MP4 output turned into mov_text, as above); either way that processed
    stream is taken out of the pool, so no pass hands it to another
    original. Pinned to None, a track is lost however much a survivor
    looks like it.

    The passes cannot do this. A job that writes a language onto a kept
    track can make it look exactly like a track it dropped: an untagged
    subtitle kept and tagged English, and an English one of the same codec
    removed. Pass 2 then pairs the dropped English original with the kept
    track, one to one, and the untagged original is reported lost. The
    sidecar stored the track that survived, never the one that was
    destroyed, and a revert put the kept subtitle's text in both slots.
    Unpinned tracks still go through the passes, against what the pins left.
    """
    processed = build_manifest(
        processed_probe, original_path="", original_container=None,
    )["streams"]

    matched: dict[int, int | None] = {}
    unmatched: list[dict] = []
    by_index = {p["index"]: p for p in processed}
    claimed: set[int] = set()
    for original in manifest.get("streams", []):
        if pinned is None or original.get("index") not in pinned:
            unmatched.append(original)
            continue
        target = by_index.get(pinned[original["index"]])
        if target is None:
            matched[id(original)] = None
            continue
        claimed.add(target["index"])
        matched[id(original)] = (
            target["index"] if _payload_key(target) == _payload_key(original) else None
        )
    remaining = [p for p in processed if p["index"] not in claimed]

    # Progressively looser, each over what the previous could not place.
    # Running them as a sequence rather than merging them is what stops a
    # loose match claiming a stream a tighter one owns.
    for key_fn in (_full_key, _language_key, _payload_key):
        unmatched, remaining = _pair_pass(key_fn, unmatched, remaining, matched)

    for original in unmatched:
        matched[id(original)] = None

    return [(s, matched[id(s)]) for s in manifest.get("streams", [])]


def find_lost_streams(manifest: dict, processed_probe: dict) -> list[dict]:
    """
    Return the manifest entries with no counterpart in the processed file.
    A thin view over match_streams — see there for how matching works.
    """
    return [s for s, index in match_streams(manifest, processed_probe)
            if index is None]
