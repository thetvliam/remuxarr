"""
A job and a revert keep a file's Dolby Vision (issue #62).

Reproduced with real FFmpeg before this file existed, on a 10-second cut of
a real Profile 5 file (the Kodi wiki's "Dolby Vision Mystery Box" sample)
and on files built here. A job writing a Dolby Vision stream to an MP4 kept
every RPU, as the packets are copied, and lost the stream's configuration
record, which FFmpeg's MP4 muxer writes only under -strict unofficial. It
wrote Profile 5 as hev1 besides. Played from USB on an HDR television, such
a file came out green and purple, and as hev1 with its record too; as dvh1
with its record it played in Dolby Vision. A revert from it gave back a
Matroska without the record, and a job rewriting an MP4 as an MP4 lost it
the same way.

The job now asks for the record wherever a kept video stream has one, and
writes Profile 5 as dvh1 where its source's tag cannot be kept
(ffmpeg.dolby_vision_args). AC3 Forge's add and undo, which lost the
record on an MP4 the same way, use that rule too. A revert writes dvh1
back into Matroska as hvc1, which FFmpeg accepts there, and asks for the
record going back into an MP4 (ffmpeg.build_restore_command).

The files here carry a record and no RPUs: FFmpeg's dovi_rpu filter writes
one from the colour properties, and nothing in the pinned build writes
RPUs. They test the record and the sample entry, not playback. FFmpeg
cannot write dvhe at all, so that file is a dvh1 one with its sample entry
renamed in place.

Verified by mutation, 18 applied, 18 killed. Each was first run against
the whole suite as it stood (2844 tests) with the change in place, and all
18 survived it. Fifteen were proposed; the last three came after the
testlib explorer found a dvh1 stream without its record failing a revert.

  - no -strict unofficial           -> the command, every real job and
                                       revert
  - -strict on every MP4            -> the command without Dolby Vision,
                                       and for a bare dvh1
  - -strict on a Matroska too       -> the Matroska command
  - dvh1 never written              -> the command, the Profile 5 and dvhe
                                       jobs, the conversion with a subtitle,
                                       the dvhe revert
  - dvh1 for every profile          -> the Profile 8 command and job
  - -tag:v for every video stream   -> the command, the job and the revert
                                       with cover art (FFmpeg refuses it)
  - a kept tag rewritten            -> the command for hvc1, hev1, dvh1
  - dvhe counted as kept            -> the dvhe command, job and revert
  - execute_ffmpeg not passing it   -> every real job but the combined one
  - execute_ffmpeg_combined not
    passing it                      -> the conversion with a subtitle
  - _prepare_extra_streams not
    finding it                      -> every real job and revert
  - -strict for a tag and no record -> the command for a bare dvh1
  - no hvc1 going back to Matroska  -> the restore command, the reverts of
                                       the converted Matroska
  - hvc1 for every video stream     -> the restore command, the revert with
                                       cover art
  - no -strict going back to MP4    -> the restore command, the MP4 reverts
  - the restore not handed it       -> every real revert
  - hvc1 only with a record         -> the restore command with the record
                                       lost
  - a bare dvh1 not counted         -> dolby_vision's own test

The last two are killed by the command and the helper alone: a real file
gets a dvh1 stream without its record from an AC3 Forge run, which the
explorer runs (tools/testlib; its check 10 reported the revert failing
before this case was handled).

AC3 Forge, and the rule moving into dolby_vision_args, which the job and
both Forge commands now share: 17 more applied, 17 killed. The nine rule
mutants above that are about the job's command (from -strict never given to
the bare dvh1) were re-aimed at dolby_vision_args, and the tests as they
stood killed each with exactly the tests listed above: the move lost
nothing. The Forge ones were first run against the whole suite as it stood
(2873 tests) with the change in place, and all 7 survived it.

  - the job not using the rule      -> every job command and real job
  - the add not using it            -> the add commands, the real adds, the
                                       revert after a job and an add
  - the undo not using it           -> the undo commands, the real undos
  - the job data not carrying it    -> every real add, the revert after one
  - the worker not passing it to
    the add                         -> the same
  - the worker not passing it to
    the undo                        -> every real undo
  - -strict for a QuickTime undo    -> the QuickTime undo command
  - places counted over every
    stream, not the video ones      -> the real dvhe add with the audio first

The Forge tests kill the re-aimed rule mutants as well, all but the one
giving every profile dvh1: the Profile 8 file there carries hev1, a tag
the muxer keeps, which that mutant leaves alone.
"""
import asyncio
import json
import subprocess

