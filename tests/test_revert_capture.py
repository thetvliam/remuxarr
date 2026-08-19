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
import time

import pytest


# ── Harness ──────────────────────────────────────────────────────────────────

def _memory_db(monkeypatch):
    """An isolated in-memory database, installed as SessionLocal."""
    from sqlalchemy.orm import sessionmaker

    from tests.conftest import memory_engine

    from app.database.models import Base
    import app.database.session as session_mod

    engine = memory_engine()
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine)
    monkeypatch.setattr(session_mod, "SessionLocal", factory)
    return factory


@pytest.fixture(autouse=True)
def isolated_db(monkeypatch):
    """
    Every test in this file gets its own empty database.

    Autouse because capture reads the file's existing revert point before
    doing anything, so any test that calls it touches the database whether
    or not it looks like a database test. Without this they fall through
    to the real SessionLocal and hit the suite-wide sqlite file that
    conftest points REMUXARR_DATABASE_PATH at.

    That is how these tests passed locally and failed in CI: the shared
    file had been left behind by earlier runs, complete with a
    revert_points table, so the query succeeded and returned nothing. A
    clean checkout has no such file, the table does not exist, and ten
    tests fail on "no such table". Reaching a real database at all was the
    bug — the ambient state only decided whether it showed.
    """
    return _memory_db(monkeypatch)


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


def _patch_probes(monkeypatch, original, produced, source_path="/m/Show.mkv"):
    """
    Return probe results keyed by PATH, not by call order.

    Call order is not part of capture's contract — it probes the produced
    file always, the source only on a file's first job, and the sidecar
    after writing it — and an order-keyed stub silently hands back the
    wrong file when that changes, which reads as a logic bug in the code
    under test rather than a broken harness.

    A sidecar path is answered by DERIVING its streams from the command
    that wrote it, so the stub agrees with the file the real FFmpeg would
    have produced. Returning a fixed shape instead would make capture's
    layout check pass on a sidecar nothing actually wrote — and that check
    exists precisely because assuming the written layout was how the
    indices came to be wrong.
    """
    import app.core.revert_capture as rc

    by_path = {source_path: original, "/tmp/job_1.remuxarr_tmp": produced}
    state = _MOCK_STATE

    def fake(path, *_a, **_k):
        if path in by_path:
            return by_path[path]
        if path.endswith(SIDECAR_SUFFIX) and state["commands"]:
            return _sidecar_probe(state["commands"][-1], by_path, state)
        raise AssertionError(f"unexpected probe of {path!r}")

    monkeypatch.setattr(rc, "probe_file", fake)


_MOCK_STATE = {"commands": [], "sidecars": {}}
SIDECAR_SUFFIX = ".remuxarr_revert"


def _sidecar_probe(cmd, by_path, state):
    """The streams a sidecar command maps, in the order it maps them."""
    inputs = [cmd[i + 1] for i, a in enumerate(cmd) if a == "-i"]
    maps = [cmd[i + 1] for i, a in enumerate(cmd) if a == "-map"]

    streams = []
    for spec in maps:
        input_n, _, index = spec.partition(":")
        source_path = inputs[int(input_n)]
        source = (by_path.get(source_path)
                  or state["sidecars"].get(source_path)
                  or {"streams": []})
        match = next((st for st in source["streams"]
                      if st["index"] == int(index)), None)
        assert match is not None, f"command maps {spec}, which does not exist"
        streams.append({**match, "index": len(streams)})

    probe = {"streams": streams,
             "format": {"format_name": "matroska,webm", "duration": "60.0"}}
    state["sidecars"][cmd[-1]] = probe
    return probe


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

    commands = _MOCK_STATE["commands"]
    commands.clear()
    _MOCK_STATE["sidecars"].clear()

    async def fake(cmd):
        commands.append(cmd)
        if fails:
            raise rc._Unavailable("FFmpeg failed writing the sidecar (rc=1)")
        os.makedirs(os.path.dirname(cmd[-1]), exist_ok=True)
        with open(cmd[-1], "wb") as f:
            f.write(b"x" * 2048)

    monkeypatch.setattr(rc, "_run", fake)
    return commands


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


