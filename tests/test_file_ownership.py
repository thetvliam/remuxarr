"""
A job, a revert and AC3 Forge keep a file's owner, group and permissions.

The bug this file exists for (issue #65)
----------------------------------------
Everything a run writes into a library is copied to a .part beside its
destination and swapped in (subprocess_runner.run_staged_subprocess). The
.part was created by whoever runs Remuxarr, root in the image, with the
mode its umask leaves, so a file owned by Unraid's nobody:users (99:100)
with mode 664 came back owned by root with 644, after a job in place, a
conversion to MP4, a revert, and as the subtitle a job extracted beside
it. A program running as that user, Plex or Sonarr say, could then read
the file but no longer change it. Reproduced with the real worker before
the fix, every one of those as 0:0 0644.

Each .part is now given the owner, group and permission bits of the file
the run was handed (the processed file, for a revert) before the swap.

Changing the owner needs root, so the owner half of these tests means
something only when they run as root: the files are then given to 99:100
first. Run as anyone else, as CI runs them, the files stay the runner's
and only their mode is tested for real; test_the_part_is_given_the_owner
_and_group_by_number covers the owner there. The mode used is one the
runner's umask would not give a new file.

All but that one fail against the code before the fix with the file left
as 0:0 0644, run as root; it fails there because the function it calls
did not exist, so its evidence is the mutants below.

Verified by mutation, 11 applied, 11 killed, all eleven surviving the suite
as it stood first. Run as root and again as an ordinary user, every mutant
is killed both ways, by a file left with the wrong owner or mode unless
said otherwise:

  • No os.chown                           → killed, run as root by every
                                            test; as another user by
                                            test_the_part_is_given_the_
                                            owner_and_group_by_number and
                                            test_a_refused_owner_change_
                                            does_not_fail_the_job (nothing
                                            logged)
  • No os.chmod                           → killed, by every test
  • Each output's owner read from its own
    final path                            → killed, by the conversion and
                                            both extraction tests, whose
                                            outputs do not exist yet
  • Only the first output given it        → killed, only by test_a_subtitle_
                                            extracted_during_a_remux_takes_
                                            the_media_files
  • A refused os.chown failing the run    → killed, only by test_a_refused_
                                            owner_change_does_not_fail_the_
                                            job (the job did not run)
  • The revert taking the sidecar's       → killed, only by test_a_revert_
                                            gives_them_back
  • The setuid, setgid and sticky bits
    passed on                             → killed, only by test_the_part_
                                            is_given_the_owner_and_group_
                                            by_number
  • execute_ffmpeg passing no path        → killed, by the job, conversion,
                                            revert and refusal tests, and
                                            the extraction on its own,
                                            whose remux it runs
  • execute_subtitle_extraction passing
    no path                               → killed, only by test_a_subtitle_
                                            extracted_on_its_own_takes_the_
                                            media_files
  • execute_ffmpeg_combined passing no
    path                                  → killed, only by test_a_subtitle_
                                            extracted_during_a_remux_takes_
                                            the_media_files
  • AC3 Forge passing no path             → killed, only by test_an_ac3_
                                            forge_add_and_its_undo_keep_them

No equivalent mutants.
"""
import asyncio
import json
import logging
import os
import stat
import subprocess

from tests import test_quicktime_files as _qt
from tests.test_quicktime_files import needs_ffmpeg

# A fixture shared by assignment rather than imported, which pyflakes would
# report as unused.
rig = _qt.rig

ROOT = os.geteuid() == 0
OWNER = (99, 100)


def _umask_mode():
    old = os.umask(0)
    os.umask(old)
    return 0o666 & ~old


# Not what a file written with the runner's umask gets, so a mode that
# survives a run was kept, not merely written again.
MODE = 0o660 if _umask_mode() != 0o660 else 0o640


def _set(rig, key, value):
    from app.database.models import AppSetting

    with rig.Session() as db:
        db.merge(AppSetting(key=key, value=json.dumps(value)))
        db.commit()


