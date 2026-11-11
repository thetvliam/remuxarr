"""
Revert capture follows each kept track to where the job put it
(revert_capture._pins, revert.match_streams' pinned) rather than matching
it by metadata the job may have just rewritten.

A job that writes a language onto a kept track can make it look exactly
like a track it removed: an untagged subtitle kept and tagged English, and
an English subtitle of the same codec dropped. Matching paired the dropped
English original with the kept track and reported the untagged one lost,
so the sidecar stored the track that survived and never the one that was
destroyed. Reverting put the kept subtitle's text in both slots, and the
English subtitle was gone for good. It showed in the testlib explorer, on
a file whose untagged subtitle had been answered English in Subtitle
Language Review while Keep Subtitle Languages did not keep English.

The real-file tests compare every restored stream's packets with the
original's, since the restored metadata is right either way: it is written
from the manifest.
"""

import asyncio
import os
import shutil
import subprocess

import pytest

from app.core.revert import build_manifest, match_streams, positional_pairs

ffmpeg_required = pytest.mark.skipif(
    shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None,
    reason="ffmpeg/ffprobe not available",
)


def _ffmpeg(*args):
    subprocess.run(["ffmpeg", "-v", "error", "-y", *args], check=True)


def _packets(path):
    """Each stream's packet checksum, in stream order."""
    from app.core.probe import probe_file

    count = len(probe_file(str(path))["streams"])
    return [
        subprocess.run(["ffmpeg", "-v", "error", "-i", str(path), "-map", f"0:{i}",
                        "-c", "copy", "-f", "md5", "-"],
                       capture_output=True, text=True, check=True).stdout.strip()
        for i in range(count)
    ]


def _languages(path):
    from app.core.probe import probe_file

    return [(s["codec_type"], (s.get("tags") or {}).get("language"))
            for s in probe_file(str(path))["streams"]]


@pytest.fixture
def lib(tmp_path, monkeypatch):
    """A recycle volume and a live database; the test writes the file."""
    from sqlalchemy.orm import sessionmaker

    from tests.conftest import memory_engine

    from app.config import settings as app_settings
    from app.database.models import Base, MediaFile
    import app.database.session as session_mod

    media_dir = tmp_path / "media"
    media_dir.mkdir()
    recycle = tmp_path / "recycle"
    recycle.mkdir()
    monkeypatch.setattr(app_settings, "RECYCLE_DIR", str(recycle), raising=False)

    engine = memory_engine()
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine)
    monkeypatch.setattr(session_mod, "SessionLocal", factory)
    import app.core.worker as worker_mod
    monkeypatch.setattr(worker_mod, "SessionLocal", factory)

    path = media_dir / "Film.mkv"
    db = factory()
    media = MediaFile(path=str(path), filename="Film.mkv", directory=str(media_dir),
                      size=0, mtime=0, container="mkv", status="processed")
    db.add(media)
    db.commit()
    return {"db": db, "media": media, "path": path, "tmp": tmp_path}


def _srt(lib, name, text):
    path = lib["tmp"] / name
    path.write_text(f"1\n00:00:00,000 --> 00:00:01,000\n{text}\n\n")
    return str(path)


def _tone(lib, name, frequency):
    path = lib["tmp"] / name
    _ffmpeg("-f", "lavfi", "-i", f"sine=frequency={frequency}:duration=1",
            "-ac", "2", "-ar", "48000", "-c:a", "aac", str(path))
    return str(path)


def _video(lib):
    path = lib["tmp"] / "v.mkv"
    _ffmpeg("-f", "lavfi", "-i", "testsrc=size=160x120:rate=10:duration=1",
            "-c:v", "libx264", "-pix_fmt", "yuv420p", str(path))
    return str(path)


def _run_job(lib, ffmpeg_args, *, job_id, removed):
    """
    Do to the file what a real job does, with capture in the same window:
    FFmpeg writes a temp output, capture runs while both files exist, then
    the temp is swapped in and the revert point recorded.
    """
    from app.core.revert_capture import capture
    from app.core.worker import _record_revert_point

    produced = lib["tmp"] / f"job{job_id}.remuxarr_tmp"
    _ffmpeg("-i", str(lib["path"]), *ffmpeg_args, "-f", "matroska", str(produced))
    captured, error = asyncio.run(capture(
        input_path=str(lib["path"]), produced_path=str(produced),
        file_id=lib["media"].id, job_id=job_id,
        app_cfg={"revert_enabled": True, "revert_require_point": False},
        removed=frozenset(removed),
    ))
    assert error is None, error
    os.replace(produced, lib["path"])
    if captured:
        _record_revert_point(lib["media"].id, captured, str(lib["path"]), [])
    return captured


