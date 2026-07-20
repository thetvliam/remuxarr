"""
Regression tests driven by a real sample-video library.

This collection of files (diverse containers, codecs, audio layouts, and
subtitle types) has repeatedly surfaced real bugs — most recently the
container-detection regression where ffprobe's shared "matroska,webm"
format_name caused every MKV to be misread as a webm container. A test
that runs the ACTUAL decision engine against these ACTUAL file structures
catches that class of problem immediately, which unit tests on synthetic
inputs missed.

Two data sources, in priority order:

  1. LIVE — if REMUXARR_SAMPLE_DIR points at a directory of real media
     files, each is probed with the real ffprobe and fed through the real
     pipeline. This is what to run after touching decision/probe/ffmpeg
     code:
         REMUXARR_SAMPLE_DIR=/media/testing/for_processing pytest tests/test_sample_library.py -v

  2. FIXTURE (default) — a committed snapshot parsed from an
     `ffmpeg -i` / ffprobe dump of the same library
     (tests/sample_library/sample_files_before.json). Runs anywhere,
     including CI, with no media files present. Regenerate it from a
     fresh dump with:
         python tests/sample_library/parse_ffprobe_dump.py dump.txt \
             tests/sample_library/sample_files_before.json

Both paths converge on the same {path, format_name, streams} record shape
and run it through the production extract_format_info / extract_tracks /
analyze_file, so the fixture exercises the real parsing code too.

golden_decisions.json is the expected per-file decision (container +
outcome + target container) under CONFIG below. It's a deliberate
snapshot: an INTENTIONAL behavior change should be reviewed and then
committed by regenerating it —
    REMUXARR_UPDATE_GOLDEN=1 pytest tests/test_sample_library.py::test_decision_snapshot
— which rewrites the file so the diff is visible in review.
"""
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.config import settings as app_settings
from app.core.decision import IMAGE_BASED_SUBS, analyze_file
from app.core.ffmpeg import build_ffmpeg_command, determine_output_path
from app.core.probe import extract_format_info, extract_tracks, is_media_file

_HERE = Path(__file__).parent
_FIXTURE = _HERE / "sample_library" / "sample_files_before.json"
_GOLDEN = _HERE / "sample_library" / "golden_decisions.json"

# The settings the golden snapshot was generated under. Keep in sync with
# golden_decisions.json (regenerate the golden if you change these).
CONFIG = {
    "keep_audio_languages":          ["eng"],
    "keep_default_audio":            True,
    "keep_subtitle_languages":       ["eng"],
    "keep_forced_subtitles":         True,
    "und_audio_threshold":           2,
    "fix_undefined_language":        "always_leave",
    "undefined_language_value":      "eng",
    "undefined_language_mode":       "all_undefined_per_type",
    "prefer_mp4_container":          True,
    "extract_text_subtitles_to_srt": True,
    "add_faststart_to_mp4":          True,
    "image_subtitle_handling":       "always_ask",
}


# ── Data loading (live ffprobe, else committed fixture) ─────────────────────

def _ffprobe_directory(directory: str) -> list[dict]:
    records = []
    for path in sorted(Path(directory).rglob("*")):
        if not path.is_file() or not is_media_file(str(path)):
            continue
        out = subprocess.run(
            [app_settings.FFPROBE_PATH, "-v", "quiet", "-print_format", "json",
             "-show_format", "-show_streams", str(path)],
            capture_output=True, text=True,
        )
        if out.returncode != 0:
            continue
        data = json.loads(out.stdout)
        records.append({
            "path": str(path),
            "format_name": data.get("format", {}).get("format_name", ""),
            "streams": data.get("streams", []),
        })
    return records


def _load_records() -> list[dict]:
    sample_dir = os.environ.get("REMUXARR_SAMPLE_DIR")
    if sample_dir and os.path.isdir(sample_dir):
        recs = _ffprobe_directory(sample_dir)
        if recs:
            return recs
    return json.loads(_FIXTURE.read_text())


_RECORDS = _load_records()
_GOLDEN_DATA = json.loads(_GOLDEN.read_text())


