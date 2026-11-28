"""
A revert gives Matroska cover art back as the attachment it was.

Matroska stores a cover as an image attachment. FFmpeg's demuxer reports
one as a video stream with the attached_pic disposition, and a revert used
to map it like any other stream, so FFmpeg 8.1.3 wrote it back as a
still-image video track (V_MJPEG): the original's attachment was gone and a
player could offer the picture as a second video. A user's library showed
it: two episodes came back with 17 attachments of 18 and an MJPEG track.
The revert now copies each cover's image out and attaches it again with
every tag the point recorded (ffmpeg.restore_layout), as a job keeps one.
-attach adds a file after every mapped stream, so a cover that came before
a font comes back after it, which the user accepted: the order of a file's
attachments plays no part in playback.

ffprobe reports a cover the same either way, so the round trips read the
result with mkvmerge, which tells tracks and attachments apart.

Verified by mutation, 13 applied, 13 killed. Run first against the suite as
it stood before this file, the cover still mapped (the old behaviour)
survived all 3156 tests; the attachments numbered wrongly was already killed
there, by three round trips the change runs through. Killed now: the cover
still mapped; never attached; numbered wrongly; written without its tags; an
MP4's cover attached; a copy-out failure ignored; copies left behind after
a revert, or after a failed copy-out; copied out of the wrong file; a
still-image track, or a flagged stream with no name and type, taken for a
cover (one mutant each); the number of covers copied out not checked; the
revert point named by its file. Not run: the languages put back by their
place in the manifest rather than in the output, which is the same here, as
covers always follow the tracks.
"""
import json
import os
import subprocess

import pytest

from app.core.ffmpeg import RestoreUnsupported, build_restore_command, restore_layout
from tests import test_revert_multiple_jobs as _rm


# ── The layout and the command ───────────────────────────────────────────────

def _stream(index, kind, *, cover=False, processed=None, sidecar=None, tags=None,
            language=None):
    s = {"index": index, "type": kind, "codec": "h264" if kind == "video" else "aac",
         "disposition": ["attached_pic"] if cover else [], "tags": dict(tags or {}),
         "language": language, "title": None}
    if processed is not None:
        s["processed_index"] = processed
    if sidecar is not None:
        s["sidecar_index"] = sidecar
    return s


_COVER_TAGS = {"filename": "cover.jpg", "mimetype": "image/jpeg"}
_FONT_TAGS = {"filename": "font.ttf", "mimetype": "font/ttf"}


def _manifest(container="mkv", *, covers=1, fonts_after=0):
    """Video and audio, a font, then `covers` covers and `fonts_after` fonts."""
    streams = [_stream(0, "video", processed=0), _stream(1, "audio", processed=1),
               _stream(2, "attachment", processed=2, tags=_FONT_TAGS)]
    for n in range(covers):
        streams.append(_stream(len(streams), "video", cover=True, sidecar=n,
                               tags={**_COVER_TAGS, "filename": f"cover{n}.jpg"}))
    for _ in range(fonts_after):
        streams.append(_stream(len(streams), "attachment", processed=len(streams),
                               tags=_FONT_TAGS))
    return {"container": container, "streams": streams}


def _maps(cmd):
    """The stream each -map takes: attachments are read through an input
    of their own (_sparse_openings), so only the index is compared."""
    return [cmd[i + 1].split(":")[1] for i, arg in enumerate(cmd) if arg == "-map"]


def test_a_matroska_cover_is_attached_again_not_mapped():
    layout = restore_layout(_manifest())
    assert [s["index"] for s, _ in layout.mapped] == [0, 1, 2]
    assert [(s["index"], where) for s, where in layout.covers] == [(3, (1, 0))]


def test_an_mp4_cover_is_still_mapped():
    """An MP4 keeps a cover as a stream FFmpeg's MP4 muxer writes as cover
    art, so one is mapped as before, even one with a name and type."""
    layout = restore_layout(_manifest("mp4"))
    assert [s["index"] for s, _ in layout.mapped] == [0, 1, 2, 3]
    assert layout.covers == []


def test_a_still_image_track_is_mapped():
    """A video stream without attached_pic is a real track, and an
    attached_pic stream without a name and type is no Matroska cover."""
    manifest = _manifest(covers=0)
    manifest["streams"] += [_stream(3, "video", processed=3, tags=_COVER_TAGS),
                            _stream(4, "video", cover=True, processed=4,
                                    tags={"mimetype": "image/jpeg"})]
    assert restore_layout(manifest).covers == []


