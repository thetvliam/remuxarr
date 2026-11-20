"""
A phone's or camera's own recording is left as it is.

Remuxarr is for downloaded TV and movie media (the user's call, PARKED 45),
and a job costs a recording what a download does not have. Measured on real
recordings run through the real scan and worker: a fast-start rewrite of an
Android phone's video lost its device tag and had its brand rewritten, and
any job on an iPhone's video dropped its metadata tracks, which no revert
point could hold. One kind of file was already left alone, one holding a
data track FFmpeg cannot write back; of 18 real recordings that covered
10, and the other 8 were all rewritten under one settings profile or
another.

An MP4 or QuickTime file with any sign of a recording is now left alone
(probe.recording_signs): a data track beyond its chapters, timecode and
"gpmd", a GPS tag, a device tag, or a camera's brand, a QuickTime movie's
among them. Nothing in the settings changes that (decision.analyze_file),
and AC3 Forge refuses the file too, though it still undoes an add it
made before this rule, which costs the file nothing more. The scan
records the signs on the file's row (media_files.recording_signs), as it
records its fonts, so a full scan, which decides an unchanged file from
its row, leaves it alone too; and the worker reads the signs again from
the file at every pickup, before writing anything, which covers a row
from before the column and a file replaced since the scan. The reason
goes in History, and no Language Review question is raised.

tests/real_recordings/probes.json holds what ffprobe says of 23 real files,
tag values removed: the 18 recordings (iPhones, Android phones, GoPro and
Sony cameras, a thermal camera app, a 3GPP video), two editor exports and
three of Apple's demonstration files. The signs expected of each here were
written by hand from that output. All 18 recordings have one. Of the other
five, the two QuickTime movies have the brand and three have none: no
metadata tells an editor's MP4 export from a download, and it is processed
like one.

A "gpmd" track is not a sign, though it is GoPro's telemetry: released
versions of AC3 Forge and of revert left one behind in any MP4 with
chapters, and counting it would leave those downloads alone for good. The
five real GoPro files are all known by their "fdsc" track and "firmware"
tag.

These replace test_unwritable_data.py, whose file the first rule was. The
real-file tests run the real scan, worker, queue and Forge.

Verified by mutation, 35 applied, 35 killed. Each was first run against
the whole suite as it stood without this file (2578 tests): 32 survived
it, and counting a "gpmd" track, counting a timecode track, and Forge not
offering a row not looked at yet were killed there, by the GoPro-layout
job in test_data_streams.py and by test_forge_candidates.py:

  • a sign ignored, one mutant for each of
    the four                                → the real recordings
  • the chapter track counted               → the download, the tracks that
                                              are no sign
  • a timecode track counted, a "gpmd"
    track counted                           → the tracks that are no sign,
                                              the file with only those two
  • looking beyond MP4 and QuickTime        → the other containers
  • a brand dropped, one mutant for each
    of the four, and the 3GPP prefix        → that brand, and the real file
                                              that has it
  • "location" dropped, Apple's location
    key dropped, the per-language form
    dropped                                 → that tag, and for the first
                                              two the real recordings
  • "make", "model", "firmware", the Apple
    prefix, the Android prefix, one each    → that tag; the real GoPro,
                                              iPhone and Android files for
                                              the last three
  • the file's format info not reporting
    the signs                               → the real recordings
  • the file never left alone               → the decision, no setting
  • Prefer MP4 switching it off, fast
    start switching it off, one each        → the decision, no setting
  • its Language Review questions raised    → a question alone
  • a rescan not storing the signs          → the full rescan
  • a first scan not storing them           → the scan, every setting on
  • the decision's input missing them       → the scan, every setting on,
                                              the full rescan, the pickup
  • the worker not looking at pickup        → the pickup
  • the migration missing, the migration
    defaulting to "looked at"               → the migration
  • Forge not refusing the file             → Forge
  • Forge offering recordings, Forge not
    offering a row not looked at yet        → what Forge offers

Letting a Forge undo through came after, with 3 more applied and 3 killed.
Against the suite as it stood (2681 tests) the undo still being refused
survived; an add let through with it, and the check dropped, were killed
there by the Forge refusal test:

  • the undo still refused                  → the undo
  • the add let through too                 → the refusal, the undo
  • the check dropped                       → the refusal

Reading the signs at every pickup came after that, with 2 applied and 2
killed. Reading them only for a row never looked at is the code as it
stood, which the suite as it stood (2708 tests) passed; not reading them
at all was killed there by the pickup of a row from before the column:

  • not read at pickup                      → the pickup, the recording put
                                              in a queued file's place
  • read only for a row never looked at     → the recording put in a queued
                                              file's place
"""
import asyncio
import hashlib
import json
import sqlite3
import subprocess
from pathlib import Path

