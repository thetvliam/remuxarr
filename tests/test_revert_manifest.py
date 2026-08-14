"""
revert.py manifests, lost-stream detection, and the sidecar command.

What is actually at risk here, in order:

  1. Under-capture. A stream reported as surviving when it did not is a
     revert point that is silently wrong — nothing discovers it until
     someone reverts and the track is gone for good. Over-capture only
     costs disk. find_lost_streams is built around that asymmetry and the
     tests below assert the asymmetry directly, not just the happy path.

  2. Faithfulness of the manifest. extract_tracks' normalisations are all
     reasonable for deciding what to do to a file and all wrong to restore
     from: it hides attachments, infers `forced` from the track title, and
     turns a missing language tag into "und". Three tests here exist only
     to pin those differences, because a future refactor "simplifying"
     build_manifest onto extract_tracks would pass every other test in
     this file.

  3. The attachment-only sidecar. FFmpeg writes a zero-track Matroska
     file, exits 0, and produces something ffprobe cannot open. Both
     halves of that are verified against real FFmpeg below rather than
     asserted from argv, because the whole point is that the argv looks
     fine.

Verified by mutation, 15 applied, 15 killed. Four of those initially
SURVIVED and are worth recording, because the tests that now kill them
were missing and the docstring above already claimed the property they
protect:

  • Pass 1 matching on payload instead of exact identity, and _full_key
    collapsing to _payload_key, both survived the first run. The tests in
    place dropped the LAST of two similar tracks, which passes either
    way through iteration order. Dropping the FIRST is what separates
    them: payload-only matching pairs the destroyed eng track against the
    surviving fre one, reports fre as lost, and the sidecar then captures
    a track that is still in the file while the destroyed one is gone for
    good. The loss count stays right the whole time.
  • Folding language into the video payload key, and title into the
    fallback key, both survived because every re-tag test used audio.
    Subtitles take the fallback key and are re-titled routinely.

The remaining mutations, killed on the first run:

  • Pass 2 removed entirely                      → killed (re-tag reported lost)
  • language folded into the audio payload key   → killed
  • `remaining.pop()` dropped from either pass   → killed (duplicate matches)
  • attachments filtered out of build_manifest   → killed
  • disposition stored as all flags, not set ones→ killed
  • language defaulted to "und"                  → killed
  • forced inferred from the title               → killed
  • SidecarUnsupported guard removed             → killed (real ffprobe fails)
  • mov_text override dropped                    → killed (real ffmpeg fails)
  • subtitle_ordinal counting every mapped stream→ killed

No equivalent mutants recorded for this file.

The FFmpeg-backed tests skip rather than fail when no ffmpeg binary is
present, matching how the rest of the suite treats it. They are the only
tests here that can catch a container-compatibility change, so a run
without ffmpeg is a weaker run, not an equivalent one.
"""
import shutil
import subprocess

import pytest


ffmpeg_required = pytest.mark.skipif(
    shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None,
    reason="ffmpeg/ffprobe not available",
)


# ── Builders ─────────────────────────────────────────────────────────────────

def _stream(index, codec_type, codec, **kw):
    """An ffprobe-shaped stream object."""
    tags = {}
    for key in ("language", "title", "filename", "mimetype"):
        if kw.get(key) is not None:
            tags[key] = kw[key]

    s = {"index": index, "codec_type": codec_type, "codec_name": codec}
    if tags:
        s["tags"] = tags
    if kw.get("disposition"):
        s["disposition"] = kw["disposition"]
    for key in ("channels", "sample_rate", "width", "height"):
        if kw.get(key) is not None:
            s[key] = kw[key]
    return s


def _from_one(streams):
    """Sources tuple for the single-input case: everything from input 0."""
    return [(s, 0, s["index"]) for s in streams]


def _probe(*streams, chapters=0):
    return {"streams": list(streams), "chapters": [{}] * chapters}


def _manifest(*streams, chapters=0):
    from app.core.revert import build_manifest

    return build_manifest(_probe(*streams, chapters=chapters),
                          original_path="/m/Show.mkv", original_container="mkv")


# ── Manifest faithfulness ────────────────────────────────────────────────────