import pytest

from app.core.decision import ProcessingDecision
from app.core.ffmpeg import build_ffmpeg_command, build_restore_command
from app.core.forge import build_add_ac3_command, build_undo_command
from app.core.probe import DOVI_RECORD, DolbyVision, dolby_vision
from tests import test_creation_date as dated
from tests import test_quicktime_files as _qt
from tests.test_creation_date import _audio_languages, _run_job, needs_ffmpeg

# The real scan, worker and revert on a file-backed database, English audio
# kept, revert points on: test_creation_date.py's. forge_rig is the same
# with the Forge module too, test_quicktime_files.py's. Bound by
# assignment: pyflakes reads an imported fixture as unused.
rig = dated.rig
forge_rig = _qt.rig

MATROSKA = "[0][0][0][0]"   # how ffprobe shows a Matroska stream's codec tag


# ── Which streams are Dolby Vision ───────────────────────────────────────────

def _record(profile):
    return {"side_data_type": DOVI_RECORD, "dv_profile": profile, "dv_level": 1,
            "dv_bl_signal_compatibility_id": 0 if profile == 5 else 1}


def test_a_stream_is_dolby_vision_by_its_record_or_its_sample_entry():
    """A dvh1 stream whose record is gone still counts: a revert has to know
    the tag to write it back into Matroska. A record on anything but video
    does not, nor does a plain HEVC tag."""
    probe = {"streams": [
        {"index": 0, "codec_type": "video", "codec_tag_string": MATROSKA,
         "side_data_list": [_record(5)]},
        {"index": 1, "codec_type": "video", "codec_tag_string": "dvh1"},
        {"index": 2, "codec_type": "video", "codec_tag_string": "dvhe",
         "side_data_list": [{"side_data_type": "Display Matrix"}]},
        {"index": 3, "codec_type": "video", "codec_tag_string": "hvc1"},
        {"index": 4, "codec_type": "audio", "side_data_list": [_record(8)]},
    ]}

    assert dolby_vision(probe) == {0: DolbyVision(5, MATROSKA),
                                   1: DolbyVision(None, "dvh1"),
                                   2: DolbyVision(None, "dvhe")}
    assert dolby_vision(None) == {}
    assert dolby_vision({}) == {}


# ── The job's command ────────────────────────────────────────────────────────

def _track(index, kind="video"):
    return {"stream_index": index, "track_type": kind, "codec": "hevc", "language": "und"}


def _job(dv, target="mp4", tracks=None):
    decision = ProcessingDecision(should_process=True, reason="", target_container=target)
    return build_ffmpeg_command("in", "out.tmp", decision, tracks or [_track(0)],
                                dolby_vision=dv)


def _tags(cmd):
    return [(cmd[i], cmd[i + 1]) for i, a in enumerate(cmd[:-1]) if a.startswith("-tag")]


def _strict(cmd):
    """Whether -strict unofficial is given, before the output's format."""
    found = [i for i, a in enumerate(cmd[:-1]) if a == "-strict" and cmd[i + 1] == "unofficial"]
    assert all(i < cmd.index("-f") for i in found)
    return bool(found)


@pytest.mark.parametrize("source_tag", [MATROSKA, "dvhe"], ids=["from Matroska", "from dvhe"])
def test_profile_5_into_an_mp4_asks_for_its_record_and_dvh1(source_tag):
    """A Matroska has no tag to keep, and dvhe is one the MP4 muxer would
    replace with hev1."""
    cmd = _job({0: DolbyVision(5, source_tag)})

    assert _strict(cmd)
    assert _tags(cmd) == [("-tag:v:0", "dvh1")]


