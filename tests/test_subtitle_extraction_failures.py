"""
Regression tests for subtitle-extraction failure handling.

Combined pass silent subtitle loss:
    execute_ffmpeg_combined used to stage the main output first and move
    the SRT sidecars afterwards with partial-success semantics. Because
    extracted subtitles are removed from the muxed output, "main file
    staged, SRT temp missing" meant the subtitle no longer existed
    anywhere — gone from the media file, never written to disk — while
    the job still recorded SUCCESS with only a log warning. The fix
    passes ALL outputs (main + every SRT) to run_staged_subprocess as one
    staged set, whose two-phase verify-then-swap semantics guarantee
    either every output lands or nothing is touched. The tests here pin
    that guarantee at the layer it lives in (run_staged_subprocess),
    using trivial shell commands rather than FFmpeg so they run anywhere.

Two-pass path lacked the encoding-failure → manual-review routing:
    only the combined path classified subtitle encoding failures via
    _is_subtitle_encoding_failure; the two-pass extraction loop failed
    the job outright for the identical underlying file. The routing fix
    lives in _run_job (async + DB, exercised in integration), but its
    load-bearing assumption is unit-testable: the classifier must match
    the error shape of a STANDALONE single-extraction command, not just
    the combined multi-output form. FFmpeg's canonical message for the
    production case is locked in below verbatim.

Run from the project root:
    pytest tests/ -v
"""
import asyncio
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.core.subprocess_runner import StagedOutput, run_staged_subprocess
from app.core.worker import _is_subtitle_encoding_failure


# ═══════════════════════════════════════════════════════════════════════════
# All-or-nothing staging invariant
# ═══════════════════════════════════════════════════════════════════════════

def _no_debris(*paths: str) -> bool:
    """True when neither the temp nor its .part sibling survives."""
    return not any(
        os.path.exists(p) for path in paths for p in (path, path + ".part")
    )


def test_missing_output_fails_everything_and_originals_survive(tmp_path):
    """
    The scenario, reduced to its invariant: the subprocess exits
    cleanly (rc=0) but produces only SOME of its declared outputs — the
    exact "main remux fine, SRT temp missing" shape that previously
    recorded success while a subtitle silently vanished.

    Required outcome: the run FAILS as a whole, and every pre-existing
    destination file (the user's media file and any earlier sidecar) is
    byte-for-byte untouched — no half-staged set, no .part debris.
    """
    main_tmp = tmp_path / "main.tmp"
    srt_tmp  = tmp_path / "srt.tmp"          # deliberately never created
    main_dst = tmp_path / "movie.mkv"
    srt_dst  = tmp_path / "movie.en.srt"

    main_dst.write_bytes(b"ORIGINAL MEDIA BYTES")
    srt_dst.write_bytes(b"ORIGINAL SIDECAR BYTES")

    cmd = ["/bin/sh", "-c", f"printf 'NEW MEDIA' > '{main_tmp}'"]
    outputs = [
        StagedOutput(temp_path=str(main_tmp), final_path=str(main_dst)),
        StagedOutput(temp_path=str(srt_tmp),  final_path=str(srt_dst)),
    ]

    result = asyncio.run(run_staged_subprocess(cmd, outputs))

    assert not result.success, (
        "A clean exit with a missing declared output must fail the whole "
        "run — reporting success here is the exact silent-subtitle-loss "
        "bug."
    )
    assert str(srt_tmp) in (result.error or ""), (
        "The error should name the missing temp so the failure is "
        "diagnosable from the job's error_message."
    )
    assert main_dst.read_bytes() == b"ORIGINAL MEDIA BYTES", (
        "The media file was modified even though the staged set was "
        "incomplete — originals must survive a partial-output run."
    )
    assert srt_dst.read_bytes() == b"ORIGINAL SIDECAR BYTES"
    assert _no_debris(str(main_tmp), str(srt_tmp)), (
        "Temps/.part files left behind after a failed run."
    )


