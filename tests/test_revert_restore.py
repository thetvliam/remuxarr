"""
Rebuilding the original from the processed file plus its sidecar.

The failure this file is built around is not a crash. It is a restore
that succeeds, produces a playable file, and gets something subtly wrong:
a track in the wrong position, a disposition the original never had, a
language tag left at the value the job wrote. Every one of those looks
like a working revert until someone plays the file and the wrong audio
track comes up by default.

So the assertions are about faithfulness rather than success:

  • Order comes from the manifest, which is the ORIGINAL order, not the
    processed file's.
  • Language, title and dispositions are written back explicitly and
    CLEARED where the manifest recorded none. Left to stream copy they
    would inherit the job's values — precisely the ones being reverted.
  • A manifest that cannot say where a stream lives is refused outright
    rather than partially rebuilt.

The last one is worth being blunt about: a partial rebuild reports
success while silently dropping a track the user asked to get back. An
honest failure leaves them with the processed file they already had.

test_real_round_trip is the one that matters most. It captures from a
real file and restores from the result, then compares stream-by-stream —
the only test here that can catch FFmpeg not doing what the argv says.

Verified by mutation, 13 applied, 13 killed. One initially SURVIVED and
is recorded rather than quietly fixed: swapping the sidecar/processed
preference. Capture makes those two annotations mutually exclusive today,
so the mutant was genuinely equivalent under current behaviour — the
ordering was correct by accident, not by contract. Rather than log it as
an equivalent mutant, the preference is now stated in the code and pinned
by test_the_sidecar_wins_when_a_stream_is_in_both, because the direction
matters the moment capture over-captures: the sidecar holds the original
codec and tagging, the processed file holds the job's rewritten version.

The rest, killed on the first run:

  • Manifest order replaced with processed order  → killed
  • sidecar_index and processed_index swapped     → killed
  • Missing-annotation branch returns instead of
    raising                                        → killed
  • Empty language/title omitted rather than
    cleared                                        → killed
  • Disposition "0" replaced with omitting the
    option                                         → killed
  • Disposition flags joined with "," not "+"     → killed
  • Metadata indices taken from the manifest's own
    index rather than the output position          → killed
  • -c copy dropped                                → killed
  • Muxer defaulted to matroska on an unknown
    container                                      → killed
  • Chapters dropped instead of carried from the
    processed file                                 → killed
  • Attachments given -metadata:s                  → killed
  • Sidecar and processed inputs swapped           → killed
  • Stream tags not recorded in the manifest       → killed
  • Metadata not cleared before restoring          → killed
  • Recorded tags not written back                 → killed
  • Attachments skipped before their tags restored → killed
  • language/title written before the tag set      → killed

The clearing mutant survived at first, because a same-container fixture
never produces residue to clean up — the reported case was an MKV to MP4
job, where the muxer adds handler_name and vendor_id to every surviving
stream. test_real_round_trip_through_mp4_leaves_no_container_residue
builds its original in one pass straight from lavfi for the same reason:
routing the audio through an .m4a first, as the other fixtures here do,
puts the residue on the original itself and the test proves nothing.

No equivalent mutants.
"""
import json
import shutil
import subprocess

import pytest


ffmpeg_required = pytest.mark.skipif(
    shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None,
    reason="ffmpeg/ffprobe not available",
)


def _entry(index, kind, codec, *, processed=None, sidecar=None,
           language=None, title=None, disposition=None, **extra):
    out = {
        "index": index, "type": kind, "codec": codec,
        "language": language, "title": title,
        "disposition": disposition or [],
        "channels": None, "sample_rate": None, "width": None, "height": None,
        "filename": None, "mimetype": None,
    }
    out.update(extra)
    if processed is not None:
        out["processed_index"] = processed
    if sidecar is not None:
        out["sidecar_index"] = sidecar
    return out


def _manifest(*streams, container="mkv"):
    return {"version": 1, "path": "/m/Show.mkv", "container": container,
            "streams": list(streams), "chapters": 0}


def _build(manifest):
    from app.core.ffmpeg import build_restore_command

    return build_restore_command("/m/Show.mkv", "/recycle/1.remuxarr_revert",
                                 "/tmp/out.remuxarr_tmp", manifest)


def _maps(cmd):
    return [cmd[i + 1] for i, a in enumerate(cmd) if a == "-map"]


# ── Sourcing and order ───────────────────────────────────────────────────────

def test_streams_are_pulled_from_whichever_file_holds_them():
    cmd = _build(_manifest(
        _entry(0, "video", "h264", processed=0),
        _entry(1, "audio", "aac", processed=1, language="eng"),
        _entry(2, "audio", "ac3", sidecar=0, language="fre"),
    ))

    assert _maps(cmd) == ["0:0", "0:1", "1:0"]


