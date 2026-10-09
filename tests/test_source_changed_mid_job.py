"""
A file that changes on disk while its job is running.

Sonarr and Radarr write to the library whenever they like: an upgrade
replaces an episode, a rename moves it, a delete removes it. A job that has
already read the old file used to carry on regardless and swap its output
into place, so every one of those ended as a success that had done damage:

  • an upgrade at the same name was overwritten by a remux of the old file;
  • an upgrade landing during an MKV-to-MP4 conversion was then DELETED, by
    the step that removes the original after a container change;
  • a deleted file was written back;
  • a renamed file left two copies of the episode, under both names.

The job now records the source's size and mtime when it starts, and the
same for the name a container change writes to, and checks them twice:
before the revert capture, and after the output has been copied beside its
destination, just before it replaces anything. A mismatch cancels the job
with nothing written, and whatever is at the path now is decided afresh.

Everything here runs the real worker against real FFmpeg and a file-backed
database (the worker does its database work on executor threads). The
change is injected at a chosen point rather than raced: inside the revert
capture call, which runs after the first check and before the copy, or
just before the first check. A fixed point is what makes each test pin one
check rather than whichever happened to fire.

Confirmed unprotected before these tests were written: 19 mutations to the
new code, each run against the whole 1711-test suite, all 19 survived. The
runner never calling the swap hook, or ignoring its answer; either adapter
not passing it through; the worker's hook answering None, or any one of
the four calls in _run_job not passing it; the check before capture
removed; the identity reduced to size alone, or to mtime alone; the
output-name check dropped; the cancellation recorded as a failure; the
re-evaluation skipped; the captured revert sidecar kept; the scan stamp not
reset; the subtitle files this job extracted kept; and the original
deleted after a conversion without checking it is still the file the job
read. 19 applied, 19 killed, by this file and by the swap-hook tests in
test_staging_hook.py, test_audio_transcode_retry.py and
test_source_file_preservation.py.

A file gone before its job's turn came later and is covered under "Gone
before its turn"; its mutation record is in test_job_preflight.py.
"""
import asyncio
import glob
import hashlib
import os
import shutil
import subprocess
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

import app.core.worker as worker
import app.database.session as session_module
from app.config import settings as app_settings
from app.core import revert_capture, scanner
from app.database.models import (AppSetting, Base, MediaFile, NotificationState,
                                 PlannedAction, QueueItem, RevertPoint)

pytestmark = pytest.mark.skipif(
    shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None,
    reason="needs real ffmpeg and ffprobe",
)


# ── Harness ──────────────────────────────────────────────────────────────────

@pytest.fixture
def lib(tmp_path, monkeypatch):
    """
    A library directory, a file-backed database every session factory in
    the job's path points at, and scratch temp and recycle directories.

    revert_capture and recycle import SessionLocal from the session module
    at call time, so it is patched there as well as on the worker.
    """
    engine = create_engine(f"sqlite:///{tmp_path / 'remuxarr.db'}",
                           connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine)
    monkeypatch.setattr(worker, "SessionLocal", factory)
    monkeypatch.setattr(session_module, "SessionLocal", factory)
    monkeypatch.setattr(app_settings, "TEMP_DIR", str(tmp_path / "tmp"))
    monkeypatch.setattr(app_settings, "RECYCLE_DIR", str(tmp_path / "recycle"))
    (tmp_path / "recycle").mkdir()
    (tmp_path / "library").mkdir()
    (tmp_path / "downloads").mkdir()

    rig = SimpleNamespace(Session=factory, root=tmp_path,
                          library=tmp_path / "library",
                          downloads=tmp_path / "downloads")
    _settings(rig, dry_run_mode=False)
    yield rig
    engine.dispose()


def _settings(rig, **values):
    import json
    with rig.Session() as db:
        for key, value in values.items():
            db.merge(AppSetting(key=key, value=json.dumps(value)))
        db.commit()


