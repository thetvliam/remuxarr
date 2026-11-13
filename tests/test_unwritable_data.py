"""
A file holding data tracks FFmpeg cannot write back is left as it is.

The user's iPhone video IMG_8810.mov holds HEVC, AAC and four metadata
tracks ("mebx"). FFmpeg's MP4 and QuickTime muxers write a data track's
sample description only for "tmcd", "gpmd" and their own RTP hint tracks
(mov_write_stsd_tag, libavformat/movenc.c n8.1.3), so a job cannot keep
such a track (ffmpeg.find_data_streams): it dropped all four, and a revert
point could not keep them either, as its Matroska sidecar holds no data
stream, so they were gone for good. With Require Revert Point on, the job
failed instead, and every full scan queued it again.

The user's call (PARKED 45): Remuxarr is for downloaded TV and movie media,
not a phone's or camera's own recordings, so such a file is left as it is,
the reason in History, and raises no Language Review questions, as a file
in a container Remuxarr cannot write is (item 11). The scan records the
file's unwritable data tracks on its row (probe.unwritable_data_entries,
media_files.unwritable_data), as it records its fonts, so a full scan, which
decides an unchanged file from its row, leaves it alone too; and the worker
looks at a row from before the column, at pickup, before writing anything.
Only an MP4 or QuickTime file: a TS recording's data streams are dropped as
they always were.

These run the real scan, worker and queue on the iPhone-style file
test_data_streams.py builds, six seconds long.

Verified by mutation, 11 applied, 11 killed. Each was first run against the
whole suite as it stood (2556 tests): 8 survived it, and counting a track
the muxer writes itself, ignoring QuickTime, and the file-level input
missing the value were killed there by the GoPro, timecode, chapter-track
and preflight tests:

  • the file never left alone             → the decision, the scan, the
                                            full rescan, the pickup
  • its Language Review questions raised  → a question alone
  • no unwritable track ever found        → which tracks, the scan
  • looking beyond MP4 and QuickTime      → which tracks
  • a rescan not storing what it found    → the full rescan
  • a first scan not storing it           → the scan
  • the worker not looking at pickup      → the pickup
  • the migration missing                 → the migration

The kills are from this file.
"""
import asyncio
import hashlib
import sqlite3

import pytest
from sqlalchemy import create_engine

from app.core.decision import analyze_file
from app.core.probe import extract_format_info, probe_file, unwritable_data_entries
from tests import test_quicktime_files as _qt
from tests.conftest import make_file_info, make_track
from tests.test_data_streams import _as_iphone_metadata, _camera
from tests.test_quicktime_files import _WS, needs_ffmpeg

# That file's fixture: a file-backed database every session points at,
# scratch folders and an empty library, English audio kept and revert
# points on. Bound by assignment, as test_forge_undo_identity.py does:
# pyflakes reads an imported fixture as unused.
rig = _qt.rig

REASON = "holds data tracks Remuxarr cannot write back (mebx)"


# ── Which data tracks cannot be written back ─────────────────────────────────

def _probe(format_name, brand, *streams):
    return {"format": {"format_name": format_name, "tags": {"major_brand": brand}},
            "streams": [dict(s, index=i) for i, s in enumerate(streams)]}


def _data(tag, codec="none"):
    return {"codec_type": "data", "codec_name": codec, "codec_tag_string": tag}


_MOV = "mov,mp4,m4a,3gp,3g2,mj2"
_VIDEO = {"codec_type": "video", "codec_tag_string": "hvc1"}
_AUDIO = {"codec_type": "audio", "codec_tag_string": "mp4a"}


@pytest.mark.parametrize("probe, expected", [
    (_probe(_MOV, "qt  ", _VIDEO, _AUDIO, *[_data("mebx")] * 4), ["mebx"]),    # the iPhone's
    (_probe(_MOV, "mp41", _VIDEO, _data("tmcd"), _AUDIO, _data("gpmd", "bin_data")), []),
    (_probe(_MOV, "qt  ", _VIDEO, _data("tmcd"), _AUDIO), []),
    (_probe(_MOV, "isom", _VIDEO, _AUDIO, _data("rtmd")), ["rtmd"]),          # a Sony's
    (_probe("mpegts", "", _VIDEO, _AUDIO, _data("[0][0][0][0]", "scte_35")), []),
], ids=["iphone", "gopro", "quicktime-timecode", "sony", "ts"])
def test_the_tracks_ffmpeg_cannot_write_back_are_found(probe, expected):
    assert unwritable_data_entries(probe) == expected
    assert extract_format_info(probe)["unwritable_data"] == ",".join(expected)


# ── The decision ─────────────────────────────────────────────────────────────

def _decide(settings, unwritable, *, french=True):
    """An MP4 with an untagged track to ask about, and a French one to drop
    unless french is False."""
    tracks = [make_track(stream_index=0, track_type="video", codec="hevc"),
              make_track(stream_index=1, track_type="audio", codec="aac",
                         language="eng", is_default=True),
              make_track(stream_index=2, track_type="audio", codec="aac", language=None)]
    if french:
        tracks.append(make_track(stream_index=3, track_type="audio", codec="aac",
                                 language="fre"))
    file_info = make_file_info(path="/m/IMG_8810.mov", container="mp4", video_codec="hevc")
    file_info["unwritable_data"] = unwritable
    return analyze_file(file_info, tracks, settings)