def _revert(lib):
    from app.core.revert_restore import restore_revert_point
    from app.database.models import RevertPoint

    lib["db"].expire_all()
    point = lib["db"].query(RevertPoint).one()
    outcome = asyncio.run(restore_revert_point(point.id))
    assert outcome.success, outcome.error


# ── Real files ───────────────────────────────────────────────────────────────

@ffmpeg_required
def test_a_dropped_subtitle_is_stored_when_a_kept_one_is_tagged_its_language(lib):
    """The explorer's file: an untagged subtitle and an English one."""
    _ffmpeg("-i", _video(lib), "-i", _tone(lib, "a.m4a", 440),
            "-i", _srt(lib, "und.srt", "UND"), "-i", _srt(lib, "eng.srt", "ENG"),
            "-map", "0", "-map", "1", "-map", "2", "-map", "3", "-c", "copy",
            "-metadata:s:a:0", "language=eng", "-metadata:s:s:1", "language=eng",
            str(lib["path"]))
    pristine = _packets(lib["path"])
    assert pristine[2] != pristine[3], "fixture changed: the subtitles must differ"

    # Keep the untagged subtitle, tagged English; drop the English one.
    _run_job(lib, ["-map", "0:0", "-map", "0:1", "-map", "0:2", "-c", "copy",
                   "-metadata:s:s:0", "language=eng"], job_id=1, removed={3})
    _revert(lib)

    assert _packets(lib["path"]) == pristine
    assert _languages(lib["path"]) == [("video", None), ("audio", "eng"),
                                       ("subtitle", None), ("subtitle", "eng")]


@ffmpeg_required
def test_a_dropped_audio_track_is_stored_when_a_kept_one_is_tagged_its_language(lib):
    """Two AAC stereo tracks, untagged and English: the same shape in audio."""
    _ffmpeg("-i", _video(lib), "-i", _tone(lib, "a.m4a", 440), "-i", _tone(lib, "b.m4a", 880),
            "-map", "0", "-map", "1", "-map", "2", "-c", "copy",
            "-metadata:s:a:1", "language=eng", str(lib["path"]))
    pristine = _packets(lib["path"])
    assert pristine[1] != pristine[2], "fixture changed: the audio tracks must differ"

    _run_job(lib, ["-map", "0:0", "-map", "0:1", "-c", "copy",
                   "-metadata:s:a:0", "language=eng"], job_id=1, removed={2})
    _revert(lib)

    assert _packets(lib["path"]) == pristine


@ffmpeg_required
def test_a_later_job_follows_its_tracks_from_where_the_first_left_them(lib):
    """
    The first job drops the German audio; the second keeps the untagged
    subtitle, tagged English, and drops the English one. The second job's
    input indices are not the original's, which is what processed_index is
    for.
    """
    _ffmpeg("-i", _video(lib), "-i", _tone(lib, "a.m4a", 440), "-i", _tone(lib, "b.m4a", 660),
            "-i", _srt(lib, "und.srt", "UND"), "-i", _srt(lib, "eng.srt", "ENG"),
            *[a for i in range(5) for a in ("-map", str(i))], "-c", "copy",
            "-metadata:s:a:0", "language=eng", "-metadata:s:a:1", "language=ger",
            "-metadata:s:s:1", "language=eng", str(lib["path"]))
    pristine = _packets(lib["path"])

    _run_job(lib, ["-map", "0:0", "-map", "0:1", "-map", "0:3", "-map", "0:4",
                   "-c", "copy"], job_id=1, removed={2})
    _run_job(lib, ["-map", "0:0", "-map", "0:1", "-map", "0:2", "-c", "copy",
                   "-metadata:s:s:0", "language=eng"], job_id=2, removed={3})
    _revert(lib)

    assert _packets(lib["path"]) == pristine


@ffmpeg_required
def test_a_later_job_pins_each_track_by_where_it_sits_in_the_file_it_was_handed(lib):
    """
    Three AAC stereo tracks, English, German, French. The first job drops
    English; the second drops German, which now sits where French did in
    the original. Pinned by its original index, German would be taken for
    the French track the second job kept, and never stored.
    """
    _ffmpeg("-i", _video(lib), "-i", _tone(lib, "a.m4a", 440), "-i", _tone(lib, "b.m4a", 660),
            "-i", _tone(lib, "c.m4a", 880),
            *[a for i in range(4) for a in ("-map", str(i))], "-c", "copy",
            "-metadata:s:a:0", "language=eng", "-metadata:s:a:1", "language=ger",
            "-metadata:s:a:2", "language=fre", str(lib["path"]))
    pristine = _packets(lib["path"])

    _run_job(lib, ["-map", "0:0", "-map", "0:2", "-map", "0:3", "-c", "copy"],
             job_id=1, removed={1})
    _run_job(lib, ["-map", "0:0", "-map", "0:2", "-c", "copy"], job_id=2, removed={1})
    _revert(lib)

    assert _packets(lib["path"]) == pristine


