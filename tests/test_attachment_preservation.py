"""
Regression tests for attachment loss on remux.

The bug: build_ffmpeg_command's -map arguments are built from
extract_tracks(), which returns only video, audio and subtitle streams.
No attachment was ever named in a map, FFmpeg's default stream selection
does not pick them up, and the mux reports success. Every remux —
including a pure language re-tag that changes nothing else — silently
destroyed the file's fonts, cover art and posters.

Nothing surfaced it. The job succeeded, the sizes looked right, and the
only symptom was styled subtitles rendering in a fallback typeface at
some later point, with no way back to the original.

The argv tests below are the cheap half. The load-bearing ones run real
FFmpeg against a real file with a real attachment and check the
attachment is in the output, because the bug was invisible at exactly
the level argv assertions operate at — the command looked correct, and
was correct, for the streams it knew about.

Verified by mutation, 4 applied, 4 killed:

  • The attachment map removed entirely     → killed (the original bug)
  • "0:t?" → "0:t" (optional marker dropped)→ killed, real FFmpeg exits
                                              non-zero on the majority of
                                              files, which have none
  • The out_fmt gate removed, mapping into
    every container                          → killed, real MP4 mux fails
                                              at header write
  • Gate widened to include "mp4"            → killed, same

No equivalent mutants.
"""
import asyncio
import json
import os
import shutil
import subprocess

import pytest

from app.core.decision import analyze_file
from app.core.ffmpeg import build_ffmpeg_command
from tests.conftest import make_file_info, make_track


ffmpeg_required = pytest.mark.skipif(
    shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None,
    reason="ffmpeg/ffprobe not available",
)


def _drop_foreign_audio(path, container, settings):
    """A file needing only a track drop — no container change involved."""
    tracks = [
        make_track(stream_index=0, track_type="video", codec="h264"),
        make_track(stream_index=1, track_type="audio", codec="aac",
                   language="eng", is_default=True),
        make_track(stream_index=2, track_type="audio", codec="aac",
                   language="fre"),
    ]
    file_info = make_file_info(path=path, container=container, video_codec="h264")
    decision = analyze_file(file_info, tracks, settings)
    assert decision.should_process, "fixture no longer produces work"
    return decision, tracks


# ── argv ─────────────────────────────────────────────────────────────────────

def test_matroska_output_maps_attachments(settings):
    settings["prefer_mp4_container"] = False
    decision, tracks = _drop_foreign_audio("/media/Show.mkv", "mkv", settings)

    cmd = build_ffmpeg_command("/media/Show.mkv", "/media/out.mkv",
                               decision, tracks)

    assert "0:t?" in cmd, "attachments are not mapped; they will be destroyed"


def test_attachment_map_is_optional(settings):
    """
    Most files have no attachments at all. Without the "?" FFmpeg treats
    an unmatched map as an error and every one of those jobs fails.
    """
    settings["prefer_mp4_container"] = False
    decision, tracks = _drop_foreign_audio("/media/Show.mkv", "mkv", settings)

    cmd = build_ffmpeg_command("/media/Show.mkv", "/media/out.mkv",
                               decision, tracks)

    assert "0:t" not in cmd, "the non-optional form would fail on most files"


def test_mp4_output_does_not_map_attachments(settings):
    """
    Not a defensive skip. MP4 cannot store an attachment and the mux
    fails at header write, so mapping one turns a working job into a
    failing one.
    """
    settings["prefer_mp4_container"] = True
    decision, tracks = _drop_foreign_audio("/media/Show.mp4", "mp4", settings)

    cmd = build_ffmpeg_command("/media/Show.mp4", "/media/out.mp4",
                               decision, tracks)

    assert "0:t?" not in cmd


# ── Against real FFmpeg ──────────────────────────────────────────────────────

def _source_with_attachment(tmp_path, container="mkv"):
    """A file with video, two audio languages, and a font attachment."""
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
    font = tmp_path / "Roboto.ttf"
    font.write_bytes(b"\x00\x01\x02\x03" * 256)

    source = tmp_path / f"source.{container}"
    subprocess.run(
        ["ffmpeg", "-v", "error", "-y", "-i", str(video), "-i", str(audio),
         "-i", str(audio),
         "-map", "0:v", "-map", "1:a", "-map", "2:a",
         "-metadata:s:a:0", "language=eng", "-metadata:s:a:1", "language=fre",
         "-c", "copy", "-attach", str(font),
         "-metadata:s:t", "mimetype=application/x-truetype-font",
         "-f", "matroska", str(source)],
        check=True,
    )
    return source


def _streams(path):
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-show_streams", "-of", "json", str(path)],
        capture_output=True, text=True,
    )
    assert out.returncode == 0, f"{path} is unreadable"
    return json.loads(out.stdout)["streams"]


@ffmpeg_required
def test_real_remux_preserves_the_attachment(tmp_path, settings):
    """
    The reproduction, run through the production command builder rather
    than a hand-written command — the point being that the builder itself
    was what dropped it.
    """
    settings["prefer_mp4_container"] = False
    source = _source_with_attachment(tmp_path)
    assert any(s["codec_type"] == "attachment" for s in _streams(source))

    decision, tracks = _drop_foreign_audio(str(source), "mkv", settings)
    out = tmp_path / "out.mkv"
    subprocess.run(build_ffmpeg_command(str(source), str(out), decision, tracks),
                   check=True)

    kinds = [s["codec_type"] for s in _streams(out)]
    assert "attachment" in kinds, "the attachment was destroyed by the remux"
    assert kinds.count("audio") == 1, "fixture should still have dropped the fre track"

    attachment = next(s for s in _streams(out) if s["codec_type"] == "attachment")
    assert attachment["tags"]["filename"] == "Roboto.ttf"


@ffmpeg_required
def test_real_remux_of_a_file_without_attachments_still_succeeds(tmp_path, settings):
    """
    The majority case, and what the "?" is for. A non-optional map turns
    every ordinary file into a failed job.
    """
    settings["prefer_mp4_container"] = False
    plain = tmp_path / "plain.mkv"
    subprocess.run(
        ["ffmpeg", "-v", "error", "-y", "-f", "lavfi",
         "-i", "testsrc=size=160x120:rate=10:duration=2",
         "-f", "lavfi", "-i", "sine=frequency=440:duration=2",
         "-f", "lavfi", "-i", "sine=frequency=880:duration=2",
         "-map", "0:v", "-map", "1:a", "-map", "2:a",
         "-metadata:s:a:0", "language=eng", "-metadata:s:a:1", "language=fre",
         "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac",
         "-f", "matroska", str(plain)],
        check=True,
    )

    decision, tracks = _drop_foreign_audio(str(plain), "mkv", settings)
    out = tmp_path / "out.mkv"
    subprocess.run(build_ffmpeg_command(str(plain), str(out), decision, tracks),
                   check=True)

    assert [s["codec_type"] for s in _streams(out)] == ["video", "audio"]


@ffmpeg_required
def test_real_mp4_output_from_an_attachment_bearing_source_succeeds(tmp_path, settings):
    """
    Why the map is gated on the output format. The source has an
    attachment and the target is MP4, which cannot hold one — mapping it
    fails the whole mux at header write, so the gate is the difference
    between a working conversion and a broken one. The attachment is
    genuinely lost here, which is a property of MP4 rather than a
    regression.
    """
    settings["prefer_mp4_container"] = True
    source = _source_with_attachment(tmp_path)
    decision, tracks = _drop_foreign_audio(str(source), "mkv", settings)
    decision.target_container = "mp4"

    out = tmp_path / "out.mp4"
    subprocess.run(build_ffmpeg_command(str(source), str(out), decision, tracks),
                   check=True)

    kinds = [s["codec_type"] for s in _streams(out)]
    assert "attachment" not in kinds
    assert kinds.count("video") == 1