def _name(record: dict) -> str:
    return record["path"].rsplit("/", 1)[-1]


def _analyse(record: dict):
    """Run the production pipeline on one record → (container, tracks, decision)."""
    container = extract_format_info(
        {"format": {"format_name": record["format_name"]}}
    )["container"]
    tracks = extract_tracks({"streams": record["streams"]})
    file_info = {
        "path": record["path"],
        "container": container,
        "video_codec": next(
            (t["codec"] for t in tracks if t["track_type"] == "video"), None
        ),
    }
    return container, tracks, analyze_file(file_info, tracks, CONFIG)


def _outcome(decision) -> str:
    if decision.is_manual_review:
        return "manual_review"
    if not decision.should_process:
        return "skip"
    return "process"


# Parametrise per file so a failure names the offending file.
_IDS = [_name(r) for r in _RECORDS]


# ═══════════════════════════════════════════════════════════════════════════
# Sanity: the library loaded and matches the golden set
# ═══════════════════════════════════════════════════════════════════════════

def test_library_loaded():
    assert _RECORDS, "no sample records loaded (fixture missing?)"
    # Fixture path should carry the full known library.
    if not os.environ.get("REMUXARR_SAMPLE_DIR"):
        assert len(_RECORDS) == len(_GOLDEN_DATA) == 18


# ═══════════════════════════════════════════════════════════════════════════
# THE regression guard: container detection on real format_names
# ═══════════════════════════════════════════════════════════════════════════

@pytest.mark.parametrize("record", _RECORDS, ids=_IDS)
def test_container_matches_extension(record):
    """
    Every .mkv must resolve to "mkv" and every .avi to "avi". ffprobe
    reports format_name="matroska,webm" for ALL Matroska files, so a
    detector that keys on "webm" mislabels every MKV as a webm container
    and routes it to `-f webm`, which FFmpeg rejects — the exact
    regression this suite exists to catch. No MKV here is truly webm
    (verified: all have non-webm codecs and .mkv extensions).
    """
    container, _, _ = _analyse(record)
    ext = record["path"].rsplit(".", 1)[-1].lower()
    expected = {"mkv": "mkv", "avi": "avi", "mp4": "mp4",
                "webm": "mkv"}.get(ext, ext)
    assert container == expected, (
        f"{_name(record)}: extension .{ext} but container detected as "
        f"'{container}' (expected '{expected}')"
    )


def test_no_mkv_is_detected_as_webm():
    """Blunt, explicit form of the regression guard."""
    for r in _RECORDS:
        container, _, _ = _analyse(r)
        assert container != "webm", (
            f"{_name(r)} was detected as a webm container — the "
            "matroska,webm mislabel regression is back."
        )


# ═══════════════════════════════════════════════════════════════════════════
# Full decision snapshot (container + outcome + target container)
# ═══════════════════════════════════════════════════════════════════════════

def test_decision_snapshot():
    """
    Pin every file's decision under CONFIG. Catches unintended behavior
    drift across the whole library at once. To accept an INTENTIONAL
    change, regenerate the golden (see module docstring) and review the
    diff.
    """
    current = {}
    for r in _RECORDS:
        container, _, d = _analyse(r)
        current[_name(r)] = {
            "container": container,
            "outcome": _outcome(d),
            "target_container": d.target_container if _outcome(d) == "process" else None,
        }

    if os.environ.get("REMUXARR_UPDATE_GOLDEN"):
        _GOLDEN.write_text(json.dumps(current, indent=1, sort_keys=True))
        pytest.skip("golden snapshot regenerated (REMUXARR_UPDATE_GOLDEN)")

    # Compare only files we have golden entries for (live dir may differ).
    mismatches = {
        n: (current[n], _GOLDEN_DATA[n])
        for n in current if n in _GOLDEN_DATA and current[n] != _GOLDEN_DATA[n]
    }
    assert not mismatches, "decision drift vs golden:\n" + "\n".join(
        f"  {n}: got {got} — expected {exp}" for n, (got, exp) in mismatches.items()
    )


# ═══════════════════════════════════════════════════════════════════════════
# Config-independent coherence invariants (hold for the whole library)
# ═══════════════════════════════════════════════════════════════════════════