def test_attachments_are_recorded():
    """
    extract_tracks drops these entirely, so a manifest built on it cannot
    record that a font existed — and the remux path destroys attachments
    on every job, making this the most commonly lost stream there is.
    """
    m = _manifest(
        _stream(0, "video", "h264", width=1920, height=1080),
        _stream(1, "attachment", "ttf", filename="Roboto.ttf",
                mimetype="application/x-truetype-font"),
    )

    att = [s for s in m["streams"] if s["type"] == "attachment"]
    assert len(att) == 1
    assert att[0]["filename"] == "Roboto.ttf"
    assert att[0]["mimetype"] == "application/x-truetype-font"


def test_cover_art_is_recorded():
    """
    attached_pic video streams are skipped by extract_tracks because
    mapping them into MP4 breaks the muxer. That is a processing concern;
    an inventory that omits them cannot restore them.
    """
    m = _manifest(
        _stream(0, "video", "h264", width=1920, height=1080),
        _stream(1, "video", "mjpeg", width=600, height=600,
                disposition={"attached_pic": 1}),
    )

    assert len(m["streams"]) == 2
    assert m["streams"][1]["disposition"] == ["attached_pic"]


def test_forced_is_not_inferred_from_the_title():
    """
    extract_tracks reads "English (Forced)" as forced=True. Persisting
    that inference would have revert set a disposition flag the original
    file never carried.
    """
    m = _manifest(
        _stream(0, "subtitle", "subrip", language="eng",
                title="English (Forced)"),
    )

    assert m["streams"][0]["disposition"] == []


def test_missing_language_tag_stays_missing():
    """
    "und" is a value; no tag is the absence of one. Restoring the former
    where the latter was is a metadata change made by the operation whose
    entire purpose is to change nothing.
    """
    m = _manifest(_stream(0, "audio", "aac", channels=2))

    assert m["streams"][0]["language"] is None


def test_only_set_disposition_flags_are_stored():
    m = _manifest(
        _stream(0, "audio", "aac", channels=6,
                disposition={"default": 1, "forced": 0, "dub": 0, "original": 1}),
    )

    assert m["streams"][0]["disposition"] == ["default", "original"]


def test_manifest_records_version_container_and_chapter_count():
    m = _manifest(_stream(0, "video", "h264"), chapters=12)

    from app.core.revert import MANIFEST_VERSION

    assert m["version"] == MANIFEST_VERSION
    assert m["container"] == "mkv"
    assert m["path"] == "/m/Show.mkv"
    assert m["chapters"] == 12


# ── Lost-stream detection ────────────────────────────────────────────────────

def test_dropped_audio_track_is_detected():
    from app.core.revert import find_lost_streams

    original = _manifest(
        _stream(0, "video", "h264", width=1920, height=1080),
        _stream(1, "audio", "aac", channels=6, language="eng"),
        _stream(2, "audio", "aac", channels=6, language="fre"),
    )
    processed = _probe(
        _stream(0, "video", "h264", width=1920, height=1080),
        _stream(1, "audio", "aac", channels=6, language="eng"),
    )

    lost = find_lost_streams(original, processed)
    assert [s["language"] for s in lost] == ["fre"]
    assert lost[0]["index"] == 2, "index must refer to the ORIGINAL file"


def test_retagged_track_is_not_reported_as_lost():
    """
    The case pass 2 exists for. A language fix rewrites metadata without
    touching payload — the track is still there, and capturing it would
    put a redundant copy of every re-tagged stream in the sidecar.
    """
    from app.core.revert import find_lost_streams

    original = _manifest(
        _stream(1, "audio", "ac3", channels=6, language=None),
    )
    processed = _probe(
        _stream(1, "audio", "ac3", channels=6, language="eng"),
    )

    assert find_lost_streams(original, processed) == []


def test_retag_and_drop_together_are_told_apart():
    """
    Both happen in one job routinely: fix an undefined tag on one track,
    drop a foreign-language track in the same pass.
    """
    from app.core.revert import find_lost_streams

    original = _manifest(
        _stream(1, "audio", "ac3", channels=6, language=None),
        _stream(2, "audio", "ac3", channels=6, language="fre"),
    )
    processed = _probe(
        _stream(1, "audio", "ac3", channels=6, language="eng"),
    )

    lost = find_lost_streams(original, processed)
    assert [s["index"] for s in lost] == [2]