def _make(path, *, title="OLD", seconds=1, audio=("eng",), subtitle=False):
    """
    A real file. The container comes from the extension; an MP4 is written
    without faststart, so under the defaults it is rewritten in place.
    """
    path = str(path)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    cmd = ["ffmpeg", "-v", "error", "-y",
           "-f", "lavfi", "-i", f"testsrc=size=160x120:rate=10:duration={seconds}"]
    for n, _language in enumerate(audio):
        cmd += ["-f", "lavfi", "-i", f"sine=frequency={440 + 220 * n}:duration={seconds}"]
    if subtitle:
        srt = path + ".input.srt"
        with open(srt, "w") as f:
            f.write("1\n00:00:00,000 --> 00:00:00,900\nHello\n")
        cmd += ["-i", srt]
    cmd += ["-map", "0:v"]
    for n, language in enumerate(audio):
        cmd += ["-map", f"{n + 1}:a", f"-metadata:s:a:{n}", f"language={language}"]
    if subtitle:
        cmd += ["-map", f"{len(audio) + 1}:s", "-metadata:s:s:0", "language=eng",
                "-c:s", "srt"]
    cmd += ["-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p",
            "-c:a", "aac", "-metadata", f"title={title}", path]
    subprocess.run(cmd, check=True)
    if subtitle:
        os.remove(path + ".input.srt")
    return path


def _sha(path):
    with open(path, "rb") as f:
        return hashlib.sha256(f.read()).hexdigest()


class _WS:
    async def broadcast_json(self, payload):
        pass


def _queue(rig, path, **ids):
    with rig.Session() as db:
        item = scanner.queue_single_file(db, str(path), **ids)
        assert item is not None and item.status == "pending", "fixture queued nothing"
        return item.id


def _run_next(rig):
    """Claim and run the next job the way the worker loop does."""
    async def go():
        loop = asyncio.get_running_loop()
        job_id = worker._claim_next()
        assert job_id is not None
        await worker._run_and_broadcast(job_id, _WS(), loop)
        return job_id
    return asyncio.run(go())


def _job(rig, job_id):
    with rig.Session() as db:
        job = db.get(QueueItem, job_id)
        return SimpleNamespace(status=job.status, error=job.error_message or "",
                               file_id=job.file_id)


def _jobs_for(rig, file_id):
    with rig.Session() as db:
        return [(j.id, j.status, j.sonarr_series_id)
                for j in db.query(QueueItem).filter(QueueItem.file_id == file_id)
                                            .order_by(QueueItem.id)]


def _failures_counted(rig):
    with rig.Session() as db:
        state = db.get(NotificationState, 1)
        return state.consecutive_failures if state else 0


def _leftovers(rig):
    return (glob.glob(str(rig.root / "**" / "*.part"), recursive=True)
            + glob.glob(str(rig.root / "**" / "*.remuxarr_tmp"), recursive=True))


def during_the_copy(monkeypatch, change):
    """
    Make `change` happen once, inside the revert capture call: after the
    check before capture has passed, before the output is copied to the
    destination. Only the swap check can see it.
    """
    real = revert_capture.capture
    state = {"done": False, "captured": None}

    async def capture_then_change(**kwargs):
        result = await real(**kwargs)
        if not state["done"]:
            state["done"] = True
            state["captured"] = result[0]
            change()
        return result

    monkeypatch.setattr(revert_capture, "capture", capture_then_change)
    return state


def just_before_capture(monkeypatch, change):
    """
    Make `change` happen once, after FFmpeg has finished reading the source
    and immediately before the worker's before_staging hook runs.
    """
    state = {"done": False}

    def wrap(real):
        async def run(**kwargs):
            hook = kwargs["before_staging"]

            async def change_then_hook(produced):
                if not state["done"]:
                    state["done"] = True
                    change()
                return await hook(produced)

            return await real(**{**kwargs, "before_staging": change_then_hook})
        return run

    monkeypatch.setattr(worker, "execute_ffmpeg", wrap(worker.execute_ffmpeg))
    monkeypatch.setattr(worker, "execute_ffmpeg_combined",
                        wrap(worker.execute_ffmpeg_combined))
    return state


def assert_cancelled_cleanly(rig, job_id, why):
    job = _job(rig, job_id)
    assert job.status == "cancelled", (job.status, job.error)
    assert why in job.error, job.error
    assert "Nothing was written" in job.error
    assert _failures_counted(rig) == 0, "counted towards the failure email"
    assert _leftovers(rig) == []


# ── Nothing changes: nothing new happens ─────────────────────────────────────

@pytest.mark.parametrize("name", ["Show - S01E01.mp4", "Show - S01E01.mkv"])
def test_an_untouched_file_is_processed_as_before(lib, name):
    """
    The control. The checks must not fire on a job's own activity: an
    in-place rewrite, and a conversion that deletes its original.
    """
    source = _make(lib.library / name)
    job_id = _queue(lib, source)

    _run_next(lib)

    assert _job(lib, job_id).status == "success", _job(lib, job_id).error
    if name.endswith(".mkv"):
        assert not os.path.exists(source)
        assert os.path.exists(source[:-4] + ".mp4")
    assert _leftovers(lib) == []


