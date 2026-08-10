"""
_process_next_forge — the loop step that runs an AC3 forge job.

Uncovered end to end. It matters more than its size suggests because forge is
the only pipeline that rewrites a file IN PLACE: run_forge_command is handed
output_path=input_path, so there is no untouched original to fall back on if
this function mishandles a failure. Everything the main remux pipeline gets
from staging a separate output file, forge has to get right here.

The collaborators all come from app.core.forge and are imported inside the
function body, so they are patched on that module. What is under test is the
orchestration — what gets claimed, what gets broadcast, which branch a
failure takes, and what is passed to whom — not FFmpeg itself.

Verified by mutation: 17 mutations of _process_next_forge, each killed by at
least one test here. Two are worth naming because nothing else catches them:

  • dropping the load-time settle path (`if job_data is None:` → `if False:`),
    which strands a job in the UI at whatever the last poll saw, and
  • letting the command builders sit OUTSIDE the try, so a ValueError from an
    unknown container escapes uncaught and leaves the row at "processing" —
    the exact wedged state recover_interrupted_jobs exists to clean up.
"""
import asyncio
from dataclasses import dataclass

import pytest


# ── Harness ──────────────────────────────────────────────────────────────────

@dataclass
class _Result:
    success:     bool
    output_path: str | None
    output_size: int | None
    error:       str | None


class _WS:
    """Records broadcasts in order so the event sequence can be asserted."""

    def __init__(self):
        self.events = []

    async def broadcast_json(self, payload):
        self.events.append(payload)

    def names(self):
        return [e["event"] for e in self.events]

    def first(self, name):
        return next(e for e in self.events if e["event"] == name)


def _job_data(**over):
    data = {
        "file_path":            "/media/Show/ep.mkv",
        "is_undo":              False,
        "container":            "mkv",
        "aac_stream_index":     1,
        "audio_track_count":    2,
        "undo_audio_output_index": 1,
        "job_timeout_minutes":  120,
    }
    data.update(over)
    return data


@pytest.fixture
def forge(monkeypatch, tmp_path):
    """
    Patch app.core.forge with recording stubs and return the namespace so a
    test can adjust individual pieces. Defaults describe a successful add.
    """
    import app.core.forge as forge_mod
    import app.core.worker as worker

    calls = {
        "claimed":   0,
        "finish":    [],
        "progress":  [],
        "built":     [],
        "ran":       [],
        "plex":      [],
    }

    def claim_next_forge_job():
        calls["claimed"] += 1
        return 7 if calls["claimed"] == 1 else None

    def load_forge_job_data(job_id):
        return _job_data()

    def build_add_ac3_command(**kw):
        calls["built"].append(("add", kw))
        return ["ffmpeg", "add"]

    def build_undo_command(**kw):
        calls["built"].append(("undo", kw))
        return ["ffmpeg", "undo"]

    async def run_forge_command(**kw):
        calls["ran"].append(kw)
        return _Result(True, kw["output_path"], 4242, None)

    def update_forge_progress(job_id, percent, action):
        calls["progress"].append((job_id, percent, action))

    def finish_forge_job(job_id, success, output_size, error):
        calls["finish"].append(
            {"job_id": job_id, "success": success,
             "output_size": output_size, "error": error}
        )

    def load_forge_final_state(job_id):
        return {"status": "success", "filename": "ep.mkv", "error": None}

    for name, fn in [
        ("claim_next_forge_job", claim_next_forge_job),
        ("load_forge_job_data", load_forge_job_data),
        ("build_add_ac3_command", build_add_ac3_command),
        ("build_undo_command", build_undo_command),
        ("run_forge_command", run_forge_command),
        ("update_forge_progress", update_forge_progress),
        ("finish_forge_job", finish_forge_job),
        ("load_forge_final_state", load_forge_final_state),
    ]:
        monkeypatch.setattr(forge_mod, name, fn)

    # Keep staging inside tmp_path — the real _pick_temp_dir consults free
    # space on TEMP_DIR and can fall back to the media directory, which does
    # not exist here.
    monkeypatch.setattr(worker, "_pick_temp_dir", lambda _p: str(tmp_path))

    # Plex notification is fire-and-forget; record instead of firing.
    monkeypatch.setattr(
        worker, "_load_forge_plex_notify_data",
        lambda jid: calls["plex"].append(jid) or {"job_id": jid},
    )

    async def _noop_plex(data, loop):
        pass

    monkeypatch.setattr(worker, "_trigger_plex_notify", _noop_plex)

    forge_mod._calls = calls
    return forge_mod


def _run(ws):
    import app.core.worker as worker

    return asyncio.run(worker._process_next_forge(ws))


# ── Claiming ─────────────────────────────────────────────────────────────────