# ── Detecting fonts, so the decision engine can see them ─────────────────────
#
# The tests above pin that a remux keeps attachments. These pin that the
# pipeline can tell a file HAS fonts in the first place, which is what a
# policy about styled subtitles has to be built on. extract_tracks() keeps
# only video, audio and subtitle streams, so an attachment has never
# reached the decision engine — the count is carried in file_info instead.
#
# The fixtures are shaped from a real anime release: 17 font attachments
# with uppercase filenames, two ASS subtitle tracks, and a cover.jpg that
# must not be mistaken for one of them.

def _attachment(filename, mimetype=None):
    tags = {"filename": filename}
    if mimetype is not None:
        tags["mimetype"] = mimetype
    return {"codec_type": "attachment", "tags": tags}


def test_fonts_are_counted_from_their_mimetype():
    """
    The modern shape: mkvmerge writes font/ttf and font/otf. Both count,
    which is why the check is a prefix rather than a list of exact types.
    """
    from app.core.probe import count_font_attachments

    probe = {"streams": [
        _attachment("FTLTLT.TTF", "font/ttf"),
        _attachment("Dunkirk.otf", "font/otf"),
        # No usable extension, so only the mimetype can identify it. Without
        # one of these the mimetype branch is untestable: every font whose
        # filename ends in .ttf is found by the fallback regardless of what
        # the mimetype check does.
        _attachment("embedded_font_0", "font/collection"),
    ]}

    assert count_font_attachments(probe) == 3


def test_the_legacy_font_mimetypes_are_recognised():
    """
    Older mkvmerge and other muxers write application/x-truetype-font and
    relatives. A file muxed years ago is exactly the kind that has styled
    subtitles worth protecting, so missing these misses the case.
    """
    from app.core.probe import count_font_attachments

    # Filenames deliberately carry no font extension, so these can only be
    # found by their mimetype — otherwise the fallback answers for them and
    # dropping this whole branch changes nothing observable.
    probe = {"streams": [
        _attachment("NotoSans", "application/x-truetype-font"),
        _attachment("Fontin_Sans_BI", "application/vnd.ms-opentype"),
        _attachment("augie", "application/x-font-ttf"),
    ]}

    assert count_font_attachments(probe) == 3


def test_a_font_with_no_usable_mimetype_is_found_by_its_filename():
    """
    Some muxers write application/octet-stream and leave the filename as
    the only signal. The real release this is shaped from names its fonts
    in upper case — GARABD.TTF, FRABK.TTF — so the suffix comparison has
    to be case-insensitive or every font in that file is missed.
    """
    from app.core.probe import count_font_attachments

    probe = {"streams": [
        _attachment("GARABD.TTF", "application/octet-stream"),
        _attachment("Jellyka CuttyCupcakes.ttf"),          # no mimetype at all
    ]}

    assert count_font_attachments(probe) == 2


def test_cover_art_is_not_counted_as_a_font():
    """
    The release this is shaped from carries cover.jpg alongside its fonts.
    It never could be counted — ffprobe reports cover art as
    codec_type="video" with attached_pic, not as an attachment — but the
    assertion is here because "the file has attachments" and "the file has
    fonts" are the two things this must not confuse.
    """
    from app.core.probe import count_font_attachments

    probe = {"streams": [
        {"codec_type": "video", "disposition": {"attached_pic": 1},
         "tags": {"filename": "cover.jpg", "mimetype": "image/jpeg"}},
        _attachment("cover.jpg", "image/jpeg"),
    ]}

    assert count_font_attachments(probe) == 0


def test_a_non_font_attachment_is_not_counted():
    """
    Attachments are not only fonts. Counting any of them would put files
    carrying a release note or a chapter image into a review queue meant
    for styled subtitles.
    """
    from app.core.probe import count_font_attachments

    probe = {"streams": [
        _attachment("readme.txt", "text/plain"),
        _attachment("thumbnail.png", "image/png"),
    ]}

    assert count_font_attachments(probe) == 0


def test_a_stream_that_is_not_an_attachment_is_not_examined():
    """
    Recorded as an equivalent mutation rather than pinned: removing the
    codec_type == "attachment" check changes no count for any real ffprobe
    output, because only attachment streams ever carry a font mimetype or
    a font filename. Cover art carries filename cover.jpg, subtitle tracks
    carry titles rather than filenames, and no muxer writes font/* on a
    media stream. The check is kept because it states what is being
    counted, and this test holds the ordinary shape rather than a
    contrived one built to make the mutant die.
    """
    from app.core.probe import count_font_attachments

    probe = {"streams": [
        {"codec_type": "subtitle", "codec_name": "ass",
         "tags": {"title": "Signs and Songs [Saiki]"}},
        _attachment("FTLTLT.TTF", "font/ttf"),
    ]}

    assert count_font_attachments(probe) == 1


def test_a_file_with_no_attachments_counts_zero():
    from app.core.probe import count_font_attachments

    probe = {"streams": [
        {"codec_type": "video", "codec_name": "hevc"},
        {"codec_type": "audio", "codec_name": "aac"},
        {"codec_type": "subtitle", "codec_name": "ass"},
    ]}

    assert count_font_attachments(probe) == 0


def test_the_font_count_travels_with_the_container_and_duration():
    """
    Carried in extract_format_info so every caller gets it without
    changing five call sites, and so it reaches analyze_file's file_info
    where the policy will read it.
    """
    from app.core.probe import extract_format_info

    probe = {
        "format": {"format_name": "matroska,webm", "duration": "1431.018"},
        "streams": [
            {"codec_type": "video", "codec_name": "hevc"},
            _attachment("FTLTLT.TTF", "font/ttf"),
            _attachment("GARABD.TTF", "font/ttf"),
        ],
    }

    info = extract_format_info(probe)

    assert info["container"] == "mkv"
    assert info["font_attachments"] == 2


# ── Matroska cover art ───────────────────────────────────────────────────────
#
# Matroska stores cover art as an image attachment, but FFmpeg's demuxer
# reports it as a video stream marked attached_pic. "0:t?" therefore never
# matched it and extract_tracks skips it, so every job that left a file as
# MKV dropped its cover. Mapping that stream back is no fix: the muxer
# writes it as a real still-image video track. The executors copy each
# cover out and re-attach it instead. Found by tools/testlib/explore.py.
#
# Verified by mutation against the whole suite, 9 applied, 9 killed:
#
#   • covers copied out and attached for MP4 output too
#   • attachment numbering ignoring the attachments already mapped
#   • covers copied out but none handed to the command
#   • cleanup of the copied-out covers removed
#   • every cover copied from the first cover stream
#   • filename and mimetype swapped on the attach
#   • a failed copy carrying on without the cover
#   • the combined pass not cleaning up its covers
#   • a probe failure failing the job
#
# The first was killed by a conversion to MP4 that had to succeed without
# the cover. That test now keeps the cover
# (test_a_conversion_to_mp4_keeps_a_matroska_cover, below); the mutant,
# re-aimed at the new MP4 branch of _prepare_extra_streams, is killed by it and
# seven other tests there.

