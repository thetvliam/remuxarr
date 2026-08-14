"""
Revert point capture — the orchestration, and its wiring into the worker.

The module under test runs inside a window where the source file is
intact and one line away from being overwritten. Everything here is
ultimately about two questions: does it record the right thing, and does
it stay out of the way when it cannot.

The distinction it exists to hold
---------------------------------
IMPOSSIBLE (nothing destroyed, or only attachments destroyed) must never
block a job, whatever revert_require_point says. UNAVAILABLE (volume
missing, disk full, FFmpeg failed) is what that setting governs.
Collapsing the two gives you either jobs failing over a file whose only
loss was a font, or a silently broken recycle volume. Several tests below
exist only to keep them apart, because the natural refactor — one
"capture failed" path — passes everything else.

Sidecar lifecycle
-----------------
A sidecar is written during the window but the row that points at it is
written only after the job succeeds, because staging can still fail
afterwards. That leaves four exits from _run_job and every one of them
has to either record the sidecar or delete it. A leaked sidecar is
invisible: nothing scans the recycle volume, and with no row there is
nothing left to find it by.

Verified by mutation, 14 applied, 14 killed. One initially SURVIVED and
is the more useful finding: removing the recycle-directory readiness
check entirely. The unmounted-volume test still passed, because without
the check the run reached FFmpeg, FFmpeg failed on the missing directory,
and that failure produced an error too — the test was passing through a
completely different mechanism than the one it named, and spawning a real
FFmpeg to do it. It now stubs the FFmpeg run to succeed, so only the
readiness check can refuse, and asserts the message identifies the mount
rather than being any refusal at all.

The rest, killed on the first run:

  • revert_enabled check removed                → killed
  • require flag ignored, never blocks          → killed
  • require flag inverted, always blocks        → killed
  • SidecarUnsupported treated as UNAVAILABLE   → killed (blocks a job it
                                                   must not)
  • "nothing lost" treated as UNAVAILABLE       → killed
  • sidecar name drops the job_id               → killed (collision
                                                   overwrites a live point)
  • manifest built from the produced file       → killed
  • row recorded even when the job failed       → killed
  • sidecar not deleted on the failure path     → killed
  • sidecar not deleted on the exception path   → killed
  • stale sidecar kept across a retry           → killed
  • processed fingerprint never stat'ed         → killed
  • failed row write keeps the sidecar          → killed

No equivalent mutants.
"""
import asyncio
import json
import os

import pytest


# ── Harness ──────────────────────────────────────────────────────────────────

@pytest.fixture
def recycle(tmp_path, monkeypatch):
    from app.config import settings as app_settings

    root = tmp_path / "recycle"
    root.mkdir()
    monkeypatch.setattr(app_settings, "RECYCLE_DIR", str(root), raising=False)
    return root


@pytest.fixture
def enabled():
    return {"revert_enabled": True, "revert_require_point": False}


def _probe(*streams):
    return {"streams": list(streams), "format": {"format_name": "matroska,webm"}}


def _s(index, codec_type, codec, **kw):
    tags = {k: v for k, v in kw.items()
            if k in ("language", "title", "filename") and v is not None}
    out = {"index": index, "codec_type": codec_type, "codec_name": codec}
    if tags:
        out["tags"] = tags
    if kw.get("channels"):
        out["channels"] = kw["channels"]
    return out


def _patch_probes(monkeypatch, original, produced):
    """Return the two probe results in call order: source, then output."""
    import app.core.revert_capture as rc

    results = iter([original, produced])
    monkeypatch.setattr(rc, "probe_file", lambda _p: next(results))


def _patch_ffmpeg(monkeypatch, *, fails=False):
    """
    Stand in for the sidecar FFmpeg run without spawning one.

    Deliberately creates the destination directory as well as the file, so
    that a test about some OTHER refusal cannot pass by accident when the
    refusal it meant to test is removed and FFmpeg fails on the missing
    directory instead. That is not hypothetical — it is how the
    unmounted-volume test passed before the readiness check had a mutant
    aimed at it, quietly spawning a real FFmpeg in the process.
    """
    import app.core.revert_capture as rc

    async def fake(cmd):
        if fails:
            raise rc._Unavailable("FFmpeg failed writing the sidecar (rc=1)")
        os.makedirs(os.path.dirname(cmd[-1]), exist_ok=True)
        with open(cmd[-1], "wb") as f:
            f.write(b"x" * 2048)

    monkeypatch.setattr(rc, "_run", fake)