def test_the_command_attaches_each_cover_with_its_tags():
    """Numbered after the attachments mapped, as -metadata:s:t:N counts
    them, with every recorded tag, the name and type included."""
    manifest = _manifest(covers=2)
    manifest["streams"][3]["tags"]["title"] = "Front"
    cmd = build_restore_command("in.mkv", "side.mkv", "out.mkv", manifest,
                                cover_paths=["/t/c0", "/t/c1"])
    assert _maps(cmd) == ["0", "1", "2"]
    at = cmd.index("-attach")
    assert cmd[at:at + 9] == [
        "-attach", "/t/c0",
        "-metadata:s:t:1", "filename=cover0.jpg", "-metadata:s:t:1", "mimetype=image/jpeg",
        "-metadata:s:t:1", "title=Front", "-attach"]
    assert cmd[at + 9:at + 14] == [
        "/t/c1", "-metadata:s:t:2", "filename=cover1.jpg",
        "-metadata:s:t:2", "mimetype=image/jpeg"]
    assert cmd.index("-attach") < cmd.index("out.mkv")


def test_attachments_mapped_after_a_cover_count_before_it():
    """A font after the cover is mapped, so it is numbered first and the
    cover after it."""
    cmd = build_restore_command("in.mkv", "side.mkv", "out.mkv",
                                _manifest(fonts_after=1), cover_paths=["/t/c0"])
    assert _maps(cmd) == ["0", "1", "2", "4"]
    at = cmd.index("-attach")
    assert cmd[at + 2] == "-metadata:s:t:2"


def test_covers_not_copied_out_are_refused():
    """A command without them would give the file back without its covers."""
    with pytest.raises(RestoreUnsupported, match="1 cover"):
        build_restore_command("in.mkv", "side.mkv", "out.mkv", _manifest())


# ── Round trips ──────────────────────────────────────────────────────────────

def _matroska(path):
    """mkvmerge -J's track codecs, and each attachment's name, type and size."""
    out = json.loads(subprocess.run(["mkvmerge", "-J", str(path)], capture_output=True,
                                    text=True, check=True).stdout)
    return ([t["codec"] for t in out["tracks"]],
            [(a["file_name"], a["content_type"], a["size"])
             for a in out.get("attachments") or []])


@pytest.fixture
def cover_first(tmp_path, monkeypatch):
    """English and French audio, then a cover, then a font: the cover comes
    before the font, so it moves after it."""
    rig = _rm._library(tmp_path, monkeypatch, audio=[(440, "eng"), (880, "fre")])
    cover = tmp_path / "cover.jpg"
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "color=c=red:s=64x48",
                    "-frames:v", "1", str(cover)], check=True)
    font = tmp_path / "Font.ttf"
    font.write_bytes(b"FONTDATA" * 32)
    staged = tmp_path / "staged.mkv"
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", str(rig["path"]), "-map", "0",
                    "-c", "copy",
                    "-attach", str(cover), "-metadata:s:t:0", "mimetype=image/jpeg",
                    "-metadata:s:t:0", "filename=cover.jpg",
                    "-attach", str(font), "-metadata:s:t:1", "mimetype=font/ttf",
                    "-metadata:s:t:1", "filename=Font.ttf",
                    "-f", "matroska", str(staged)], check=True)
    os.replace(staged, rig["path"])
    stat = rig["path"].stat()
    rig["media"].size, rig["media"].mtime = stat.st_size, stat.st_mtime
    rig["db"].commit()
    rig["cover"] = cover.read_bytes()
    return rig


def _drop_french(rig):
    """A job keeping the video, the English audio and both attachments, the
    cover attached again as a job does (ffmpeg.cover_attach_args), so the
    processed file holds it."""
    cover = rig["tmp"] / "cover.jpg"
    _rm._run_job(rig, ["-map", "0:0", "-map", "0:1", "-map", "0:t?", "-c", "copy",
                       "-attach", str(cover), "-metadata:s:t:1", "mimetype=image/jpeg",
                       "-metadata:s:t:1", "filename=cover.jpg"], job_id=1)


def test_a_cover_before_a_font_comes_back_after_it(cover_first):
    tracks, attachments = _matroska(cover_first["path"])
    assert [a[0] for a in attachments] == ["cover.jpg", "Font.ttf"]
    _drop_french(cover_first)

    outcome = _rm._revert(cover_first)

    assert outcome.success, outcome.error
    back_tracks, back_attachments = _matroska(cover_first["path"])
    assert back_tracks == tracks, "the cover came back as a track"
    assert back_attachments == [attachments[1], attachments[0]]
    assert _rm._mjpeg_bytes(cover_first["path"], cover_first["tmp"]) == [cover_first["cover"]]