# ── The four ways the library changes under a job ────────────────────────────

def test_an_upgrade_at_the_same_name_survives_and_is_decided_again(lib, monkeypatch):
    """
    The one that used to destroy the upgrade outright. The webhook that
    announced it found this job running and queued nothing, so the job's
    end is what has to look at the new file — and it must still know which
    Sonarr series it belongs to.
    """
    source = _make(lib.library / "Show - S01E01.mp4")
    upgrade = _make(lib.downloads / "upgrade.mp4", title="NEW", seconds=2)
    upgrade_sha = _sha(upgrade)
    job_id = _queue(lib, source, sonarr_series_id=7)
    during_the_copy(monkeypatch, lambda: os.replace(upgrade, source))

    _run_next(lib)

    assert_cancelled_cleanly(lib, job_id, "was replaced or modified")
    assert _sha(source) == upgrade_sha, "the upgrade was overwritten"
    later = _jobs_for(lib, _job(lib, job_id).file_id)
    assert later[-1][1:] == ("pending", 7), later


def test_an_upgrade_during_a_conversion_is_neither_converted_over_nor_deleted(
        lib, monkeypatch):
    source = _make(lib.library / "Show - S01E02.mkv")
    upgrade = _make(lib.downloads / "upgrade.mkv", title="NEW", seconds=2)
    upgrade_sha = _sha(upgrade)
    job_id = _queue(lib, source)
    during_the_copy(monkeypatch, lambda: os.replace(upgrade, source))

    _run_next(lib)

    assert_cancelled_cleanly(lib, job_id, "was replaced or modified")
    assert _sha(source) == upgrade_sha
    assert not os.path.exists(source[:-4] + ".mp4"), "the old file was converted anyway"
    assert _jobs_for(lib, _job(lib, job_id).file_id)[-1][1] == "pending"


def test_a_file_deleted_mid_job_stays_deleted(lib, monkeypatch):
    source = _make(lib.library / "Show - S01E03.mp4")
    job_id = _queue(lib, source)
    during_the_copy(monkeypatch, lambda: os.remove(source))

    _run_next(lib)

    assert_cancelled_cleanly(lib, job_id, "is no longer there")
    assert not os.path.exists(source), "the deleted file was written back"
    assert [j[1] for j in _jobs_for(lib, _job(lib, job_id).file_id)] == ["cancelled"]


def test_a_file_renamed_mid_job_is_not_left_under_both_names(lib, monkeypatch):
    source = _make(lib.library / "Show - S01E04 - TBA.mp4")
    renamed = source.replace(" - TBA", " - The Title")
    original_sha = _sha(source)
    job_id = _queue(lib, source)
    during_the_copy(monkeypatch, lambda: os.rename(source, renamed))

    _run_next(lib)

    assert_cancelled_cleanly(lib, job_id, "is no longer there")
    assert not os.path.exists(source), "a second copy appeared under the old name"
    assert _sha(renamed) == original_sha
    assert [j[1] for j in _jobs_for(lib, _job(lib, job_id).file_id)] == ["cancelled"]


def test_a_rename_the_webhook_reports_mid_job_is_picked_up_under_the_new_name(
        lib, monkeypatch):
    """
    With the Rename webhook arriving while the job runs, the file's record
    has moved to the new name by the time the job stops (see
    test_rename_tracking.py). The re-evaluation then reads the record's
    path, so the file is queued again under its new name, on the same
    record, rather than dropped.
    """
    source = _make(lib.library / "Show - S01E07 - TBA.mp4")
    renamed = source.replace(" - TBA", " - The Title")
    job_id = _queue(lib, source)

    def sonarr_renames_and_reports_it():
        os.rename(source, renamed)
        with lib.Session() as db:
            assert scanner.move_renamed_file(db, source, renamed) == "moved"

    during_the_copy(monkeypatch, sonarr_renames_and_reports_it)

    _run_next(lib)

    assert_cancelled_cleanly(lib, job_id, "is no longer there")
    file_id = _job(lib, job_id).file_id
    with lib.Session() as db:
        assert db.get(MediaFile, file_id).path == renamed
    assert [j[1] for j in _jobs_for(lib, file_id)] == ["cancelled", "pending"]
    assert not os.path.exists(source)