def test_the_container_is_the_projects_normalised_name(recycle, enabled,
                                                       monkeypatch):
    """
    Restore looks the original container up in ffmpeg._CONTAINER_FORMAT,
    which is keyed on the project's normalised names — "mkv", "mp4". The
    raw ffprobe format_name is neither: it reports "matroska,webm" for
    every Matroska file and "mov,mp4,m4a,3gp,3g2,mj2" for every MP4, so
    taking its first element yields "matroska" (not a key at all, making
    every revert refuse) and "mov" (a key, but the wrong container —
    restore would write a MOV where an MP4 belongs).
    """
    _patch_probes(monkeypatch, _ORIGINAL, _PRODUCED)
    _patch_ffmpeg(monkeypatch)

    captured, _ = _capture(app_cfg=enabled)

    from app.core.ffmpeg import _CONTAINER_FORMAT

    assert captured.original_container == "mkv"
    assert captured.original_container in _CONTAINER_FORMAT


def test_an_mp4_original_is_recorded_as_mp4_not_mov(recycle, enabled,
                                                    monkeypatch):
    mp4_original = {
        "streams": _ORIGINAL["streams"],
        "format": {"format_name": "mov,mp4,m4a,3gp,3g2,mj2"},
    }
    _patch_probes(monkeypatch, mp4_original, _PRODUCED)
    _patch_ffmpeg(monkeypatch)

    captured, _ = _capture(app_cfg=enabled)

    assert captured.original_container == "mp4"


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


def test_the_manifest_records_where_every_stream_ended_up(recycle, enabled,
                                                          monkeypatch):
    """
    Restore reads these rather than re-matching against the processed
    file, which by then may have been re-tagged or re-scanned. Resolving
    it here, while both files are known-good, is what stops a revert
    putting the wrong track back.
    """
    _patch_probes(monkeypatch, _ORIGINAL, _PRODUCED)
    _patch_ffmpeg(monkeypatch)

    captured, _ = _capture(app_cfg=enabled)
    streams = json.loads(captured.manifest_json)["streams"]

    survivors = [s for s in streams if s.get("processed_index") is not None]
    lost = [s for s in streams if s.get("sidecar_index") is not None]

    assert [s["index"] for s in survivors] == [0, 1]
    assert [s["index"] for s in lost] == [2]
    assert lost[0]["sidecar_index"] == 0
    assert lost[0]["processed_index"] is None


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


# ── The written sidecar must match the recorded indices ──────────────────────

def _reorder_sidecar_probe(monkeypatch):
    """Make the sidecar come back in a different order than it was mapped."""
    import app.core.revert_capture as rc

    real = rc.probe_file

    def shuffled(path, *a, **k):
        probe = real(path, *a, **k)
        if path.endswith(SIDECAR_SUFFIX):
            streams = list(reversed(probe["streams"]))
            return {**probe,
                    "streams": [{**st, "index": i}
                                for i, st in enumerate(streams)]}
        return probe

    monkeypatch.setattr(rc, "probe_file", shuffled)


def test_a_sidecar_written_in_a_different_order_is_refused(recycle, monkeypatch):
    """
    sidecar_index is positional, so it is only correct if the muxer writes
    streams in the order they were mapped. _plan_sources arranges the
    sources so it does — but that is a claim about FFmpeg, and it is the
    exact claim that was wrong before: the Matroska muxer puts attachments
    after every real track, so mapping [subtitle, font, cover-art] wrote
    [subtitle, cover-art, font] and every index past the font pointed at
    the wrong stream. Files still muxed. Jobs still reported success. The
    restored file had the cover art carrying a font's name.

    Nothing triggers this check while the assumption holds, which is why
    the mismatch is forced here. Recording indices that point at the wrong
    streams is worse than recording none.
    """
    # Two lost streams of DIFFERENT types, or reversing the sidecar's
    # order changes nothing and the test passes without exercising the
    # check at all.
    original = _probe(
        _s(0, "video", "h264"),
        _s(1, "audio", "aac", channels=2, language="eng"),
        _s(2, "audio", "ac3", channels=6, language="fre"),
        _s(3, "subtitle", "subrip", language="ger"),
    )
    _patch_probes(monkeypatch, original, _PRODUCED)
    _patch_ffmpeg(monkeypatch)
    _reorder_sidecar_probe(monkeypatch)

    captured, error = _capture(
        app_cfg={"revert_enabled": True, "revert_require_point": True})

    assert captured is None
    assert error and "order" in error