def test_order_follows_the_manifest_not_the_processed_file():
    """
    The manifest is in ORIGINAL order. A restore that emits streams in the
    processed file's order produces a playable file whose track order has
    silently changed — which a player turns into the wrong default track.
    """
    cmd = _build(_manifest(
        _entry(0, "video", "h264", processed=0),
        # The dropped track sat between the two survivors originally.
        _entry(1, "audio", "ac3", sidecar=0, language="fre"),
        _entry(2, "audio", "aac", processed=1, language="eng"),
    ))

    assert _maps(cmd) == ["0:0", "1:0", "0:1"]


def test_the_sidecar_wins_when_a_stream_is_in_both():
    """
    Capture makes the two annotations mutually exclusive today, so this
    pins a contract rather than a current behaviour — deliberately.

    Matching errs towards "lost", so an over-captured stream is one the
    sidecar holds in its original codec and tagging while the processed
    file holds the job's rewritten version. Preferring the processed copy
    would restore exactly what the user is undoing.
    """
    cmd = _build(_manifest(
        _entry(0, "video", "h264", processed=0),
        _entry(1, "subtitle", "subrip", processed=1, sidecar=0, language="eng"),
    ))

    assert _maps(cmd) == ["0:0", "1:0"]


def test_a_stream_in_neither_file_is_refused():
    """
    Partially rebuilding would report success while dropping a track the
    user asked to get back. The processed file they already have is a
    better outcome than a file that looks restored and is not.
    """
    from app.core.ffmpeg import RestoreUnsupported

    with pytest.raises(RestoreUnsupported):
        _build(_manifest(
            _entry(0, "video", "h264", processed=0),
            _entry(1, "audio", "ac3"),
        ))


def test_an_unknown_original_container_is_refused():
    from app.core.ffmpeg import RestoreUnsupported

    with pytest.raises(RestoreUnsupported):
        _build(_manifest(_entry(0, "video", "h264", processed=0),
                         container="flv"))


def test_the_original_container_decides_the_muxer():
    cmd = _build(_manifest(_entry(0, "video", "h264", processed=0),
                           container="mp4"))

    assert cmd[cmd.index("-f") + 1] == "mp4"


# ── Metadata faithfulness ────────────────────────────────────────────────────

def _meta_for(cmd, out_index):
    out = {}
    for i, a in enumerate(cmd):
        if a == f"-metadata:s:{out_index}":
            key, _, value = cmd[i + 1].partition("=")
            out[key] = value
        elif a == f"-disposition:{out_index}":
            out["disposition"] = cmd[i + 1]
    return out


def test_language_and_title_are_restored():
    cmd = _build(_manifest(
        _entry(0, "video", "h264", processed=0),
        _entry(1, "audio", "aac", processed=1, language="fre",
               title="French 5.1"),
    ))

    assert _meta_for(cmd, 1)["language"] == "fre"
    assert _meta_for(cmd, 1)["title"] == "French 5.1"


def test_absent_tags_are_cleared_not_skipped():
    """
    The re-tagging case, and the whole reason metadata is rewritten rather
    than copied. The job ADDED a language tag to a track that had none;
    leaving the copied value in place makes revert a partial undo that
    looks complete.
    """
    cmd = _build(_manifest(
        _entry(0, "video", "h264", processed=0),
        _entry(1, "audio", "ac3", processed=1, language=None, title=None),
    ))

    meta = _meta_for(cmd, 1)
    assert meta["language"] == "", "a tag the job added would survive the revert"
    assert meta["title"] == ""


def test_dispositions_are_restored():
    cmd = _build(_manifest(
        _entry(0, "video", "h264", processed=0),
        _entry(1, "audio", "aac", processed=1, disposition=["default"]),
        _entry(2, "subtitle", "subrip", sidecar=0,
               disposition=["forced", "hearing_impaired"]),
    ))

    assert _meta_for(cmd, 1)["disposition"] == "default"
    assert _meta_for(cmd, 2)["disposition"] == "forced+hearing_impaired"


def test_no_dispositions_is_written_as_an_explicit_clear():
    """
    Omitting the option inherits whatever the copied stream carries, which
    is the job's version. FFmpeg spells "no flags" as "0".
    """
    cmd = _build(_manifest(
        _entry(0, "video", "h264", processed=0),
        _entry(1, "audio", "aac", processed=1, disposition=[]),
    ))

    assert _meta_for(cmd, 1)["disposition"] == "0"