def test_another_profile_into_an_mp4_asks_for_its_record_only():
    cmd = _job({0: DolbyVision(8, MATROSKA)})

    assert _strict(cmd)
    assert _tags(cmd) == []


@pytest.mark.parametrize("source_tag", ["hvc1", "hev1", "dvh1"])
def test_a_tag_the_mp4_muxer_keeps_is_kept(source_tag):
    """An MP4's own tag, which a revert then gives back."""
    cmd = _job({0: DolbyVision(5, source_tag)})

    assert _strict(cmd)
    assert _tags(cmd) == []


@pytest.mark.parametrize("dv", [None, {}], ids=["none handed over", "none found"])
def test_a_file_without_dolby_vision_gets_neither(dv):
    """-strict unofficial also lets the MP4 muxer take what it otherwise
    refuses, so no other file is written under it."""
    cmd = _job(dv)

    assert not _strict(cmd)
    assert _tags(cmd) == []


def test_a_sample_entry_without_a_record_gets_neither():
    """There is no record to write, and dvh1 is a tag the muxer keeps."""
    cmd = _job({0: DolbyVision(None, "dvh1")})

    assert not _strict(cmd)
    assert _tags(cmd) == []


def test_a_matroska_output_gets_neither():
    """Matroska writes the record unasked, and has no sample entry."""
    cmd = _job({0: DolbyVision(5, MATROSKA)}, target="mkv")

    assert not _strict(cmd)
    assert _tags(cmd) == []


def test_the_tag_names_the_dolby_vision_streams_place_among_the_videos():
    """-tag:v:N counts the output's video streams, which the job maps first,
    in order; the stream's index in the source is another number."""
    cmd = _job({3: DolbyVision(5, MATROSKA)}, tracks=[_track(0), _track(3)])

    assert _tags(cmd) == [("-tag:v:1", "dvh1")]


# ── The revert's command ─────────────────────────────────────────────────────

def _entry(index, kind, codec, *, processed=None, sidecar=None):
    out = {"index": index, "type": kind, "codec": codec, "language": None, "title": None,
           "disposition": [], "channels": None, "sample_rate": None, "width": None,
           "height": None, "filename": None, "mimetype": None}
    if processed is not None:
        out["processed_index"] = processed
    if sidecar is not None:
        out["sidecar_index"] = sidecar
    return out


def _restore(dv, container="mkv", format_tags=None):
    """The original had German audio first, which the job removed and the
    sidecar holds, then the video, now the processed file's stream 0: so
    the video goes back as output stream 1."""
    manifest = {"version": 1, "path": "/m/Show.mkv", "container": container,
                "streams": [_entry(0, "audio", "aac", sidecar=0),
                            _entry(1, "video", "hevc", processed=0),
                            _entry(2, "audio", "aac", processed=1)],
                "chapters": 0, "format_tags": format_tags or {}}
    return build_restore_command("/m/Show.mp4", "/recycle/1.remuxarr_revert",
                                 "/tmp/out.remuxarr_tmp", manifest, dolby_vision=dv)


@pytest.mark.parametrize("profile", [5, None], ids=["with its record", "record lost"])
def test_dvh1_going_back_into_matroska_is_written_as_hvc1(profile):
    """FFmpeg refuses dvh1 in a Matroska. Without its record too, which a
    copy that did not ask for it leaves. By the stream's place in the
    output, which here is not its index in the processed file."""
    cmd = _restore({0: DolbyVision(profile, "dvh1")})

    assert _tags(cmd) == [("-tag:1", "hvc1")]
    assert not _strict(cmd)


def test_hev1_going_back_into_matroska_needs_nothing():
    cmd = _restore({0: DolbyVision(8, "hev1")})

    assert _tags(cmd) == []
    assert not _strict(cmd)


@pytest.mark.parametrize("profile, strict", [(5, True), (None, False)],
                         ids=["with its record", "record lost"])