import pytest
from sqlalchemy import create_engine

from app.core.decision import analyze_file
from app.core.probe import extract_format_info, probe_file, recording_signs
from tests import test_quicktime_files as _qt
from tests.conftest import make_file_info, make_track
from tests.test_data_streams import _as_iphone_metadata, _camera
from tests.test_quicktime_files import _WS, needs_ffmpeg

# That file's fixture: a file-backed database every session points at,
# scratch folders and an empty library, English audio kept and revert
# points on. Bound by assignment, as test_forge_undo_identity.py does:
# pyflakes reads an imported fixture as unused.
rig = _qt.rig

DATA, GPS, DEVICE, BRAND = "data tracks", "GPS location", "device tags", "camera brand"
REASON = "This looks like a phone's or camera's own recording"


# ── Real files' probes ───────────────────────────────────────────────────────

REAL = {e["name"]: e["probe"] for e in json.loads(
    (Path(__file__).parent / "real_recordings" / "probes.json").read_text())["files"]}

RECORDINGS = {
    "Issue 234 dotnet.mov":           [DATA, GPS, DEVICE, BRAND],   # iPhone
    "Issue 264 dotnet.mov":           [DATA, GPS, DEVICE, BRAND],   # iPhone
    "with-gps.mov":                   [DATA, GPS, DEVICE, BRAND],   # iPhone
    "apple-livephoto-quicktime.mov":  [DATA, GPS, DEVICE],          # iPhone, no brand shown
    "with-gps.mp4":                   [GPS, DEVICE],                # Android
    "Nokia 6.1.mp4":                  [GPS, DEVICE, BRAND],         # Android, "nvr1"
    "Issue 118 (dotnet).mp4":         [GPS],                        # Android
    "Issue 269 (dotnet).mp4":         [GPS],                        # Android
    "Issue 515 (java).mp4":           [GPS],                        # an iOS app
    "FLIR Thermal.mp4":               [GPS],                        # a thermal camera app
    "ftyp3gp5.mp4":                   [GPS, BRAND],                 # "3gp5"
    "Sony ILCE-7M3 (A7M3).mp4":       [DATA, BRAND],                # "rtmd", "XAVC"
    "Issue 306.mp4":                  [BRAND],                      # "MSNV" and nothing else
    "hero5.mp4":                      [DATA, GPS, DEVICE],          # GoPro
    "max-heromode.mp4":               [DATA, GPS, DEVICE],          # GoPro
    "Fusion.mp4":                     [DATA, GPS, DEVICE],          # GoPro
    "hero8.mp4":                      [DATA, DEVICE],               # GoPro
    "karma.mp4":                      [DATA, DEVICE],               # GoPro
}
OTHERS = {
    "xmp480qt.mov":      [BRAND],   # an editor's QuickTime export
    "sample_iTunes.mov": [BRAND],   # Apple's demonstration movie
    "uuid540.mp4":       [],        # an editor's MP4 export
    "sample_mpeg4.mp4":  [],        # Apple's demonstration files
    "sample_iPod.m4v":   [],
}


def test_every_real_file_is_accounted_for():
    assert sorted(REAL) == sorted({**RECORDINGS, **OTHERS})
    assert len(RECORDINGS) == 18


@pytest.mark.parametrize("name", sorted(RECORDINGS))
def test_a_real_recording_is_known_by_its_signs(name):
    assert RECORDINGS[name], "a recording with no sign expected of it"
    assert recording_signs(REAL[name]) == RECORDINGS[name]
    assert extract_format_info(REAL[name])["recording_signs"] == ",".join(RECORDINGS[name])


@pytest.mark.parametrize("name", sorted(OTHERS))
def test_a_real_file_that_is_no_recording(name):
    assert recording_signs(REAL[name]) == OTHERS[name]


# ── Each sign ────────────────────────────────────────────────────────────────

