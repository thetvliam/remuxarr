"""
A job keeps the data streams FFmpeg can write back: a GoPro's telemetry,
and a QuickTime file's timecode, which a revert puts back where it was.

A camera's or a phone's file carries data streams beside its tracks. The
user's GoPro file GX010042.MP4 holds video, AAC, a timecode track (tmcd)
and GoPro telemetry (bin_data, "gpmd"); their iPhone file IMG_8810.mov holds
video, AAC and four metadata tracks ("mebx"). No track list holds a data
stream, so every job writing such a file dropped them all, and the revert
point could not keep them either, as its Matroska sidecar holds none: the
capture failed, so the data was gone for good, or with Require Revert
Point on the job failed (PARKED 38). The explorer cannot see this: its
check 3 counts no data stream.

The executors now map each data stream the output's muxer writes properly
(ffmpeg.find_data_streams), after its tracks: "gpmd" into an MP4 or a
QuickTime file, and a QuickTime file's tmcd, which the QuickTime muxer
copies, so a revert puts it back where it was. An MP4's tmcd is written
again by the muxer, last (probe.timecode_track_indices), so in the GoPro's
layout, timecode before telemetry, the two change places, in the job and in
the revert alike. No other data stream can be kept: both muxers write a
data track's sample description only for "tmcd" and "gpmd"
(mov_write_stsd_tag, libavformat/movenc.c n8.1.3), so a copied "mebx" track
would come out with an empty one. A file holding one, the iPhone's, is left
as it is (test_unwritable_data.py).

These run the real scan, worker and revert on files shaped like the user's,
six seconds long. Telemetry is made as GoPro-style "gpmd" by copying a
chapter track into an MP4 (probe.chapter_track_indices tells why that
gives one); the iPhone's track is a QuickTime timecode track whose sample
entry and handler are rewritten to "mebx" and "meta", as nothing in FFmpeg
writes one (_as_iphone_metadata, which test_unwritable_data.py uses).

Verified by mutation, 8 applied, 8 killed. Each was first run against the
whole suite as it stood (2548 tests): 6 survived it, and the MP4 taking a
tmcd and QuickTime taking any data stream were killed there by the
timecode and chapter-track tests:

  • the data maps removed                  → argv, the GoPro job and
                                             revert, the QuickTime file,
                                             the combined pass
  • an MP4 taking "tmcd" too               → which streams, the GoPro job
                                             and revert, the combined pass
  • QuickTime taking any data stream       → which streams (and, when
                                             first run, an iPhone-style
                                             track this file then held)
  • QuickTime leaving out "tmcd"           → which streams, the QuickTime
                                             file
  • execute_ffmpeg not passing them        → the GoPro job and revert,
                                             the QuickTime file
  • the combined pass not passing them     → the combined pass
  • the QuickTime branch handing none      → the QuickTime file
  • the MP4 branch handing none            → the GoPro job and revert,
                                             the combined pass

The kills are from this file, run with test_timecode_track.py,
test_mp4_chapter_track.py, test_quicktime_files.py and
test_attachment_preservation.py.
"""
import asyncio
import hashlib
import struct
import subprocess

import pytest

from app.core.decision import analyze_file
from app.core.ffmpeg import build_ffmpeg_command, find_data_streams
from app.core.probe import extract_tracks, probe_file
from tests import test_quicktime_files as _qt
from tests.conftest import make_file_info
from tests.test_quicktime_files import _run_job, needs_ffmpeg

# That file's fixture: a file-backed database every session points at,
# scratch folders and an empty library, English audio kept and revert
# points on. Bound by assignment, as test_forge_undo_identity.py does:
# pyflakes reads an imported fixture as unused.
rig = _qt.rig

SECONDS = 6
RATE = "30000/1001"
TIMECODE = "12:59:45:12"


# ── Which data streams a job keeps ───────────────────────────────────────────