def test_going_back_into_an_mp4_asks_for_the_record(profile, strict):
    """And keeps the tag the processed file has, which is the original's."""
    cmd = _restore({0: DolbyVision(profile, "dvh1")}, container="mp4")

    assert _strict(cmd) is strict
    assert _tags(cmd) == []


def test_going_back_into_a_quicktime_file_is_left_alone():
    """That muxer never writes the record, and -strict changes how it picks
    codec tags."""
    cmd = _restore({0: DolbyVision(5, "dvh1")}, container="mp4",
                   format_tags={"major_brand": "qt  "})

    assert cmd[cmd.index("-f") + 1] == "mov"
    assert not _strict(cmd)
    assert _tags(cmd) == []


def test_a_revert_without_dolby_vision_gets_neither():
    for dv in (None, {}):
        cmd = _restore(dv)
        assert not _strict(cmd)
        assert _tags(cmd) == []


# ── Real files ───────────────────────────────────────────────────────────────

_COLOURS = {5: "color_primaries=unknown:color_trc=unknown:colorspace=unknown",
            8: "color_primaries=bt2020:color_trc=smpte2084:colorspace=bt2020nc"}


def _dv_file(path, *, profile=5, muxer="matroska", tag=None, cover=False, subtitle=False,
             surround=False, audio_first=False):
    """
    A Dolby Vision file with English and German audio, so a job has a track
    to drop and a revert point is made. Profile 5 has no colour description,
    which dovi_rpu's guess makes Profile 5; 8 is BT.2020 with PQ, which it
    makes 8.1. `tag` is an MP4's sample entry, dvh1 or dvhe, or FFmpeg's
    hev1 if None. `cover` gives a Matroska cover art, `subtitle` an English
    text subtitle. `surround` makes the English AAC 5.1, for AC3 Forge, and
    `audio_first` puts the video after the audio.
    """
    cmd = ["ffmpeg", "-v", "error", "-y",
           "-f", "lavfi", "-i", "testsrc=size=128x72:rate=10:duration=1",
           "-f", "lavfi", "-i", "sine=frequency=440:duration=1",
           "-f", "lavfi", "-i", "sine=frequency=550:duration=1"]
    if subtitle:
        srt = path.parent / "in.srt"
        srt.write_text("1\n00:00:00,100 --> 00:00:00,900\nHello\n", encoding="utf-8")
        cmd += ["-i", str(srt)]
    cmd += (["-map", "1:a", "-map", "2:a", "-map", "0:v"] if audio_first
            else ["-map", "0:v", "-map", "1:a", "-map", "2:a"])
    cmd += ["-c:v", "libx265", "-preset", "ultrafast", "-pix_fmt", "yuv420p10le",
            "-x265-params", "log-level=error", "-vf", f"setparams={_COLOURS[profile]}",
            "-bsf:v", "dovi_rpu=compression=none",
            "-c:a", "aac", "-metadata:s:a:0", "language=eng", "-metadata:s:a:1", "language=ger"]
    if surround:
        cmd += ["-ac:a:0", "6"]
    if subtitle:
        cmd += ["-map", "3:s", "-c:s", "mov_text" if muxer == "mp4" else "subrip",
                "-metadata:s:s:0", "language=eng"]
    if cover:
        jpg = path.parent / "cover.jpg"
        subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i",
                        "color=c=red:s=64x64:d=1", "-frames:v", "1", str(jpg)], check=True)
        cmd += ["-attach", str(jpg), "-metadata:s:t:0", "mimetype=image/jpeg",
                "-metadata:s:t:0", "filename=cover.jpg"]
    if muxer == "mp4":
        cmd += ["-strict", "unofficial"] + (["-tag:v", "dvh1"] if tag else [])
    cmd += ["-f", muxer, str(path)]
    subprocess.run(cmd, check=True)
    if tag == "dvhe":
        data = path.read_bytes()
        assert data.count(b"dvh1") == 1, "the sample entry is not the only dvh1"
        path.write_bytes(data.replace(b"dvh1", b"dvhe"))
    return path


