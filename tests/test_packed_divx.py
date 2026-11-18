"""
An AVI whose video has packed B-frames stays an AVI.

Many DivX 5 and Xvid encoders stored a B-frame inside the packet of the
frame before it ("packed B-frames"). Generating presentation times on input
(ffmpeg.build_ffmpeg_command) fixes an unpacked AVI's conversion to MP4, but
not a packed one: on a real DivX 5.01 file ("DivX501b484p"), 744 of the
first 1500 frames still came out timed in storage order. Unpacking the video
first fixes that but changes its bytes, so a revert could no longer give
back the original; most Plex clients transcode MPEG-4 Part 2 video anyway.
So such a file is not converted (decided by the user, PARKED 12).

probe.is_packed_divx reads the encoder's signature the way FFmpeg does;
scanner.packed_divx_for reads it for an AVI with MPEG-4 Part 2 video wherever
a decision is made (the scan, the worker at pickup, the queue routes); and
analyze_file keeps such a file out of MP4 as an incompatible codec does.

Verified by mutation, 12 applied, 12 killed, each run first against the
whole suite as it stood (2506 tests), which all twelve survived:

  • the signature never read as packed      → reading it, and every real
                                              file test
  • any signature read as packed            → the two unpacked ones
  • the "Build" spelling not read           → its packed case
  • read for any container, or any video    → only an AVI with MPEG-4 is
                                              read, one mutant each
  • the scan, the worker or the queue
    routes not reading it                   → the scan, the worker, the
                                              queue routes, one each
  • the decision ignoring it                → not converted, other changes
                                              as an AVI, both reasons, the
                                              scan, the worker
  • the reason never naming it              → not converted, both
                                              reasons, the scan
  • the tag sentence's hint kept with it    → both reasons
  • it named as the reason with Prefer
    MP4 Container off                       → Prefer MP4 off

Not mutated: reading the extradata with the packet (dump_extra). libxvid
writes the signature in both, so no file here tells the two apart.
"""
import asyncio
import json
import shutil
import subprocess

import pytest

from app.core.decision import analyze_file
from app.core.probe import has_packed_divx_signature, is_packed_divx
from conftest import make_file_info, make_track
from tests import test_quicktime_files as _qt
from tests.test_quicktime_files import _WS

# That file's fixture: a file-backed database every session points at,
# scratch folders and an empty library. Bound by assignment, as
# test_forge_undo_identity.py does: pyflakes reads an imported fixture as
# unused.
rig = _qt.rig


def _has_libxvid() -> bool:
    if shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None:
        return False
    out = subprocess.run(["ffmpeg", "-hide_banner", "-encoders"],
                         capture_output=True, text=True).stdout
    return " libxvid " in out


needs_libxvid = pytest.mark.skipif(not _has_libxvid(), reason="ffmpeg with libxvid not available")

USER_DATA = b"\x00\x00\x01\xb2"
NEXT_START_CODE = b"\x00\x00\x01\xb6"

PACKED_REASON = ("Its video stores B-frames packed, the way some DivX and Xvid encoders did, "
                 "which cannot be converted to MP4 correctly, so it stays AVI.")


# ── Reading the signature ────────────────────────────────────────────────────

@pytest.mark.parametrize("signature, packed", [
    (b"DivX503b1393p", True),
    # A real DivX 5.01 file the user ran the check on.
    (b"DivX501b484p", True),
    # FFmpeg reads the older "Build" spelling too.
    (b"DivX500Build413p", True),
    # A real DivX 5.00 file the user ran the check on: no packing.
    (b"DivX500Build413", False),
    (b"DivX503b1393", False),
    (b"XviD0074", False),
])
def test_the_signature_is_read_as_ffmpeg_reads_it(signature, packed):
    assert has_packed_divx_signature(b"\x00" * 7 + USER_DATA + signature + NEXT_START_CODE) is packed


def test_only_in_user_data():
    assert has_packed_divx_signature(b"title: DivX503b1393p") is False


def test_only_an_avi_with_mpeg4_video_is_read(monkeypatch):
    """Anything else is not read at all: packing is only read where it
    stops a conversion doing harm."""
    import app.core.scanner as scanner

    read = []
    monkeypatch.setattr(scanner, "is_packed_divx", lambda path, ffmpeg: read.append(path) or True)
    mpeg4 = [{"track_type": "video", "codec": "mpeg4"}]

    assert scanner.packed_divx_for("/x/a.mkv", "mkv", mpeg4) is None
    assert scanner.packed_divx_for("/x/b.avi", "avi", [{"track_type": "video", "codec": "h264"}]) is None
    assert read == []
    assert scanner.packed_divx_for("/x/c.avi", "avi", mpeg4) is True
    assert read == ["/x/c.avi"]


# ── The decision ─────────────────────────────────────────────────────────────

def _avi(*audio):
    tracks = [make_track(stream_index=0, track_type="video", codec="mpeg4")]
    for codec, language in audio:
        tracks.append(make_track(stream_index=len(tracks), track_type="audio",
                                 codec=codec, language=language, is_default=len(tracks) == 1))
    return make_file_info(path="/media/x/clip.avi", container="avi", video_codec="mpeg4"), tracks


def test_a_packed_avi_is_not_converted(settings):
    settings.update(prefer_mp4_container=True)
    file_info, tracks = _avi(("mp3", "eng"))

    unpacked = analyze_file(file_info, tracks, settings, has_packed_bframes=False)
    packed = analyze_file(file_info, tracks, settings, has_packed_bframes=True)

    assert unpacked.target_container == "mp4"
    assert (packed.should_process, packed.actions, packed.reason) == (False, [], PACKED_REASON)
    assert packed.mp4_blocked_beyond_subtitles is True