def test_identical_streams_are_matched_one_for_one():
    """
    Two identical tracks in, one out, means exactly one was lost. Without
    consuming a match when it is used, both would pair against the single
    survivor and the loss would go unreported — the under-capture failure.
    """
    from app.core.revert import find_lost_streams

    original = _manifest(
        _stream(1, "audio", "aac", channels=2, language="eng"),
        _stream(2, "audio", "aac", channels=2, language="eng"),
    )
    processed = _probe(
        _stream(1, "audio", "aac", channels=2, language="eng"),
    )

    assert len(find_lost_streams(original, processed)) == 1


def test_the_right_one_of_two_similar_tracks_is_reported():
    """
    Why pass 1 matches exactly, and why it runs FIRST.

    Two tracks with identical payload and different languages; the first
    is dropped and the second survives. Matching on payload alone pairs
    the dropped eng track against the surviving fre one, consumes it, and
    then reports fre as lost — the wrong index. The sidecar would capture
    a track that is still in the file and leave the destroyed one
    unrecoverable, while the count of losses stayed right and nothing
    looked wrong.

    A version of this test that drops the LAST track passes either way,
    by accident of iteration order. It has to be the first.
    """
    from app.core.revert import find_lost_streams

    original = _manifest(
        _stream(1, "audio", "aac", channels=2, language="eng"),
        _stream(2, "audio", "aac", channels=2, language="fre"),
    )
    processed = _probe(
        _stream(2, "audio", "aac", channels=2, language="fre"),
    )

    lost = find_lost_streams(original, processed)
    assert [s["language"] for s in lost] == ["eng"]
    assert [s["index"] for s in lost] == [1]


def test_retagged_subtitle_title_is_not_reported_as_lost():
    """
    Subtitles take the fallback payload key, which has no type-specific
    properties to lean on. Folding title into it would report every
    re-titled subtitle as destroyed — and re-titling is a normal outcome
    of subtitle language handling, not an edge case.
    """
    from app.core.revert import find_lost_streams

    original = _manifest(
        _stream(2, "subtitle", "subrip", language="eng", title="English (SDH)"),
    )
    processed = _probe(
        _stream(2, "subtitle", "subrip", language="eng", title="English"),
    )

    assert find_lost_streams(original, processed) == []


def test_retagged_video_is_not_reported_as_lost():
    """
    Video is never dropped by the pipeline, so a video stream reported as
    lost can only be a matching bug — and it would put a whole video track
    in the sidecar, which is the one thing the size estimates for this
    feature assume never happens.
    """
    from app.core.revert import find_lost_streams

    original = _manifest(
        _stream(0, "video", "h264", width=1920, height=1080, language=None),
    )
    processed = _probe(
        _stream(0, "video", "h264", width=1920, height=1080, language="eng"),
    )

    assert find_lost_streams(original, processed) == []


def test_lost_attachment_is_detected():
    """The bug's fingerprint: nothing in the job's plan mentions this."""
    from app.core.revert import find_lost_streams

    original = _manifest(
        _stream(0, "video", "h264", width=1920, height=1080),
        _stream(1, "attachment", "ttf", filename="Roboto.ttf"),
    )
    processed = _probe(_stream(0, "video", "h264", width=1920, height=1080))

    lost = find_lost_streams(original, processed)
    assert [s["type"] for s in lost] == ["attachment"]


def test_codec_change_is_treated_as_loss():
    """
    MKV to MP4 rewrites subrip as mov_text. Erring towards "lost" captures
    the subrip original — which is both the safe direction and the better
    thing to restore from.
    """
    from app.core.revert import find_lost_streams

    original = _manifest(_stream(2, "subtitle", "subrip", language="eng"))
    processed = _probe(_stream(2, "subtitle", "mov_text", language="eng"))

    assert len(find_lost_streams(original, processed)) == 1


def test_untouched_file_loses_nothing():
    from app.core.revert import find_lost_streams

    streams = (
        _stream(0, "video", "h264", width=1920, height=1080),
        _stream(1, "audio", "eac3", channels=6, language="eng",
                disposition={"default": 1}),
        _stream(2, "subtitle", "subrip", language="eng", title="English"),
        _stream(3, "attachment", "ttf", filename="Roboto.ttf"),
    )

    assert find_lost_streams(_manifest(*streams), _probe(*streams)) == []