def _probe(*streams):
    return {"streams": [dict(s, index=i) for i, s in enumerate(streams)]}


def _data(tag, codec="none"):
    return {"codec_type": "data", "codec_name": codec, "codec_tag_string": tag}


_VIDEO = {"codec_type": "video", "codec_tag_string": "avc1"}
_AUDIO = {"codec_type": "audio", "codec_tag_string": "mp4a"}
_GOPRO = _probe(_VIDEO, _data("tmcd"), _AUDIO, _data("gpmd", "bin_data"))
_OTHERS = _probe(_VIDEO, _AUDIO, _data("mebx"), _data("rtmd"),
                 _data("text", "bin_data"))     # an iPhone's, a Sony's, a chapter track


@pytest.mark.parametrize("probe, fmt, expected", [
    (_GOPRO, "mp4", [3]),          # its tmcd the MP4 muxer writes itself
    (_GOPRO, "mov", [1, 3]),
    (_OTHERS, "mp4", []),
    (_OTHERS, "mov", []),
], ids=["gopro-mp4", "gopro-quicktime", "others-mp4", "others-quicktime"])
def test_a_job_keeps_the_data_streams_the_muxer_writes(probe, fmt, expected):
    assert find_data_streams(probe, fmt) == expected


def test_data_streams_are_mapped_after_every_track(settings):
    from tests.test_attachment_preservation import _drop_foreign_audio

    decision, tracks = _drop_foreign_audio("/media/m.mp4", "mp4", settings)
    plain = build_ffmpeg_command("/media/m.mp4", "/tmp/o", decision, tracks)
    cmd = build_ffmpeg_command("/media/m.mp4", "/tmp/o", decision, tracks,
                               data_streams=[4], mp4_covers=[5])

    maps = [cmd[i + 1] for i, a in enumerate(cmd) if a == "-map"]
    assert maps == [plain[i + 1] for i, a in enumerate(plain) if a == "-map"] + ["0:4", "0:5"]
    assert "-c:d" not in cmd


# ── Real files ───────────────────────────────────────────────────────────────

def _telemetry(folder):
    """
    A six-second MP4 holding one GoPro-style data track: bin_data tagged
    "gpmd", a packet a second, made by copying a chapter track into an MP4.
    """
    chapters = folder / "telemetry.chapters"
    chapters.write_text(";FFMETADATA1\n" + "".join(
        f"[CHAPTER]\nTIMEBASE=1/1000\nSTART={n * 1000}\nEND={n * 1000 + 1000}\ntitle=T{n}\n"
        for n in range(SECONDS)))
    with_chapters, out = folder / "telemetry_src.mp4", folder / "telemetry.mp4"
    subprocess.run(["ffmpeg", "-v", "error", "-y",
                    "-f", "lavfi", "-i", f"testsrc=size=64x36:rate={RATE}:duration={SECONDS}",
                    "-i", str(chapters), "-map", "0", "-map_chapters", "1",
                    "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p",
                    "-f", "mp4", str(with_chapters)], check=True)
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", str(with_chapters),
                    "-map", "0:1", "-map_chapters", "-1", "-c", "copy",
                    "-f", "mp4", str(out)], check=True)
    for f in (chapters, with_chapters):
        f.unlink()
    return out


