"""
Forge job completion and staged output copying.

Covers two pieces of behaviour that had no suite coverage:

  • finish_forge_job() — the terminal-state writer, including the undo status
    mapping. Its signature changed when the unused output_path parameter was
    removed, and nothing tested it.

  • _stage_parts() — the copy-and-fsync pass extracted so it could be moved off
    the event loop. The extraction preserved a subtle contract: part_paths is
    caller-owned and appended to as work completes, so a partially-finished run
    is still visible to the caller's OSError cleanup handler.

Grown from the ad-hoc scripts used to verify those changes.
"""
import asyncio
import os
import threading
import time

import pytest


from app.core.forge import finish_forge_job
from app.core.subprocess_runner import StagedOutput, _stage_parts


# ── finish_forge_job ─────────────────────────────────────────────────────────

@pytest.fixture
def db():
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    from app.database.models import Base

    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()

    # finish_forge_job opens its own session via SessionLocal; point that at
    # this engine so the test can observe what it wrote.
    import app.core.forge as forge_mod
    import app.database.session as session_mod
    original = session_mod.SessionLocal
    session_mod.SessionLocal = sessionmaker(bind=engine)
    forge_mod.SessionLocal = sessionmaker(bind=engine)
    yield session
    session_mod.SessionLocal = original
    forge_mod.SessionLocal = original


def _job(db, is_undo=False, progress=42.0):
    from app.database.models import Ac3ForgeJob, MediaFile

    name = f"{'undo' if is_undo else 'fwd'}-{id(object()):x}.mkv"
    mf = MediaFile(path=f"/m/{name}", filename=name, directory="/m", size=1, mtime=1.0)
    db.add(mf)
    db.commit()
    job = Ac3ForgeJob(file_id=mf.id, status="processing", is_undo=is_undo,
                      progress=progress)
    db.add(job)
    db.commit()
    return job.id


def _read(db, job_id):
    from app.database.models import Ac3ForgeJob

    db.expire_all()
    return db.get(Ac3ForgeJob, job_id)


def test_forward_success_marks_complete(db):
    jid = _job(db)
    finish_forge_job(jid, True, 123456789, None)

    job = _read(db, jid)
    assert job.status == "success"
    assert job.progress == 100.0
    assert job.output_size == 123456789
    assert job.error_message is None
    assert job.completed_at is not None


def test_forward_failure_does_not_force_progress_to_100(db):
    """A failed job should not read as having finished its work."""
    jid = _job(db, progress=42.0)
    finish_forge_job(jid, False, None, "ffmpeg exploded")

    job = _read(db, jid)
    assert job.status == "failed"
    assert job.progress == 42.0
    assert job.error_message == "ffmpeg exploded"


def test_undo_success_maps_to_undone(db):
    """Undo jobs get their own terminal states, not the forward ones."""
    jid = _job(db, is_undo=True)
    finish_forge_job(jid, True, 999, None)
    assert _read(db, jid).status == "undone"


def test_undo_failure_maps_to_undo_failed(db):
    jid = _job(db, is_undo=True)
    finish_forge_job(jid, False, None, "layout mismatch")

    job = _read(db, jid)
    assert job.status == "undo_failed"
    assert job.error_message == "layout mismatch"


def test_unknown_job_id_is_a_safe_noop(db):
    finish_forge_job(999999, True, 1, None)


def test_output_path_is_reachable_without_being_stored(db):
    """
    finish_forge_job deliberately takes no output_path: forge rewrites in
    place, so the path is always the source path and is already on the row.
    """
    jid = _job(db)
    finish_forge_job(jid, True, 555, None)

    job = _read(db, jid)
    assert job.media_file.path.startswith("/m/fwd-")


def test_stale_five_argument_call_raises(db):
    """The removed parameter must fail loudly, not silently shift output_size."""
    jid = _job(db)
    with pytest.raises(TypeError):
        finish_forge_job(jid, True, "/m/some.mkv", 555, None)


# ── _stage_parts ─────────────────────────────────────────────────────────────

def test_stage_parts_copies_and_leaves_final_untouched(tmp_path):
    temp = tmp_path / "src.tmp"
    temp.write_bytes(b"payload" * 1000)
    final = tmp_path / "out.mkv"

    parts: list[str] = []
    _stage_parts([StagedOutput(temp_path=str(temp), final_path=str(final))], parts)

    assert parts == [str(final) + ".part"]
    assert (tmp_path / "out.mkv.part").read_bytes() == b"payload" * 1000
    assert not final.exists(), "staging must not touch the destination"