def _capture(**kw):
    from app.core.revert_capture import capture

    kw.setdefault("input_path", "/m/Show.mkv")
    kw.setdefault("produced_path", "/tmp/job_1.remuxarr_tmp")
    kw.setdefault("file_id", 7)
    kw.setdefault("job_id", 1)
    return asyncio.run(capture(**kw))


# A job that dropped one audio track.
_ORIGINAL = _probe(
    _s(0, "video", "h264"),
    _s(1, "audio", "aac", channels=2, language="eng"),
    _s(2, "audio", "aac", channels=6, language="fre"),
)
_PRODUCED = _probe(
    _s(0, "video", "h264"),
    _s(1, "audio", "aac", channels=2, language="eng"),
)


# ── The happy path ───────────────────────────────────────────────────────────

def test_a_dropped_track_is_captured(recycle, enabled, monkeypatch):
    _patch_probes(monkeypatch, _ORIGINAL, _PRODUCED)
    _patch_ffmpeg(monkeypatch)

    captured, error = _capture(app_cfg=enabled)

    assert error is None
    assert captured is not None
    assert os.path.exists(captured.sidecar_path)
    assert captured.sidecar_size == 2048
    assert captured.original_path == "/m/Show.mkv"


def test_the_manifest_describes_the_original_not_the_result(recycle, enabled,
                                                            monkeypatch):
    """
    The manifest is the thing revert rebuilds from. Built from the produced
    file it would describe the very state the user is trying to undo.
    """
    _patch_probes(monkeypatch, _ORIGINAL, _PRODUCED)
    _patch_ffmpeg(monkeypatch)

    captured, _ = _capture(app_cfg=enabled)
    manifest = json.loads(captured.manifest_json)

    assert len(manifest["streams"]) == 3
    assert [s["language"] for s in manifest["streams"]] == [None, "eng", "fre"]


def test_sidecar_names_include_both_file_and_job(recycle, enabled, monkeypatch):
    """
    file_id alone collides when a file is processed twice before retention
    sweeps the first sidecar, and the second write silently overwrites a
    revert point another row still points at.
    """
    _patch_probes(monkeypatch, _ORIGINAL, _PRODUCED)
    _patch_ffmpeg(monkeypatch)
    first, _ = _capture(app_cfg=enabled, file_id=7, job_id=1)

    _patch_probes(monkeypatch, _ORIGINAL, _PRODUCED)
    _patch_ffmpeg(monkeypatch)
    second, _ = _capture(app_cfg=enabled, file_id=7, job_id=2)

    assert first.sidecar_path != second.sidecar_path
    assert os.path.exists(first.sidecar_path), "the first sidecar was overwritten"


# ── Disabled ─────────────────────────────────────────────────────────────────

def test_nothing_happens_when_the_feature_is_off(recycle, monkeypatch):
    import app.core.revert_capture as rc

    monkeypatch.setattr(rc, "probe_file",
                        lambda _p: pytest.fail("probed with the feature off"))

    captured, error = _capture(app_cfg={"revert_enabled": False})

    assert (captured, error) == (None, None)


# ── IMPOSSIBLE: never blocks ─────────────────────────────────────────────────

def test_a_job_that_destroyed_nothing_records_nothing(recycle, monkeypatch):
    _patch_probes(monkeypatch, _ORIGINAL, _ORIGINAL)
    _patch_ffmpeg(monkeypatch)

    captured, error = _capture(
        app_cfg={"revert_enabled": True, "revert_require_point": True},
    )

    assert captured is None
    assert error is None, "blocked a job that had nothing to revert"


def test_attachment_only_loss_does_not_block_even_when_required(recycle,
                                                                monkeypatch):
    """
    Matroska cannot hold a file whose only stream is an attachment, so no
    sidecar is possible. Failing the job over that would mean a library
    with fonts could not be processed at all.
    """
    original = _probe(
        _s(0, "video", "h264"),
        _s(1, "attachment", "ttf", filename="Roboto.ttf"),
    )
    produced = _probe(_s(0, "video", "h264"))
    _patch_probes(monkeypatch, original, produced)
    _patch_ffmpeg(monkeypatch)

    captured, error = _capture(
        app_cfg={"revert_enabled": True, "revert_require_point": True},
    )

    assert captured is None
    assert error is None, "blocked a job whose only loss was unstoreable"


# ── UNAVAILABLE: governed by the setting ─────────────────────────────────────