def test_a_matching_sidecar_layout_is_accepted(recycle, enabled, monkeypatch):
    """
    The other half: the check must not refuse the normal case, or every
    revert point silently stops being created.
    """
    _patch_probes(monkeypatch, _ORIGINAL, _PRODUCED)
    _patch_ffmpeg(monkeypatch)

    captured, error = _capture(app_cfg=enabled)

    assert error is None
    assert captured is not None


# ── Staying off the event loop ───────────────────────────────────────────────

@pytest.mark.parametrize("slow_call", ["probe_file", "_load_existing_point"])
def test_capture_does_not_block_the_event_loop(recycle, enabled, monkeypatch,
                                               slow_call):
    """
    capture() is awaited from inside run_staged_subprocess's hook, which
    runs on the main loop alongside every job's progress broadcasts and
    every HTTP handler. It does two ffprobes and a database query, all
    synchronous — and ffprobe on a spun-down array takes hundreds of
    milliseconds.

    Measured at 412 ms of stalled loop per probe before this, against
    12 ms through an executor. The symptom is not a slow revert; it is
    every OTHER job's progress freezing while one job finishes, which
    reads as the app hanging.

    Each call is slowed deliberately and in turn: a fast stub cannot tell
    a blocking call from a non-blocking one, which is exactly why this
    went unnoticed while every other test passed.

    The database query is parametrised alongside the probes even though an
    in-memory query is microseconds. In production the engine has a 30
    second busy timeout, so a read waiting behind another job's write lock
    is precisely the case that would stall the loop for seconds — and the
    only way to test the property rather than the coincidence is to make
    the call slow on purpose.
    """
    import app.core.revert_capture as rc

    _patch_probes(monkeypatch, _ORIGINAL, _PRODUCED)
    _patch_ffmpeg(monkeypatch)

    original = getattr(rc, slow_call)

    def slow(*a, **k):
        time.sleep(0.25)
        return original(*a, **k)

    monkeypatch.setattr(rc, slow_call, slow)

    ticks = []

    async def heartbeat(stop):
        while not stop.is_set():
            ticks.append(time.monotonic())
            await asyncio.sleep(0.01)

    async def run():
        stop = asyncio.Event()
        beat = asyncio.create_task(heartbeat(stop))
        await asyncio.sleep(0.05)
        await rc.capture(input_path="/m/Show.mkv",
                         produced_path="/tmp/job_1.remuxarr_tmp",
                         file_id=7, job_id=1, app_cfg=enabled)
        await asyncio.sleep(0.05)
        stop.set()
        await beat

    asyncio.run(run())

    gaps = [b - a for a, b in zip(ticks, ticks[1:])]
    assert gaps, "the heartbeat never ran — the measurement is broken"
    assert max(gaps) < 0.15, (
        f"the event loop stalled for {max(gaps) * 1000:.0f} ms during capture; "
        f"blocking work is running on it instead of an executor"
    )


# ── Source planning and re-annotation ────────────────────────────────────────
#
# Reached through capture only in combinations our own jobs cannot
# currently produce, so they are exercised directly. The alternative is
# recording them as equivalent mutants, which would be true today and
# quietly false the first time matching changes its mind about a stream.

def _stream(index, **kw):
    out = {"index": index, "type": "audio", "codec": "aac",
           "processed_index": None}
    out.update(kw)
    return out


def test_sources_prefer_the_previous_sidecar_over_the_current_file():
    """
    The sidecar copy came out of the pristine original. The copy in the
    current file has been through however many jobs since and may have
    been re-tagged or re-encoded on the way.
    """
    from app.core.revert_capture import _plan_sources

    both = _stream(2, sidecar_index=0, processed_index=5)

    assert _plan_sources([both], has_previous_sidecar=True) == [(both, 1, 0)]


def test_sources_use_the_current_file_for_newly_lost_streams():
    from app.core.revert_capture import _plan_sources

    new_loss = _stream(2, processed_index=5)

    assert _plan_sources([new_loss], has_previous_sidecar=True) == [(new_loss, 0, 5)]