@pytest.mark.parametrize("record", _RECORDS, ids=_IDS)
def test_analyse_never_raises(record):
    """The decision engine must handle every real file without throwing."""
    _analyse(record)  # raises → test fails, naming the file


@pytest.mark.parametrize("record", _RECORDS, ids=_IDS)
def test_processed_files_build_a_valid_command(record):
    """
    For every file the engine decides to process, build_ffmpeg_command
    must succeed and target a supported container. This is what would
    have flagged the webm break at the command layer even if the
    container check somehow passed.
    """
    _, tracks, d = _analyse(record)
    if not d.should_process or d.is_manual_review:
        return
    out = determine_output_path(record["path"], d)
    cmd = build_ffmpeg_command(record["path"], out, d, tracks)  # raises on bad container
    assert "-f" in cmd
    out_fmt = cmd[cmd.index("-f") + 1]
    assert out_fmt != "webm", (
        f"{_name(record)}: command targets '-f webm' for a non-webm file"
    )


@pytest.mark.parametrize("record", _RECORDS, ids=_IDS)
def test_actions_reference_real_streams(record):
    """Every action must point at a stream index that exists in the file."""
    _, tracks, d = _analyse(record)
    valid = {t["stream_index"] for t in tracks}
    for a in d.actions:
        si = getattr(a, "stream_index", None)
        if si is not None:
            assert si in valid, (
                f"{_name(record)}: action {a.action_type} targets missing "
                f"stream {si} (valid: {sorted(valid)})"
            )


@pytest.mark.parametrize("record", _RECORDS, ids=_IDS)
def test_extraction_only_targets_text_subtitles(record):
    """
    An extract-to-SRT action must never target an image-based subtitle
    (PGS/VOBSUB/DVD/DVB) — those can't become SRT and must go to manual
    review instead.
    """
    _, tracks, d = _analyse(record)
    codec_by_index = {t["stream_index"]: (t["codec"] or "").lower() for t in tracks}
    for a in d.actions:
        if a.action_type == "extract_subtitle":
            codec = codec_by_index.get(getattr(a, "stream_index", None), "")
            assert codec not in IMAGE_BASED_SUBS, (
                f"{_name(record)}: tried to extract image-based subtitle "
                f"'{codec}' to SRT"
            )


# ═══════════════════════════════════════════════════════════════════════════
# Specific confirmed behaviors
# ═══════════════════════════════════════════════════════════════════════════

def test_pgs_samples_go_to_manual_review():
    """
    The image-based-subtitle samples must flag for manual review (the
    user-confirmed correct behavior) — with the reason naming the
    image-based codec, not a subtitle-encoding problem.
    """
    pgs = ["POCAWE_Sample.mkv", "Forced Sub Sample (PGS).mkv"]
    by_name = {_name(r): r for r in _RECORDS}
    for name in pgs:
        if name not in by_name:
            pytest.skip(f"{name} not in the loaded library")
        _, _, d = _analyse(by_name[name])
        assert d.is_manual_review, f"{name} should be manual_review"
        assert "image-based" in (d.reason or "").lower(), (
            f"{name}: manual-review reason should cite the image-based "
            f"codec, got: {d.reason!r}"
        )


def test_text_subtitle_samples_are_not_manual_review():
    """
    The subrip/ass forced samples are extractable text subtitles — they
    must PROCESS, not land in manual review. This guards the specific
    regression where a webm container error was misclassified as a
    subtitle-encoding failure and wrongly flagged these files.
    """
    text_subs = [
        "Forced Sub Sample.mkv",
        "Forced Sub Sample (und test).mkv",
        "Anime HEVC Main10 1080p Sample2.mkv",
    ]
    by_name = {_name(r): r for r in _RECORDS}
    for name in text_subs:
        if name not in by_name:
            pytest.skip(f"{name} not in the loaded library")
        _, _, d = _analyse(by_name[name])
        assert not d.is_manual_review, (
            f"{name}: an extractable text-subtitle file was wrongly flagged "
            f"for manual review (reason: {d.reason!r})"
        )