def _camera(path, *, brand, telemetry=True):
    """
    A camera's file in the GoPro's layout: English video with a timecode,
    its tmcd track, English and German AAC, then telemetry. Written with the
    QuickTime muxer, the only one that places a tmcd track; brand "qt  "
    keeps it a QuickTime movie, "mp41" makes it an MP4 as a GoPro's is.
    """
    staged = path.with_name("staged.mov")
    cmd = ["ffmpeg", "-v", "error", "-y",
           "-f", "lavfi", "-i", f"testsrc=size=64x36:rate={RATE}:duration={SECONDS}",
           "-f", "lavfi", "-i", f"sine=frequency=440:duration={SECONDS}",
           "-f", "lavfi", "-i", f"sine=frequency=550:duration={SECONDS}",
           "-map", "0", "-map", "1", "-map", "2",
           "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p", "-c:a", "aac",
           "-metadata:s:v:0", "language=eng",
           "-metadata:s:a:0", "language=eng", "-metadata:s:a:1", "language=ger",
           "-timecode", TIMECODE, "-f", "mov", str(staged)]
    subprocess.run(cmd, check=True)
    inputs, order = ["-i", str(staged)], ["-map", "0:0", "-map", "0:3", "-map", "0:1", "-map", "0:2"]
    telemetry_file = _telemetry(path.parent) if telemetry else None
    if telemetry_file:
        inputs += ["-i", str(telemetry_file)]
        order += ["-map", "1:0"]
    subprocess.run(["ffmpeg", "-v", "error", "-y", *inputs, *order, "-c", "copy",
                    "-movflags", "+faststart", "-f", "mov", str(path)], check=True)
    staged.unlink()
    if telemetry_file:
        telemetry_file.unlink()   # not left in a library a scan reads
    with open(path, "r+b") as f:
        f.seek(8)
        f.write(brand.encode())
    return path


def _boxes(data, start, end):
    pos = start
    while pos + 8 <= end:
        size, kind = struct.unpack(">I4s", data[pos:pos + 8])
        yield pos, size, kind
        pos += size


def _as_iphone_metadata(path):
    """
    Rewrite the file's tmcd track in place as an iPhone metadata track:
    sample entry "mebx", handler "meta". FFmpeg then reads it as a data
    stream tagged "mebx" with no timecode, as it reads an iPhone's.
    """
    data = bytearray(path.read_bytes())

    def find(start, end, *kinds):
        for pos, size, kind in _boxes(data, start, end):
            if kind == kinds[0].encode():
                if len(kinds) == 1:
                    yield pos, size
                else:
                    yield from find(pos + 8, pos + size, *kinds[1:])

    done = 0
    for moov, msize in find(0, len(data), "moov"):
        for trak, tsize in find(moov + 8, moov + msize, "trak"):
            (hdlr, _), = find(trak + 8, trak + tsize, "mdia", "hdlr")
            if bytes(data[hdlr + 16:hdlr + 20]) != b"tmcd":
                continue
            data[hdlr + 16:hdlr + 20] = b"meta"
            (stsd, _), = find(trak + 8, trak + tsize, "mdia", "minf", "stbl", "stsd")
            assert bytes(data[stsd + 20:stsd + 24]) == b"tmcd"
            data[stsd + 20:stsd + 24] = b"mebx"
            done += 1
    assert done == 1
    path.write_bytes(bytes(data))
    return path


def _streams(path):
    """Each stream's type, sample entry and language, in order."""
    return [(s["codec_type"], s.get("codec_tag_string"), (s.get("tags") or {}).get("language"))
            for s in probe_file(str(path))["streams"]]


def _packets(path, tag):
    """The packets of the first stream with that sample entry."""
    index = [s[1] for s in _streams(path)].index(tag)
    data = subprocess.run(["ffmpeg", "-v", "error", "-i", str(path), "-map", f"0:{index}",
                           "-c", "copy", "-f", "data", "-"],
                          capture_output=True, check=True).stdout
    assert data, f"the {tag} track holds nothing"
    return hashlib.sha256(data).hexdigest()


def _revert(rig):
    from app.core.revert_restore import restore_revert_point
    from app.database.models import RevertPoint

    with rig.Session() as db:
        point_id = db.query(RevertPoint).one().id
    outcome = asyncio.run(restore_revert_point(point_id))
    assert outcome.success, outcome.error


VIDEO, TMCD, ENG, GER = ("video", "avc1", "eng"), ("data", "tmcd", "eng"), \
    ("audio", "mp4a", "eng"), ("audio", "mp4a", "ger")