def test_no_pending_job_is_a_no_op(forge, monkeypatch):
    """
    Returns False so _loop knows nothing ran and can go back to sleep.
    Returning True on an empty queue would spin the loop.
    """
    monkeypatch.setattr(forge, "claim_next_forge_job", lambda: None)
    ws = _WS()

    assert _run(ws) is False
    assert ws.events == [], "broadcast for a job that was never claimed"


def test_a_claimed_job_announces_itself_before_doing_any_work(forge):
    """
    forge_job_started must precede the run, or the UI shows nothing while a
    long in-place rewrite is already underway.
    """
    ws = _WS()
    assert _run(ws) is True

    assert ws.names()[0] == "forge_job_started"
    assert ws.events[0]["job_id"] == 7


# ── Settling at load time ────────────────────────────────────────────────────

def test_a_job_that_settles_at_load_time_still_broadcasts_its_result(forge, monkeypatch):
    """
    load_forge_job_data can reach a terminal state by itself — file missing,
    probe failure, AC3 already absent (→ undone), or a layout mismatch
    (→ failed). This used to return silently, leaving the row settled in the
    database but the UI showing it running until some later poll happened by.
    """
    monkeypatch.setattr(forge, "load_forge_job_data", lambda jid: None)
    monkeypatch.setattr(
        forge, "load_forge_final_state",
        lambda jid: {"status": "undone", "filename": "ep.mkv", "error": None},
    )
    ws = _WS()

    assert _run(ws) is True, "a claimed-and-settled job still counts as ran"

    done = ws.first("forge_job_completed")
    assert done["status"] == "undone"
    assert done["filename"] == "ep.mkv"


def test_a_job_that_settles_at_load_time_never_runs_ffmpeg(forge, monkeypatch):
    """The whole point of the early exit: no command is built or executed."""
    monkeypatch.setattr(forge, "load_forge_job_data", lambda jid: None)
    _run(_WS())

    assert forge._calls["built"] == []
    assert forge._calls["ran"] == []


def test_settling_at_load_time_does_not_double_finish_the_job(forge, monkeypatch):
    """
    load_forge_job_data already wrote the terminal state. Calling
    finish_forge_job again would overwrite it — an "undone" job would be
    rewritten as a plain failure.
    """
    monkeypatch.setattr(forge, "load_forge_job_data", lambda jid: None)
    _run(_WS())

    assert forge._calls["finish"] == []


# ── In-place staging ─────────────────────────────────────────────────────────

def test_the_temp_file_is_named_from_the_job_id_not_the_source(forge, tmp_path):
    """
    Appending a suffix to an already-long Sonarr-style filename can exceed the
    255-byte NAME_MAX component limit — confirmed in production on the main
    pipeline with a 247-byte name. A job id is always short and unique.
    """
    _run(_WS())

    temp = forge._calls["ran"][0]["temp_path"]
    assert temp == str(tmp_path / "forge_7.forge_tmp")
    assert "ep" not in temp.rsplit("/", 1)[-1], (
        "temp name derived from the source filename — NAME_MAX risk"
    )


def test_the_file_is_rewritten_in_place(forge):
    """
    output_path is the input path. This is what makes every failure branch
    below load-bearing: there is no separate output to discard.
    """
    _run(_WS())
    ran = forge._calls["ran"][0]
    assert ran["output_path"] == ran["input_path"] == "/media/Show/ep.mkv"


def test_the_timeout_matches_the_main_pipeline_formula(forge):
    """Minutes → seconds, the same conversion _run_job uses."""
    _run(_WS())
    assert forge._calls["ran"][0]["timeout_seconds"] == 7200.0


def test_no_configured_timeout_means_no_timeout(forge, monkeypatch):
    """
    A falsy timeout must become None, not 0.0 — passing 0.0 to the runner
    would time every job out instantly.
    """
    monkeypatch.setattr(
        forge, "load_forge_job_data",
        lambda jid: _job_data(job_timeout_minutes=0),
    )
    _run(_WS())
    assert forge._calls["ran"][0]["timeout_seconds"] is None


# ── Add vs undo ──────────────────────────────────────────────────────────────

def test_an_add_job_builds_the_add_command(forge):
    _run(_WS())

    kind, kw = forge._calls["built"][0]
    assert kind == "add"
    assert kw["aac_stream_index"] == 1
    assert kw["audio_track_count"] == 2