def _image(tmp_path, name, colour):
    path = tmp_path / name
    subprocess.run(
        ["ffmpeg", "-v", "error", "-y", "-f", "lavfi",
         "-i", f"color=c={colour}:s=64x48", "-frames:v", "1", str(path)],
        check=True,
    )
    return path


def _source_with_covers(tmp_path, covers, fonts=1, subtitle=False):
    """
    Video, eng and fre audio, optionally an eng SubRip track, `fonts` font
    attachments and then each (filename, mimetype, image path) in `covers`
    attached as Matroska stores cover art.
    """
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
    inputs = ["-i", str(video), "-i", str(audio), "-i", str(audio)]
    maps = ["-map", "0:v", "-map", "1:a", "-map", "2:a"]
    if subtitle:
        srt = tmp_path / "s.srt"
        srt.write_text("1\n00:00:00,000 --> 00:00:01,000\nHello\n")
        inputs += ["-i", str(srt)]
        maps += ["-map", "3:s", "-metadata:s:s:0", "language=eng"]

    attach = []
    entries = [(f"Font{n}.ttf", "application/x-truetype-font", None)
               for n in range(fonts)] + list(covers)
    for i, (filename, mimetype, image) in enumerate(entries):
        if image is None:
            image = tmp_path / filename
            image.write_bytes(bytes([i]) * 1024)
        attach += ["-attach", str(image),
                   f"-metadata:s:t:{i}", f"mimetype={mimetype}",
                   f"-metadata:s:t:{i}", f"filename={filename}"]

    source = tmp_path / "source.mkv"
    subprocess.run(
        ["ffmpeg", "-v", "error", "-y", *inputs, *maps,
         "-metadata:s:a:0", "language=eng", "-metadata:s:a:1", "language=fre",
         "-c", "copy", *attach, "-f", "matroska", str(source)],
        check=True,
    )
    return source


def _cover_streams(path):
    return [s for s in _streams(path) if s["codec_type"] == "video"
            and (s.get("disposition") or {}).get("attached_pic")]


def _bytes_of(path, stream_index, tmp_path):
    dest = tmp_path / f"stream_{stream_index}.bin"
    subprocess.run(
        ["ffmpeg", "-v", "error", "-y", "-i", str(path),
         "-map", f"0:{stream_index}", "-c", "copy", "-f", "rawvideo", str(dest)],
        check=True,
    )
    return dest.read_bytes()


def _tag(stream, key):
    tags = stream.get("tags") or {}
    return tags.get(key, tags.get(key.upper()))


def _job(source, settings, subtitle=False):
    """A decision that keeps the file as MKV and drops the fre track."""
    settings["prefer_mp4_container"] = False
    settings["extract_text_subtitles_to_srt"] = subtitle
    tracks = [
        make_track(stream_index=0, track_type="video", codec="h264"),
        make_track(stream_index=1, track_type="audio", codec="aac",
                   language="eng", is_default=True),
        make_track(stream_index=2, track_type="audio", codec="aac",
                   language="fre"),
    ]
    if subtitle:
        tracks.append(make_track(stream_index=3, track_type="subtitle",
                                 codec="subrip", language="eng"))
    decision = analyze_file(
        make_file_info(path=str(source), container="mkv", video_codec="h264"),
        tracks, settings,
    )
    assert decision.should_process, "fixture no longer produces work"
    assert decision.target_container == "mkv"
    return decision, tracks


def _cover_temps(source, job_id):
    from app.core.ffmpeg import _pick_temp_dir
    folder = _pick_temp_dir(str(source))
    return [name for name in os.listdir(folder)
            if name.startswith(f"job_{job_id}_cover_")] if os.path.isdir(folder) else []


def _run(source, decision, tracks, job_id, **kw):
    from app.core.ffmpeg import execute_ffmpeg
    return asyncio.run(execute_ffmpeg(
        str(source), str(source), decision, tracks, job_id=job_id, **kw))


def _assert_cover_kept(out, image, filename, mimetype, tmp_path):
    covers = [s for s in _cover_streams(out) if _tag(s, "filename") == filename]
    assert len(covers) == 1, f"{filename} is not in the output as cover art"
    assert _tag(covers[0], "mimetype") == mimetype
    assert _bytes_of(out, covers[0]["index"], tmp_path) == image.read_bytes(), \
        f"{filename} came back with different bytes"


@ffmpeg_required
def test_a_job_that_keeps_a_file_as_mkv_keeps_its_cover_art(tmp_path, settings):
    """
    The reproduction, through the executor a job uses. The cover stays an
    attachment: one real video stream, not two, and the font survives too.
    """
    jpeg = _image(tmp_path, "cover.jpg", "red")
    source = _source_with_covers(tmp_path, [("cover.jpg", "image/jpeg", jpeg)])
    decision, tracks = _job(source, settings)

    result = _run(source, decision, tracks, job_id=9101)

    assert result.success is True, result.error
    _assert_cover_kept(source, jpeg, "cover.jpg", "image/jpeg", tmp_path)
    streams = _streams(source)
    real_video = [s for s in streams if s["codec_type"] == "video"
                  and not (s.get("disposition") or {}).get("attached_pic")]
    assert len(real_video) == 1, "the cover came back as a second video track"
    assert len([s for s in streams if s["codec_type"] == "audio"]) == 1
    fonts = [s for s in streams if s["codec_type"] == "attachment"]
    assert [_tag(s, "filename") for s in fonts] == ["Font0.ttf"]


@ffmpeg_required
def test_the_combined_pass_keeps_cover_art_too(tmp_path, settings):
    """
    A job that also extracts a subtitle runs execute_ffmpeg_combined, which
    builds its own call. The cover must survive there as well.
    """
    from app.core.ffmpeg import execute_ffmpeg_combined

    jpeg = _image(tmp_path, "cover.jpg", "red")
    source = _source_with_covers(tmp_path, [("cover.jpg", "image/jpeg", jpeg)],
                                 subtitle=True)
    decision, tracks = _job(source, settings, subtitle=True)
    assert any(a.action_type == "extract_subtitle" for a in decision.actions)
    srt = tmp_path / "source.en.srt"

    result, srts = asyncio.run(execute_ffmpeg_combined(
        str(source), str(source), decision, tracks, [(3, str(srt))], job_id=9102))

    assert result.success is True, result.error
    assert srts[0].success and srt.exists()
    _assert_cover_kept(source, jpeg, "cover.jpg", "image/jpeg", tmp_path)
    assert not [s for s in _streams(source) if s["codec_type"] == "subtitle"]
    assert _cover_temps(source, 9102) == []


@ffmpeg_required
def test_each_cover_keeps_its_own_name_and_type_beside_fonts(tmp_path, settings):
    """
    Re-attached covers are numbered after the attachments already mapped,
    so with fonts in the file a wrong count puts a cover's name on a font.
    Two covers of different types catch a swap between them as well.
    """
    jpeg = _image(tmp_path, "cover.jpg", "red")
    png = _image(tmp_path, "cover_land.png", "blue")
    source = _source_with_covers(
        tmp_path, [("cover.jpg", "image/jpeg", jpeg),
                   ("cover_land.png", "image/png", png)], fonts=2)
    decision, tracks = _job(source, settings)

    result = _run(source, decision, tracks, job_id=9103)

    assert result.success is True, result.error
    _assert_cover_kept(source, jpeg, "cover.jpg", "image/jpeg", tmp_path)
    _assert_cover_kept(source, png, "cover_land.png", "image/png", tmp_path)
    fonts = [s for s in _streams(source) if s["codec_type"] == "attachment"]
    assert sorted((_tag(s, "filename"), _tag(s, "mimetype")) for s in fonts) == [
        ("Font0.ttf", "application/x-truetype-font"),
        ("Font1.ttf", "application/x-truetype-font"),
    ]