_MOV = "mov,mp4,m4a,3gp,3g2,mj2"
_VIDEO = {"codec_type": "video", "codec_name": "hevc", "codec_tag_string": "hvc1"}
_AUDIO = {"codec_type": "audio", "codec_name": "aac", "codec_tag_string": "mp4a"}


def _probe(*extra_streams, brand="isom", tags=None, format_name=_MOV, chapters=0):
    return {"format": {"format_name": format_name,
                       "tags": {"major_brand": brand, **(tags or {})}},
            "chapters": [{"id": n} for n in range(chapters)],
            "streams": [dict(s, index=i)
                        for i, s in enumerate((_VIDEO, _AUDIO, *extra_streams))]}


def _data(tag, codec="none"):
    return {"codec_type": "data", "codec_name": codec, "codec_tag_string": tag}


def test_a_download_has_no_sign():
    """An MP4 as a download comes: tagged the way iTunes-style taggers and
    encoders tag one, with chapters and their track, and a timecode."""
    tags = {"title": "A Film", "encoder": "HandBrake 1.6", "show": "Show",
            "hd_video": "1", "media_type": "10", "iTunEXTC": "mpaa|PG-13|300|",
            "iTunMOVI": "<plist/>", "creation_time": "2020-05-06T07:08:09.000000Z",
            "compatible_brands": "isomiso2avc1mp41"}
    probe = _probe(_data("text", "bin_data"), _data("tmcd"), tags=tags, chapters=3)

    assert recording_signs(probe) == []
    assert extract_format_info(probe)["recording_signs"] == ""


@pytest.mark.parametrize("tag, codec", [("mebx", "none"), ("fdsc", "none"), ("rtmd", "none"),
                                        ("camm", "none"), ("rtp ", "none")])
def test_a_data_track_is_a_sign(tag, codec):
    assert recording_signs(_probe(_data(tag, codec))) == [DATA]


def test_a_text_track_in_a_file_with_no_chapters_is_a_data_track():
    """Only a file with chapters has a chapter track (chapter_track_indices)."""
    assert recording_signs(_probe(_data("text", "bin_data"), chapters=0)) == [DATA]


@pytest.mark.parametrize("stream, chapters", [
    (_data("text", "bin_data"), 2),      # the chapter track
    (_data("tmcd"), 0),                  # a timecode track
    (_data("gpmd", "bin_data"), 0),      # what AC3 Forge and revert once left behind
], ids=["chapters", "timecode", "gpmd"])
def test_the_data_tracks_that_are_no_sign(stream, chapters):
    assert recording_signs(_probe(stream, chapters=chapters)) == []


@pytest.mark.parametrize("key", ["location", "location-eng", "location-fra",
                                 "com.apple.quicktime.location.ISO6709"])
def test_a_gps_tag_is_a_sign(key):
    signs = recording_signs(_probe(tags={key: "+51.5-000.1/"}))

    assert GPS in signs
    assert signs == ([GPS, DEVICE] if key.startswith("com.apple.") else [GPS])


@pytest.mark.parametrize("key", ["make", "model", "firmware", "com.android.version",
                                 "com.android.capture.fps", "com.apple.quicktime.make",
                                 "com.apple.photos.originating.signature"])
def test_a_device_tag_is_a_sign(key):
    assert recording_signs(_probe(tags={key: "x"})) == [DEVICE]


@pytest.mark.parametrize("key", ["locations", "maker", "models", "apple", "com.example.make",
                                 "title", "encoder", "iTunEXTC", "handler_name"])
def test_a_tag_that_only_resembles_one_is_no_sign(key):
    assert recording_signs(_probe(tags={key: "x"})) == []


@pytest.mark.parametrize("brand", ["qt  ", "XAVC", "MSNV", "nvr1", "3gp4", "3gp5", "3g2a"])
def test_a_cameras_brand_is_a_sign(brand):
    assert recording_signs(_probe(brand=brand)) == [BRAND]


@pytest.mark.parametrize("brand", ["isom", "mp42", "mp41", "M4V ", "avc1", "dash", "iso5", ""])
def test_a_brand_any_file_has_is_no_sign(brand):
    """"mp41" is a GoPro's brand and many other files' too."""
    assert recording_signs(_probe(brand=brand)) == []


def test_a_file_with_no_tags_at_all():
    probe = {"format": {"format_name": _MOV}, "streams": [dict(_VIDEO, index=0)]}

    assert recording_signs(probe) == []
    assert recording_signs(None) == []
    assert recording_signs({}) == []