def test_metadata_indices_are_output_positions():
    """
    -metadata:s:N addresses the OUTPUT stream. Using the manifest's own
    index would work only while nothing was ever reordered or dropped,
    which is the one situation revert never runs in.
    """
    cmd = _build(_manifest(
        _entry(0, "video", "h264", processed=0),
        _entry(7, "audio", "aac", sidecar=0, language="fre"),
    ))

    assert _meta_for(cmd, 1)["language"] == "fre"
    assert _meta_for(cmd, 7) == {}


def test_attachments_get_no_metadata_options():
    cmd = _build(_manifest(
        _entry(0, "video", "h264", processed=0),
        _entry(1, "attachment", "ttf", sidecar=0, filename="Roboto.ttf"),
    ))

    assert _meta_for(cmd, 1) == {}
    assert "1:0" in _maps(cmd)


def test_chapters_come_from_the_processed_file():
    """They survive a remux, so the processed file still has them."""
    cmd = _build(_manifest(_entry(0, "video", "h264", processed=0)))

    assert cmd[cmd.index("-map_chapters") + 1] == "0"


def test_streams_are_copied_never_re_encoded():
    cmd = _build(_manifest(_entry(0, "video", "h264", processed=0)))

    assert cmd[cmd.index("-c") + 1] == "copy"


# ── Against real FFmpeg ──────────────────────────────────────────────────────

def _ffprobe(path):
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-show_streams", "-show_format",
         "-of", "json", str(path)],
        capture_output=True, text=True,
    )
    assert out.returncode == 0, f"{path} is unreadable"
    return json.loads(out.stdout)


def _tags(path):
    """Per-stream tag dictionaries, in order."""
    return [dict(s.get("tags") or {}) for s in _ffprobe(path)["streams"]]


def _summarise(path):
    """Everything a revert is supposed to put back, in order."""
    out = []
    for s in _ffprobe(path)["streams"]:
        tags = s.get("tags") or {}
        disp = s.get("disposition") or {}
        out.append((
            s["codec_type"], s["codec_name"],
            tags.get("language"), tags.get("title"), tags.get("filename"),
            tuple(sorted(f for f, v in disp.items() if v == 1)),
        ))
    return out


def _round_trip_fixture(tmp_path):
    """
    A real original, the file a job would leave behind, and the sidecar
    holding what that job destroyed.

    The original has two audio languages, a subtitle, a font attachment,
    and dispositions and titles worth losing. The job drops the French
    audio and the attachment.
    """
    from app.core.ffmpeg import build_sidecar_command
    from app.core.revert import build_manifest, match_streams

    video = tmp_path / "v.mkv"
    subprocess.run(
        ["ffmpeg", "-v", "error", "-y", "-f", "lavfi",
         "-i", "testsrc=size=160x120:rate=10:duration=1",
         "-c:v", "libx264", "-pix_fmt", "yuv420p", str(video)], check=True)
    audio = tmp_path / "a.m4a"
    subprocess.run(
        ["ffmpeg", "-v", "error", "-y", "-f", "lavfi",
         "-i", "sine=frequency=440:duration=1", "-c:a", "aac", str(audio)],
        check=True)
    subs = tmp_path / "s.srt"
    subs.write_text("1\n00:00:00,000 --> 00:00:01,000\nhello\n\n")
    font = tmp_path / "Roboto.ttf"
    font.write_bytes(b"\x00\x01\x02\x03" * 256)

    original = tmp_path / "original.mkv"
    subprocess.run(
        ["ffmpeg", "-v", "error", "-y", "-i", str(video), "-i", str(audio),
         "-i", str(audio), "-i", str(subs),
         "-map", "0:v", "-map", "1:a", "-map", "2:a", "-map", "3:s",
         "-metadata:s:a:0", "language=eng", "-metadata:s:a:0", "title=English",
         "-metadata:s:a:1", "language=fre", "-metadata:s:a:1", "title=Francais",
         "-metadata:s:s:0", "language=eng",
         "-disposition:a:0", "default", "-disposition:s:0", "forced",
         "-c", "copy", "-attach", str(font),
         "-metadata:s:t", "mimetype=application/x-truetype-font",
         "-f", "matroska", str(original)], check=True)

    processed = tmp_path / "processed.mkv"
    subprocess.run(
        ["ffmpeg", "-v", "error", "-y", "-i", str(original),
         "-map", "0:0", "-map", "0:1", "-map", "0:3", "-c", "copy",
         "-f", "matroska", str(processed)], check=True)

    manifest = build_manifest(_ffprobe(original), original_path=str(original),
                              original_container="mkv")
    matches = match_streams(manifest, _ffprobe(processed))
    lost = [s for s, i in matches if i is None]
    assert len(lost) == 2, "fixture should lose the fre audio and the font"

    sidecar = tmp_path / "1.remuxarr_revert"
    subprocess.run(
        build_sidecar_command([str(original)], str(sidecar),
                              [(s, 0, s["index"]) for s in lost]),
        check=True)

    sidecar_indices = {id(s): n for n, s in enumerate(lost)}
    for stream, produced_index in matches:
        stream["processed_index"] = produced_index
        if id(stream) in sidecar_indices:
            stream["sidecar_index"] = sidecar_indices[id(stream)]

    return original, processed, sidecar, manifest