def test_the_file_is_left_as_it_is_with_no_questions(settings):
    settings["fix_undefined_language_audio"] = "always_ask"
    work = _decide(settings, "")
    assert work.should_process, "the fixture no longer produces work"
    assert work.undefined_audio_flags, "the fixture no longer raises a question"

    left = _decide(settings, "mebx")

    assert left.should_process is False and left.actions == []
    assert REASON in left.reason
    assert left.undefined_audio_flags == [] and left.audio_language_mismatch is None


def test_a_question_alone_is_not_raised_either(settings):
    """
    With nothing else to do, the question would still have gone to Language
    Review, and no answer to it could ever be used: the file is not written.
    """
    settings["fix_undefined_language_audio"] = "always_ask"
    assert _decide(settings, "", french=False).undefined_audio_flags, \
        "the fixture no longer raises a question"

    assert _decide(settings, "mebx", french=False).undefined_audio_flags == []


# ── Real files ───────────────────────────────────────────────────────────────

def _iphone_video(rig):
    """The iPhone-style file, English and German audio: a job would drop the
    German track, and the metadata track with it."""
    return _as_iphone_metadata(_camera(rig.library / "IMG_8810.mov", brand="qt  ",
                                       telemetry=False))


def _digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _row_and_items(rig):
    from app.database.models import MediaFile, QueueItem

    with rig.Session() as db:
        media = db.query(MediaFile).one()
        items = [(i.status, i.reason) for i in db.query(QueueItem).all()]
        return media.unwritable_data, media.status, items


def _scan(rig, force=False):
    from app.core import scanner

    with rig.Session() as db:
        scanner.scan_library(db, [str(rig.library)], force_probe=force)


@needs_ffmpeg
def test_the_scan_leaves_the_file_alone(rig):
    movie = _iphone_video(rig)
    before = _digest(movie)

    _scan(rig)

    unwritable, status, items = _row_and_items(rig)
    assert unwritable == "mebx"
    assert status == "skipped"
    assert [s for s, _ in items] == ["skipped"] and REASON in items[0][1]
    assert _digest(movie) == before


@needs_ffmpeg
def test_a_full_rescan_of_a_row_from_before_leaves_it_alone(rig):
    """
    A row from before the column has it null; a full rescan probes the file
    again and records what it holds, so the file is still left alone.
    """
    from app.database.models import MediaFile, QueueItem

    movie = _iphone_video(rig)
    before = _digest(movie)
    _scan(rig)
    with rig.Session() as db:
        db.query(MediaFile).one().unwritable_data = None
        db.query(QueueItem).delete()
        db.commit()

    _scan(rig, force=True)

    unwritable, _, items = _row_and_items(rig)
    assert unwritable == "mebx"
    assert [s for s, _ in items] == ["skipped"]
    assert _digest(movie) == before


@needs_ffmpeg
def test_the_worker_looks_at_a_row_from_before_at_pickup(rig):
    """
    A row from before the column, queued before it existed: the worker
    probes the file at pickup anyway, records what it holds, and leaves it
    alone without writing anything.
    """
    from app.database.models import MediaFile, QueueItem

    movie = _iphone_video(rig)
    before = _digest(movie)
    _scan(rig)
    with rig.Session() as db:
        media = db.query(MediaFile).one()
        media.unwritable_data, media.status = None, "queued"
        db.query(QueueItem).delete()
        db.add(QueueItem(file_id=media.id, status="pending"))
        db.commit()

    async def go():
        job_id = rig.worker._claim_next()
        await rig.worker._run_and_broadcast(job_id, _WS(), asyncio.get_running_loop())
    asyncio.run(go())

    unwritable, status, items = _row_and_items(rig)
    assert unwritable == "mebx"
    assert status == "skipped"
    assert [s for s, _ in items] == ["skipped"] and REASON in items[0][1]
    assert _digest(movie) == before


@needs_ffmpeg
def test_a_gopro_file_is_still_processed(rig):
    """Its data tracks are ones FFmpeg writes back (test_data_streams.py)."""
    movie = _camera(rig.library / "GX010042.MP4", brand="mp41")
    assert unwritable_data_entries(probe_file(str(movie))) == []

    _scan(rig)

    unwritable, _, items = _row_and_items(rig)
    assert unwritable == ""
    assert [s for s, _ in items] == ["pending"]


# ── The migration ────────────────────────────────────────────────────────────

def test_existing_files_come_out_not_looked_at(tmp_path, monkeypatch):
    """
    A file already in the library gains the column null, which is what
    tells the worker to look at pickup. A default of "" would record every
    such file as looked at and holding nothing, and an iPhone video already
    there would be processed.
    """
    from app.database.models import Base
    from tests.test_schema_migration import _point_the_engine_at, _restore_the_engine

    path = tmp_path / "remuxarr.db"
    engine = create_engine(f"sqlite:///{path}")
    Base.metadata.tables["media_files"].create(engine)
    engine.dispose()
    conn = sqlite3.connect(path)
    conn.execute("ALTER TABLE media_files DROP COLUMN unwritable_data")
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
        value = conn.execute("SELECT unwritable_data FROM media_files").fetchone()
    finally:
        conn.close()
    assert value == (None,)