def test_a_first_job_reads_streams_at_their_original_indices():
    from app.core.revert_capture import _plan_sources

    fresh = _stream(2)

    assert _plan_sources([fresh], has_previous_sidecar=False) == [(fresh, 0, 2)]


def test_a_stream_in_neither_input_is_unavailable():
    """
    Writing the sidecar without it would leave the manifest claiming a
    slot that holds something else, and restore would map it.
    """
    import app.core.revert_capture as rc

    with pytest.raises(rc._Unavailable):
        rc._plan_sources([_stream(2)], has_previous_sidecar=True)


def test_reannotation_clears_a_stale_sidecar_index():
    """
    A stream that was in the previous sidecar and is matched in the new
    output keeps no slot. Overwriting without clearing would leave the old
    index, and restore prefers the sidecar when both are set — so that
    track would come from whatever now sits at that slot.
    """
    from app.core.revert_capture import _reannotate

    revived = _stream(2, sidecar_index=0)
    matches = [(revived, 4)]

    _reannotate(matches, sources=[])

    assert revived["processed_index"] == 4
    assert "sidecar_index" not in revived


def test_reannotation_numbers_sidecar_slots_positionally():
    from app.core.revert_capture import _reannotate

    first, second = _stream(1), _stream(3)
    matches = [(first, None), (second, None)]

    _reannotate(matches, sources=[(first, 0, 9), (second, 1, 7)])

    assert first["sidecar_index"] == 0
    assert second["sidecar_index"] == 1
    assert first["processed_index"] is None


# ── A point must still describe the file in front of it ──────────────────────

def test_a_point_whose_fingerprint_no_longer_matches_is_superseded(recycle,
                                                                   monkeypatch):
    """
    Two routes reach this, and both end in a sidecar mixing content from
    files that were never the same file.

    A Sonarr upgrade replaces the media between jobs: the stored manifest
    describes the previous release and the sidecar holds that release's
    tracks. And clear_database wipes media_files without enforced foreign
    keys, so a leftover revert point keeps a file_id the next scanned file
    inherits — extending then reads a completely different file's
    manifest.

    Neither is detectable later. The sentinel gets re-established against
    whatever was just produced, so it passes from then on.
    """
    from app.core.revert_capture import _load_existing_point

    source = recycle.parent / "Show.mkv"
    source.write_bytes(b"the file as it is now")

    db, _sidecar = _unusable_point(
        recycle, monkeypatch,
        manifest=json.dumps({"version": 2, "streams": [], "path": str(source)}),
        media_path=source)

    # Something else rewrote the file after the point was recorded.
    source.write_bytes(b"a different release entirely, of a different size")

    existing = _load_existing_point(7, str(source))

    assert existing is not None, "the row must come back so it can be replaced"
    assert existing.usable is False


def test_a_point_matching_the_file_is_usable(recycle, monkeypatch):
    """
    The other direction: the check must not reject the ordinary case, or
    every second job silently starts a fresh point and reverting stops
    reaching the pristine original.
    """
    from app.core.revert_capture import _load_existing_point

    source = recycle.parent / "Show.mkv"
    source.write_bytes(b"untouched since the last job")

    db, _sidecar = _unusable_point(
        recycle, monkeypatch,
        manifest=json.dumps({"version": 2, "streams": [], "path": str(source)}),
        media_path=source)

    existing = _load_existing_point(7, str(source))

    assert existing is not None
    assert existing.usable is True


# ── Manifest versioning ──────────────────────────────────────────────────────

def test_an_older_manifest_layout_is_reported_as_unusable(recycle, monkeypatch,
                                                          tmp_path):
    """
    processed_index and sidecar_index mean something different in an
    earlier layout. Building a sidecar on them would map real slots to
    the wrong streams — worse than losing the ability to go all the way
    back for that one file.

    Unusable, though, is not absent: the row still comes back so capture
    can take it over rather than leave a duplicate behind.
    """
    from sqlalchemy.orm import sessionmaker

    from tests.conftest import memory_engine

    from app.core.revert_capture import _load_existing_point
    from app.database.models import Base, RevertPoint
    import app.database.session as session_mod

    engine = memory_engine()
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine)
    monkeypatch.setattr(session_mod, "SessionLocal", factory)

    sidecar = recycle / "7_1.remuxarr_revert"
    sidecar.write_bytes(b"payload")

    db = factory()
    db.add(RevertPoint(file_id=7, sidecar_path=str(sidecar), sidecar_size=7,
                       manifest=json.dumps({"version": 1, "streams": []}),
                       original_path="/m/Show.mkv"))
    db.commit()

    existing = _load_existing_point(7, "/m/Show.mkv")
    assert existing is not None, "the row must still be returned, to be replaced"
    assert existing.usable is False
    assert existing.manifest is None