# ── Sidecar command ──────────────────────────────────────────────────────────

def test_attachment_only_loss_is_refused():
    """
    See the real-FFmpeg test below for why this is not over-caution.
    """
    from app.core.ffmpeg import SidecarUnsupported, build_sidecar_command

    lost = _manifest(_stream(1, "attachment", "ttf",
                             filename="Roboto.ttf"))["streams"]

    with pytest.raises(SidecarUnsupported):
        build_sidecar_command(["/m/Show.mkv"], "/recycle/1.remuxarr_revert",
                              _from_one(lost))


def test_mov_text_is_converted_and_others_are_copied():
    """
    Per-output-stream specifiers, and the ordinal counts subtitles going
    into the SIDECAR — not stream indices in either file.
    """
    from app.core.ffmpeg import build_sidecar_command

    lost = _manifest(
        _stream(3, "audio", "aac", channels=2, language="fre"),
        _stream(5, "subtitle", "subrip", language="fre"),
        _stream(7, "subtitle", "mov_text", language="ger"),
    )["streams"]

    cmd = build_sidecar_command(["/m/Show.mp4"], "/recycle/1.remuxarr_revert",
                                _from_one(lost))

    assert "-c:s:1" in cmd, "the mov_text stream is the second subtitle out"
    assert cmd[cmd.index("-c:s:1") + 1] == "srt"
    assert "-c:s:0" not in cmd, "the subrip stream must be left as a copy"
    assert cmd.index("-c") < cmd.index("-c:s:1"), "override must follow the base codec"


def test_sidecar_maps_original_indices_and_drops_chapters():
    from app.core.ffmpeg import build_sidecar_command

    lost = _manifest(_stream(4, "audio", "ac3", channels=6, language="spa"))["streams"]
    cmd = build_sidecar_command(["/m/Show.mkv"], "/recycle/1.remuxarr_revert",
                                _from_one(lost))

    assert "0:4" in cmd
    assert cmd[cmd.index("-map_chapters") + 1] == "-1"
    assert cmd[cmd.index("-f") + 1] == "matroska"
    assert cmd[-1] == "/recycle/1.remuxarr_revert"


# ── Against real FFmpeg ──────────────────────────────────────────────────────

def _build_source(tmp_path):
    """A small MKV with two audio tracks, a subtitle and a font attachment."""
    video = tmp_path / "v.mkv"
    subprocess.run(
        ["ffmpeg", "-v", "error", "-y", "-f", "lavfi",
         "-i", "testsrc=size=160x120:rate=10:duration=2",
         "-c:v", "libx264", "-pix_fmt", "yuv420p", str(video)],
        check=True,
    )
    audio = tmp_path / "a.m4a"
    subprocess.run(
        ["ffmpeg", "-v", "error", "-y", "-f", "lavfi",
         "-i", "sine=frequency=440:duration=2", "-c:a", "aac", str(audio)],
        check=True,
    )
    subs = tmp_path / "s.srt"
    subs.write_text("1\n00:00:00,000 --> 00:00:01,000\nhello\n\n")
    font = tmp_path / "Font.ttf"
    font.write_bytes(b"\x00\x01\x02\x03" * 256)

    source = tmp_path / "source.mkv"
    subprocess.run(
        ["ffmpeg", "-v", "error", "-y", "-i", str(video), "-i", str(audio),
         "-i", str(audio), "-i", str(subs),
         "-map", "0:v", "-map", "1:a", "-map", "2:a", "-map", "3:s",
         "-metadata:s:a:0", "language=eng", "-metadata:s:a:1", "language=fre",
         "-metadata:s:s:0", "language=eng",
         "-c", "copy", "-attach", str(font),
         "-metadata:s:t", "mimetype=application/x-truetype-font",
         "-f", "matroska", str(source)],
        check=True,
    )
    return source


def _ffprobe(path):
    import json

    out = subprocess.run(
        ["ffprobe", "-v", "error", "-show_streams", "-show_chapters",
         "-of", "json", str(path)],
        capture_output=True, text=True,
    )
    return out.returncode, (json.loads(out.stdout) if out.returncode == 0 else {})