@ffmpeg_required
def test_a_file_without_cover_art_copies_nothing_out(tmp_path, settings, monkeypatch):
    """No cover, no extraction: the job runs exactly as it did before."""
    from app.core import ffmpeg as ffmpeg_module

    def no_extraction(*a, **k):
        raise AssertionError("a cover was extracted from a file with none")
    monkeypatch.setattr(ffmpeg_module, "_cover_extraction_command", no_extraction)

    source = _source_with_covers(tmp_path, [])
    decision, tracks = _job(source, settings)

    result = _run(source, decision, tracks, job_id=9105)

    assert result.success is True, result.error
    assert [_tag(s, "filename") for s in _streams(source)
            if s["codec_type"] == "attachment"] == ["Font0.ttf"]


def test_the_command_attaches_only_the_covers_it_is_given(settings):
    """argv: no covers, no -attach; covers come after the attachment map."""
    from app.core.ffmpeg import CoverArt

    decision, tracks = _drop_foreign_audio("/media/m.mkv", "mkv", {
        **settings, "prefer_mp4_container": False})
    plain = build_ffmpeg_command("/media/m.mkv", "/tmp/o", decision, tracks)
    assert "-attach" not in plain

    cover = CoverArt(path="/tmp/c", filename="cover.jpg", mimetype="image/jpeg",
                     ordinal=2, stream_index=5)
    cmd = build_ffmpeg_command("/media/m.mkv", "/tmp/o", decision, tracks, covers=[cover])
    i = cmd.index("-attach")
    assert cmd.index("0:t?") < i < len(cmd) - 1
    assert cmd[i:i + 5] == ["-attach", "/tmp/c", "-metadata:s:t:2", "mimetype=image/jpeg",
                            "-metadata:s:t:2"]
    assert cmd[i + 5] == "filename=cover.jpg"
    assert cmd[-1] == "/tmp/o"


@ffmpeg_required
def test_cover_temps_are_removed_when_the_run_is_stopped(tmp_path, settings):
    """
    The copied-out covers are inputs, not outputs, so the staged runner does
    not know them. They must go however the run ends; here it is stopped at
    the swap, after FFmpeg has read them.
    """
    jpeg = _image(tmp_path, "cover.jpg", "red")
    source = _source_with_covers(tmp_path, [("cover.jpg", "image/jpeg", jpeg)])
    decision, tracks = _job(source, settings)
    original = source.read_bytes()

    async def refuse():
        return "stopped for the test"

    result = _run(source, decision, tracks, job_id=9107, before_swap=refuse)

    assert result.success is False
    assert source.read_bytes() == original
    assert _cover_temps(source, 9107) == []

    result = _run(source, decision, tracks, job_id=9108)
    assert result.success is True, result.error
    assert _cover_temps(source, 9108) == []


@ffmpeg_required
def test_a_cover_that_cannot_be_copied_out_fails_the_job_first(
        tmp_path, settings, monkeypatch):
    """
    Carrying on would drop the cover, the bug this fixes. The job fails
    before the main FFmpeg run starts, with the source untouched and the
    cover named in the error.
    """
    from app.core import ffmpeg as ffmpeg_module

    jpeg = _image(tmp_path, "cover.jpg", "red")
    source = _source_with_covers(tmp_path, [("cover.jpg", "image/jpeg", jpeg)])
    decision, tracks = _job(source, settings)
    original = source.read_bytes()

    monkeypatch.setattr(ffmpeg_module, "_cover_extraction_command",
                        lambda src, index, dest: ["ffmpeg", "-v", "error", "-i",
                                                  str(tmp_path / "missing.mkv"), dest])

    async def never(*a, **k):
        raise AssertionError("the main FFmpeg run started anyway")
    monkeypatch.setattr(ffmpeg_module, "run_staged_subprocess", never)

    result = _run(source, decision, tracks, job_id=9109)

    assert result.success is False
    assert "cover.jpg" in result.error
    assert source.read_bytes() == original
    assert _cover_temps(source, 9109) == []


@ffmpeg_required
def test_a_probe_failure_runs_the_job_as_before(tmp_path, settings, monkeypatch):
    """
    The worker already decides from stored tracks when a probe fails rather
    than fail the job over it, and this follows suit: the job runs, without
    re-attaching anything.
    """
    from app.core import ffmpeg as ffmpeg_module
    from app.core.probe import ProbeError

    def fail(*a, **k):
        raise ProbeError("unreadable for the test")
    monkeypatch.setattr(ffmpeg_module, "probe_file", fail)

    jpeg = _image(tmp_path, "cover.jpg", "red")
    source = _source_with_covers(tmp_path, [("cover.jpg", "image/jpeg", jpeg)])
    decision, tracks = _job(source, settings)

    result = _run(source, decision, tracks, job_id=9110)

    assert result.success is True, result.error
    assert _cover_temps(source, 9110) == []


# ── MP4 cover art ────────────────────────────────────────────────────────────
#
# An MP4 holds a cover as the image itself in its iTunes metadata, and
# FFmpeg reports it as a video stream marked attached_pic, as it does a
# Matroska cover. extract_tracks leaves covers out of the track lists the
# command is built from, so every job writing an MP4 dropped them all: an
# MP4's own, and a Matroska's in a conversion. FFmpeg 8.1's MP4 muxer
# writes a mapped JPEG, PNG or BMP cover back as cover art, byte for byte,
# so the executors map them (find_mp4_covers). Found by
# tools/testlib/explore.py as a known loss (PARKED 35).
#
# The sources are six seconds long with chapters, like the testlib's
# iTunes file: the chapter track duplication of PARKED 13 showed only in a
# file that long, never in a one- or two-second one.
#
# Verified by mutation against the files that reach app/core/ffmpeg.py,
# the two executor mutants against the whole suite. All 10 survived the
# suite before these tests and are killed by them:
#
#   • the cover maps removed from the command
#   • every attached_pic stream mapped, whatever its image type
#   • a QuickTime file's covers handed over too
#   • -c:v copy only when the file has a real video track
#   • every MJPEG, PNG or BMP stream taken for a cover, flag or no flag
#   • execute_ffmpeg not passing the covers to the command
#   • the combined pass not passing them
#   • the MP4 branch handing over no covers
#   • a probe failure failing a job that writes an MP4
#   • covers prepared for Matroska output only

_CHAPTERS = (";FFMETADATA1\n"
             "[CHAPTER]\nTIMEBASE=1/1000\nSTART=0\nEND=3000\ntitle=One\n"
             "[CHAPTER]\nTIMEBASE=1/1000\nSTART=3000\nEND=6000\ntitle=Two\n")