def test_partial_failure_leaves_completed_parts_visible(tmp_path):
    """
    The contract the caller's OSError handler depends on: when output 2 of 3
    fails, part_paths must still contain output 1 so cleanup can remove it.
    """
    ok = tmp_path / "a.tmp"
    ok.write_bytes(b"x" * 100)

    outputs = [
        StagedOutput(temp_path=str(ok), final_path=str(tmp_path / "a.mkv")),
        StagedOutput(temp_path=str(tmp_path / "missing.tmp"),
                     final_path=str(tmp_path / "b.mkv")),
        StagedOutput(temp_path=str(ok), final_path=str(tmp_path / "c.mkv")),
    ]

    parts: list[str] = []
    with pytest.raises(OSError):
        _stage_parts(outputs, parts)

    assert parts == [str(tmp_path / "a.mkv") + ".part"]
    assert os.path.exists(parts[0]), "the completed .part must exist for cleanup"
    assert not os.path.exists(str(tmp_path / "c.mkv") + ".part")


def test_stage_parts_runs_off_the_event_loop(tmp_path):
    """
    Staging must execute on a worker thread. Uses a small file — this asserts
    the threading contract, not throughput; the large-file responsiveness
    measurement stays a manual tool.
    """
    temp = tmp_path / "src.tmp"
    temp.write_bytes(b"z" * 4096)
    outputs = [StagedOutput(temp_path=str(temp),
                            final_path=str(tmp_path / "out.mkv"))]

    seen = {}

    def spy(o, p):
        seen["thread"] = threading.get_ident()
        _stage_parts(o, p)

    async def driver():
        parts: list[str] = []
        seen["loop"] = threading.get_ident()
        await asyncio.get_running_loop().run_in_executor(None, spy, outputs, parts)
        return parts

    parts = asyncio.run(driver())
    assert seen["thread"] != seen["loop"], "staging ran on the event loop thread"
    assert os.path.exists(parts[0])


def test_cancellation_shield_lets_the_copy_settle():
    """
    Moving staging to an executor made it interruptible where the inline loop
    was not. A thread-pool thread cannot be interrupted, so cleanup must not
    run while the copy is still going — it would delete .part files the thread
    then recreates.
    """
    async def driver():
        finished = threading.Event()
        started = threading.Event()

        def slow(_outputs, part_paths):
            started.set()
            time.sleep(0.2)
            part_paths.append("late.part")
            finished.set()

        parts: list[str] = []
        staging = asyncio.ensure_future(
            asyncio.get_running_loop().run_in_executor(None, slow, [], parts)
        )

        async def body():
            try:
                await asyncio.shield(staging)
            except asyncio.CancelledError:
                try:
                    await staging
                except Exception:
                    pass
                assert finished.is_set(), \
                    "cleanup would have raced a still-running copy thread"
                raise

        task = asyncio.create_task(body())
        await asyncio.get_running_loop().run_in_executor(None, started.wait)
        task.cancel()

        with pytest.raises(asyncio.CancelledError):
            await task

        assert finished.is_set()
        assert parts == ["late.part"]

    asyncio.run(driver())


# ═══════════════════════════════════════════════════════════════════════════
# run_staged_subprocess — the failure and cleanup paths
#
# Found by an independent mutation audit (Phase 2): the atomic-swap mechanics
# were tested, the failure and cleanup paths were not (4 of 8 mutations
# survived). These run a real subprocess — /bin/sh writing the temp files —
# so the staging pass operates on a real filesystem, which is where the bugs
# this code was written against actually live.
# ═══════════════════════════════════════════════════════════════════════════

from app.core.subprocess_runner import run_staged_subprocess  # noqa: E402


def _writer_cmd(pairs):
    """A shell command that writes given contents to given temp paths."""
    script = "; ".join(f"printf '%s' '{content}' > '{path}'"
                       for path, content in pairs)
    return ["/bin/sh", "-c", script]


def _run(cmd, outputs, **kw):
    return asyncio.run(run_staged_subprocess(cmd, outputs, **kw))


def test_a_successful_run_swaps_every_output_into_place(tmp_path):
    temp  = tmp_path / "a.tmp"
    final = tmp_path / "a.mkv"
    final.write_bytes(b"ORIGINAL")

    res = _run(_writer_cmd([(str(temp), "NEW")]),
               [StagedOutput(temp_path=str(temp), final_path=str(final))])

    assert res.success is True
    assert final.read_bytes() == b"NEW"