def _film(path, *, audio=("eng", "ger"), subtitle=False, channels=2):
    """A Matroska with a video, one audio track per language in `audio`,
    and, with `subtitle`, an English SubRip track."""
    cmd = ["ffmpeg", "-v", "error", "-y",
           "-f", "lavfi", "-i", "testsrc=size=64x36:rate=10:duration=1"]
    maps = ["-map", "0:v"]
    meta = []
    for n, language in enumerate(audio):
        cmd += ["-f", "lavfi", "-i", f"sine=frequency={440 + 110 * n}:duration=1"]
        maps += ["-map", f"{n + 1}:a"]
        meta += [f"-metadata:s:a:{n}", f"language={language}"]
    if subtitle:
        srt = path.parent.parent / "in.srt"  # outside the library
        srt.write_text("1\n00:00:00,100 --> 00:00:00,900\nHello\n", encoding="utf-8")
        cmd += ["-i", str(srt)]
        maps += ["-map", f"{len(audio) + 1}:s"]
        meta += ["-metadata:s:s:0", "language=eng"]
    cmd += maps + ["-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p",
                   "-c:a", "aac", "-ac", str(channels), "-c:s", "srt", *meta,
                   "-f", "matroska", str(path)]
    subprocess.run(cmd, check=True)
    return path


def _hand_over(path):
    """
    Give the file MODE and, run as root, Unraid's nobody:users. Returns the
    owner, group and mode a run has to leave on what it writes.
    """
    os.chmod(path, MODE)
    if ROOT:
        os.chown(path, *OWNER)
    st = os.stat(path)
    return st.st_uid, st.st_gid, MODE


def _owned(path):
    st = os.stat(path)
    return st.st_uid, st.st_gid, stat.S_IMODE(st.st_mode)


def _audio(path):
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "a", "-show_entries",
         "stream=codec_name:stream_tags=language", "-of", "csv=p=0", str(path)],
        capture_output=True, text=True, check=True).stdout
    return out.split()


def _run_job(rig, path):
    """Queue the file as a scan would and run the job. Returns what it
    planned and where the file is afterwards."""
    from app.core import scanner
    from app.database.models import MediaFile

    with rig.Session() as db:
        item = scanner.queue_single_file(db, str(path))
        assert item is not None and item.status == "pending", "nothing was queued"
        planned = [a.action_type for a in item.planned_actions]
        file_id = item.file_id

    async def go():
        job_id = rig.worker._claim_next()
        await rig.worker._run_and_broadcast(job_id, _qt._WS(), asyncio.get_running_loop())
    asyncio.run(go())

    with rig.Session() as db:
        return planned, db.get(MediaFile, file_id).path


@needs_ffmpeg
def test_a_job_keeps_the_files_owner_group_and_permissions(rig):
    """A remux in place, through execute_ffmpeg."""
    source = _film(rig.library / "Film.mkv")
    expected = _hand_over(source)

    planned, after = _run_job(rig, source)

    assert planned == ["copy_track", "drop_track"]
    assert _audio(after) == ["aac,eng"], "the job did not run"
    assert _owned(after) == expected


@needs_ffmpeg
def test_a_conversion_to_mp4_keeps_them(rig):
    """The output has a name of its own, which did not exist before."""
    _set(rig, "prefer_mp4_container", True)
    source = _film(rig.library / "works.mkv")
    expected = _hand_over(source)

    _, after = _run_job(rig, source)

    assert after.endswith("works.mp4")
    assert _owned(after) == expected


@needs_ffmpeg
def test_a_subtitle_extracted_during_a_remux_takes_the_media_files(rig):
    """
    One FFmpeg run writes the file and the subtitle beside it
    (execute_ffmpeg_combined). A subtitle belongs to whoever the media file
    does.
    """
    _set(rig, "extract_text_subtitles_to_srt", True)
    source = _film(rig.library / "Show.mkv", subtitle=True)
    expected = _hand_over(source)

    planned, after = _run_job(rig, source)

    assert "extract_subtitle" in planned and "drop_track" in planned
    [srt] = rig.library.glob("Show*.srt")
    assert _owned(srt) == expected
    assert _owned(after) == expected


@needs_ffmpeg
def test_a_subtitle_extracted_on_its_own_takes_the_media_files(rig):
    """
    A video with a subtitle and nothing else to do is extracted first, by
    execute_subtitle_extraction, and remuxed after.
    """
    _set(rig, "extract_text_subtitles_to_srt", True)
    source = _film(rig.library / "Clip.mkv", audio=(), subtitle=True)
    expected = _hand_over(source)

    planned, after = _run_job(rig, source)

    assert planned == ["extract_subtitle"]
    [srt] = rig.library.glob("Clip*.srt")
    assert _owned(srt) == expected
    assert _owned(after) == expected