def _mp4_with_covers(tmp_path, covers, *, video=True, still_track=None,
                     subtitle=False):
    """
    A six-second MP4 with chapters: video unless `video` is False, eng and
    fre audio, an eng mov_text subtitle if `subtitle`, then each image in
    `covers` as cover art, as iTunes and FFmpeg store it. still_track, an
    image, goes in as a real video track instead, without the attached_pic
    flag, as a file forged before 95a9dc9 carries its cover (PARKED 14).
    """
    chapters = tmp_path / "chapters.txt"
    chapters.write_text(_CHAPTERS)
    inputs, maps, n = [], [], 0
    if video:
        inputs += ["-f", "lavfi", "-i", "testsrc=size=160x120:rate=24:duration=6"]
        maps += ["-map", f"{n}:v"]
        n += 1
    for freq in (440, 880):
        inputs += ["-f", "lavfi", "-i", f"sine=frequency={freq}:duration=6"]
        maps += ["-map", f"{n}:a"]
        n += 1
    if subtitle:
        srt = tmp_path / "s.srt"
        srt.write_text("1\n00:00:01,000 --> 00:00:04,000\nHello\n")
        inputs += ["-i", str(srt)]
        maps += ["-map", f"{n}:s"]
        n += 1
    flags = []
    real_video = 1 if video else 0
    if still_track is not None:
        inputs += ["-i", str(still_track)]
        maps += ["-map", f"{n}:v"]
        n += 1
        real_video += 1
    for i, image in enumerate(covers):
        inputs += ["-i", str(image)]
        maps += ["-map", f"{n}:v"]
        flags += [f"-disposition:v:{real_video + i}", "attached_pic"]
        n += 1
    inputs += ["-i", str(chapters)]

    source = tmp_path / "source.mp4"
    subprocess.run(
        ["ffmpeg", "-v", "error", "-y", *inputs, *maps, "-map_chapters", str(n),
         "-c:v:0", "libx264" if video else "copy", "-pix_fmt", "yuv420p",
         "-c:v:1", "copy", "-c:v:2", "copy", "-c:v:3", "copy",
         "-c:a", "aac", "-c:s", "mov_text",
         "-metadata:s:a:0", "language=eng", "-metadata:s:a:1", "language=fre",
         "-metadata:s:s:0", "language=eng", *flags,
         "-f", "mp4", str(source)],
        check=True,
    )
    return source


def _mp4_tracks(source):
    """The source's tracks as the scanner stores them (covers left out)."""
    from app.core.probe import extract_tracks, probe_file
    return extract_tracks(probe_file(str(source)))


def _mp4_job(source, settings, *, subtitle=False):
    """A decision that keeps an MP4 as MP4 and drops its fre track."""
    settings["prefer_mp4_container"] = True
    settings["extract_text_subtitles_to_srt"] = subtitle
    tracks = _mp4_tracks(source)
    video = next((t["codec"] for t in tracks if t["track_type"] == "video"), None)
    decision = analyze_file(
        make_file_info(path=str(source), container="mp4", video_codec=video),
        tracks, settings,
    )
    assert decision.should_process, "fixture no longer produces work"
    assert decision.target_container == "mp4"
    assert [a.stream_index for a in decision.actions if a.action_type == "drop_track"] \
        == [t["stream_index"] for t in tracks if t["language"] == "fre"]
    return decision, tracks


def _covers_by_codec(path, tmp_path):
    """{codec: bytes} of every cover in `path`, each marked attached_pic."""
    found = {}
    for s in _cover_streams(path):
        assert s["codec_name"] not in found, f"two {s['codec_name']} covers"
        found[s["codec_name"]] = _bytes_of(path, s["index"], tmp_path)
    return found


def _video_tracks(path):
    """Video streams that are tracks, not cover art."""
    return [s for s in _streams(path) if s["codec_type"] == "video"
            and not (s.get("disposition") or {}).get("attached_pic")]


def _no_extraction(monkeypatch):
    """Fail the test if any cover is copied out: only Matroska needs that."""
    from app.core import ffmpeg as ffmpeg_module

    def no_extraction(*a, **k):
        raise AssertionError("a cover was copied out for an MP4 output")
    monkeypatch.setattr(ffmpeg_module, "_cover_extraction_command", no_extraction)


@ffmpeg_required
def test_an_mp4_job_keeps_the_files_cover(tmp_path, settings, monkeypatch):
    """
    The reproduction: a job that only drops a track kept the file as MP4
    and lost its cover. The cover stays cover art, with its own bytes, and
    is not mistaken for a second video track; the chapters stay too.
    """
    _no_extraction(monkeypatch)
    jpeg = _image(tmp_path, "cover.jpg", "red")
    source = _mp4_with_covers(tmp_path, [jpeg])
    decision, tracks = _mp4_job(source, settings)

    result = _run(source, decision, tracks, job_id=9131)

    assert result.success is True, result.error
    assert _covers_by_codec(source, tmp_path) == {"mjpeg": jpeg.read_bytes()}
    assert len(_video_tracks(source)) == 1, "the cover came back as a video track"
    assert [s["codec_type"] for s in _streams(source)].count("audio") == 1
    from app.core.probe import probe_file
    assert len(probe_file(str(source)).get("chapters") or []) == 2


@ffmpeg_required
def test_each_mp4_cover_keeps_its_own_bytes(tmp_path, settings):
    """Two covers of different types, each mapped from its own stream."""
    jpeg = _image(tmp_path, "cover.jpg", "red")
    png = _image(tmp_path, "cover_land.png", "blue")
    source = _mp4_with_covers(tmp_path, [jpeg, png])
    decision, tracks = _mp4_job(source, settings)

    result = _run(source, decision, tracks, job_id=9132)

    assert result.success is True, result.error
    assert _covers_by_codec(source, tmp_path) == {
        "mjpeg": jpeg.read_bytes(), "png": png.read_bytes()}


@ffmpeg_required
def test_an_audio_only_mp4_keeps_its_cover(tmp_path, settings):
    """
    With no video track the command set no -c:v at all, and FFmpeg sent the
    cover to its default encoder for MP4, H.264, which cannot store one:
    the job failed.
    """
    jpeg = _image(tmp_path, "cover.jpg", "red")
    source = _mp4_with_covers(tmp_path, [jpeg], video=False)
    decision, tracks = _mp4_job(source, settings)
    assert not [t for t in tracks if t["track_type"] == "video"]

    result = _run(source, decision, tracks, job_id=9133)

    assert result.success is True, result.error
    assert _covers_by_codec(source, tmp_path) == {"mjpeg": jpeg.read_bytes()}


@ffmpeg_required
def test_a_still_image_track_is_not_taken_for_a_cover(tmp_path, settings):
    """
    A JPEG still-image track without the attached_pic flag is a real track:
    the command maps it with the other video already, and mapping it again
    as a cover would give the file a second copy.
    """
    jpeg = _image(tmp_path, "still.jpg", "green")
    source = _mp4_with_covers(tmp_path, [], still_track=jpeg)
    decision, tracks = _mp4_job(source, settings)
    assert [t["codec"] for t in tracks if t["track_type"] == "video"] == ["h264", "mjpeg"]

    result = _run(source, decision, tracks, job_id=9134)

    assert result.success is True, result.error
    assert [s["codec_name"] for s in _video_tracks(source)] == ["h264", "mjpeg"]
    assert _cover_streams(source) == []


@ffmpeg_required
def test_the_combined_pass_keeps_mp4_cover_art_too(tmp_path, settings):
    """execute_ffmpeg_combined builds its own call, so it gets its own test."""
    from app.core.ffmpeg import execute_ffmpeg_combined

    jpeg = _image(tmp_path, "cover.jpg", "red")
    source = _mp4_with_covers(tmp_path, [jpeg], subtitle=True)
    decision, tracks = _mp4_job(source, settings, subtitle=True)
    sub = next(a.stream_index for a in decision.actions
               if a.action_type == "extract_subtitle")
    srt = tmp_path / "source.en.srt"

    result, srts = asyncio.run(execute_ffmpeg_combined(
        str(source), str(source), decision, tracks, [(sub, str(srt))], job_id=9135))

    assert result.success is True, result.error
    assert srts[0].success and srt.exists()
    assert _covers_by_codec(source, tmp_path) == {"mjpeg": jpeg.read_bytes()}
    assert not [s for s in _streams(source) if s["codec_type"] == "subtitle"]