@pytest.mark.parametrize("format_name", ["matroska,webm", "mpegts", "avi", "asf"])
def test_only_an_mp4_or_quicktime_file_is_looked_at(format_name):
    """Every sign at once, in a container no phone or camera of the
    samples writes: a TS recording's data streams are dropped as they
    always were, and a Matroska's tags are its own."""
    probe = _probe(_data("mebx"), brand="qt  ", format_name=format_name,
                   tags={"location": "+51.5-000.1/", "make": "x"})

    assert recording_signs(probe) == []
    assert recording_signs(dict(probe, format=dict(probe["format"], format_name=_MOV))) \
        == [DATA, GPS, DEVICE, BRAND]


# ── The decision ─────────────────────────────────────────────────────────────

def _decide(settings, signs, *, french=True, container="mp4"):
    """A file with an untagged track to ask about, and a French one to drop
    unless french is False."""
    tracks = [make_track(stream_index=0, track_type="video", codec="hevc"),
              make_track(stream_index=1, track_type="audio", codec="aac",
                         language="eng", is_default=True),
              make_track(stream_index=2, track_type="audio", codec="aac", language=None)]
    if french:
        tracks.append(make_track(stream_index=3, track_type="audio", codec="aac",
                                 language="fre"))
    file_info = make_file_info(path="/m/IMG_8810.mov", container=container, video_codec="hevc")
    file_info["recording_signs"] = signs
    return analyze_file(file_info, tracks, settings)


def test_the_file_is_left_as_it_is_with_no_questions(settings):
    settings["fix_undefined_language_audio"] = "always_ask"
    work = _decide(settings, "")
    assert work.should_process, "the fixture no longer produces work"
    assert work.undefined_audio_flags, "the fixture no longer raises a question"

    left = _decide(settings, "data tracks,GPS location")

    assert left.should_process is False and left.actions == []
    assert left.reason == (f"{REASON} (data tracks, GPS location), "
                           "so it is left as it is.")
    assert left.undefined_audio_flags == [] and left.audio_language_mismatch is None


def test_a_question_alone_is_not_raised_either(settings):
    """
    With nothing else to do, the question would still have gone to Language
    Review, and no answer to it could ever be used: the file is not written.
    """
    settings["fix_undefined_language_audio"] = "always_ask"
    assert _decide(settings, "", french=False).undefined_audio_flags, \
        "the fixture no longer raises a question"

    assert _decide(settings, "camera brand", french=False).undefined_audio_flags == []


@pytest.mark.parametrize("change", [
    {"prefer_mp4_container": True},
    {"prefer_mp4_container": False},
    {"add_faststart_to_mp4": True},
    {"add_faststart_to_mp4": False},
    {"extract_text_subtitles_to_srt": True},
    {"keep_audio_languages": []},
    {"keep_audio_languages": ["fre"], "keep_default_audio": False},
    {"fix_undefined_language_audio": "always_fix"},
    {"prefer_mp4_container": True, "add_faststart_to_mp4": True,
     "extract_text_subtitles_to_srt": True, "fix_undefined_language_audio": "always_fix"},
], ids=lambda change: "+".join(f"{k}={v}" for k, v in change.items())[:60])
def test_no_setting_turns_a_recording_into_a_job(settings, change):
    settings.update(change)

    left = _decide(settings, "GPS location")

    assert left.should_process is False and left.actions == []
    assert left.reason.startswith(REASON)


# ── Real files ───────────────────────────────────────────────────────────────

def _iphone_video(rig):
    """The iPhone-style file, English and German audio: a job would drop the
    German track, and the metadata track with it."""
    return _as_iphone_metadata(_camera(rig.library / "IMG_8810.mov", brand="qt  ",
                                       telemetry=False))


def _android_video(rig):
    """
    An Android phone's video as far as its signs go: an MP4 like any
    download but for the GPS position in its tags. English and German
    audio, so a job would drop the German track, and its index at the end,
    so a fast-start rewrite alone would be a job too.
    """
    path = rig.library / "VID_20170222.mp4"
    subprocess.run(["ffmpeg", "-v", "error", "-y",
                    "-f", "lavfi", "-i", "testsrc=size=64x36:rate=10:duration=1",
                    "-f", "lavfi", "-i", "sine=frequency=440:duration=1",
                    "-f", "lavfi", "-i", "sine=frequency=550:duration=1",
                    "-map", "0:v", "-map", "1:a", "-map", "2:a",
                    "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p",
                    "-c:a", "aac",
                    "-metadata:s:a:0", "language=eng", "-metadata:s:a:1", "language=ger",
                    "-metadata", "location=+51.5074-000.1278/",
                    "-f", "mp4", str(path)], check=True)
    return path