def test_one_that_needs_other_changes_gets_them_as_an_avi(settings):
    settings.update(prefer_mp4_container=True, keep_audio_languages=["eng"],
                    keep_default_audio=False)
    file_info, tracks = _avi(("mp3", "eng"), ("mp3", "fre"))

    decision = analyze_file(file_info, tracks, settings, has_packed_bframes=True)

    assert decision.should_process is True
    assert decision.target_container == "avi"
    assert not any(a.action_type == "change_container" for a in decision.actions)
    assert [a.stream_index for a in decision.actions if a.action_type == "drop_track"] == [2]


def test_with_a_tag_it_cannot_keep_it_says_both(settings):
    """The packing says why it stays an AVI, so the tag sentence adds no
    hint of its own."""
    settings.update(prefer_mp4_container=True, fix_undefined_language_audio="always_fix")
    file_info, tracks = _avi(("mp3", "und"))

    decision = analyze_file(file_info, tracks, settings, has_packed_bframes=True)

    assert decision.reason == (PACKED_REASON + " AVI files cannot store language tags, "
                               "so this one's are left as they are.")


def test_with_prefer_mp4_off_the_packing_is_not_the_reason(settings):
    settings.update(prefer_mp4_container=False, fix_undefined_language_audio="always_fix")
    file_info, tracks = _avi(("mp3", "und"))

    decision = analyze_file(file_info, tracks, settings, has_packed_bframes=True)

    assert decision.reason == ("AVI files cannot store language tags, so this one's are "
                               "left as they are. Turn on Prefer MP4 Container to convert it.")


# ── Real files ───────────────────────────────────────────────────────────────

def _xvid(path, *, packed):
    """One second of Xvid from libxvid: with B-frames it writes them packed
    and signs the stream "DivX503b1393p"; without, it does neither."""
    subprocess.run(["ffmpeg", "-v", "error", "-y",
                    "-f", "lavfi", "-i", "testsrc=size=64x36:rate=24:duration=1",
                    "-f", "lavfi", "-i", "sine=duration=1",
                    "-map", "0:v", "-map", "1:a", "-c:v", "libxvid", "-bf", "2" if packed else "0",
                    "-q:v", "5", "-c:a", "libmp3lame", "-metadata:s:a:0", "language=eng",
                    "-f", "avi", str(path)], check=True)
    return path


@needs_libxvid
def test_a_packed_file_is_told_apart(tmp_path):
    assert is_packed_divx(str(_xvid(tmp_path / "packed.avi", packed=True))) is True
    assert is_packed_divx(str(_xvid(tmp_path / "plain.avi", packed=False))) is False
    # FFmpeg's own encoder writes B-frames unpacked, as testlib file 18 has.
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi",
                    "-i", "testsrc=size=64x36:rate=24:duration=1", "-c:v", "mpeg4",
                    "-vtag", "XVID", "-bf", "2", "-f", "avi", str(tmp_path / "lavc.avi")],
                   check=True)
    assert is_packed_divx(str(tmp_path / "lavc.avi")) is False
    assert is_packed_divx(str(tmp_path / "missing.avi")) is None


@needs_libxvid
def test_a_scan_leaves_a_packed_avi_as_it_is(rig):
    from app.core import scanner
    from app.database.models import AppSetting, QueueItem

    with rig.Session() as db:
        db.merge(AppSetting(key="prefer_mp4_container", value=json.dumps(True)))
        db.commit()
    clip = _xvid(rig.library / "Clip.avi", packed=True)

    with rig.Session() as db:
        assert scanner.queue_single_file(db, str(clip)) is None
        assert [(q.status, q.reason) for q in db.query(QueueItem)] == [("skipped", PACKED_REASON)]


@needs_libxvid
def test_the_worker_does_not_convert_one_either(rig):
    """
    Queued before the check existed, or for a change the file no longer
    needs: the worker decides again at pickup, and reads the packing too.
    """
    from app.core import scanner
    from app.database.models import AppSetting, QueueItem

    with rig.Session() as db:
        db.merge(AppSetting(key="prefer_mp4_container", value=json.dumps(True)))
        db.commit()
    clip = _xvid(rig.library / "Clip.avi", packed=True)
    before = clip.read_bytes()
    with rig.Session() as db:
        scanner.queue_single_file(db, str(clip))
        item = db.query(QueueItem).one()
        item.status = "pending"
        db.commit()

    async def go():
        job_id = rig.worker._claim_next()
        await rig.worker._run_and_broadcast(job_id, _WS(), asyncio.get_running_loop())
    asyncio.run(go())

    assert sorted(p.name for p in rig.library.iterdir()) == ["Clip.avi"]
    assert clip.read_bytes() == before


@needs_libxvid
def test_the_queue_routes_read_the_packing(rig):
    """A preview, or an answer applied from Review, decides the file the
    way a scan does."""
    from app.api.routes.queue import _build_analysis_inputs
    from app.core import scanner
    from app.database.models import MediaFile

    clip = _xvid(rig.library / "Clip.avi", packed=True)
    with rig.Session() as db:
        scanner.queue_single_file(db, str(clip))
        _, _, kwargs = _build_analysis_inputs(db, db.query(MediaFile).one())

    assert kwargs["has_packed_bframes"] is True