def test_a_successful_run_leaves_no_part_or_temp_files(tmp_path):
    temp  = tmp_path / "a.tmp"
    final = tmp_path / "a.mkv"

    _run(_writer_cmd([(str(temp), "NEW")]),
         [StagedOutput(temp_path=str(temp), final_path=str(final))])

    assert not (tmp_path / "a.mkv.part").exists()
    assert not temp.exists(), "temp file left behind after a successful swap"


def test_a_staging_failure_is_reported_as_a_failure(tmp_path, monkeypatch):
    """
    A staging OSError — ENOSPC above all — must not be reported as success.

    Traced rather than assumed. For an IN-PLACE job (the common case) the
    original is still on disk, so the caller's os.path.getsize succeeds and
    returns the ORIGINAL's size, the deletion guard sees output_path ==
    input_path and deletes nothing, and the job is recorded as a success:
    media.status becomes "processed" and last_processed is stamped while the
    remux never happened. The file then looks done and is never reprocessed.

    Audit ref: SUB-04.
    """
    import app.core.subprocess_runner as sr

    temp  = tmp_path / "a.tmp"
    final = tmp_path / "a.mkv"
    final.write_bytes(b"ORIGINAL")

    def _boom(outputs, part_paths):
        raise OSError(28, "No space left on device")

    monkeypatch.setattr(sr, "_stage_parts", _boom)

    res = _run(_writer_cmd([(str(temp), "NEW")]),
               [StagedOutput(temp_path=str(temp), final_path=str(final))])

    assert res.success is False, (
        "a staging failure was reported as success — the job will be recorded "
        "as processed while the remux never happened"
    )
    assert "originals untouched" in res.error


def test_a_staging_failure_leaves_every_original_untouched(tmp_path, monkeypatch):
    """The promise the error message makes has to be true."""
    import app.core.subprocess_runner as sr

    temp  = tmp_path / "a.tmp"
    final = tmp_path / "a.mkv"
    final.write_bytes(b"ORIGINAL")

    monkeypatch.setattr(sr, "_stage_parts",
                        lambda o, p: (_ for _ in ()).throw(OSError("disk full")))

    _run(_writer_cmd([(str(temp), "NEW")]),
         [StagedOutput(temp_path=str(temp), final_path=str(final))])

    assert final.read_bytes() == b"ORIGINAL"


def test_a_staging_failure_cleans_up_the_part_files(tmp_path, monkeypatch):
    """
    Each leaked .part is the size of a full media file, and this fires
    precisely when the disk is already full — the worst possible moment to
    leak. The handler cleans both the paths staged so far AND the full
    expected set, because a failure mid-copy leaves one .part that was never
    appended to part_paths.

    Audit ref: SUB-05.
    """
    import app.core.subprocess_runner as sr

    temp_a, temp_b = tmp_path / "a.tmp", tmp_path / "b.tmp"
    fin_a,  fin_b  = tmp_path / "a.mkv", tmp_path / "b.mkv"

    def _partial(outputs, part_paths):
        # First output stages fine, second dies mid-copy.
        p = outputs[0].final_path + ".part"
        with open(p, "wb") as f:
            f.write(b"STAGED")
        part_paths.append(p)
        with open(outputs[1].final_path + ".part", "wb") as f:
            f.write(b"HALF")
        raise OSError(28, "No space left on device")

    monkeypatch.setattr(sr, "_stage_parts", _partial)

    res = _run(_writer_cmd([(str(temp_a), "A"), (str(temp_b), "B")]),
               [StagedOutput(temp_path=str(temp_a), final_path=str(fin_a)),
                StagedOutput(temp_path=str(temp_b), final_path=str(fin_b))])

    assert res.success is False
    leaked = sorted(p.name for p in tmp_path.glob("*.part"))
    assert leaked == [], f"leaked .part files after a staging failure: {leaked}"


def test_a_staging_failure_cleans_up_the_temp_files(tmp_path, monkeypatch):
    import app.core.subprocess_runner as sr

    temp  = tmp_path / "a.tmp"
    final = tmp_path / "a.mkv"

    monkeypatch.setattr(sr, "_stage_parts",
                        lambda o, p: (_ for _ in ()).throw(OSError("disk full")))

    _run(_writer_cmd([(str(temp), "NEW")]),
         [StagedOutput(temp_path=str(temp), final_path=str(final))])

    assert not temp.exists(), "temp left behind after a staging failure"


