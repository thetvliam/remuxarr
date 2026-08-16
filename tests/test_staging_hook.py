"""
run_staged_subprocess's before_staging hook.

The hook exists for one reason: it is the only point in a run where the
original file and the finished output both exist. One line further on,
os.replace starts overwriting originals and the pre-job state is gone.
Every test here is really about that window and about the promise that
declining inside it costs nothing.

Three properties, in the order they matter:

  1. The window is real. When the hook runs, the originals must still be
     byte-for-byte what they were and the finished temps must all exist.
     A hook that fires after staging, or before the subprocess produced
     anything, is useless for its purpose while still looking wired up.

  2. Declining is free. Returning an error string aborts with every
     original untouched and no .part or temp files left behind. This is
     what makes "refuse to process when a revert point cannot be
     recorded" a genuine option instead of a best-effort one.

  3. Absent means unchanged. The default path must behave exactly as it
     did before the hook existed, because every current caller uses it.

Verified by mutation, 9 applied, 9 killed. One initially SURVIVED and is
worth recording: the ffmpeg adapter passing its wrapper through
unconditionally instead of only when a hook was supplied. The wrapper
closes over the caller's hook, so that calls None(...) and raises
TypeError on every hookless job — which is every job in the codebase
today. Nothing caught it, because every test in this file talked to
run_staged_subprocess directly and never went through the adapter. The
three adapter tests at the end exist for that, and they run real FFmpeg,
because a mocked adapter would have had the same blind spot.

The rest, killed on the first run:

  • Hook relocated to after the swap loop     → killed (originals already
                                                 overwritten when it ran)
  • Hook relocated above the failure checks   → killed (runs on failure,
                                                 and with temps absent)
  • Hook removed entirely                     → killed
  • Error string ignored, run continues       → killed
  • Error returned but temps not cleaned      → killed
  • Error swallowed and reported as success   → killed
  • Hook called even when None                → killed
  • Return value truthiness inverted          → killed

No equivalent mutants.
"""
import asyncio
import os
import shutil
import subprocess

import pytest

from app.core.decision import analyze_file
from app.core.ffmpeg import execute_ffmpeg_combined
from app.core.subprocess_runner import StagedOutput, run_staged_subprocess
from tests.conftest import make_file_info, make_track


def _writer_cmd(pairs):
    script = "; ".join(f"printf '%s' '{content}' > '{path}'"
                       for path, content in pairs)
    return ["/bin/sh", "-c", script]


def _run(cmd, outputs, **kw):
    return asyncio.run(run_staged_subprocess(cmd, outputs, **kw))


# ── The window ───────────────────────────────────────────────────────────────

def test_hook_sees_originals_intact_and_outputs_finished(tmp_path):
    """
    The whole point. Both versions of the file have to be readable at the
    same moment, or the hook cannot compare them.
    """
    temp = tmp_path / "a.tmp"
    final = tmp_path / "a.mkv"
    final.write_bytes(b"ORIGINAL")

    seen = {}

    async def hook():
        seen["original"] = final.read_bytes()
        seen["output"] = temp.read_bytes()
        seen["part_exists"] = (tmp_path / "a.mkv.part").exists()
        return None

    res = _run(_writer_cmd([(str(temp), "NEW")]),
               [StagedOutput(temp_path=str(temp), final_path=str(final))],
               before_staging=hook)

    assert res.success is True
    assert seen["original"] == b"ORIGINAL", "the original was already overwritten"
    assert seen["output"] == b"NEW", "the finished output was not available"
    assert seen["part_exists"] is False, "staging had already started"
    assert final.read_bytes() == b"NEW", "the swap did not happen afterwards"


def test_hook_does_not_run_when_the_subprocess_fails(tmp_path):
    """
    Nothing was produced, so there is nothing to compare and no revert
    point to record. Running here would have the hook reason about a
    file that does not exist.
    """
    temp = tmp_path / "a.tmp"
    final = tmp_path / "a.mkv"
    final.write_bytes(b"ORIGINAL")
    calls = []

    async def hook():
        calls.append(1)
        return None

    res = _run(["/bin/sh", "-c", "exit 3"],
               [StagedOutput(temp_path=str(temp), final_path=str(final))],
               before_staging=hook)

    assert res.success is False
    assert calls == [], "hook ran despite the subprocess failing"