GPMD = ("data", "gpmd", "eng")


@needs_ffmpeg
def test_a_gopro_job_keeps_the_telemetry_and_the_revert_gives_it_back(rig):
    """
    The job drops the German track and keeps the rest, the telemetry with
    its packets; the muxer writes the timecode track again, last. There is
    now a revert point, which there could not be with the telemetry lost,
    and it gives the file back, the timecode last.
    """
    movie = _camera(rig.library / "GX010042.MP4", brand="mp41")
    assert _streams(movie) == [VIDEO, TMCD, ENG, GER, GPMD], "fixture changed"
    telemetry, timecode = _packets(movie, "gpmd"), _packets(movie, "tmcd")

    assert "drop_track" in _run_job(rig, movie)

    assert _streams(movie) == [VIDEO, ENG, GPMD, TMCD]
    assert (_packets(movie, "gpmd"), _packets(movie, "tmcd")) == (telemetry, timecode)

    _revert(rig)

    assert _streams(movie) == [VIDEO, ENG, GER, GPMD, TMCD]
    assert (_packets(movie, "gpmd"), _packets(movie, "tmcd")) == (telemetry, timecode)


@needs_ffmpeg
def test_a_quicktime_job_keeps_its_data_where_it_was(rig):
    """
    The QuickTime muxer copies the timecode track and the telemetry, which
    the job maps after the tracks it keeps; the revert maps every stream in
    the original's order, so it gives back exactly the file the job was
    handed, the timecode before the audio again.
    """
    movie = _camera(rig.library / "Camera.mov", brand="qt  ")
    before = _streams(movie)
    assert before == [VIDEO, TMCD, ENG, GER, GPMD], "fixture changed"
    telemetry, timecode = _packets(movie, "gpmd"), _packets(movie, "tmcd")

    assert "drop_track" in _run_job(rig, movie)

    assert _streams(movie) == [VIDEO, ENG, TMCD, GPMD]
    assert (_packets(movie, "gpmd"), _packets(movie, "tmcd")) == (telemetry, timecode)

    _revert(rig)

    assert _streams(movie) == before
    assert (_packets(movie, "gpmd"), _packets(movie, "tmcd")) == (telemetry, timecode)


@needs_ffmpeg
def test_the_combined_pass_keeps_data_streams_too(tmp_path, settings):
    """execute_ffmpeg_combined builds its own call, so it gets its own test."""
    from app.core.ffmpeg import execute_ffmpeg_combined

    source = tmp_path / "GX010042.MP4"
    srt = tmp_path / "s.srt"
    srt.write_text("1\n00:00:01,000 --> 00:00:04,000\nHello\n")
    camera = _camera(tmp_path / "camera.mov", brand="mp41")
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", str(camera), "-i", str(srt),
                    "-map", "0:0", "-map", "0:2", "-map", "0:3", "-map", "1:0", "-map", "0:4",
                    "-c", "copy", "-c:s", "mov_text", "-metadata:s:s:0", "language=eng",
                    "-f", "mp4", str(source)], check=True)
    telemetry = _packets(source, "gpmd")
    settings["prefer_mp4_container"] = True
    settings["extract_text_subtitles_to_srt"] = True
    tracks = extract_tracks(probe_file(str(source)))
    decision = analyze_file(make_file_info(path=str(source), container="mp4", video_codec="h264"),
                            tracks, settings)
    sub = next(a.stream_index for a in decision.actions if a.action_type == "extract_subtitle")
    out_srt = tmp_path / "GX010042.en.srt"

    result, srts = asyncio.run(execute_ffmpeg_combined(
        str(source), str(source), decision, tracks, [(sub, str(out_srt))], job_id=9301))

    assert result.success is True, result.error
    assert srts[0].success and out_srt.exists()
    assert ("data", "gpmd", "eng") in _streams(source)
    assert _packets(source, "gpmd") == telemetry