@ffmpeg_required
def test_a_conversion_to_mp4_keeps_a_matroska_cover(tmp_path, settings, monkeypatch):
    """
    A Matroska cover converted to MP4 is mapped, not copied out and
    attached: MP4 cannot take an attachment, and re-attaching there fails
    the mux at header write. It loses the filename and mimetype MP4 has no
    place for, and keeps its bytes.

    This test used to pin the opposite, a conversion that succeeded
    without the cover (test_a_conversion_to_mp4_is_unchanged): the loss
    PARKED 35 records, not a property of MP4.
    """
    from app.core.ffmpeg import execute_ffmpeg

    _no_extraction(monkeypatch)
    jpeg = _image(tmp_path, "cover.jpg", "red")
    source = _source_with_covers(tmp_path, [("cover.jpg", "image/jpeg", jpeg)])
    decision, tracks = _job(source, settings)
    settings["prefer_mp4_container"] = True
    decision = analyze_file(
        make_file_info(path=str(source), container="mkv", video_codec="h264"),
        tracks, settings,
    )
    assert decision.target_container == "mp4"
    out = tmp_path / "source.mp4"

    result = asyncio.run(execute_ffmpeg(str(source), str(out), decision, tracks,
                                        job_id=9104))

    assert result.success is True, result.error
    assert _covers_by_codec(out, tmp_path) == {"mjpeg": jpeg.read_bytes()}
    assert len(_video_tracks(out)) == 1
    assert _cover_temps(source, 9104) == []


@pytest.mark.parametrize("name,mimetype", [("cover.gif", "image/gif"),
                                           ("cover.tiff", "image/tiff")])
@ffmpeg_required
def test_a_cover_mp4_cannot_hold_is_left_out(tmp_path, settings, name, mimetype):
    """
    FFmpeg's Matroska demuxer reports GIF and TIFF attachments as covers
    too, and an MP4 cannot store either: mapped, they fail the whole mux at
    header write. The conversion goes ahead without them, as it does
    without fonts, and a revert point keeps them.
    """
    from app.core.ffmpeg import execute_ffmpeg

    image = _image(tmp_path, name, "red")
    source = _source_with_covers(tmp_path, [(name, mimetype, image)])
    assert len(_cover_streams(source)) == 1, "fixture's cover is not a cover"
    decision, tracks = _job(source, settings)
    settings["prefer_mp4_container"] = True
    decision = analyze_file(
        make_file_info(path=str(source), container="mkv", video_codec="h264"),
        tracks, settings,
    )
    out = tmp_path / "source.mp4"

    result = asyncio.run(execute_ffmpeg(str(source), str(out), decision, tracks,
                                        job_id=9136))

    assert result.success is True, result.error
    assert _cover_streams(out) == []


@ffmpeg_required
def test_a_quicktime_files_cover_is_not_mapped(tmp_path, settings):
    """
    FFmpeg's QuickTime muxer writes no iTunes metadata. It accepts a mapped
    cover and writes nothing, but the cover still takes a track ID and
    moves the next track's, so a QuickTime file kept as one gets no cover
    map at all. The file is the MP4 fixture with its major brand made qt,
    which FFmpeg reads as a QuickTime movie with a cover.
    """
    from app.core.ffmpeg import _prepare_extra_streams
    from app.core.probe import is_quicktime, probe_file

    jpeg = _image(tmp_path, "cover.jpg", "red")
    source = _mp4_with_covers(tmp_path, [jpeg])
    decision, _tracks = _mp4_job(source, settings)
    with open(source, "r+b") as f:
        f.seek(8)
        f.write(b"qt  ")
    probe = probe_file(str(source))
    assert is_quicktime(probe)
    cover = [s["index"] for s in probe["streams"]
             if (s.get("disposition") or {}).get("attached_pic")]
    assert len(cover) == 1

    def prepare(quicktime):
        extra = asyncio.run(_prepare_extra_streams(str(source), decision, str(tmp_path),
                                                   9137, quicktime))
        return extra.covers, extra.mp4_covers, extra.error

    assert prepare(quicktime=True) == ([], [], None)
    assert prepare(quicktime=False) == ([], cover, None)


@ffmpeg_required
def test_a_probe_failure_runs_an_mp4_job_as_before(tmp_path, settings, monkeypatch):
    """As for Matroska: no probe, no covers, and the job still runs."""
    from app.core import ffmpeg as ffmpeg_module
    from app.core.probe import ProbeError

    jpeg = _image(tmp_path, "cover.jpg", "red")
    source = _mp4_with_covers(tmp_path, [jpeg])
    decision, tracks = _mp4_job(source, settings)

    def fail(*a, **k):
        raise ProbeError("unreadable for the test")
    monkeypatch.setattr(ffmpeg_module, "probe_file", fail)

    result = _run(source, decision, tracks, job_id=9138)

    assert result.success is True, result.error


def test_mp4_covers_are_mapped_after_every_track_and_copied(settings):
    """
    argv: covers come after the last track's map, so no track's output
    index moves, and before the stream flags. -c:v copy is there for them
    when the file has no video track of its own.
    """
    decision, tracks = _drop_foreign_audio("/media/m.mp4", "mp4", settings)
    plain = build_ffmpeg_command("/media/m.mp4", "/tmp/o", decision, tracks)
    cmd = build_ffmpeg_command("/media/m.mp4", "/tmp/o", decision, tracks,
                               mp4_covers=[5, 6])

    maps = [cmd[i + 1] for i, a in enumerate(cmd) if a == "-map"]
    assert maps == [cmd[i + 1] for i, a in enumerate(plain) if a == "-map"] + ["0:5", "0:6"]
    assert cmd.index("0:6") < cmd.index("-disposition:0")

    audio_only = [t for t in tracks if t["track_type"] != "video"]
    without = build_ffmpeg_command("/media/m.mp4", "/tmp/o", decision, audio_only)
    with_covers = build_ffmpeg_command("/media/m.mp4", "/tmp/o", decision, audio_only,
                                       mp4_covers=[5])
    assert "-c:v" not in without
    assert with_covers[with_covers.index("-c:v") + 1] == "copy"


# ── Matroska cover art through AC3 Forge ─────────────────────────────────────
#
# Both forge commands copy every stream with "-map 0", which takes the
# attached_pic stream FFmpeg reports for a Matroska cover and writes it as a
# real still-image video track. They now leave each cover out of the map and
# attach it again, from the covers load_forge_job_data finds in the probe it
# already makes. An MP4's cover survives "-map 0" as cover art, so MP4 is
# left alone.
#
# The add also maps attachments after the AC3 it encodes. Copied ahead of
# it, an attachment made the Matroska muxer stop with "Received a packet for
# an attachment stream" (FFmpeg 8.1 and 6.1), so every add on an MKV with
# fonts failed. The undo only copies and was never affected.
#
# Verified by mutation against the whole suite, 15 applied, 15 killed:
#
#   • covers left in the add's "-map 0" / the undo's
#   • covers not attached again by the add / the undo
#   • attachments not held back from the add's "-map 0"
#   • attachments mapped back before the AC3 / never mapped back
#   • the worker not giving covers to the add builder / the undo builder
#   • the worker not removing the copies
#   • the worker running the command after a failed copy
#   • the finder taking an MP4's cover, which has no filename or mimetype
#   • covers unmapped by attachment number rather than stream index
#   • the loader recording no covers
#   • copying out not cleaning up when cancelled
#
# The main pipeline's nine above were rerun after the helper was split and
# all killed again.

