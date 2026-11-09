"""
AC3 Forge undo removes the AC3 the forge added, and no other.

Undo used to take the last audio track for the forged AC3 whenever it was
AC3 with six channels. That holds while the forged track survives, as the
forge appends it and the remux pipeline never reorders. It fails once a job
has dropped the forged track from a file that already had an AC3 5.1 of
its own. Reproduced with real FFmpeg before this file existed:

  • French AAC 5.1 and English AC3 5.1, forged (a French AC3 appended);
  • French audio dropped from Keep Audio Languages, so the job removed the
    French AAC and, as it inherits the AAC's language, the forged AC3;
  • undo then removed the English AC3, recorded "undone" with no error,
    and left a file with video alone.

The add now writes a marker, FORGE_AC3_HANDLER_NAME, as the new track's
handler_name, and the job records whether the probe after the add found
it (Ac3ForgeJob.ac3_marked). A marked undo goes by the marker alone. An
unmarked one (an add from before the marker, a container that keeps no
handler_name, an add whose closing probe failed) keeps the last-track rule,
and an undo of either kind refuses to remove a file's only audio track.

The pure cases of resolve_forge_ac3_for_undo are in
test_forge_undo_resolution, beside the ones that pinned the last-track rule,
with the revert's per-job settling; the column's migration is in
test_schema_migration.

Verified by mutation, 14 applied, 14 killed. Every one was run against the
full suite as it stood (2250 tests) before any of these tests existed, with
one mechanical change: test_answers_go_back_to_the_tracks_they_were_given_for
built Track rows by unpacking the probe's dict, which fails on the new
handler_name key, and now writes them with replace_track_rows, as
_finish_job does. All fourteen survived that baseline:

  • the add writing no marker               → the two dropped-track runs,
                                              both MP4 runs, the forced/SDH/
                                              dub check, and the revert test
                                              in test_revert_multiple_jobs
                                              that asserts the job is marked.
                                              The unmarked run fails too, but
                                              only because its stand-in for
                                              the old command finds no marker
                                              to strip
  • the undo ignoring ac3_marked            → the two dropped-track runs
  • the revert's settling ignoring it       → test_forge_undo_resolution
  • a marked job with no marker left falling
    back to the last-track rule             → the two dropped-track runs, the
                                              unmarked run and the AVI and
                                              MPEG-TS runs (the add decides
                                              ac3_marked through the same
                                              rule), test_forge_undo_resolution
  • several marked tracks taking the last   → test_forge_undo_resolution
  • the probe not reporting handler_name    → the two dropped-track runs,
                                              both MP4 runs, the forced/SDH/
                                              dub check, the revert test
  • the probe reading only the lower-case
    key, which Matroska does not use        → the two dropped-track runs, the
                                              MP4 run that starts as
                                              Matroska, the forced/SDH/dub
                                              check, the revert test. The run
                                              that starts as MP4 survives it,
                                              as MP4 uses the lower-case key
  • the add never recording ac3_marked      → the two dropped-track runs,
                                              both MP4 runs, the revert test
  • the migration missing                   → test_schema_migration
  • no only-audio refusal                   → the unmarked run,
                                              test_forge_undo_resolution
  • a marked track's shape not checked      → test_forge_undo_resolution
  • ac3_marked set without the probe
    finding the marker                      → the AVI and MPEG-TS runs, the
                                              unmarked run
  • the revert settling an only-audio file  → test_forge_undo_resolution
  • the undo not handling only-audio        → the unmarked run. Unhandled,
                                              that run raised on a log line
                                              formatting the missing index;
                                              what FFmpeg would make of it
                                              was not checked

The revert test's claim, that "success" there needs the restore to write
handler_name back, was checked with one more mutant, not baseline evidence:
the restore skipping that tag fails it.
"""
import hashlib
import json
import subprocess

import pytest

from tests import test_forge_keeps_tracks_current as _kept
from tests.test_forge_keeps_tracks_current import (
    _audio, _drop_french, _forge, _queue, _run_queued, needs_ffmpeg,
)

# That file's fixture: a file-backed database both pipelines share, and a
# library Movie.mkv. Bound by assignment, not imported by name: pyflakes
# reads an imported fixture as unused and every test's parameter as a
# redefinition of it.
lib = _kept.lib