def _unusable_point(recycle, monkeypatch, *, manifest, sidecar_exists=True,
                    media_path=None):
    """
    A revert point already on file that capture cannot build on.

    media_path, when given, is a real file whose size and mtime the point
    records — which is what makes it USABLE. Without it the fingerprint
    check rejects the point regardless of its manifest, so a test meaning
    to exercise the extend path would silently exercise the supersede one.
    """
    from sqlalchemy.orm import sessionmaker

    from tests.conftest import memory_engine

    from app.database.models import Base, RevertPoint
    import app.database.session as session_mod

    engine = memory_engine()
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine)
    monkeypatch.setattr(session_mod, "SessionLocal", factory)

    sidecar = recycle / "7_1.remuxarr_revert"
    if sidecar_exists:
        sidecar.write_bytes(b"tracks from an older build")

    fingerprint = {}
    if media_path is not None:
        stat = os.stat(media_path)
        fingerprint = {"processed_size": stat.st_size,
                       "processed_mtime": stat.st_mtime}

    db = factory()
    db.add(RevertPoint(file_id=7, sidecar_path=str(sidecar), sidecar_size=26,
                       manifest=manifest, original_path="/m/Show.mkv",
                       **fingerprint))
    db.commit()
    return db, sidecar


@pytest.mark.parametrize("label,manifest,sidecar_exists", [
    ("older manifest layout", json.dumps({"version": 1, "streams": []}), True),
    ("unreadable manifest",   "{not json",                               True),
    ("sidecar gone",          json.dumps({"version": 2, "streams": []}), False),
])
def test_an_unusable_point_is_superseded_not_duplicated(recycle, enabled,
                                                        monkeypatch, label,
                                                        manifest,
                                                        sidecar_exists):
    """
    Reported from a real install: the recycle bin counts fell by two on
    every revert, from thirteen to eleven to nine.

    A point written by an earlier build carries an older manifest layout,
    so capture cannot extend it. Treating that as "no point exists" left
    the old row in place and created a second beside it — two entries for
    one file, two sidecars, both listed as restorable. Only one could ever
    work, since the older row's fingerprint stops matching the moment the
    new job rewrites the file, and reverting deleted both.

    Superseding takes the row over instead. The old sidecar goes with it.
    """
    db, _sidecar = _unusable_point(recycle, monkeypatch, manifest=manifest,
                                   sidecar_exists=sidecar_exists)
    _patch_probes(monkeypatch, _ORIGINAL, _PRODUCED)
    _patch_ffmpeg(monkeypatch)

    captured, error = _capture(app_cfg=enabled, file_id=7, job_id=2)

    assert error is None
    assert captured is not None
    assert captured.replaces_point_id == 1, (
        f"{label}: capture would create a second revert point for this file"
    )


def test_superseding_a_point_does_not_reuse_its_sidecar(recycle, enabled,
                                                        monkeypatch):
    """
    The old sidecar's contents do not correspond to the new manifest's
    annotations, so building the new one from it would map real slots to
    the wrong streams. It is passed along only to be deleted.

    The command is asserted rather than the outcome, because an unused
    -i is invisible in the result: FFmpeg still opens every input it is
    given, so passing the old sidecar along would work fine right up until
    the case where that sidecar is the thing that went missing — which is
    one of the three reasons a point becomes unusable in the first place.
    """
    db, old_sidecar = _unusable_point(
        recycle, monkeypatch,
        manifest=json.dumps({"version": 1, "streams": []}))
    _patch_probes(monkeypatch, _ORIGINAL, _PRODUCED)
    commands = _patch_ffmpeg(monkeypatch)

    captured, _ = _capture(app_cfg=enabled, file_id=7, job_id=2)

    assert captured.replaces_sidecar_path == str(old_sidecar)
    assert captured.sidecar_path != str(old_sidecar)

    inputs = [commands[0][i + 1] for i, a in enumerate(commands[0]) if a == "-i"]
    assert inputs == ["/m/Show.mkv"], (
        "the superseded sidecar was passed to FFmpeg as an input"
    )