def _video(path):
    """The first video stream's codec tag and Dolby Vision record."""
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-print_format", "json", "-show_streams",
         "-select_streams", "v:0", str(path)],
        capture_output=True, text=True, check=True).stdout
    stream = json.loads(out)["streams"][0]
    record = next((d for d in stream.get("side_data_list") or []
                   if d.get("side_data_type") == DOVI_RECORD), None)
    return stream.get("codec_tag_string"), record


def _packets(path):
    """A checksum of each of the first video stream's packets, in order."""
    return subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries",
         "packet=data_hash", "-show_data_hash", "SHA256", "-of", "csv=p=0", str(path)],
        capture_output=True, text=True, check=True).stdout.split()


def _covers(path):
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-print_format", "json", "-show_streams", str(path)],
        capture_output=True, text=True, check=True).stdout
    return [s["codec_name"] for s in json.loads(out)["streams"]
            if (s.get("disposition") or {}).get("attached_pic")]


def _revert(rig):
    from app.core.revert_restore import restore_revert_point
    from app.database.models import RevertPoint

    with rig.Session() as db:
        point_id = db.query(RevertPoint).one().id
    outcome = asyncio.run(restore_revert_point(point_id))
    assert outcome.success, outcome.error


@needs_ffmpeg
def test_a_profile_5_matroska_converted_to_mp4_is_dvh1_with_its_record(rig):
    rig.set("prefer_mp4_container", True)
    source = _dv_file(rig.library / "Film.mkv")
    tag, record = _video(source)
    assert record is not None and record["dv_profile"] == 5, "fixture has no Profile 5 record"
    packets = _packets(source)

    planned, after = _run_job(rig, source)

    assert "change_container" in planned
    assert after.endswith(".mp4")
    assert _video(after) == ("dvh1", record)
    assert _packets(after) == packets


@needs_ffmpeg
def test_a_profile_8_matroska_converted_to_mp4_keeps_its_record(rig):
    """With FFmpeg's own sample entry, which nothing here has had a player
    judge yet."""
    rig.set("prefer_mp4_container", True)
    source = _dv_file(rig.library / "Film.mkv", profile=8)
    _, record = _video(source)
    assert record is not None and record["dv_profile"] == 8

    _, after = _run_job(rig, source)

    assert after.endswith(".mp4")
    assert _video(after) == ("hev1", record)


@needs_ffmpeg
def test_a_profile_5_matroska_with_cover_art_is_converted(rig):
    """The cover goes into the MP4 as a video stream of its own, which the
    dvh1 must not reach: the mux fails if it does."""
    rig.set("prefer_mp4_container", True)
    source = _dv_file(rig.library / "Film.mkv", cover=True)
    _, record = _video(source)

    _, after = _run_job(rig, source)

    assert after.endswith(".mp4"), "the conversion failed"
    assert _video(after) == ("dvh1", record)
    assert _covers(after) == ["mjpeg"]


@needs_ffmpeg
@pytest.mark.parametrize("tag", ["dvh1", "dvhe"])
def test_a_dolby_vision_mp4_keeps_its_record_as_dvh1(rig, tag):
    """A job that only drops the German track. dvh1 is kept as it was;
    dvhe, which FFmpeg turned into hev1, is written as dvh1."""
    source = _dv_file(rig.library / "Film.mp4", muxer="mp4", tag=tag)
    found, record = _video(source)
    assert (found, record is not None) == (tag, True), "fixture is not what it says"

    _run_job(rig, source)

    assert _audio_languages(source) == ["eng"], "the job did not run"
    assert _video(source) == ("dvh1", record)


@needs_ffmpeg
def test_a_conversion_that_also_extracts_a_subtitle_keeps_the_record(rig):
    """That job runs execute_ffmpeg_combined, which builds its own call."""
    rig.set("prefer_mp4_container", True)
    rig.set("extract_text_subtitles_to_srt", True)
    source = _dv_file(rig.library / "Show.mkv", subtitle=True)
    _, record = _video(source)

    planned, after = _run_job(rig, source)

    assert "extract_subtitle" in planned
    assert list(rig.library.glob("*.srt")), "no subtitle was extracted"
    assert _video(after) == ("dvh1", record)