def test_complete_output_set_stages_together(tmp_path):
    """
    Success-path counterpart: when every declared output exists, all of
    them replace their destinations and no temps or .part files remain.
    Guards against over-rotating into 'nothing ever stages'.
    """
    main_tmp = tmp_path / "main.tmp"
    srt_tmp  = tmp_path / "srt.tmp"
    main_dst = tmp_path / "movie.mkv"
    srt_dst  = tmp_path / "movie.en.srt"

    main_dst.write_bytes(b"OLD MEDIA")
    srt_dst.write_bytes(b"OLD SIDECAR")

    cmd = [
        "/bin/sh", "-c",
        f"printf 'NEW MEDIA' > '{main_tmp}' && printf 'NEW SIDECAR' > '{srt_tmp}'",
    ]
    outputs = [
        StagedOutput(temp_path=str(main_tmp), final_path=str(main_dst)),
        StagedOutput(temp_path=str(srt_tmp),  final_path=str(srt_dst)),
    ]

    result = asyncio.run(run_staged_subprocess(cmd, outputs))

    assert result.success, f"Expected success, got error: {result.error!r}"
    assert main_dst.read_bytes() == b"NEW MEDIA"
    assert srt_dst.read_bytes() == b"NEW SIDECAR"
    assert _no_debris(str(main_tmp), str(srt_tmp))


def test_nonzero_exit_fails_and_originals_survive(tmp_path):
    """
    A subprocess that produces its outputs but exits non-zero (the
    combined pass's subtitle-cascade shape) must also leave every
    original untouched and clean up its temps.
    """
    main_tmp = tmp_path / "main.tmp"
    main_dst = tmp_path / "movie.mkv"
    main_dst.write_bytes(b"ORIGINAL")

    cmd = ["/bin/sh", "-c", f"printf 'PARTIAL' > '{main_tmp}'; exit 1"]
    outputs = [StagedOutput(temp_path=str(main_tmp), final_path=str(main_dst))]

    result = asyncio.run(run_staged_subprocess(cmd, outputs))

    assert not result.success
    assert result.returncode == 1
    assert main_dst.read_bytes() == b"ORIGINAL"
    assert _no_debris(str(main_tmp))


# ═══════════════════════════════════════════════════════════════════════════
# Encoding-failure classifier must cover the single-command error shape
# ═══════════════════════════════════════════════════════════════════════════

def test_classifier_matches_standalone_extraction_error():
    """
    FFmpeg's verbatim message for the production failure this feature
    exists for (a text subtitle whose bytes aren't valid UTF-8), as
    emitted by a STANDALONE `-map 0:N -c:s srt` command — the two-pass
    path's shape, which carries no combined-command markers like
    "sist#". If the classifier ever stops matching this, the two-pass
    routing silently degrades back to raw job failures.
    """
    err = (
        "[srt @ 0x55d1c2a4b0c0] Invalid UTF-8 in decoded subtitles text; "
        "maybe missing -sub_charenc option\n"
        "Error while decoding stream #0:2: Invalid data found when "
        "processing input"
    )
    assert _is_subtitle_encoding_failure(err) is True


def test_classifier_matches_combined_command_error():
    """The multi-output form (sist# marker) must keep matching too."""
    err = (
        "Error while decoding sist#0:2 -> #1:0/srt: Invalid data found "
        "when processing input"
    )
    assert _is_subtitle_encoding_failure(err) is True


def test_classifier_rejects_non_subtitle_failures():
    """
    Disk/filesystem and audio-copy failures must NOT be routed to
    subtitle review — they'd strand a genuinely failed job in the wrong
    UI with a misleading "non-UTF-8 subtitle" reason.
    """
    assert _is_subtitle_encoding_failure(
        "av_interleaved_write_frame(): No space left on device"
    ) is False
    assert _is_subtitle_encoding_failure(
        "[aost#0:1/copy] Error submitting a packet to the muxer: "
        "Invalid data found when processing input"
    ) is False
    assert _is_subtitle_encoding_failure(None) is False
    assert _is_subtitle_encoding_failure("") is False