def _digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _row_and_items(rig):
    from app.database.models import MediaFile, QueueItem

    with rig.Session() as db:
        media = db.query(MediaFile).one()
        items = [(i.status, i.reason) for i in db.query(QueueItem).all()]
        return media.recording_signs, media.status, items


def _scan(rig, force=False):
    from app.core import scanner

    with rig.Session() as db:
        scanner.scan_library(db, [str(rig.library)], force_probe=force)


@needs_ffmpeg
@pytest.mark.parametrize("make, signs", [
    (_iphone_video, "data tracks,camera brand"),
    (_android_video, "GPS location"),
], ids=["iphone", "android"])
def test_the_scan_leaves_the_file_alone(rig, make, signs):
    movie = make(rig)
    before = _digest(movie)
    assert ",".join(recording_signs(probe_file(str(movie)))) == signs, "fixture has other signs"

    _scan(rig)

    stored, status, items = _row_and_items(rig)
    assert stored == signs
    assert status == "skipped"
    assert [s for s, _ in items] == ["skipped"]
    assert items[0][1] == f"{REASON} ({signs.replace(',', ', ')}), so it is left as it is."
    assert _digest(movie) == before


@needs_ffmpeg
def test_the_scan_leaves_it_alone_with_every_setting_asking_for_a_job(rig):
    """Prefer MP4, fast start, subtitle extraction and dropping every
    language but French, all on, and the Android video is still not
    queued."""
    from app.database.models import AppSetting

    movie = _android_video(rig)
    before = _digest(movie)
    with rig.Session() as db:
        for key, value in [("prefer_mp4_container", True), ("add_faststart_to_mp4", True),
                           ("extract_text_subtitles_to_srt", True),
                           ("keep_audio_languages", ["fre"])]:
            db.merge(AppSetting(key=key, value=json.dumps(value)))
        db.commit()

    _scan(rig)

    _, status, items = _row_and_items(rig)
    assert status == "skipped"
    assert [s for s, _ in items] == ["skipped"] and items[0][1].startswith(REASON)
    assert _digest(movie) == before


@needs_ffmpeg
def test_a_full_rescan_of_a_row_from_before_leaves_it_alone(rig):
    """
    A row from before the column has it null; a full rescan probes the file
    again and records its signs, so the file is still left alone.
    """
    from app.database.models import MediaFile, QueueItem

    movie = _android_video(rig)
    before = _digest(movie)
    _scan(rig)
    with rig.Session() as db:
        db.query(MediaFile).one().recording_signs = None
        db.query(QueueItem).delete()
        db.commit()

    _scan(rig, force=True)

    stored, _, items = _row_and_items(rig)
    assert stored == "GPS location"
    assert [s for s, _ in items] == ["skipped"]
    assert _digest(movie) == before


@needs_ffmpeg
def test_the_worker_looks_at_a_row_from_before_at_pickup(rig):
    """
    A row from before the column, queued before it existed: the worker
    probes the file at pickup anyway, records its signs, and leaves it
    alone without writing anything.
    """
    from app.database.models import MediaFile, QueueItem

    movie = _android_video(rig)
    before = _digest(movie)
    _scan(rig)
    with rig.Session() as db:
        media = db.query(MediaFile).one()
        media.recording_signs, media.status = None, "queued"
        db.query(QueueItem).delete()
        db.add(QueueItem(file_id=media.id, status="pending"))
        db.commit()

    async def go():
        job_id = rig.worker._claim_next()
        await rig.worker._run_and_broadcast(job_id, _WS(), asyncio.get_running_loop())
    asyncio.run(go())

    stored, status, items = _row_and_items(rig)
    assert stored == "GPS location"
    assert status == "skipped"
    assert [s for s, _ in items] == ["skipped"] and items[0][1].startswith(REASON)
    assert _digest(movie) == before