def _source(path, audio, fmt="matroska"):
    """
    Two seconds of video and an audio track for each (codec, language,
    channels, tags) in `audio`, tags being extra stream metadata or None.
    """
    cmd = ["ffmpeg", "-v", "error", "-y",
           "-f", "lavfi", "-i", "testsrc=size=64x36:rate=5:duration=2"]
    for n, _ in enumerate(audio):
        cmd += ["-f", "lavfi", "-i", f"sine=frequency={440 + 110 * n}:duration=2"]
    cmd += ["-map", "0:v"]
    for n, (codec, language, channels, tags) in enumerate(audio):
        cmd += ["-map", f"{n + 1}:a", f"-c:a:{n}", codec, f"-ac:a:{n}", str(channels),
                f"-metadata:s:a:{n}", f"language={language}"]
        for key, value in (tags or {}).items():
            cmd += [f"-metadata:s:a:{n}", f"{key}={value}"]
    cmd += ["-c:v", "mpeg4", "-f", fmt, str(path)]
    subprocess.run(cmd, check=True)
    return path


def _codecs(path):
    """(codec, channels, language) of each audio stream, in order."""
    return [(codec, channels, language) for codec, channels, language, _ in _audio(path)]


def _sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _job(lib, job_id):
    from app.database.models import Ac3ForgeJob

    with lib.Session() as db:
        job = db.get(Ac3ForgeJob, job_id)
        return job.status, job.error_message, job.ac3_marked


def _media_path(lib):
    from app.database.models import MediaFile
    from pathlib import Path

    with lib.Session() as db:
        return Path(db.query(MediaFile).one().path)


def _forge_then_drop_french(lib, audio):
    """
    Forge the file, then run the job that dropping French audio queues.
    Returns the add's job id and the file's sha256 after the job.
    """
    _source(lib.video, audio)
    _queue(lib)
    add_id, status = _forge(lib)
    assert status == "success"

    _drop_french(lib, "keep_audio_languages")
    assert _queue(lib) is not None, "dropping French audio queued nothing"
    assert _run_queued(lib) == ("success", None)
    return add_id, _sha(lib.video)


FRENCH_AAC_51 = ("aac", "fre", 6, None)
ENGLISH_AC3_51 = ("ac3", "eng", 6, None)


@needs_ffmpeg
def test_undo_leaves_the_files_own_ac3_when_a_job_dropped_the_forged_one(lib):
    """The reproduction. The English AC3 was the original's; it stays."""
    add_id, after_job = _forge_then_drop_french(lib, [FRENCH_AAC_51, ENGLISH_AC3_51])
    assert _codecs(lib.video) == [("ac3", 6, "eng")], "the job did not drop what the scenario needs"
    assert _job(lib, add_id)[2] is True, "the add was not recorded as marked"

    _forge(lib, undo_of=add_id)

    assert _job(lib, add_id)[:2] == ("undone", None)
    assert _codecs(lib.video) == [("ac3", 6, "eng")], "undo removed the file's own AC3"
    assert _sha(lib.video) == after_job, "undo rewrote a file it had nothing to remove from"


@needs_ffmpeg
def test_undo_leaves_the_files_own_ac3_where_other_audio_would_have_hidden_the_loss(lib):
    """
    The same with an English stereo track first, so the only-audio refusal
    cannot be what saves the AC3: the last-track rule would have removed it
    and left the stereo track, a file that still plays. The AAC also carries
    the handler name an MP4 origin leaves on it, which the forged copy would
    inherit if the add did not replace it.
    """
    add_id, after_job = _forge_then_drop_french(lib, [
        ("aac", "eng", 2, None),
        ("aac", "fre", 6, {"handler_name": "SoundHandler"}),
        ENGLISH_AC3_51,
    ])
    assert _codecs(lib.video) == [("aac", 2, "eng"), ("ac3", 6, "eng")]

    _forge(lib, undo_of=add_id)

    assert _job(lib, add_id)[:2] == ("undone", None)
    assert _codecs(lib.video) == [("aac", 2, "eng"), ("ac3", 6, "eng")]
    assert _sha(lib.video) == after_job