def test_unmounted_volume_is_survivable_by_default(tmp_path, enabled,
                                                   monkeypatch):
    from app.config import settings as app_settings

    missing = tmp_path / "not-mounted"
    monkeypatch.setattr(app_settings, "RECYCLE_DIR", str(missing), raising=False)
    _patch_probes(monkeypatch, _ORIGINAL, _PRODUCED)
    _patch_ffmpeg(monkeypatch)

    captured, error = _capture(app_cfg=enabled)

    assert captured is None
    assert error is None, "a missing volume must not fail jobs by default"
    assert not missing.exists(), "wrote into an unmounted volume"


def test_unmounted_volume_blocks_when_required(tmp_path, monkeypatch):
    from app.config import settings as app_settings

    missing = tmp_path / "not-mounted"
    monkeypatch.setattr(app_settings, "RECYCLE_DIR", str(missing), raising=False)
    _patch_probes(monkeypatch, _ORIGINAL, _PRODUCED)
    # Everything downstream is made to succeed, so the readiness check is
    # the only thing that can refuse — and the message has to say WHY, or
    # the operator gets a cryptic FFmpeg error for a missing bind mount.
    _patch_ffmpeg(monkeypatch)

    captured, error = _capture(
        app_cfg={"revert_enabled": True, "revert_require_point": True},
    )

    assert captured is None
    assert error and str(missing) in error
    assert "mounted" in error
    assert not missing.exists(), "wrote into an unmounted volume"


def test_a_failed_sidecar_write_blocks_when_required(recycle, monkeypatch):
    _patch_probes(monkeypatch, _ORIGINAL, _PRODUCED)
    _patch_ffmpeg(monkeypatch, fails=True)

    captured, error = _capture(
        app_cfg={"revert_enabled": True, "revert_require_point": True},
    )

    assert captured is None
    assert error is not None


def test_a_failed_sidecar_write_is_survivable_by_default(recycle, enabled,
                                                         monkeypatch):
    _patch_probes(monkeypatch, _ORIGINAL, _PRODUCED)
    _patch_ffmpeg(monkeypatch, fails=True)

    captured, error = _capture(app_cfg=enabled)

    assert (captured, error) == (None, None)


def test_an_unprobeable_file_is_treated_as_unavailable(recycle, monkeypatch):
    import app.core.revert_capture as rc

    def boom(_p):
        raise rc.ProbeError("not a media file")

    monkeypatch.setattr(rc, "probe_file", boom)

    _, error = _capture(
        app_cfg={"revert_enabled": True, "revert_require_point": True},
    )
    assert error is not None


# ── Recording the row ────────────────────────────────────────────────────────

@pytest.fixture
def db(monkeypatch):
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    from app.database.models import Base

    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine)

    import app.core.worker as worker_mod
    monkeypatch.setattr(worker_mod, "SessionLocal", factory)
    return factory()


def test_the_row_fingerprints_the_file_as_the_job_left_it(db, tmp_path):
    """
    processed_size/mtime describe the PRODUCED file, not the source. Read
    from the source they would never match what is on disk, and revert
    would refuse every time.
    """
    from app.core.worker import _record_revert_point
    from app.core.revert_capture import CapturedRevertPoint
    from app.database.models import RevertPoint

    produced = tmp_path / "Show.mkv"
    produced.write_bytes(b"processed output")

    _record_revert_point(
        7,
        CapturedRevertPoint(
            sidecar_path="/recycle/7_1.remuxarr_revert",
            sidecar_size=2048,
            manifest_json="{}",
            original_path="/m/Show.mkv",
            original_container="matroska",
        ),
        str(produced),
    )

    row = db.query(RevertPoint).one()
    assert row.file_id == 7
    assert row.processed_size == len(b"processed output")
    assert row.processed_mtime == pytest.approx(produced.stat().st_mtime)


def test_a_failed_row_write_discards_the_sidecar(db, recycle, monkeypatch):
    """
    A sidecar with no row is unreachable: nothing scans the recycle volume
    and nothing else records the path. Keeping it would leak the disk the
    retention cap exists to bound.
    """
    from app.core.worker import _record_revert_point
    from app.core.revert_capture import CapturedRevertPoint
    import app.core.worker as worker_mod

    sidecar = recycle / "7_1.remuxarr_revert"
    sidecar.write_bytes(b"payload")

    def boom():
        raise RuntimeError("database is gone")

    monkeypatch.setattr(worker_mod, "SessionLocal", boom)

    _record_revert_point(
        7,
        CapturedRevertPoint(sidecar_path=str(sidecar), sidecar_size=7,
                            manifest_json="{}", original_path="/m/Show.mkv",
                            original_container="matroska"),
        None,
    )

    assert not sidecar.exists(), "sidecar leaked after the row write failed"


# ── Sidecar lifecycle through _run_job ───────────────────────────────────────