def test_extending_a_usable_point_does_pass_its_sidecar(recycle, enabled,
                                                        monkeypatch):
    """
    The other side of the same assertion — otherwise "don't pass the old
    sidecar" could be satisfied by never passing one at all, which breaks
    every genuine extension.
    """
    from app.database.models import RevertPoint

    # A real file, because a usable point is one whose recorded size and
    # mtime still match the file being processed. Pointing at a path that
    # does not exist would make this exercise the supersede path while
    # claiming to test the extend one.
    source = recycle.parent / "Show.mkv"
    source.write_bytes(b"the file as the previous job left it")

    db, old_sidecar = _unusable_point(
        recycle, monkeypatch,
        manifest=json.dumps({"version": 2, "streams": [], "path": str(source),
                             "container": "mkv", "duration": 60.0}),
        media_path=source)
    # Make it usable: a current manifest describing the original.
    point = db.query(RevertPoint).one()
    manifest = json.loads(_capture_manifest())
    point.manifest = json.dumps(manifest)
    db.commit()

    _patch_probes(monkeypatch, _ORIGINAL, _PRODUCED, source_path=str(source))
    commands = _patch_ffmpeg(monkeypatch)
    # The existing sidecar holds the one stream the earlier job destroyed.
    # Registered explicitly because nothing in this test wrote it through
    # the mocked FFmpeg, so the harness cannot derive its contents.
    _MOCK_STATE["sidecars"][str(old_sidecar)] = {
        "streams": [dict(_ORIGINAL["streams"][2], index=0)],
        "format": {"format_name": "matroska,webm"},
    }

    _capture(app_cfg=enabled, file_id=7, job_id=2, input_path=str(source))

    inputs = [commands[0][i + 1] for i, a in enumerate(commands[0]) if a == "-i"]
    assert inputs == [str(source), str(old_sidecar)]


def _capture_manifest():
    """A current-layout manifest for the standard fixture original."""
    from app.core.revert import build_manifest, match_streams

    manifest = build_manifest(_ORIGINAL, original_path="/m/Show.mkv",
                              original_container="mkv")
    matches = match_streams(manifest, _PRODUCED)
    lost = [s for s, i in matches if i is None]
    sidecar_indices = {id(s): n for n, s in enumerate(lost)}
    for stream, produced_index in matches:
        stream["processed_index"] = produced_index
        if id(stream) in sidecar_indices:
            stream["sidecar_index"] = sidecar_indices[id(stream)]
    return json.dumps(manifest)


def test_a_point_whose_sidecar_is_gone_is_reported_as_unusable(recycle,
                                                               monkeypatch):
    """
    Extending needs the previous sidecar as an input. Without it on disk
    the rebuild would fail at FFmpeg with a missing-input error instead of
    doing the useful thing — and the row is still returned so it can be
    taken over rather than duplicated.
    """
    from sqlalchemy.orm import sessionmaker

    from tests.conftest import memory_engine

    from app.core.revert import MANIFEST_VERSION
    from app.core.revert_capture import _load_existing_point
    from app.database.models import Base, RevertPoint
    import app.database.session as session_mod

    engine = memory_engine()
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine)
    monkeypatch.setattr(session_mod, "SessionLocal", factory)

    db = factory()
    db.add(RevertPoint(file_id=7, sidecar_path=str(recycle / "gone"),
                       sidecar_size=7,
                       manifest=json.dumps({"version": MANIFEST_VERSION,
                                            "streams": []}),
                       original_path="/m/Show.mkv"))
    db.commit()

    existing = _load_existing_point(7, "/m/Show.mkv")
    assert existing is not None
    assert existing.usable is False


# ── Recording the row ────────────────────────────────────────────────────────

@pytest.fixture
def db(monkeypatch):
    from sqlalchemy.orm import sessionmaker

    from tests.conftest import memory_engine

    from app.database.models import Base

    engine = memory_engine()
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