def test_a_file_appearing_at_the_output_name_is_not_overwritten(lib, monkeypatch):
    """
    The source untouched, and something else written to the name the
    conversion is about to use — Radarr importing an MP4 release of the
    same film, say. Only the output-name check can see this.
    """
    source = _make(lib.library / "Movie (2020).mkv")
    source_sha = _sha(source)
    newcomer = _make(lib.downloads / "other.mp4", title="OTHER", seconds=2)
    newcomer_sha = _sha(newcomer)
    target = source[:-4] + ".mp4"
    job_id = _queue(lib, source)
    during_the_copy(monkeypatch, lambda: shutil.copy2(newcomer, target))

    _run_next(lib)

    assert_cancelled_cleanly(lib, job_id, "appeared or changed at the name")
    assert _sha(target) == newcomer_sha, "the file at the output name was overwritten"
    assert _sha(source) == source_sha


# ── Gone before its turn ─────────────────────────────────────────────────────

def test_a_file_gone_before_its_turn_is_cancelled_not_failed(lib):
    """
    Sonarr upgraded the episode to a release with another extension, or
    deleted it, while the job waited in the queue. The job used to fail
    with "File not found on disk" and count towards the failure email.
    """
    source = _make(lib.library / "Show" / "Show - S05E01.mkv")
    job_id = _queue(lib, source)
    os.remove(source)

    _run_next(lib)

    job = _job(lib, job_id)
    assert job.status == "cancelled", (job.status, job.error)
    assert "no longer on disk" in job.error
    assert _failures_counted(lib) == 0
    assert [j[1] for j in _jobs_for(lib, job.file_id)] == ["cancelled"]


def test_a_file_gone_with_its_folder_still_fails_and_is_counted(lib):
    """The shape of an unmounted share: a failure, and the email counts it."""
    folder = lib.library / "Show" / "Season 02"
    source = _make(folder / "Show - S02E01.mkv")
    job_id = _queue(lib, source)
    shutil.rmtree(folder)

    _run_next(lib)

    job = _job(lib, job_id)
    assert job.status == "failed", (job.status, job.error)
    assert "check that it is mounted" in job.error
    assert _failures_counted(lib) == 1


# ── What counts as changed ───────────────────────────────────────────────────

def test_an_in_place_edit_that_keeps_the_size_is_noticed(lib, monkeypatch):
    """
    A tag rewritten in place without changing the file's length, which is
    what mkvpropedit-style editors do. Size alone cannot see it.
    """
    source = _make(lib.library / "Show - S01E05.mp4", title="TITLE-AAAAAAAA")
    job_id = _queue(lib, source)
    edited = {}

    def retag():
        with open(source, "rb") as f:
            data = f.read()
        assert data.count(b"TITLE-AAAAAAAA") == 1
        size = len(data)
        with open(source, "r+b") as f:
            f.write(data.replace(b"TITLE-AAAAAAAA", b"TITLE-BBBBBBBB"))
        st = os.stat(source)
        os.utime(source, (st.st_atime, st.st_mtime + 10))
        assert os.path.getsize(source) == size
        edited["sha"] = _sha(source)

    during_the_copy(monkeypatch, retag)

    _run_next(lib)

    assert_cancelled_cleanly(lib, job_id, "was replaced or modified")
    assert _sha(source) == edited["sha"]


def test_an_upgrade_carrying_the_old_mtime_is_noticed(lib, monkeypatch):
    """
    Sonarr can set a file's mtime to the episode's air date, so an upgrade
    can arrive with exactly the mtime of the file it replaces. Mtime alone
    cannot see it.
    """
    source = _make(lib.library / "Show - S01E06.mp4")
    upgrade = _make(lib.downloads / "upgrade.mp4", title="NEW", seconds=2)
    upgrade_sha = _sha(upgrade)
    job_id = _queue(lib, source)

    def import_with_air_date():
        st = os.stat(source)
        os.replace(upgrade, source)
        os.utime(source, (st.st_atime, st.st_mtime))
        assert os.stat(source).st_mtime == st.st_mtime

    during_the_copy(monkeypatch, import_with_air_date)

    _run_next(lib)

    assert_cancelled_cleanly(lib, job_id, "was replaced or modified")
    assert _sha(source) == upgrade_sha


# ── Both execution paths ─────────────────────────────────────────────────────

def _planned(rig, job_id):
    with rig.Session() as db:
        return {a.action_type for a in db.query(PlannedAction)
                                          .filter(PlannedAction.queue_item_id == job_id)}