@needs_ffmpeg
def test_an_unmarked_undo_refuses_to_remove_the_files_only_audio(lib, monkeypatch):
    """
    An add from before the marker, simulated by the add command without it.
    Nothing can tell this job its AC3 is gone, so the last-track rule takes
    the English AC3 for it, and the only-audio refusal is what stops the
    file losing its sound.
    """
    real = lib.forge.build_add_ac3_command

    def unmarked(*args, **kwargs):
        cmd = real(*args, **kwargs)
        at = next(n for n, arg in enumerate(cmd) if arg.startswith("handler_name="))
        return cmd[:at - 1] + cmd[at + 1:]

    monkeypatch.setattr(lib.forge, "build_add_ac3_command", unmarked)
    add_id, after_job = _forge_then_drop_french(lib, [FRENCH_AAC_51, ENGLISH_AC3_51])
    assert _job(lib, add_id)[2] is False

    _forge(lib, undo_of=add_id)

    status, error, _ = _job(lib, add_id)
    assert status == "undo_failed"
    assert "only audio track" in error, error
    assert _sha(lib.video) == after_job, "the file was changed"


@needs_ffmpeg
def test_the_marker_is_not_read_as_forced_sdh_or_dub(lib):
    """
    probe applies its forced, SDH and dub patterns to every tag value, so a
    marker that matched one would change how the decision engine treats the
    forged track.
    """
    from app.config import settings as app_settings
    from app.core.forge import FORGE_AC3_HANDLER_NAME
    from app.core.probe import extract_tracks, probe_file

    _source(lib.video, [("aac", "eng", 6, None)])
    _queue(lib)
    _forge(lib)

    ac3 = [t for t in extract_tracks(probe_file(str(lib.video), app_settings.FFPROBE_PATH))
           if t["codec"] == "ac3"]
    assert [t["handler_name"] for t in ac3] == [FORGE_AC3_HANDLER_NAME]
    assert [(t["is_forced"], t["is_hearing_impaired"], t["is_dub"]) for t in ac3] \
        == [(False, False, False)]


def _prefer_mp4(lib):
    from app.database.models import AppSetting

    with lib.Session() as db:
        db.merge(AppSetting(key="prefer_mp4_container", value=json.dumps(True)))
        db.commit()


@needs_ffmpeg
@pytest.mark.parametrize("start", ["matroska", "mp4"])
def test_the_marker_outlives_a_job_that_writes_mp4(lib, start):
    """
    FORGE_AC3_HANDLER_NAME's reason for being a handler name: it is the tag
    that survives a job from Matroska to MP4, where a tag of our own is
    lost, and from MP4 to MP4, where a title is. Lost, a marked undo would
    take the AC3 for already removed and leave it in the file.
    """
    if start == "mp4":
        lib.video.unlink()
        lib.video = lib.video.with_suffix(".mp4")
    _source(lib.video, [("aac", "eng", 6, None), ("aac", "fre", 2, None)], fmt=start)
    _prefer_mp4(lib)
    _queue(lib)
    add_id, _ = _forge(lib)
    assert _job(lib, add_id)[2] is True

    _drop_french(lib, "keep_audio_languages")
    assert _queue(lib) is not None
    assert _run_queued(lib) == ("success", None)
    lib.video = _media_path(lib)
    assert lib.video.suffix == ".mp4"
    assert _codecs(lib.video) == [("aac", 6, "eng"), ("ac3", 6, "eng")]

    _forge(lib, undo_of=add_id)

    assert _job(lib, add_id)[:2] == ("undone", None)
    assert _codecs(lib.video) == [("aac", 6, "eng")], "the forged AC3 is still there"


@needs_ffmpeg
@pytest.mark.parametrize("suffix, fmt", [(".avi", "avi"), (".ts", "mpegts")])
def test_a_container_that_keeps_no_handler_name_falls_back_to_the_last_track(lib, suffix, fmt):
    """
    AVI and MPEG-TS drop the marker the add writes. Marked regardless, the
    undo would find no marker and take the AC3 for already removed. The
    probe after the add finds none, so the job stays unmarked and the undo
    removes the last audio track as it always did.
    """
    lib.video.unlink()
    lib.video = lib.video.with_suffix(suffix)
    _source(lib.video, [("aac", "eng", 6, None)], fmt=fmt)
    _queue(lib)
    add_id, _ = _forge(lib)
    assert _codecs(lib.video)[-1][:2] == ("ac3", 6), "the forge added nothing"
    assert _job(lib, add_id)[2] is False

    _forge(lib, undo_of=add_id)

    assert _job(lib, add_id)[:2] == ("undone", None)
    assert [codec for codec, _, _ in _codecs(lib.video)] == ["aac"]