@ffmpeg_required
def test_real_sidecar_round_trips_audio_subtitle_and_attachment(tmp_path):
    """
    The end-to-end shape: cut the destroyed streams out of a real file and
    confirm they are readable, correctly tagged, and carry the attachment.
    """
    from app.core.ffmpeg import build_sidecar_command
    from app.core.revert import build_manifest

    source = _build_source(tmp_path)
    rc, probe = _ffprobe(source)
    assert rc == 0

    manifest = build_manifest(probe, original_path=str(source),
                              original_container="mkv")
    lost = [s for s in manifest["streams"]
            if s["language"] == "fre" or s["type"] == "attachment"]
    assert len(lost) == 2, "fixture did not produce the expected streams"

    sidecar = tmp_path / "1.remuxarr_revert"
    subprocess.run(
        build_sidecar_command([str(source)], str(sidecar), _from_one(lost)),
        check=True)

    rc, side = _ffprobe(sidecar)
    assert rc == 0, "sidecar is unreadable"
    kinds = sorted(s["codec_type"] for s in side["streams"])
    assert kinds == ["attachment", "audio"]
    assert side["streams"][0]["tags"]["language"] == "fre"
    assert side["streams"][1]["tags"]["filename"] == "Font.ttf"
    assert not side.get("chapters")


@ffmpeg_required
def test_real_attachment_only_sidecar_would_be_corrupt(tmp_path):
    """
    The evidence behind SidecarUnsupported, asserted against real FFmpeg
    rather than trusted.

    FFmpeg is asked to write a Matroska file whose only stream is an
    attachment. It EXITS ZERO and produces a file of plausible size that
    ffprobe cannot open. If a future FFmpeg starts handling this properly,
    this test fails and the guard can be reconsidered — which is the point
    of pinning it here rather than only in a comment.
    """
    source = _build_source(tmp_path)
    rc, probe = _ffprobe(source)
    attachment = next(s for s in probe["streams"]
                      if s["codec_type"] == "attachment")

    out = tmp_path / "attachment_only.mkv"
    written = subprocess.run(
        ["ffmpeg", "-v", "error", "-y", "-i", str(source),
         "-map", f"0:{attachment['index']}", "-c", "copy",
         "-f", "matroska", str(out)],
        capture_output=True, text=True,
    )

    assert written.returncode == 0, "FFmpeg no longer reports success here"
    assert out.exists() and out.stat().st_size > 0
    assert _ffprobe(out)[0] != 0, "FFmpeg now writes a readable zero-track file"


@ffmpeg_required
def test_real_mov_text_sidecar_requires_the_conversion(tmp_path):
    """
    Pins the reason _SUBTITLE_TRANSCODE exists: the copy fails at header
    write, and the converted version does not.
    """
    from app.core.ffmpeg import build_sidecar_command
    from app.core.revert import build_manifest

    source = _build_source(tmp_path)
    mp4 = tmp_path / "source.mp4"
    subprocess.run(
        ["ffmpeg", "-v", "error", "-y", "-i", str(source),
         "-map", "0:v", "-map", "0:s", "-c:v", "copy", "-c:s", "mov_text",
         "-f", "mp4", str(mp4)],
        check=True,
    )

    rc, probe = _ffprobe(mp4)
    manifest = build_manifest(probe, original_path=str(mp4),
                              original_container="mp4")
    lost = [s for s in manifest["streams"] if s["type"] == "subtitle"]
    assert lost and lost[0]["codec"] == "mov_text"

    # A plain copy is what the builder must NOT emit.
    naive = subprocess.run(
        ["ffmpeg", "-v", "error", "-y", "-i", str(mp4),
         "-map", f"0:{lost[0]['index']}", "-c", "copy",
         "-f", "matroska", str(tmp_path / "naive.mkv")],
        capture_output=True, text=True,
    )
    assert naive.returncode != 0, "matroska now accepts mov_text; drop the conversion"

    sidecar = tmp_path / "1.remuxarr_revert"
    subprocess.run(
        build_sidecar_command([str(mp4)], str(sidecar), _from_one(lost)),
        check=True)

    rc, side = _ffprobe(sidecar)
    assert rc == 0
    assert side["streams"][0]["codec_name"] == "subrip"