def test_hook_does_not_run_when_a_temp_is_missing(tmp_path):
    """
    A clean exit with no output file is already a failure path. The hook
    must sit after that check, not before it.
    """
    temp = tmp_path / "a.tmp"
    final = tmp_path / "a.mkv"
    calls = []

    async def hook():
        calls.append(1)
        return None

    res = _run(["/bin/sh", "-c", "true"],
               [StagedOutput(temp_path=str(temp), final_path=str(final))],
               before_staging=hook)

    assert res.success is False
    assert calls == []


# ── Declining ────────────────────────────────────────────────────────────────

def test_returning_an_error_aborts_the_run(tmp_path):
    temp = tmp_path / "a.tmp"
    final = tmp_path / "a.mkv"
    final.write_bytes(b"ORIGINAL")

    async def hook():
        return "no room for a revert point"

    res = _run(_writer_cmd([(str(temp), "NEW")]),
               [StagedOutput(temp_path=str(temp), final_path=str(final))],
               before_staging=hook)

    assert res.success is False
    assert "revert point" in res.error


def test_declining_leaves_every_original_untouched(tmp_path):
    """
    The property the refuse-to-process setting is built on: a refusal
    inside the hook costs the user nothing but the wasted encode.
    """
    temp = tmp_path / "a.tmp"
    final = tmp_path / "a.mkv"
    final.write_bytes(b"ORIGINAL")

    async def hook():
        return "declined"

    _run(_writer_cmd([(str(temp), "NEW")]),
         [StagedOutput(temp_path=str(temp), final_path=str(final))],
         before_staging=hook)

    assert final.read_bytes() == b"ORIGINAL"


def test_declining_leaves_no_temp_or_part_files(tmp_path):
    temp = tmp_path / "a.tmp"
    final = tmp_path / "a.mkv"
    final.write_bytes(b"ORIGINAL")

    async def hook():
        return "declined"

    _run(_writer_cmd([(str(temp), "NEW")]),
         [StagedOutput(temp_path=str(temp), final_path=str(final))],
         before_staging=hook)

    assert not temp.exists(), "temp left behind after a declined run"
    assert not (tmp_path / "a.mkv.part").exists()


def test_declining_aborts_every_output_not_just_the_first(tmp_path):
    """
    All-or-nothing is this function's central contract, and the hook
    must not become the one thing that partially applies a run.
    """
    temps = [tmp_path / f"{n}.tmp" for n in ("a", "b")]
    finals = [tmp_path / f"{n}.mkv" for n in ("a", "b")]
    for f in finals:
        f.write_bytes(b"ORIGINAL")

    async def hook():
        return "declined"

    _run(_writer_cmd([(str(t), "NEW") for t in temps]),
         [StagedOutput(temp_path=str(t), final_path=str(f))
          for t, f in zip(temps, finals)],
         before_staging=hook)

    assert all(f.read_bytes() == b"ORIGINAL" for f in finals)
    assert not any(t.exists() for t in temps)


def test_a_raising_hook_propagates_and_leaves_originals_untouched(tmp_path):
    """
    An unexpected error is not a considered refusal and should be loud.
    The outer handler still has to protect the file.
    """
    temp = tmp_path / "a.tmp"
    final = tmp_path / "a.mkv"
    final.write_bytes(b"ORIGINAL")

    async def hook():
        raise RuntimeError("database went away")

    with pytest.raises(RuntimeError):
        _run(_writer_cmd([(str(temp), "NEW")]),
             [StagedOutput(temp_path=str(temp), final_path=str(final))],
             before_staging=hook)

    assert final.read_bytes() == b"ORIGINAL"
    assert not temp.exists()
    assert not (tmp_path / "a.mkv.part").exists()


# ── Absent ───────────────────────────────────────────────────────────────────

def test_no_hook_behaves_exactly_as_before(tmp_path):
    temp = tmp_path / "a.tmp"
    final = tmp_path / "a.mkv"
    final.write_bytes(b"ORIGINAL")

    res = _run(_writer_cmd([(str(temp), "NEW")]),
               [StagedOutput(temp_path=str(temp), final_path=str(final))])

    assert res.success is True
    assert final.read_bytes() == b"NEW"
    assert not temp.exists()