# ── The pieces ───────────────────────────────────────────────────────────────

def _probe(*streams):
    return {"streams": [
        {"index": i, "codec_type": kind, "codec_name": codec,
         "tags": {"language": lang} if lang else {}, "disposition": {}}
        for i, (kind, codec, lang) in enumerate(streams)
    ], "format": {}}


def _match(original, processed, pinned=None):
    manifest = build_manifest(original, original_path="/m/Film.mkv", original_container="mkv")
    return [index for _stream, index in match_streams(manifest, processed, pinned)]


_ORIGINAL = _probe(("video", "h264", None), ("subtitle", "subrip", None),
                   ("subtitle", "subrip", "eng"))
_KEPT_AND_TAGGED = _probe(("video", "h264", None), ("subtitle", "subrip", "eng"))


def test_unpinned_matching_still_takes_the_look_alike():
    """What the pins replace, kept here so the case is plain."""
    assert _match(_ORIGINAL, _KEPT_AND_TAGGED) == [0, None, 1]


def test_pinned_tracks_are_where_the_job_put_them():
    assert _match(_ORIGINAL, _KEPT_AND_TAGGED, {0: 0, 1: 1, 2: None}) == [0, 1, None]


def test_a_track_pinned_as_removed_is_lost_beside_a_look_alike():
    """Pinned to None, the English original is lost although an English
    subrip survives; unpinned, the untagged one finds it in the passes."""
    assert _match(_ORIGINAL, _KEPT_AND_TAGGED, {2: None}) == [0, 1, None]


def test_a_pinned_track_whose_payload_changed_is_lost_and_its_stream_not_reused():
    """
    A subrip track the output holds as mov_text was converted, so it is
    lost, as matching would say. The mov_text stream is still its, and is
    not handed to the other original, an unpinned mov_text track.
    """
    original = _probe(("video", "h264", None), ("subtitle", "subrip", "eng"),
                      ("subtitle", "mov_text", "eng"))
    processed = _probe(("video", "h264", None), ("subtitle", "mov_text", "eng"))
    assert _match(original, processed) == [0, None, 1]
    assert _match(original, processed, {0: 0, 1: 1}) == [0, None, None]


def _track(si, kind):
    return {"stream_index": si, "track_type": kind}


def test_positional_pairs_pair_within_each_type_and_skip_the_removed():
    before = [_track(0, "video"), _track(1, "audio"), _track(2, "subtitle"),
              _track(3, "audio"), _track(4, "subtitle")]
    after = [_track(0, "video"), _track(1, "audio"), _track(2, "audio"),
             _track(3, "subtitle")]
    assert positional_pairs(before, after, {2}) == ({0: 0, 1: 1, 3: 2, 4: 3}, [])


def test_positional_pairs_leave_out_a_type_whose_count_is_off():
    """An AC3 Forge add puts a fourth audio track where three were kept."""
    before = [_track(0, "video"), _track(1, "audio"), _track(2, "audio")]
    after = [_track(0, "video"), _track(1, "audio"), _track(2, "audio"), _track(3, "audio")]
    assert positional_pairs(before, after, set()) == ({0: 0}, [("audio", 2, 3)])


def test_pins_mark_removed_tracks_lost_even_in_a_type_left_out():
    """
    An output with one more subtitle than the job kept, from some step the
    pairing does not know about: the subtitle count is off, so the kept
    subtitle is left to matching, but the removed English one is still
    pinned lost. Unpinned, it could be paired with the extra English
    subtitle by its language.
    """
    from app.core.revert_capture import _pins

    original = _probe(("video", "h264", None), ("audio", "aac", "eng"),
                      ("subtitle", "subrip", None), ("subtitle", "subrip", "eng"))
    produced = _probe(("video", "h264", None), ("audio", "aac", "eng"),
                      ("subtitle", "subrip", None), ("subtitle", "subrip", "eng"),
                      ("subtitle", "subrip", "eng"))
    manifest = build_manifest(original, original_path="/m/Film.mkv", original_container="mkv")
    assert _pins(manifest, original, produced, frozenset({3}), extend=False) == \
        {0: 0, 1: 1, 3: None}