def _run_restore(processed, sidecar, destination, manifest):
    from app.core.ffmpeg import build_restore_command

    cmd = build_restore_command(str(processed), str(sidecar),
                                str(destination), manifest)
    # -progress writes to stdout; nothing is reading it here.
    subprocess.run([a for a in cmd if a not in ("-progress", "pipe:1")],
                   check=True, stdout=subprocess.DEVNULL)


@ffmpeg_required
def test_real_round_trip_restores_the_original(tmp_path):
    """
    Capture from a real file, then restore from the result, and compare
    stream by stream. The only test here that can catch FFmpeg not doing
    what the argv says.
    """
    original, processed, sidecar, manifest = _round_trip_fixture(tmp_path)
    before = _summarise(original)

    restored = tmp_path / "restored.mkv"
    _run_restore(processed, sidecar, restored, manifest)

    assert _summarise(restored) == before, (
        "the restored file does not match the original stream for stream"
    )


@ffmpeg_required
def test_real_round_trip_restores_stream_tags(tmp_path):
    """
    Reported from a real library after a successful revert: the restored
    file's content was perfect, but the two streams that had SURVIVED the
    job came back missing mkvmerge's BPS and NUMBER_OF_BYTES statistics
    and carrying an MP4 handler_name that means nothing in Matroska. The
    streams restored from the sidecar kept theirs — so the result was a
    file whose streams disagreed about which tags they carried, which the
    original never did.

    Cosmetic, and still wrong: a revert should not change metadata on the
    streams it did not have to touch.
    """
    original, processed, sidecar, manifest = _round_trip_fixture(tmp_path)
    before = _tags(original)

    restored = tmp_path / "restored.mkv"
    _run_restore(processed, sidecar, restored, manifest)

    after = _tags(restored)
    assert len(after) == len(before)
    for i, (o, r) in enumerate(zip(before, after)):
        # DURATION is written by the muxer from the actual stream, not
        # carried, so it legitimately differs in precision.
        o = {k: v for k, v in o.items() if k != "DURATION"}
        r = {k: v for k, v in r.items() if k != "DURATION"}
        assert o == r, f"stream {i} tags changed: {o} -> {r}"


@ffmpeg_required
def test_real_round_trip_through_mp4_leaves_no_container_residue(tmp_path):
    """
    The reported case, and the reason the metadata is CLEARED rather than
    merely overwritten.

    An MKV to MP4 job puts every surviving stream through the MP4 muxer,
    which adds handler_name and vendor_id. Those mean nothing in Matroska
    and the original never had them, so a revert that only sets the tags
    it knows about leaves them behind — on precisely the streams it did
    not need to touch. A same-container fixture cannot see this, because
    nothing adds residue in the first place.
    """
    from app.core.ffmpeg import build_sidecar_command
    from app.core.revert import build_manifest, match_streams

    # Built in ONE pass straight from lavfi, with no intermediate file.
    # Routing the audio through an .m4a first — as the other fixtures here
    # do — puts handler_name and vendor_id on the original itself, and
    # then restoring them is correct and this test proves nothing.
    original = tmp_path / "original.mkv"
    subprocess.run(
        ["ffmpeg", "-v", "error", "-y",
         "-f", "lavfi", "-i", "testsrc=size=160x120:rate=10:duration=1",
         "-f", "lavfi", "-i", "sine=frequency=440:duration=1",
         "-f", "lavfi", "-i", "sine=frequency=880:duration=1",
         "-map", "0:v", "-map", "1:a", "-map", "2:a",
         "-metadata:s:a:0", "language=jpn", "-metadata:s:a:1", "language=eng",
         "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac",
         "-f", "matroska", str(original)], check=True)

    assert not any(k in (s.get("tags") or {})
                   for s in _ffprobe(original)["streams"]
                   for k in ("handler_name", "HANDLER_NAME")), \
        "the fixture original already carries residue; the test cannot see it"

    # The job: convert to MP4, keeping only the English audio.
    processed = tmp_path / "processed.mp4"
    subprocess.run(
        ["ffmpeg", "-v", "error", "-y", "-i", str(original),
         "-map", "0:0", "-map", "0:2", "-c", "copy",
         "-f", "mp4", str(processed)], check=True)

    residue = [k for s in _ffprobe(processed)["streams"]
               for k in (s.get("tags") or {})
               if k in ("handler_name", "vendor_id")]
    assert residue, "fixture did not produce MP4 residue to clean up"

    manifest = build_manifest(_ffprobe(original), original_path=str(original),
                              original_container="mkv")
    matches = match_streams(manifest, _ffprobe(processed))
    lost = [s for s, i in matches if i is None]
    sidecar = tmp_path / "1.remuxarr_revert"
    subprocess.run(
        build_sidecar_command([str(original)], str(sidecar),
                              [(s, 0, s["index"]) for s in lost]), check=True)
    sidecar_indices = {id(s): n for n, s in enumerate(lost)}
    for stream, produced_index in matches:
        stream["processed_index"] = produced_index
        if id(stream) in sidecar_indices:
            stream["sidecar_index"] = sidecar_indices[id(stream)]

    restored = tmp_path / "restored.mkv"
    _run_restore(processed, sidecar, restored, manifest)

    for i, tags in enumerate(_tags(restored)):
        leftover = {k for k in tags if k in ("handler_name", "vendor_id",
                                             "HANDLER_NAME", "VENDOR_ID")}
        assert not leftover, (
            f"stream {i} came back carrying MP4 container residue: {leftover}"
        )


