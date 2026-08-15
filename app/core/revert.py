"""
Revert manifests — describing a file's original layout, and working out
what a job actually destroyed.

Two functions, and the interesting decisions are both about faithfulness
rather than cleverness.

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
"""

# 2: manifests describe the PRISTINE original and are extended in place by
# later jobs, rather than each job writing a fresh manifest describing
# whatever it was handed. processed_index/sidecar_index are re-resolved on
# every capture and are only meaningful against the sidecar and processed
# file recorded alongside them.
MANIFEST_VERSION = 2


def build_manifest(probe_data: dict, *, original_path: str,
                   original_container: str | None) -> dict:
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
        })

    return {
        "version":   MANIFEST_VERSION,
        "path":      original_path,
        "container": original_container,
        "streams":   streams,
        # Recorded, not stored in the sidecar. Chapters survive a remux
        # (verified against the real pipeline), so a revert that rebuilds
        # from the processed file keeps them for free; this is here so a
        # future check can notice if that ever stops being true.
        "chapters": len(probe_data.get("chapters") or []),
        # Runtime, for identifying the file later. Two different releases
        # of the same episode can share every codec, resolution and
        # channel count and still differ by a few seconds — duration is
        # the cheapest signal that separates them, and the only one in
        # this manifest that a stream-by-stream comparison cannot see.
        "duration": _as_float(probe_data.get("format", {}).get("duration")),
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


def _full_key(stream: dict) -> tuple:
    """Payload identity plus the metadata a re-tag would change."""
    return (
        _payload_key(stream),
        stream.get("language"),
        stream.get("title"),
        tuple(stream.get("disposition") or ()),
    )


def match_streams(manifest: dict, processed_probe: dict) -> list[tuple[dict, int | None]]:
    """
    Pair every manifest entry with its index in the processed file, or None
    if it is no longer there.

    Restore needs the whole mapping, not just the gaps: for each original
    stream it has to know whether to pull that stream out of the processed
    file or out of the sidecar, and at which index. find_lost_streams is
    the capture-side view of the same answer.

    Matching runs in two passes, and the order matters:

      1. Exact — payload plus language, title and dispositions. This pairs
         off every stream the job left completely alone, and pairs them
         off FIRST, so they cannot be consumed as loose matches for
         something else.
      2. Payload only. What remains on each side after pass 1 is the
         re-tagged streams and the genuinely destroyed ones; matching on
         payload alone pairs the re-tagged ones up, because a re-tag
         changes metadata without touching a byte of the stream.

    Anything still unmatched on the original side was destroyed.

    The one case this cannot resolve is two streams identical in both
    payload AND metadata where only one survived — and there it does not
    need to, because the two are interchangeable by construction.

    Where it is uncertain, it errs towards "lost". The two failure modes
    are not symmetric: capturing a stream that actually survived costs
    disk, while missing one that did not makes the revert point silently
    wrong. A container change is the common trigger — MKV to MP4 rewrites
    subrip subtitles as mov_text, the codec no longer matches, and the
    subtitle is captured. That is the right outcome anyway, since the
    subrip original is the better thing to restore from.
    """
    processed = build_manifest(
        processed_probe, original_path="", original_container=None,
    )["streams"]

    remaining = list(processed)
    matched: dict[int, int | None] = {}
    unmatched = []

    # Pass 1 — exact.
    for original in manifest.get("streams", []):
        key = _full_key(original)
        for i, candidate in enumerate(remaining):
            if _full_key(candidate) == key:
                matched[id(original)] = candidate["index"]
                remaining.pop(i)
                break
        else:
            unmatched.append(original)

    # Pass 2 — payload only, over what pass 1 could not place.
    for original in unmatched:
        key = _payload_key(original)
        for i, candidate in enumerate(remaining):
            if _payload_key(candidate) == key:
                matched[id(original)] = candidate["index"]
                remaining.pop(i)
                break
        else:
            matched[id(original)] = None

    return [(s, matched[id(s)]) for s in manifest.get("streams", [])]


def find_lost_streams(manifest: dict, processed_probe: dict) -> list[dict]:
    """
    Return the manifest entries with no counterpart in the processed file.
    A thin view over match_streams — see there for how matching works.
    """
    return [s for s, index in match_streams(manifest, processed_probe)
            if index is None]