def _forge_source(tmp_path, covers, fonts=1, container="mkv"):
    """Video and an eng AAC 5.1, then fonts and covers as Matroska keeps them."""
    video = tmp_path / "v.mkv"
    subprocess.run(
        ["ffmpeg", "-v", "error", "-y", "-f", "lavfi",
         "-i", "testsrc=size=160x120:rate=10:duration=2",
         "-c:v", "libx264", "-pix_fmt", "yuv420p", str(video)],
        check=True,
    )
    audio = tmp_path / "a51.m4a"
    subprocess.run(
        ["ffmpeg", "-v", "error", "-y", "-f", "lavfi",
         "-i", "sine=frequency=440:duration=2", "-ac", "6", "-c:a", "aac", str(audio)],
        check=True,
    )
    source = tmp_path / f"source.{container}"
    if container == "mp4":
        image = covers[0][2]
        subprocess.run(
            ["ffmpeg", "-v", "error", "-y", "-i", str(video), "-i", str(audio),
             "-i", str(image), "-map", "0:v", "-map", "1:a", "-map", "2:v",
             "-c", "copy", "-disposition:v:1", "attached_pic",
             "-metadata:s:a:0", "language=eng", str(source)],
            check=True,
        )
        return source
    attach = []
    entries = [(f"Font{n}.ttf", "application/x-truetype-font", None)
               for n in range(fonts)] + list(covers)
    for i, (filename, mimetype, image) in enumerate(entries):
        if image is None:
            image = tmp_path / filename
            image.write_bytes(bytes([i]) * 1024)
        attach += ["-attach", str(image),
                   f"-metadata:s:t:{i}", f"mimetype={mimetype}",
                   f"-metadata:s:t:{i}", f"filename={filename}"]
    subprocess.run(
        ["ffmpeg", "-v", "error", "-y", "-i", str(video), "-i", str(audio),
         "-map", "0:v", "-map", "1:a", "-metadata:s:a:0", "language=eng",
         "-c", "copy", *attach, "-f", "matroska", str(source)],
        check=True,
    )
    return source


def _forge_job_data(source, is_undo=False, job_id=1):
    from app.core.ffmpeg import find_matroska_covers
    from app.core.probe import probe_file
    container = source.suffix.lstrip(".")
    return {
        "job_id": job_id, "file_id": 1, "is_undo": is_undo,
        "file_path": str(source), "filename": source.name, "container": container,
        "aac_stream_index": 1, "audio_track_count": 1,
        "undo_audio_output_index": 1 if is_undo else None,
        "titles": {}, "original_size": os.path.getsize(source),
        "job_timeout_minutes": 5, "add_faststart": False,
        "app_cfg": {"revert_enabled": False},
        "cover_art": find_matroska_covers(probe_file(str(source))),
    }


def _run_forge(monkeypatch, job_data):
    """_run_forge_job on a real file; returns finish_forge_job's arguments."""
    import app.core.forge as forge
    import app.core.worker as worker

    finished = {}

    def finish(job_id, success, size, error, **kwargs):
        finished.update(success=success, error=error)

    with monkeypatch.context() as m:
        m.setattr(forge, "load_forge_job_data", lambda _id: dict(job_data))
        m.setattr(forge, "finish_forge_job", finish)
        m.setattr(forge, "load_forge_final_state", lambda _id: None)

        class _WS:
            async def broadcast_json(self, *_a, **_k):
                pass

        async def go():
            await worker._run_forge_job(job_data["job_id"], _WS(),
                                        asyncio.get_running_loop())
        asyncio.run(go())
    return finished


def _forge_temps(source, job_id):
    from app.core.ffmpeg import _pick_temp_dir
    folder = _pick_temp_dir(str(source))
    return [n for n in os.listdir(folder)
            if n.startswith(f"forge_{job_id}_cover_")] if os.path.isdir(folder) else []


def _audio_codecs(path):
    return [s["codec_name"] for s in _streams(path) if s["codec_type"] == "audio"]


def _real_video(path):
    return [s for s in _streams(path) if s["codec_type"] == "video"
            and not (s.get("disposition") or {}).get("attached_pic")]


@ffmpeg_required
def test_a_forge_add_on_an_mkv_keeps_its_cover_art(tmp_path, monkeypatch):
    """
    The reproduction: the cover stays cover art, not a second video track.
    No fonts, so this is the cover alone; fonts are the test after next's.
    """
    jpeg = _image(tmp_path, "cover.jpg", "red")
    source = _forge_source(tmp_path, [("cover.jpg", "image/jpeg", jpeg)], fonts=0)

    done = _run_forge(monkeypatch, _forge_job_data(source, job_id=9201))

    assert done["success"] is True, done["error"]
    assert _audio_codecs(source) == ["aac", "ac3"]
    assert len(_real_video(source)) == 1, "the cover came back as a video track"
    _assert_cover_kept(source, jpeg, "cover.jpg", "image/jpeg", tmp_path)
    assert _forge_temps(source, 9201) == []


@ffmpeg_required
def test_a_forge_undo_keeps_cover_art(tmp_path, monkeypatch):
    jpeg = _image(tmp_path, "cover.jpg", "red")
    source = _forge_source(tmp_path, [("cover.jpg", "image/jpeg", jpeg)], fonts=0)
    assert _run_forge(monkeypatch, _forge_job_data(source, job_id=9202))["success"]

    done = _run_forge(monkeypatch, _forge_job_data(source, is_undo=True, job_id=9203))

    assert done["success"] is True, done["error"]
    assert _audio_codecs(source) == ["aac"]
    assert len(_real_video(source)) == 1, "the cover came back as a video track"
    _assert_cover_kept(source, jpeg, "cover.jpg", "image/jpeg", tmp_path)
    assert _forge_temps(source, 9203) == []


@ffmpeg_required
def test_a_forge_add_keeps_each_cover_beside_fonts(tmp_path, monkeypatch):
    jpeg = _image(tmp_path, "cover.jpg", "red")
    png = _image(tmp_path, "cover_land.png", "blue")
    source = _forge_source(tmp_path, [("cover.jpg", "image/jpeg", jpeg),
                                      ("cover_land.png", "image/png", png)], fonts=2)

    done = _run_forge(monkeypatch, _forge_job_data(source, job_id=9204))

    assert done["success"] is True, done["error"]
    assert len(_real_video(source)) == 1
    _assert_cover_kept(source, jpeg, "cover.jpg", "image/jpeg", tmp_path)
    _assert_cover_kept(source, png, "cover_land.png", "image/png", tmp_path)
    assert sorted(_tag(s, "filename") for s in _streams(source)
                  if s["codec_type"] == "attachment") == ["Font0.ttf", "Font1.ttf"]