class _FakeWS:
    async def broadcast_json(self, *_a, **_k):
        return None


@pytest.fixture
def job(tmp_path, recycle, monkeypatch):
    """
    Drive _run_job with everything around the capture stubbed out, but with
    the staging hook genuinely invoked — that is the part being tested.
    """
    from types import SimpleNamespace

    import app.core.worker as worker

    source = tmp_path / "Movie.mkv"
    source.write_bytes(b"ORIGINAL")
    state = {"recorded": [], "finished": {}, "sidecars": []}

    def build(*, success=True, raises=False, hook_calls=1):
        decision = SimpleNamespace(actions=[], target_container=None)

        def fake_load(_job_id):
            return (
                {"id": 1, "is_dry_run": False},
                {"id": 7, "path": str(source), "filename": "Movie.mkv", "size": 0},
                [],
                {"job_timeout_minutes": 0, "revert_enabled": True,
                 "revert_require_point": False},
                decision,
            )

        calls = {"n": 0}

        async def fake_execute(**kwargs):
            calls["n"] += 1
            hook = kwargs.get("before_staging")

            # hook_calls > 1 stands in for the corrupt-audio retry, which
            # re-runs the whole command and so fires the hook again. Each
            # firing produces its own sidecar, as a real capture would.
            for attempt in range(hook_calls if hook else 0):
                sidecar = recycle / f"7_1_attempt{attempt}.remuxarr_revert"
                sidecar.write_bytes(b"dropped tracks")
                state["sidecars"].append(sidecar)

                from app.core.revert_capture import CapturedRevertPoint

                async def fake_capture(_sidecar=sidecar, **_kw):
                    return CapturedRevertPoint(
                        sidecar_path=str(_sidecar), sidecar_size=14,
                        manifest_json="{}", original_path=str(source),
                        original_container="matroska",
                    ), None

                monkeypatch.setattr(worker.revert_capture, "capture",
                                    fake_capture)
                await hook("/tmp/job_1.remuxarr_tmp")

            if raises:
                raise RuntimeError("ffmpeg exploded")

            ok = success
            out = kwargs["output_path"]
            if ok:
                with open(out, "wb") as f:
                    f.write(b"REMUXED")
            return SimpleNamespace(
                success=ok, output_path=out if ok else None,
                output_size=7 if ok else None,
                error=None if ok else "ffmpeg failed",
            )

        monkeypatch.setattr(worker, "_load_job_data", fake_load)
        monkeypatch.setattr(worker, "execute_ffmpeg", fake_execute)
        monkeypatch.setattr(worker, "determine_output_path",
                            lambda _in, _dec: str(source))
        monkeypatch.setattr(
            worker, "_finish_job",
            lambda jid, ok, p, s, e: state["finished"].update(ok=ok, error=e),
        )
        monkeypatch.setattr(
            worker, "_record_revert_point",
            lambda fid, cap, out: state["recorded"].append((fid, cap)),
        )

        async def driver():
            await worker._run_job(1, _FakeWS(), asyncio.get_running_loop())

        asyncio.run(driver())
        return state

    return build


def test_a_successful_job_records_the_revert_point(job):
    state = job(success=True)

    assert state["finished"]["ok"] is True
    assert len(state["recorded"]) == 1
    file_id, captured = state["recorded"][0]
    assert file_id == 7
    assert captured.sidecar_size == 14


def test_a_failed_job_deletes_the_sidecar(job):
    """
    The hook runs before staging, so a staging failure after it leaves a
    sidecar describing a file that was never written. Nothing else would
    collect it: no row points at it and nothing scans the volume.
    """
    state = job(success=False)

    assert state["finished"]["ok"] is False
    assert state["recorded"] == []
    assert not any(s.exists() for s in state["sidecars"]), "sidecar leaked"


def test_an_exception_after_the_hook_deletes_the_sidecar(job):
    state = job(raises=True)

    assert state["finished"]["ok"] is False
    assert not any(s.exists() for s in state["sidecars"]), "sidecar leaked"


def test_a_retry_does_not_leave_the_first_attempts_sidecar_behind(job):
    """
    The corrupt-audio path re-runs the whole command, so the hook fires
    twice in one job. Only the last capture is ever recorded, so the first
    attempt's sidecar has no row pointing at it — keep it and it leaks the
    full size of the dropped tracks, on every retried job.
    """
    state = job(success=True, hook_calls=2)

    first, second = state["sidecars"]
    assert not first.exists(), "the first attempt's sidecar leaked"
    assert second.exists()
    assert len(state["recorded"]) == 1
    assert state["recorded"][0][1].sidecar_path == str(second)