@needs_ffmpeg
def test_a_recording_put_in_a_queued_files_place_is_left_alone(rig):
    """
    A download is scanned and queued, and before its job starts the file
    is replaced by a phone's video with the same tracks. The worker's track
    check passes it, so the row's answer, "no recording", is all that
    stood between the job and the video. The worker reads the signs from
    the file at pickup, and leaves it alone.
    """
    video = _android_video(rig)
    download = rig.library / "copy.tmp"
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", str(video), "-map", "0",
                    "-c", "copy", "-map_metadata", "-1",
                    "-metadata:s:a:0", "language=eng", "-metadata:s:a:1", "language=ger",
                    "-f", "mp4", str(download)], check=True)
    phone_video = video.read_bytes()
    download.replace(video)
    assert recording_signs(probe_file(str(video))) == [], "fixture"
    _scan(rig)
    stored, _, items = _row_and_items(rig)
    assert stored == "" and [s for s, _ in items] == ["pending"], "fixture"

    video.write_bytes(phone_video)
    before = _digest(video)

    async def go():
        job_id = rig.worker._claim_next()
        await rig.worker._run_and_broadcast(job_id, _WS(), asyncio.get_running_loop())
    asyncio.run(go())

    stored, status, items = _row_and_items(rig)
    assert stored == "GPS location"
    assert status == "skipped"
    assert [s for s, _ in items] == ["skipped"] and items[0][1].startswith(REASON)
    assert _digest(video) == before


@needs_ffmpeg
def test_a_file_with_only_a_gpmd_and_a_timecode_track_is_still_processed(rig):
    """
    The GoPro-layout file of test_data_streams.py, which has neither a real
    GoPro's "fdsc" track nor its "firmware" tag: its data tracks are the
    two that are no sign, as in a download AC3 Forge once left a "gpmd"
    track in, and a job keeps both (test_data_streams.py).
    """
    movie = _camera(rig.library / "GX010042.MP4", brand="mp41")
    assert recording_signs(probe_file(str(movie))) == []

    _scan(rig)

    stored, _, items = _row_and_items(rig)
    assert stored == ""
    assert [s for s, _ in items] == ["pending"]


def _android_video_51(rig):
    """The Android video of _android_video with one English AAC 5.1 track,
    so a Forge candidate but for its GPS tag, and a creation date."""
    movie = rig.library / "VID_20170222.mp4"
    subprocess.run(["ffmpeg", "-v", "error", "-y",
                    "-f", "lavfi", "-i", "testsrc=size=64x36:rate=10:duration=1",
                    "-f", "lavfi", "-i", "sine=frequency=440:duration=1",
                    "-map", "0", "-map", "1", "-c:v", "libx264", "-preset", "ultrafast",
                    "-pix_fmt", "yuv420p", "-c:a", "aac", "-ac", "6",
                    "-metadata:s:a:0", "language=eng",
                    "-metadata", "location=+51.5074-000.1278/",
                    "-metadata", "creation_time=2017-02-22T08:20:28.000000Z",
                    "-f", "mp4", str(movie)], check=True)
    return movie


def _tags_and_audio(path):
    probe = probe_file(str(path))
    tags = probe["format"].get("tags") or {}
    return ({key: tags.get(key) for key in ("location", "creation_time")},
            [s["codec_name"] for s in probe["streams"] if s["codec_type"] == "audio"])


@needs_ffmpeg
def test_forge_leaves_the_file_alone_too(rig):
    """
    AC3 Forge copies every stream, and would cost a recording what a job
    does. It refuses, and the file is not changed. The audio is AAC 5.1,
    so the file is a Forge candidate; the GPS tag is its only sign.
    """
    from app.core import scanner
    from app.database.models import Ac3ForgeJob, MediaFile

    movie = _android_video_51(rig)
    before = _digest(movie)

    with rig.Session() as db:
        scanner.scan_library(db, [str(rig.library)], force_probe=True)
        job_id = rig.forge.queue_forge_job(db, db.query(MediaFile).one().id).id
    assert asyncio.run(rig.worker._process_next_forge(_WS())) is True

    with rig.Session() as db:
        job = db.get(Ac3ForgeJob, job_id)
        assert job.status == "failed"
        assert job.error_message == (f"{REASON} (GPS location), so AC3 Forge leaves it "
                                     "as it is. The file was not changed.")
    assert _digest(movie) == before