@ffmpeg_required
def test_real_round_trip_keeps_attachments_muxable(tmp_path):
    """
    A single per-stream -map_metadata replaces FFmpeg's default "copy all
    stream metadata" for the WHOLE output, not just the stream named. Any
    version of this that clears one stream and hand-writes fewer tags than
    the original had will drop the rest silently — and Matroska refuses
    outright to mux an attachment with no filename tag, which is the one
    case that fails loudly rather than quietly.
    """
    original, processed, sidecar, manifest = _round_trip_fixture(tmp_path)

    restored = tmp_path / "restored.mkv"
    _run_restore(processed, sidecar, restored, manifest)

    attachments = [s for s in _ffprobe(restored)["streams"]
                   if s["codec_type"] == "attachment"]
    assert attachments, "the attachment did not survive the restore"
    assert attachments[0]["tags"]["filename"] == "Roboto.ttf"
    assert attachments[0]["tags"]["mimetype"] == "application/x-truetype-font"


@ffmpeg_required
def test_real_round_trip_undoes_a_language_retag(tmp_path):
    """
    The case stream copy cannot handle. The job rewrote a tag without
    touching a byte of payload, so the track is still there and the
    sidecar holds nothing — only the explicit metadata rewrite puts the
    original value back.
    """
    from app.core.ffmpeg import build_restore_command
    from app.core.revert import build_manifest, match_streams

    original = tmp_path / "original.mkv"
    subprocess.run(
        ["ffmpeg", "-v", "error", "-y",
         "-f", "lavfi", "-i", "testsrc=size=160x120:rate=10:duration=1",
         "-f", "lavfi", "-i", "sine=frequency=440:duration=1",
         "-map", "0:v", "-map", "1:a",
         "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac",
         "-f", "matroska", str(original)], check=True)

    # The original audio carries no language tag at all.
    assert (_ffprobe(original)["streams"][1].get("tags") or {}).get("language") is None

    processed = tmp_path / "processed.mkv"
    subprocess.run(
        ["ffmpeg", "-v", "error", "-y", "-i", str(original),
         "-map", "0", "-c", "copy", "-metadata:s:a:0", "language=eng",
         "-f", "matroska", str(processed)], check=True)
    assert _ffprobe(processed)["streams"][1]["tags"]["language"] == "eng"

    manifest = build_manifest(_ffprobe(original), original_path=str(original),
                              original_container="mkv")
    for stream, produced_index in match_streams(manifest, _ffprobe(processed)):
        stream["processed_index"] = produced_index

    restored = tmp_path / "restored.mkv"
    cmd = build_restore_command(str(processed), str(processed), str(restored),
                                manifest)
    subprocess.run([a for a in cmd if a not in ("-progress", "pipe:1")],
                   check=True, stdout=subprocess.DEVNULL)

    tags = _ffprobe(restored)["streams"][1].get("tags") or {}
    assert tags.get("language") in (None, "", "und"), (
        f"the job's language tag survived the revert: {tags.get('language')!r}"
    )