def test_a_matroska_cover_an_mp4_job_kept_comes_back_attached(tmp_path, monkeypatch):
    """Out of the MP4, where the job kept it as cover art with no name."""
    rig = _rm._cover_library(tmp_path, monkeypatch, container="mkv")
    tracks, attachments = _matroska(rig["path"])
    _rm._run_job_into(rig, ["-map", "0:0", "-map", "0:1", "-map", f"0:{rig['cover_index']}",
                            "-c", "copy"], job_id=1, fmt="mp4", suffix=".mp4")

    outcome = _rm._revert(rig)

    assert outcome.success, outcome.error
    assert _matroska(outcome.restored_path) == (tracks, attachments)


def test_a_cover_that_cannot_be_copied_out_stops_the_revert(cover_first, monkeypatch):
    from app.core import revert_restore
    from app.database.models import RevertPoint

    _drop_french(cover_first)
    processed = cover_first["path"].read_bytes()
    copied = []

    async def fails(input_path, stream_index, dest):
        copied.append(dest)
        with open(dest, "wb") as f:
            f.write(b"half")
        return "No space left on device"

    monkeypatch.setattr(revert_restore, "copy_cover_out", fails)
    outcome = _rm._revert(cover_first)

    assert not outcome.success
    assert outcome.error == (
        f"Could not copy the cover art cover.jpg out of {cover_first['path'].name} to put "
        f"it back: No space left on device. Nothing was changed.")
    # Taken from the processed file, which the job attached it to.
    assert copied and len(copied) == 1
    assert cover_first["path"].read_bytes() == processed
    assert not any(os.path.exists(p) for p in copied), "a copied-out cover was left behind"
    cover_first["db"].expire_all()
    assert cover_first["db"].query(RevertPoint).count() == 1


def test_copied_out_covers_are_removed_after_the_revert(cover_first, monkeypatch):
    from app.core import revert_restore

    _drop_french(cover_first)
    copied = []
    real = revert_restore.copy_cover_out

    async def recorded(input_path, stream_index, dest):
        copied.append(dest)
        return await real(input_path, stream_index, dest)

    monkeypatch.setattr(revert_restore, "copy_cover_out", recorded)
    assert _rm._revert(cover_first).success
    assert copied and not any(os.path.exists(p) for p in copied)


def test_they_are_removed_when_the_revert_fails_too(cover_first, monkeypatch):
    """FFmpeg failing after the covers were copied out."""
    from app.core import revert_restore

    _drop_french(cover_first)
    copied = []
    real = revert_restore.copy_cover_out

    async def recorded(input_path, stream_index, dest):
        copied.append(dest)
        return await real(input_path, stream_index, dest)

    async def ffmpeg_fails(*args, **kwargs):
        from app.core.subprocess_runner import SubprocessRunResult
        return SubprocessRunResult(success=False, error="FFmpeg exited 1")

    monkeypatch.setattr(revert_restore, "copy_cover_out", recorded)
    monkeypatch.setattr(revert_restore, "run_staged_subprocess", ffmpeg_fails)
    assert not _rm._revert(cover_first).success
    assert copied and not any(os.path.exists(p) for p in copied)


def test_a_cover_comes_from_the_revert_point_when_the_job_dropped_it(cover_first):
    """A job that kept no attachment: the revert point holds the cover."""
    _rm._run_job(cover_first, ["-map", "0:0", "-map", "0:1", "-c", "copy"], job_id=1)
    assert _rm._revert(cover_first).success
    tracks, attachments = _matroska(cover_first["path"])
    assert [a[0] for a in attachments] == ["Font.ttf", "cover.jpg"]
    assert _rm._mjpeg_bytes(cover_first["path"], cover_first["tmp"]) == [cover_first["cover"]]



def test_a_failed_copy_out_of_the_revert_point_says_so(cover_first, monkeypatch):
    """Not by the sidecar's file name, which means nothing to the user."""
    from app.core import revert_restore

    _rm._run_job(cover_first, ["-map", "0:0", "-map", "0:1", "-c", "copy"], job_id=1)

    async def fails(input_path, stream_index, dest):
        return "Invalid data found when processing input"

    monkeypatch.setattr(revert_restore, "copy_cover_out", fails)
    outcome = _rm._revert(cover_first)
    assert outcome.error == (
        "Could not copy the cover art cover.jpg out of the revert point to put it back: "
        "Invalid data found when processing input. Nothing was changed.")