@needs_ffmpeg
def test_forge_can_undo_an_add_made_before_recordings_were_left_alone(rig, monkeypatch):
    """
    A released version forged the Android video. Its undo is let through:
    the add already cost the file what a rewrite costs, and the undo costs
    nothing more. The file comes back with its one AAC track, and with its
    GPS tag and date as they were.
    """
    import app.core.forge as forge
    import app.core.probe as probe
    from app.core import scanner
    from app.database.models import Ac3ForgeJob, MediaFile

    movie = _android_video_51(rig)
    before = _tags_and_audio(movie)
    assert before == ({"location": "+51.5074-000.1278/",
                       "creation_time": "2017-02-22T08:20:28.000000Z"}, ["aac"]), "fixture"

    with monkeypatch.context() as released:
        released.setattr(probe, "recording_signs", lambda probe_data: [])
        released.setattr(forge, "recording_signs", lambda probe_data: [])
        with rig.Session() as db:
            scanner.scan_library(db, [str(rig.library)], force_probe=True)
            job_id = rig.forge.queue_forge_job(db, db.query(MediaFile).one().id).id
        assert asyncio.run(rig.worker._process_next_forge(_WS())) is True
    with rig.Session() as db:
        assert db.get(Ac3ForgeJob, job_id).status == "success"
    assert _tags_and_audio(movie) == (before[0], ["aac", "ac3"])
    assert recording_signs(probe_file(str(movie))) == [GPS], "no longer a recording"

    with rig.Session() as db:
        job = db.get(Ac3ForgeJob, job_id)
        job.status, job.is_undo, job.progress = "undo_pending", True, 0.0
        db.commit()
    assert asyncio.run(rig.worker._process_next_forge(_WS())) is True

    with rig.Session() as db:
        job = db.get(Ac3ForgeJob, job_id)
        assert job.status == "undone", job.error_message
    assert _tags_and_audio(movie) == before


def test_forge_does_not_offer_a_recording():
    """
    A file with AAC 5.1 is a Forge candidate unless its row says it is a
    recording: the job would only refuse it. A row not looked at yet is
    still offered, and the job looks.
    """
    from sqlalchemy.orm import sessionmaker

    from app.core.forge import get_candidates
    from app.database.models import Base, MediaFile, Track
    from tests.engine_disposal import dispose_after_test

    engine = dispose_after_test(create_engine("sqlite://", connect_args={"check_same_thread": False}))
    Base.metadata.create_all(engine)
    with sessionmaker(bind=engine)() as db:
        for n, (name, signs) in enumerate([("Download.mp4", ""), ("Old row.mp4", None),
                                           ("VID_20170222.mp4", "GPS location"),
                                           ("IMG_8810.mov", "data tracks,camera brand")], 1):
            db.add(MediaFile(id=n, path=f"/m/{name}", filename=name, directory="/m",
                             size=100, mtime=1.0, recording_signs=signs))
            db.add(Track(file_id=n, stream_index=1, track_type="audio", codec="aac",
                         channels=6, channel_layout="5.1", language="eng", is_default=True))
        db.commit()

        found = get_candidates(db)

    assert found["total"] == 2
    assert [item["filename"] for item in found["items"]] == ["Download.mp4", "Old row.mp4"]


# ── The migration ────────────────────────────────────────────────────────────

def test_existing_files_come_out_not_looked_at(tmp_path, monkeypatch):
    """
    A file already in the library gains the column null, which is what
    tells the worker to look at pickup. A default of "" would record every
    such file as looked at and no recording, and a phone's video already
    there would be processed.
    """
    from app.database.models import Base
    from tests.test_schema_migration import _point_the_engine_at, _restore_the_engine

    path = tmp_path / "remuxarr.db"
    engine = create_engine(f"sqlite:///{path}")
    Base.metadata.tables["media_files"].create(engine)
    engine.dispose()
    conn = sqlite3.connect(path)
    conn.execute("ALTER TABLE media_files DROP COLUMN recording_signs")
    conn.execute("INSERT INTO media_files (path, filename, directory, size, mtime, status) "
                 "VALUES ('/m/IMG_8810.mov', 'IMG_8810.mov', '/m', 1, 1.0, 'processed')")
    conn.commit()
    conn.close()

    session_mod = _point_the_engine_at(path, monkeypatch)
    try:
        session_mod.init_db()
    finally:
        _restore_the_engine(session_mod)

    conn = sqlite3.connect(path)
    try:
        value = conn.execute("SELECT recording_signs FROM media_files").fetchone()
    finally:
        conn.close()
    assert value == (None,)