@ffmpeg_required
def test_a_forge_add_on_an_mp4_is_unchanged(tmp_path, monkeypatch):
    """
    An MP4's cover survives "-map 0" as cover art already and carries no
    filename, so nothing is copied out and the cover is still there.
    """
    from app.core import ffmpeg as ffmpeg_module

    def no_extraction(*a, **k):
        raise AssertionError("a cover was copied out of an MP4")
    monkeypatch.setattr(ffmpeg_module, "_cover_extraction_command", no_extraction)

    jpeg = _image(tmp_path, "cover.jpg", "red")
    source = _forge_source(tmp_path, [("cover.jpg", "image/jpeg", jpeg)], container="mp4")
    assert len(_cover_streams(source)) == 1

    done = _run_forge(monkeypatch, _forge_job_data(source, job_id=9205))

    assert done["success"] is True, done["error"]
    assert _audio_codecs(source) == ["aac", "ac3"]
    covers = _cover_streams(source)
    assert len(covers) == 1
    assert _bytes_of(source, covers[0]["index"], tmp_path) == jpeg.read_bytes()


@ffmpeg_required
def test_a_forge_cover_that_cannot_be_copied_out_fails_first(tmp_path, monkeypatch):
    import app.core.forge as forge
    from app.core import ffmpeg as ffmpeg_module

    jpeg = _image(tmp_path, "cover.jpg", "red")
    source = _forge_source(tmp_path, [("cover.jpg", "image/jpeg", jpeg)])
    original = source.read_bytes()
    monkeypatch.setattr(ffmpeg_module, "_cover_extraction_command",
                        lambda src, index, dest: ["ffmpeg", "-v", "error", "-i",
                                                  str(tmp_path / "missing.mkv"), dest])

    async def never(*a, **k):
        raise AssertionError("the forge FFmpeg run started anyway")
    monkeypatch.setattr(forge, "run_forge_command", never)

    done = _run_forge(monkeypatch, _forge_job_data(source, job_id=9206))

    assert done["success"] is False
    assert "cover.jpg" in done["error"]
    assert source.read_bytes() == original
    assert _forge_temps(source, 9206) == []


@ffmpeg_required
def test_forge_cover_temps_go_when_the_run_fails(tmp_path, monkeypatch):
    """The copies are inputs the staged runner does not know; they go anyway."""
    import app.core.forge as forge

    jpeg = _image(tmp_path, "cover.jpg", "red")
    source = _forge_source(tmp_path, [("cover.jpg", "image/jpeg", jpeg)])
    seen = []

    async def failing(**kwargs):
        seen.append(_forge_temps(source, 9207))
        return forge.ForgeResult(success=False, output_path=None,
                                 output_size=None, error="failed for the test")
    monkeypatch.setattr(forge, "run_forge_command", failing)

    done = _run_forge(monkeypatch, _forge_job_data(source, job_id=9207))

    assert done["success"] is False
    assert seen and seen[0], "the cover was not copied out before the run"
    assert _forge_temps(source, 9207) == []


def test_the_forge_commands_leave_covers_out_of_the_copy():
    """
    argv: each cover unmapped from "-map 0" and attached again. The add also
    holds attachments back until after the AC3; the undo, a copy, need not.
    """
    from app.core.ffmpeg import CoverArt
    from app.core.forge import build_add_ac3_command, build_undo_command

    cover = CoverArt(path="/t/c", filename="cover.jpg", mimetype="image/jpeg",
                     ordinal=1, stream_index=4)
    attach = ["-attach", "/t/c", "-metadata:s:t:1", "mimetype=image/jpeg",
              "-metadata:s:t:1", "filename=cover.jpg"]

    add = build_add_ac3_command("/m/in.mkv", "/m/t.tmp", 1, 1, container="mkv",
                                covers=[cover])
    i = add.index("0")
    assert add[i - 1:i + 5] == ["-map", "0", "-map", "-0:t", "-map", "-0:4"]
    ac3 = add.index("ac3")
    t = add.index("0:t?")
    assert ac3 < t and add[t - 1] == "-map"
    assert add[t + 1:t + 7] == attach
    assert t < add.index("-f") and add[-1] == "/m/t.tmp"

    undo = build_undo_command("/m/in.mkv", "/m/t.tmp", 1, container="mkv",
                              covers=[cover])
    i = undo.index("0")
    assert undo[i - 1:i + 5] == ["-map", "0", "-map", "-0:a:1", "-map", "-0:4"]
    j = undo.index("-attach")
    assert undo[j:j + 6] == attach and j < undo.index("-f")

    for plain in (build_add_ac3_command("/m/in.mkv", "/m/t.tmp", 1, 1, container="mkv"),
                  build_undo_command("/m/in.mkv", "/m/t.tmp", 1, container="mkv")):
        assert "-attach" not in plain and "-0:4" not in plain


@ffmpeg_required
def test_a_forge_add_on_an_mkv_with_fonts_succeeds(tmp_path, monkeypatch):
    """
    The second reproduction, no cover involved: an attachment copied ahead of
    the encoded AC3 made the Matroska muxer stop with "Received a packet for
    an attachment stream", so every add on an MKV with fonts failed.
    """
    source = _forge_source(tmp_path, [], fonts=2)

    done = _run_forge(monkeypatch, _forge_job_data(source, job_id=9208))

    assert done["success"] is True, done["error"]
    assert _audio_codecs(source) == ["aac", "ac3"]
    assert sorted(_tag(s, "filename") for s in _streams(source)
                  if s["codec_type"] == "attachment") == ["Font0.ttf", "Font1.ttf"]


@ffmpeg_required
def test_a_forge_job_loads_the_cover_art_of_the_file(tmp_path, monkeypatch):
    from sqlalchemy.orm import sessionmaker

    import app.core.forge as forge
    from app.core.ffmpeg import CoverStream
    from app.database.models import Ac3ForgeJob, Base, MediaFile
    from tests.conftest import memory_engine

    jpeg = _image(tmp_path, "cover.jpg", "red")
    source = _forge_source(tmp_path, [("cover.jpg", "image/jpeg", jpeg)], fonts=2)
    engine = memory_engine()
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine)
    monkeypatch.setattr(forge, "SessionLocal", factory)
    with factory() as db:
        media = MediaFile(path=str(source), filename=source.name,
                          directory=str(tmp_path), container="mkv",
                          size=os.path.getsize(source), mtime=0.0)
        db.add(media)
        db.flush()
        job = Ac3ForgeJob(file_id=media.id, status="processing", is_undo=False,
                          aac_stream_index=1, audio_track_count=1)
        db.add(job)
        db.commit()
        job_id = job.id

    data = forge.load_forge_job_data(job_id)

    assert data is not None
    covers, attachments = data["cover_art"]
    assert attachments == 2
    assert covers == [CoverStream(index=4, filename="cover.jpg", mimetype="image/jpeg")]


def test_copying_covers_out_leaves_nothing_when_cancelled(tmp_path, monkeypatch):
    """
    Cancelled between two covers, the first copy is removed before the
    cancellation goes on: no caller has the list yet to clean up after.
    """
    from app.core import ffmpeg as ffmpeg_module
    from app.core.ffmpeg import CoverStream, copy_out_cover_art

    calls = []

    async def copy(input_path, stream_index, dest):
        calls.append(dest)
        if len(calls) == 2:
            raise asyncio.CancelledError()
        with open(dest, "wb") as f:
            f.write(b"jpeg")
        return None
    monkeypatch.setattr(ffmpeg_module, "_copy_cover_out", copy)

    found = [CoverStream(index=3, filename="a.jpg", mimetype="image/jpeg"),
             CoverStream(index=4, filename="b.png", mimetype="image/png")]
    with pytest.raises(asyncio.CancelledError):
        asyncio.run(copy_out_cover_art("/m/in.mkv", found, 0, str(tmp_path),
                                       "job_1", ".remuxarr_tmp", "Job 1"))

    assert len(calls) == 2
    assert os.listdir(tmp_path) == []