def test_cancellation_cleans_up_temps_and_parts(tmp_path, monkeypatch):
    """
    An abort must not leak a full-sized .part. Aborts are a normal user
    action, so this is a steady leak rather than a rare one.

    Audit ref: SUB-08.
    """
    import app.core.subprocess_runner as sr

    temp  = tmp_path / "a.tmp"
    final = tmp_path / "a.mkv"

    def _stage_then_cancel(outputs, part_paths):
        p = outputs[0].final_path + ".part"
        with open(p, "wb") as f:
            f.write(b"STAGED")
        part_paths.append(p)
        raise asyncio.CancelledError()

    monkeypatch.setattr(sr, "_stage_parts", _stage_then_cancel)

    with pytest.raises(asyncio.CancelledError):
        _run(_writer_cmd([(str(temp), "NEW")]),
             [StagedOutput(temp_path=str(temp), final_path=str(final))])

    assert not (tmp_path / "a.mkv.part").exists(), (
        "a cancelled job left a full-sized .part file behind"
    )
    assert not temp.exists(), "a cancelled job left its temp file behind"


def test_every_part_is_fsynced_before_any_swap(tmp_path, monkeypatch):
    """
    A durability guarantee with no observable behaviour, so no black-box test
    can reach it — this one is deliberately white-box.

    os.replace guarantees which NAME you see, not that the new bytes survived
    a power cut. Without the fsync, a crash shortly after the swap can leave
    the new name pointing at data still in the page cache. The source comment
    reasons explicitly about that ordering; this pins the reasoning.

    Audit ref: SUB-02.
    """
    import app.core.subprocess_runner as sr

    events = []
    real_fsync, real_replace = os.fsync, os.replace

    def _fsync(fd):
        events.append("fsync")
        return real_fsync(fd)

    def _replace(src, dst):
        events.append("replace")
        return real_replace(src, dst)

    monkeypatch.setattr(sr.os, "fsync", _fsync)
    monkeypatch.setattr(sr.os, "replace", _replace)

    temps  = [tmp_path / "a.tmp", tmp_path / "b.tmp"]
    finals = [tmp_path / "a.mkv", tmp_path / "b.mkv"]

    res = _run(
        _writer_cmd([(str(temps[0]), "A"), (str(temps[1]), "B")]),
        [StagedOutput(temp_path=str(t), final_path=str(f))
         for t, f in zip(temps, finals)],
    )

    assert res.success is True
    assert events.count("fsync") == 2, f"not every part was fsynced: {events}"
    assert events.index("replace") > max(
        i for i, e in enumerate(events) if e == "fsync"
    ), f"a swap happened before the last fsync: {events}"


# ── load_forge_job_data exposes the faststart setting ────────────────────────
#
# The builders honour add_faststart and _process_next_forge passes it along —
# both pinned elsewhere. This is the remaining link: the value has to be READ
# from settings in the first place. Hardcoding True here survives every other
# test in the suite, because everything downstream stubs this function out,
# and the result would be a library with the setting off still getting
# +faststart on every forged MP4.

@pytest.mark.parametrize("stored,expected", [(True, True), (False, False)])
def test_load_forge_job_data_reads_the_faststart_setting(
        tmp_path, monkeypatch, stored, expected):
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    import app.core.forge as forge_mod
    from app.database.models import Ac3ForgeJob, Base, MediaFile
    from app.database.session import update_app_setting

    media_file = tmp_path / "Movie.mp4"
    media_file.write_bytes(b"not really an mp4, but it exists")

    engine = create_engine(f"sqlite:///{tmp_path / 'forge.db'}")
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)

    with Session() as db:
        update_app_setting(db, "add_faststart_to_mp4", stored)
        mf = MediaFile(path=str(media_file), filename="Movie.mp4",
                       directory=str(tmp_path), size=1, mtime=1.0,
                       container="mp4")
        db.add(mf)
        db.commit()
        job = Ac3ForgeJob(file_id=mf.id, status="processing", is_undo=False,
                          aac_stream_index=1, audio_track_count=2)
        db.add(job)
        db.commit()
        job_id = job.id

    monkeypatch.setattr(forge_mod, "SessionLocal", Session)

    data = forge_mod.load_forge_job_data(job_id)

    assert data is not None, "fixture did not produce a loadable job"
    assert data["add_faststart"] is expected