@needs_ffmpeg
@pytest.mark.parametrize("cover", [False, True], ids=["plain", "with cover art"])
def test_a_revert_gives_back_the_converted_matroska(rig, cover):
    """The dvh1 goes back as Matroska takes it, the record and every packet
    as they were. A cover comes back as a video stream too (the README's
    limitation), which an hvc1 must not reach either."""
    rig.set("prefer_mp4_container", True)
    source = _dv_file(rig.library / "Film.mkv", cover=cover)
    before = _video(source)
    packets = _packets(source)
    _, after = _run_job(rig, source)
    assert after.endswith(".mp4")

    _revert(rig)

    assert source.exists()
    assert _audio_languages(source) == ["eng", "ger"]
    assert _video(source) == before
    assert _packets(source) == packets


@needs_ffmpeg
@pytest.mark.parametrize("tag", ["dvh1", "dvhe"])
def test_a_revert_gives_back_the_dolby_vision_mp4(rig, tag):
    """With its record. A dvhe original comes back dvh1, as FFmpeg cannot
    write dvhe: the README's limitation, pinned here."""
    source = _dv_file(rig.library / "Film.mp4", muxer="mp4", tag=tag)
    _, record = _video(source)
    _run_job(rig, source)

    _revert(rig)

    assert _audio_languages(source) == ["eng", "ger"]
    assert _video(source) == ("dvh1", record)


# ── AC3 Forge's commands ─────────────────────────────────────────────────────

def _add(dv, container="mp4"):
    return build_add_ac3_command("in", "out.tmp", 1, 1, container=container, dolby_vision=dv)


def _undo(dv, container="mp4", quicktime=False):
    return build_undo_command("in", "out.tmp", 1, container=container, quicktime=quicktime,
                              dolby_vision=dv)


@pytest.mark.parametrize("build", [_add, _undo], ids=["add", "undo"])
def test_a_forge_run_on_an_mp4_asks_for_the_record(build):
    """And leaves a tag the MP4 muxer keeps alone, so an undo gives it back."""
    cmd = build([DolbyVision(5, "dvh1")])

    assert _strict(cmd)
    assert _tags(cmd) == []


@pytest.mark.parametrize("build", [_add, _undo], ids=["add", "undo"])
def test_a_forge_run_writes_dvhe_as_dvh1_in_its_place(build):
    """Counted among the file's video streams, as "-map 0" keeps them all
    in an MP4."""
    cmd = build([None, DolbyVision(5, "dvhe")])

    assert _strict(cmd)
    assert _tags(cmd) == [("-tag:v:1", "dvh1")]


@pytest.mark.parametrize("build", [_add, _undo], ids=["add", "undo"])
@pytest.mark.parametrize("dv", [(), [None], [DolbyVision(None, "dvh1")]],
                         ids=["no video given", "no Dolby Vision", "a bare dvh1"])
def test_a_forge_run_without_a_record_gets_neither(build, dv):
    cmd = build(dv)

    assert not _strict(cmd)
    assert _tags(cmd) == []


@pytest.mark.parametrize("build", [_add, _undo], ids=["add", "undo"])
def test_a_forge_run_on_a_matroska_gets_neither(build):
    cmd = build([DolbyVision(5, MATROSKA)], container="mkv")

    assert not _strict(cmd)
    assert _tags(cmd) == []


def test_the_undo_of_a_quicktime_file_gets_neither():
    """Written back with the QuickTime muxer, which never writes the record
    and picks some codec tags differently under -strict."""
    cmd = _undo([DolbyVision(5, "dvh1")], quicktime=True)

    assert cmd[cmd.index("-f") + 1] == "mov"
    assert not _strict(cmd)
    assert _tags(cmd) == []


# ── AC3 Forge on real files ──────────────────────────────────────────────────

