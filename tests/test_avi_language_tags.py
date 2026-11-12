"""
An AVI kept as AVI is not queued for a language tag it cannot keep.

FFmpeg's AVI muxer writes no language for a stream and its demuxer reads
none (ffmpeg.stores_language). With Prefer MP4 Container off and audio
Always Fix, an AVI with untagged audio was queued to tag it, the job wrote
a file without the tag, and every full scan queued it again (PARKED 10;
explorer checks 4 and 7 on testlib file 18). decision.analyze_file now
plans no tag for such an output; test_decision.py covers the decision.

Real FFmpeg and the real scanner.
"""
import json
import subprocess

from app.core.ffmpeg import stores_language
from tests import test_quicktime_files as _qt
from tests.test_quicktime_files import needs_ffmpeg

# That file's fixture: a file-backed database every session points at,
# scratch folders and an empty library, Prefer MP4 Container off. Bound by
# assignment, as test_forge_undo_identity.py does: pyflakes reads an
# imported fixture as unused.
rig = _qt.rig


def _avi(path, language=None):
    cmd = ["ffmpeg", "-v", "error", "-y",
           "-f", "lavfi", "-i", "testsrc=size=64x36:rate=5:duration=1",
           "-f", "lavfi", "-i", "sine=duration=1",
           "-map", "0:v", "-map", "1:a", "-c:v", "mpeg4", "-c:a", "libmp3lame"]
    if language:
        cmd += ["-metadata:s:a:0", f"language={language}"]
    subprocess.run(cmd + ["-f", "avi", str(path)], check=True)
    return path


def _audio_language(path):
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-print_format", "json", "-show_streams",
         "-select_streams", "a", str(path)],
        capture_output=True, text=True, check=True).stdout
    return (json.loads(out)["streams"][0].get("tags") or {}).get("language")


@needs_ffmpeg
def test_an_avi_cannot_keep_a_language_tag(tmp_path):
    """What the rule rests on, with the FFmpeg Remuxarr runs."""
    assert _audio_language(_avi(tmp_path / "tagged.avi", "eng")) is None
    assert stores_language("avi") is False
    assert stores_language("mkv") is True and stores_language("mp4") is True


@needs_ffmpeg
def test_an_avi_with_untagged_audio_is_not_queued(rig):
    from app.core import scanner
    from app.database.models import AppSetting, QueueItem

    with rig.Session() as db:
        db.merge(AppSetting(key="fix_undefined_language_audio",
                            value=json.dumps("always_fix")))
        db.commit()
    clip = _avi(rig.library / "Clip.avi")

    with rig.Session() as db:
        assert scanner.queue_single_file(db, str(clip)) is None
        items = [(q.status, q.reason) for q in db.query(QueueItem)]

    assert items == [("skipped", "AVI files cannot store language tags, so this one's are "
                                 "left as they are. Turn on Prefer MP4 Container to convert it.")]