@needs_ffmpeg
def test_a_revert_gives_them_back(rig):
    """The restore is written from the processed file, so it takes that
    file's owner, which the job kept: the original's."""
    from app.core.revert_restore import restore_revert_point
    from app.database.models import RevertPoint

    source = _film(rig.library / "Film.mkv")
    expected = _hand_over(source)
    _run_job(rig, source)

    with rig.Session() as db:
        point_id = db.query(RevertPoint).one().id
    outcome = asyncio.run(restore_revert_point(point_id))

    assert outcome.success, outcome.error
    assert _audio(source) == ["aac,eng", "aac,ger"]
    assert _owned(source) == expected


@needs_ffmpeg
def test_an_ac3_forge_add_and_its_undo_keep_them(rig):
    """AC3 Forge builds its own command and runs it through the same
    staging."""
    from app.core import scanner
    from app.database.models import Ac3ForgeJob, MediaFile

    source = _film(rig.library / "Film.mkv", audio=("eng",), channels=6)
    expected = _hand_over(source)

    with rig.Session() as db:
        scanner.scan_library(db, [str(rig.library)], force_probe=True)
        job_id = rig.forge.queue_forge_job(db, db.query(MediaFile).one().id).id
    assert asyncio.run(rig.worker._process_next_forge(_qt._WS())) is True
    with rig.Session() as db:
        assert db.get(Ac3ForgeJob, job_id).status == "success"
    assert _audio(source) == ["aac,eng", "ac3,eng"]
    assert _owned(source) == expected

    with rig.Session() as db:
        job = db.get(Ac3ForgeJob, job_id)
        job.status, job.is_undo, job.progress = "undo_pending", True, 0.0
        db.commit()
    assert asyncio.run(rig.worker._process_next_forge(_qt._WS())) is True
    with rig.Session() as db:
        assert db.get(Ac3ForgeJob, job_id).status == "undone"
    assert _audio(source) == ["aac,eng"]
    assert _owned(source) == expected


def test_the_part_is_given_the_owner_and_group_by_number(tmp_path, monkeypatch):
    """
    The owner half of the tests above, for a runner that cannot give a
    file away: the ids reach os.chown, which is recorded rather than run.
    The source's setgid bit is not passed on: only permission bits are.
    """
    from app.core import subprocess_runner as runner

    source = tmp_path / "Film.mkv"
    source.write_bytes(b"original")
    os.chmod(source, MODE | stat.S_ISGID)
    assert os.stat(source).st_mode & stat.S_ISGID, "fixture: setgid not set"
    part = tmp_path / "job1.remuxarr_tmp.part"
    part.write_bytes(b"output")
    given = []
    monkeypatch.setattr(os, "chown", lambda path, uid, gid: given.append((path, uid, gid)))
    st = os.stat(source)
    monkeypatch.setattr(os, "stat", lambda path, *a, **k: os.stat_result(
        (st.st_mode, st.st_ino, st.st_dev, st.st_nlink, 4242, 4343,
         st.st_size, st.st_atime, st.st_mtime, st.st_ctime)))

    runner._give_outputs_ownership(
        [runner.StagedOutput(temp_path=str(tmp_path / "job1.remuxarr_tmp"),
                             final_path=str(source))],
        str(source))
    monkeypatch.undo()

    assert given == [(str(part), 4242, 4343)]
    assert stat.S_IMODE(os.stat(part).st_mode) == MODE


@needs_ffmpeg
def test_a_refused_owner_change_does_not_fail_the_job(rig, monkeypatch, caplog):
    """
    Run as anyone but root, giving a file to another user is refused. The
    job still succeeds, the mode is still copied, and the refusal is logged.
    """
    def refuse(path, uid, gid):
        raise PermissionError(1, "Operation not permitted", path)

    source = _film(rig.library / "Film.mkv")
    _hand_over(source)
    monkeypatch.setattr(os, "chown", refuse)

    with caplog.at_level(logging.WARNING, logger="app.core.subprocess_runner"):
        _, after = _run_job(rig, source)

    assert _audio(after) == ["aac,eng"], "the job did not run"
    assert stat.S_IMODE(os.stat(after).st_mode) == MODE
    refusals = [r.getMessage() for r in caplog.records
                if r.name == "app.core.subprocess_runner"]
    assert any("Could not give" in m and "Film.mkv" in m and "the owner" in m
               for m in refusals), refusals
