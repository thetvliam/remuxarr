"""
Regression tests for two scanner/classifier behaviors: the full-scan
new/changed counting, and the subtitle-encoding-failure classifier.

Full scans miscounted existing files as "new":
    counting keyed off the `else` of `if existing and not force_probe`,
    so a forced full scan (force_probe=True) skipped the delta block and
    logged every existing file as new=<library> changed=0. Now buckets
    by is_new_file.

mov_text was a substring catch-all in the encoding classifier:
    same false-positive class as the removed "subtitle" pattern (would
    match a container/mux error naming mov_text). Removed; genuine
    encoding failures are still caught by "invalid utf-8"/"sub_charenc".

Run from the project root:
    pytest tests/test_scan_stats_and_subtitle_classifier.py -v
"""
import os
import subprocess
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.core.worker import _is_subtitle_encoding_failure


# ═══════════════════════════════════════════════════════════════════════════
# Encoding classifier no longer matches container errors
# ═══════════════════════════════════════════════════════════════════════════

def test_mov_text_container_error_not_classified_as_encoding():
    """A mux/container error that merely names mov_text is NOT an
    encoding failure and must not be routed to the non-UTF-8 review."""
    err = "[matroska @ 0x0] Subtitle codec 94213 (mov_text) is not supported in matroska"
    assert _is_subtitle_encoding_failure(err) is False


def test_genuine_encoding_failures_still_detected():
    """Removing mov_text must not lose real coverage."""
    assert _is_subtitle_encoding_failure(
        "Invalid UTF-8 in decoded subtitles text; maybe missing -sub_charenc option"
    ) is True
    assert _is_subtitle_encoding_failure(
        "Error while decoding sist#0:2 -> #1:0/srt: Invalid data found"
    ) is True


def test_non_subtitle_failures_still_rejected():
    assert _is_subtitle_encoding_failure("No space left on device") is False
    assert _is_subtitle_encoding_failure(None) is False


# ═══════════════════════════════════════════════════════════════════════════
# Full scan counts existing files as changed, not new
# ═══════════════════════════════════════════════════════════════════════════

ffmpeg_missing = os.system("which ffmpeg >/dev/null 2>&1") != 0


def _fresh_db():
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    from app.database.models import Base
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)()


@pytest.mark.skipif(ffmpeg_missing, reason="ffmpeg not available")
def test_full_scan_counts_existing_file_as_changed(tmp_path):
    """
    An existing, on-disk-unchanged file re-probed by a FULL scan
    (force_probe=True) must count as `changed`, not `new`. The old code
    logged new=<entire library> changed=0 on exactly these scans.
    """
    from app.core.scanner import ScanStats, _process_file
    from app.database.models import MediaFile

    f = tmp_path / "Movie (2020).mkv"
    subprocess.run(
        ["ffmpeg", "-v", "error", "-y",
         "-f", "lavfi", "-i", "testsrc=duration=1:size=128x72:rate=10",
         "-f", "lavfi", "-i", "sine=duration=1",
         "-c:v", "libx264", "-c:a", "aac", "-metadata:s:a:0", "language=eng",
         str(f)],
        check=True, capture_output=True,
    )

    db = _fresh_db()
    # Seed an EXISTING row matching the file's current size/mtime.
    db.add(MediaFile(id=1, path=str(f), filename=f.name, directory=str(tmp_path),
                     size=os.path.getsize(f), mtime=os.path.getmtime(f),
                     status="success", container="mkv"))
    db.commit()

    cfg = {
        "keep_audio_languages": ["eng"], "keep_subtitle_languages": ["eng"],
        "prefer_mp4_container": True, "extract_text_subtitles_to_srt": False,
        "keep_default_audio": True, "keep_forced_subtitles": True,
    }
    stats = ScanStats()
    _process_file(db, str(f), cfg, force_probe=True, dry_run=False, stats=stats)

    assert stats.new == 0, (
        f"a full rescan counted an existing file as new (new={stats.new})"
    )
    assert stats.changed == 1, (
        f"expected the existing file to count as changed, got changed={stats.changed}"
    )


@pytest.mark.skipif(ffmpeg_missing, reason="ffmpeg not available")
def test_delta_scan_new_file_counts_as_new(tmp_path):
    """Counterpart: a genuinely new file (no existing row) counts as new
    on a delta scan — the fix must not over-rotate everything into
    changed."""
    from app.core.scanner import ScanStats, _process_file

    f = tmp_path / "New Movie (2021).mkv"
    subprocess.run(
        ["ffmpeg", "-v", "error", "-y",
         "-f", "lavfi", "-i", "testsrc=duration=1:size=128x72:rate=10",
         "-f", "lavfi", "-i", "sine=duration=1",
         "-c:v", "libx264", "-c:a", "aac", "-metadata:s:a:0", "language=eng",
         str(f)],
        check=True, capture_output=True,
    )

    db = _fresh_db()  # no existing row
    cfg = {
        "keep_audio_languages": ["eng"], "keep_subtitle_languages": ["eng"],
        "prefer_mp4_container": True, "extract_text_subtitles_to_srt": False,
        "keep_default_audio": True, "keep_forced_subtitles": True,
    }
    stats = ScanStats()
    _process_file(db, str(f), cfg, force_probe=False, dry_run=False, stats=stats)

    assert stats.new == 1 and stats.changed == 0
