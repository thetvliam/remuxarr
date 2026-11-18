"""
An AVI converted to MP4 keeps its frames' presentation times.

An AVI stores no presentation time for a frame, only the order frames are
stored in. For video with B-frames, as Xvid and DivX usually have, that is
not the display order, and the job copied it into an MP4 without telling
FFmpeg to work the times out. Reproduced with real FFmpeg on testlib file
18 (six seconds of Xvid with B-frames): every frame got its storage time as
its presentation time, 48 of 144 went backwards, and FFmpeg's own frame
sync dropped the last one when decoding the result. The explorer reported
it as check 2 (PARKED 12). The command now passes -fflags +genpts for an
AVI source (ffmpeg.build_ffmpeg_command), the decision carrying the
container the scan probed (ProcessingDecision.source_container).

Packed B-frames, which many Xvid encoders write, are not fixed by this
alone, and are not covered here.

Verified by mutation, 4 applied, 4 killed, each run first against the whole
suite as it stood (2502 tests), which all four survived:

  • no genpts                               → the command, the real file
  • the decision not naming its container   → the decision, the real file
  • genpts for every source                 → no other source does
  • genpts after the -i, where it does
    nothing for the input                   → the command, the real file
"""
import json
import subprocess

from app.core.decision import Action, ProcessingDecision, analyze_file
from app.core.ffmpeg import build_ffmpeg_command
from conftest import make_file_info, make_track
from tests import test_quicktime_files as _qt
from tests.test_quicktime_files import _run_job, needs_ffmpeg

# That file's fixture: a file-backed database every session points at,
# scratch folders and an empty library. Bound by assignment, as
# test_forge_undo_identity.py does: pyflakes reads an imported fixture as
# unused.
rig = _qt.rig

FRAMES = 144


# ── The command ──────────────────────────────────────────────────────────────

def _command(source_container, target_container="mp4"):
    video = {"stream_index": 0, "track_type": "video", "codec": "mpeg4", "language": "und"}
    decision = ProcessingDecision(
        should_process=True, reason="", target_container=target_container,
        source_container=source_container,
        actions=[Action(action_type="change_container", description="")])
    return build_ffmpeg_command("in.avi", "out.tmp", decision, [video])


def test_an_avi_source_has_its_presentation_times_generated():
    cmd = _command("avi")
    # An input option: it has to come before the -i it applies to.
    assert cmd[1:5] == ["-fflags", "+genpts", "-i", "in.avi"]


def test_no_other_source_does():
    for source in ("mkv", "mp4", None):
        assert "+genpts" not in _command(source), source


def test_the_decision_names_the_container_it_was_made_for(settings):
    settings.update(prefer_mp4_container=True)
    tracks = [make_track(stream_index=0, track_type="video", codec="mpeg4"),
              make_track(stream_index=1, track_type="audio", codec="mp3", language="eng")]
    file_info = make_file_info(path="/media/x/clip.avi", container="avi", video_codec="mpeg4")

    decision = analyze_file(file_info, tracks, settings)

    assert (decision.should_process, decision.target_container,
            decision.source_container) == (True, "mp4", "avi")


# ── Real files ───────────────────────────────────────────────────────────────

def _xvid(path):
    """Six seconds of Xvid-tagged MPEG-4 with two B-frames between
    references, written unpacked, as testlib file 18 is."""
    subprocess.run(["ffmpeg", "-v", "error", "-y",
                    "-f", "lavfi", "-i", f"testsrc=size=64x36:rate=24:duration={FRAMES / 24}",
                    "-f", "lavfi", "-i", f"sine=duration={FRAMES / 24}",
                    "-map", "0:v", "-map", "1:a", "-c:v", "mpeg4", "-vtag", "XVID", "-bf", "2",
                    "-q:v", "5", "-c:a", "libmp3lame", "-metadata:s:a:0", "language=eng",
                    "-f", "avi", str(path)], check=True)
    return path


def _frame_times(path):
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries", "frame=pts",
         "-print_format", "json", str(path)],
        capture_output=True, text=True, check=True).stdout
    return [f.get("pts") for f in json.loads(out)["frames"]]


def _pictures(path, *sync):
    """Each decoded picture's hash, in order; with sync, as FFmpeg's default
    frame sync passes them, which drops one it takes for a duplicate."""
    out = subprocess.run(
        ["ffmpeg", "-v", "error", "-i", str(path), "-map", "0:v:0", *sync,
         "-f", "framemd5", "-"], capture_output=True, text=True, check=True).stdout
    return [line.rsplit(",", 1)[1].strip() for line in out.splitlines()
            if line and not line.startswith("#")]


@needs_ffmpeg
def test_an_xvid_avi_converted_to_mp4_keeps_its_frame_order(rig):
    from app.database.models import AppSetting

    with rig.Session() as db:
        db.merge(AppSetting(key="prefer_mp4_container", value=json.dumps(True)))
        db.commit()
    clip = _xvid(rig.library / "Clip.avi")
    pictures = _pictures(clip, "-fps_mode", "passthrough")
    assert len(pictures) == FRAMES
    assert None in _frame_times(clip), "fixture changed: every frame has a time"

    assert "change_container" in _run_job(rig, clip)
    converted = rig.library / "Clip.mp4"

    times = _frame_times(converted)
    assert len(times) == FRAMES
    assert all(a < b for a, b in zip(times, times[1:])), "presentation times go backwards"
    assert _pictures(converted, "-fps_mode", "passthrough") == pictures
    assert len(_pictures(converted)) == FRAMES, "a frame is dropped on decoding"