def test_the_combined_path_is_checked_too(lib, monkeypatch):
    """Remux and subtitle extraction in one FFmpeg run; nothing of it lands."""
    source = _make(lib.library / "Show - S02E01.mkv", subtitle=True)
    upgrade = _make(lib.downloads / "upgrade.mkv", title="NEW", seconds=2, subtitle=True)
    upgrade_sha = _sha(upgrade)
    job_id = _queue(lib, source)
    assert {"extract_subtitle", "change_container"} <= _planned(lib, job_id)
    during_the_copy(monkeypatch, lambda: os.replace(upgrade, source))

    _run_next(lib)

    assert_cancelled_cleanly(lib, job_id, "was replaced or modified")
    assert _sha(source) == upgrade_sha
    assert glob.glob(str(lib.library / "*.srt")) == []


def test_subtitles_extracted_before_a_refused_remux_are_removed(lib, monkeypatch):
    """
    The two-pass path writes its .srt files before the remux, and here the
    remux is what gets refused. A sidecar extracted from the old release
    must not be left next to the new one. A video file with a text
    subtitle and no audio needs extraction and nothing else, which is what
    takes this path.
    """
    _settings(lib, prefer_mp4_container=False)
    source = _make(lib.library / "Clip.mkv", audio=(), subtitle=True)
    upgrade = _make(lib.downloads / "upgrade.mkv", title="NEW", seconds=2,
                    audio=(), subtitle=True)
    job_id = _queue(lib, source)
    assert _planned(lib, job_id) == {"extract_subtitle"}
    during_the_copy(monkeypatch, lambda: os.replace(upgrade, source))

    _run_next(lib)

    assert_cancelled_cleanly(lib, job_id, "was replaced or modified")
    assert glob.glob(str(lib.library / "*.srt")) == []


# ── Around the revert capture ────────────────────────────────────────────────

def test_with_a_revert_point_required_a_vanished_source_is_cancelled_not_failed(
        lib, monkeypatch):
    """
    Capture probes the source to record it. Gone, the probe fails, and
    with a revert point required that refusal used to fail the job before
    the swap check was ever reached — counted, emailed, Failed tab.
    """
    _settings(lib, revert_enabled=True, revert_require_point=True)
    source = _make(lib.library / "Show - S03E01.mp4", audio=("eng", "fre"))
    job_id = _queue(lib, source)
    just_before_capture(monkeypatch, lambda: os.remove(source))

    _run_next(lib)

    assert_cancelled_cleanly(lib, job_id, "is no longer there")


def test_a_revert_sidecar_captured_before_the_change_is_discarded(lib, monkeypatch):
    """
    The capture ran against the file the job read and wrote a sidecar for
    it; the job then did not happen. A recorded revert point would offer to
    restore tracks into a file they never came from.
    """
    _settings(lib, revert_enabled=True)
    source = _make(lib.library / "Show - S03E02.mp4", audio=("eng", "fre"))
    upgrade = _make(lib.downloads / "upgrade.mp4", title="NEW", seconds=2)
    job_id = _queue(lib, source)
    state = during_the_copy(monkeypatch, lambda: os.replace(upgrade, source))

    _run_next(lib)

    assert state["captured"] is not None, "fixture captured no sidecar"
    assert_cancelled_cleanly(lib, job_id, "was replaced or modified")
    assert not os.path.exists(state["captured"].sidecar_path)
    with lib.Session() as db:
        assert db.query(RevertPoint).count() == 0


# ── Deciding again ───────────────────────────────────────────────────────────

def test_a_file_that_cannot_be_probed_now_is_left_for_the_next_scan(lib, monkeypatch):
    """
    Whatever replaced it is not readable media (still being written,
    say), so the re-evaluation cannot decide anything. The scan stamp is
    what makes the next delta scan look again rather than skip it as
    unchanged.
    """
    source = _make(lib.library / "Show - S04E01.mp4")
    job_id = _queue(lib, source)

    def half_written():
        with open(source, "wb") as f:
            f.write(b"not a video yet")

    during_the_copy(monkeypatch, half_written)

    _run_next(lib)

    assert_cancelled_cleanly(lib, job_id, "was replaced or modified")
    with lib.Session() as db:
        media = db.get(MediaFile, _job(lib, job_id).file_id)
        assert (media.size, media.mtime) == (-1, -1.0)
    assert [j[1] for j in _jobs_for(lib, _job(lib, job_id).file_id)] == ["cancelled"]