def test_returning_none_continues_the_run(tmp_path):
    """
    None means "carry on" — distinct from an empty string, which a
    truthiness bug would treat the same way but a caller might return
    meaning "no error message".
    """
    temp = tmp_path / "a.tmp"
    final = tmp_path / "a.mkv"

    async def hook():
        return None

    res = _run(_writer_cmd([(str(temp), "NEW")]),
               [StagedOutput(temp_path=str(temp), final_path=str(final))],
               before_staging=hook)

    assert res.success is True
    assert final.read_bytes() == b"NEW"


# ── The ffmpeg adapter ───────────────────────────────────────────────────────
#
# execute_ffmpeg_combined wraps the caller's hook so it receives the temp
# output path. The wrapper closes over `before_staging`, so passing it
# through unconditionally would call None(...) on every hookless job — which
# is every job in the codebase today. Nothing above catches that, because
# everything above talks to run_staged_subprocess directly.

ffmpeg_required = pytest.mark.skipif(
    shutil.which("ffmpeg") is None, reason="ffmpeg not available",
)


def _tiny_job(tmp_path, settings):
    """A real one-second file with a droppable foreign audio track."""
    source = tmp_path / "source.mkv"
    subprocess.run(
        ["ffmpeg", "-v", "error", "-y",
         "-f", "lavfi", "-i", "testsrc=size=160x120:rate=10:duration=1",
         "-f", "lavfi", "-i", "sine=frequency=440:duration=1",
         "-f", "lavfi", "-i", "sine=frequency=880:duration=1",
         "-map", "0:v", "-map", "1:a", "-map", "2:a",
         "-metadata:s:a:0", "language=eng", "-metadata:s:a:1", "language=fre",
         "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac",
         "-f", "matroska", str(source)],
        check=True,
    )

    settings["prefer_mp4_container"] = False
    tracks = [
        make_track(stream_index=0, track_type="video", codec="h264"),
        make_track(stream_index=1, track_type="audio", codec="aac",
                   language="eng", is_default=True),
        make_track(stream_index=2, track_type="audio", codec="aac",
                   language="fre"),
    ]
    decision = analyze_file(
        make_file_info(path=str(source), container="mkv", video_codec="h264"),
        tracks, settings,
    )
    assert decision.should_process, "fixture no longer produces work"
    return source, decision, tracks


@ffmpeg_required
def test_adapter_runs_a_job_with_no_hook(tmp_path, settings):
    """
    The default path, and the one every existing caller uses. A wrapper
    passed through unconditionally raises TypeError here on every job.
    """
    source, decision, tracks = _tiny_job(tmp_path, settings)

    result, _ = asyncio.run(execute_ffmpeg_combined(
        str(source), str(source), decision, tracks, [], job_id=1,
    ))

    assert result.success is True, result.error


@ffmpeg_required
def test_adapter_hands_the_hook_the_finished_temp_output(tmp_path, settings):
    """
    Not output_path: for an in-place remux that name still holds the
    ORIGINAL at this point, so a hook given it would compare the source
    against itself and conclude nothing was lost.
    """
    source, decision, tracks = _tiny_job(tmp_path, settings)
    original_bytes = source.read_bytes()
    seen = {}

    async def hook(temp_output):
        seen["path"] = temp_output
        seen["output_size"] = os.path.getsize(temp_output)
        seen["source_unchanged"] = source.read_bytes() == original_bytes
        return None

    result, _ = asyncio.run(execute_ffmpeg_combined(
        str(source), str(source), decision, tracks, [], job_id=1,
        before_staging=hook,
    ))

    assert result.success is True, result.error
    assert seen["path"] != str(source), "hook was handed the source path"
    assert seen["output_size"] > 0
    assert seen["source_unchanged"] is True
    assert source.read_bytes() != original_bytes, "the swap did not happen after"


@ffmpeg_required
def test_adapter_hook_can_abort_a_real_job(tmp_path, settings):
    source, decision, tracks = _tiny_job(tmp_path, settings)
    original_bytes = source.read_bytes()

    async def hook(_temp_output):
        return "no room for a revert point"

    result, _ = asyncio.run(execute_ffmpeg_combined(
        str(source), str(source), decision, tracks, [], job_id=1,
        before_staging=hook,
    ))

    assert result.success is False
    assert "revert point" in result.error
    assert source.read_bytes() == original_bytes, "source was modified anyway"