def test_an_undo_job_uses_the_freshly_resolved_track_index(forge, monkeypatch):
    """
    The undo index comes from a fresh probe at load time, NOT the stored
    add-time audio_track_count. After any pipeline that drops a track, the
    stored index points past the end, and FFmpeg silently ignores an
    unmatched negative map — the old undo rewrote the file unchanged and
    recorded a false "undone" with the AC3 still embedded.
    """
    monkeypatch.setattr(
        forge, "load_forge_job_data",
        lambda jid: _job_data(is_undo=True, undo_audio_output_index=3,
                              audio_track_count=99),
    )
    _run(_WS())

    kind, kw = forge._calls["built"][0]
    assert kind == "undo"
    assert kw["ac3_audio_output_index"] == 3
    assert "audio_track_count" not in kw, (
        "undo built from the stale stored count instead of the fresh probe"
    )


# ── Failure handling ─────────────────────────────────────────────────────────

def test_an_unknown_container_fails_the_job_instead_of_escaping(forge, monkeypatch):
    """
    The builders raise ValueError on containers their format map doesn't know,
    rather than silently defaulting to matroska and corrupting the file. That
    raise happens INSIDE the try, so it marks the job failed. If it escaped,
    the row would stay at "processing" — the wedged state that has no exit
    until the next restart.
    """
    def _boom(**kw):
        raise ValueError("unsupported container: avi")

    monkeypatch.setattr(forge, "build_add_ac3_command", _boom)
    ws = _WS()

    assert _run(ws) is True

    assert len(forge._calls["finish"]) == 1
    fin = forge._calls["finish"][0]
    assert fin["success"] is False
    assert "unsupported container" in fin["error"]


def test_a_crash_mid_run_still_finishes_the_job(forge, monkeypatch):
    """
    An exception out of run_forge_command must reach finish_forge_job. Forge
    is in-place, so a job abandoned at "processing" also leaves a possibly
    half-written file with nothing recording that fact.
    """
    async def _boom(**kw):
        raise RuntimeError("ffmpeg vanished")

    monkeypatch.setattr(forge, "run_forge_command", _boom)
    _run(_WS())

    fin = forge._calls["finish"][0]
    assert fin["success"] is False
    assert "ffmpeg vanished" in fin["error"]
    assert fin["output_size"] is None


def test_a_failed_run_is_recorded_with_its_error(forge, monkeypatch):
    """A clean (non-raising) failure still has to be recorded as a failure."""
    async def _fail(**kw):
        return _Result(False, None, None, "exit 1: invalid data")

    monkeypatch.setattr(forge, "run_forge_command", _fail)
    _run(_WS())

    fin = forge._calls["finish"][0]
    assert fin["success"] is False
    assert fin["error"] == "exit 1: invalid data"


def test_a_successful_run_records_the_output_size(forge):
    _run(_WS())

    fin = forge._calls["finish"][0]
    assert fin["success"] is True
    assert fin["output_size"] == 4242
    assert fin["error"] is None


# ── Completion and notification ──────────────────────────────────────────────

def test_progress_reaches_both_the_database_and_the_socket(forge, monkeypatch):
    """
    The UI reads the socket, but a reconnecting client reads the row — so a
    progress update that only broadcasts leaves a refreshed page stuck at
    whatever it last saw.
    """
    from app.core.forge import ForgeProgress

    async def _run_with_progress(**kw):
        await kw["progress_callback"](
            ForgeProgress(percent=41.5, current_time=12.0,
                          speed="2.1x", action="Adding AC3 5.1 track")
        )
        return _Result(True, kw["output_path"], 10, None)

    monkeypatch.setattr(forge, "run_forge_command", _run_with_progress)
    ws = _WS()
    _run(ws)

    assert forge._calls["progress"] == [(7, 41.5, "Adding AC3 5.1 track")]
    prog = ws.first("forge_job_progress")
    assert prog["progress"] == 41.5
    assert prog["speed"] == "2.1x"


def test_a_finished_job_broadcasts_its_final_state(forge):
    ws = _WS()
    _run(ws)

    assert ws.names()[-1] in ("forge_job_completed",)
    done = ws.first("forge_job_completed")
    assert done["job_id"] == 7
    assert done["status"] == "success"


@pytest.mark.parametrize("status", ["success", "undone"])
def test_plex_is_told_the_file_changed(forge, monkeypatch, status):
    """
    Both terminal states rewrite the file in place, so Plex's indexed stream
    metadata is stale either way and clients won't see the new track until
    Plex re-reads it.
    """
    monkeypatch.setattr(
        forge, "load_forge_final_state",
        lambda jid: {"status": status, "filename": "ep.mkv", "error": None},
    )
    _run(_WS())

    assert forge._calls["plex"] == [7]


def test_a_failed_job_does_not_touch_plex(forge, monkeypatch):
    """Nothing changed on disk, so there is nothing for Plex to re-read."""
    monkeypatch.setattr(
        forge, "load_forge_final_state",
        lambda jid: {"status": "failed", "filename": "ep.mkv", "error": "boom"},
    )
    _run(_WS())

    assert forge._calls["plex"] == []