def _forge_add(rig):
    """Queue the library's one file for a Forge add and run it. Returns the
    Forge job's id."""
    from app.core import scanner
    from app.database.models import Ac3ForgeJob, MediaFile

    with rig.Session() as db:
        scanner.scan_library(db, [str(rig.library)], force_probe=True)
        job_id = rig.forge.queue_forge_job(db, db.query(MediaFile).one().id).id
    assert asyncio.run(rig.worker._process_next_forge(_qt._WS())) is True
    with rig.Session() as db:
        job = db.get(Ac3ForgeJob, job_id)
        assert job.status == "success", job.error_message
    return job_id


def _forge_undo(rig, job_id):
    from app.database.models import Ac3ForgeJob

    with rig.Session() as db:
        job = db.get(Ac3ForgeJob, job_id)
        job.status, job.is_undo, job.progress = "undo_pending", True, 0.0
        db.commit()
    assert asyncio.run(rig.worker._process_next_forge(_qt._WS())) is True
    with rig.Session() as db:
        job = db.get(Ac3ForgeJob, job_id)
        assert job.status == "undone", job.error_message


def _audio(path):
    """The file's audio codecs, in order."""
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-print_format", "json", "-select_streams", "a",
         "-show_entries", "stream=codec_name", str(path)],
        capture_output=True, text=True, check=True).stdout
    return [s["codec_name"] for s in json.loads(out)["streams"]]


@needs_ffmpeg
@pytest.mark.parametrize("name, profile, muxer, tag, kept_as", [
    ("Film.mp4", 5, "mp4", "dvh1", "dvh1"),
    ("Film.mp4", 8, "mp4", None, "hev1"),
    ("Film.mkv", 5, "matroska", None, MATROSKA),
], ids=["Profile 5 MP4", "Profile 8 MP4", "Profile 5 Matroska"])
def test_an_add_and_its_undo_keep_dolby_vision(forge_rig, name, profile, muxer, tag, kept_as):
    """The record, the tag and every packet, through both. A Matroska kept
    its record before; this pins it."""
    source = _dv_file(forge_rig.library / name, profile=profile, muxer=muxer, tag=tag,
                      surround=True)
    before = _video(source)
    assert before[0] == kept_as and before[1] is not None, "fixture is not what it says"
    packets = _packets(source)

    job_id = _forge_add(forge_rig)
    assert _audio(source) == ["aac", "aac", "ac3"], "the add did not run"
    assert _video(source) == before
    assert _packets(source) == packets

    _forge_undo(forge_rig, job_id)
    assert _audio(source) == ["aac", "aac"]
    assert _video(source) == before
    assert _packets(source) == packets


@needs_ffmpeg
@pytest.mark.parametrize("audio_first", [False, True], ids=["video first", "audio first"])
def test_a_forge_run_on_a_dvhe_mp4_writes_dvh1(forge_rig, audio_first):
    """FFmpeg would write hev1 in its place. With the audio first, the
    video's place among the video streams is not its place in the file."""
    source = _dv_file(forge_rig.library / "Film.mp4", muxer="mp4", tag="dvhe",
                      surround=True, audio_first=audio_first)
    found, record = _video(source)
    assert found == "dvhe" and record is not None

    job_id = _forge_add(forge_rig)
    assert _video(source) == ("dvh1", record)

    _forge_undo(forge_rig, job_id)
    assert _video(source) == ("dvh1", record)


@needs_ffmpeg
def test_a_revert_after_a_job_and_a_forge_run_gives_back_dolby_vision(forge_rig):
    """The job converts the Matroska and drops the German track, Forge adds
    an AC3 to the MP4, and the revert gives back the Matroska as it was:
    the explorer's path (tools/testlib, check 27)."""
    from app.database.models import AppSetting

    with forge_rig.Session() as db:
        db.merge(AppSetting(key="prefer_mp4_container", value=json.dumps(True)))
        db.commit()
    source = _dv_file(forge_rig.library / "Film.mkv", surround=True)
    before = _video(source)
    packets = _packets(source)
    _, after = _run_job(forge_rig, source)
    assert after.endswith(".mp4")

    _forge_add(forge_rig)
    assert _video(after) == ("dvh1", before[1])

    _revert(forge_rig)
    assert source.exists()
    assert _audio(source) == ["aac", "aac"]
    assert _video(source) == before
    assert _packets(source) == packets
