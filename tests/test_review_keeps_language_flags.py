"""
A file sent to manual review keeps the language questions it already had.

A manual-review decision is made before language detection runs, so it
carries no language findings. Treated as "nothing to ask", it would delete
questions that still stand: the tracks are as untagged as they were. The
scan and the review answer both return before the flags are touched, as the
worker does (test_job_preflight pins that one).

It also means one file can be in Manual Review and in Audio Language Review
at once. The Review tab's badge used to be described as a count of files
for that reason, with the overlap said to be impossible. It is the sum of
the page's section counts, and these tests pin the figures it is built from.

Verified by mutation, 2 applied, 2 killed, each surviving the suite as it
stood (2035 tests) first:

  • a scan's review decision clearing the flags        → the scan test
  • a review answer that leaves the file in review
    clearing them                                       → the answer test
"""
import json
import shutil
import subprocess
from types import SimpleNamespace

import pytest


pytestmark = pytest.mark.skipif(
    shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None,
    reason="ffmpeg/ffprobe not available",
)

ASS = """[Script Info]
ScriptType: v4.00+

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Default,Arial,20,&H00FFFFFF,&H000000FF,&H00000000,&H00000000,0,0,0,0,100,100,0,0,1,1,0,2,10,10,10,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
Dialogue: 0,0:00:00.00,0:00:00.50,Default,,0,0,0,,Hello
"""


@pytest.fixture
def lib(tmp_path):
    """
    Show.mkv with two untagged audio tracks and two styled subtitles, one
    English and one French, scanned: a job is queued to convert it to MP4,
    and each audio track is a question in Audio Language Review.
    """
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    from app.core import scanner
    from app.database.models import AppSetting, Base, QueueItem

    ass = tmp_path / "styled.ass"
    ass.write_text(ASS)
    font = tmp_path / "Font.ttf"
    font.write_bytes(b"\x00\x01\x02\x03" * 256)
    library = tmp_path / "library"
    library.mkdir()
    video = library / "Show.mkv"

    def write(with_font):
        cmd = [
            "ffmpeg", "-v", "error", "-y",
            "-f", "lavfi", "-i", "testsrc=size=64x36:rate=5:duration=1",
            "-f", "lavfi", "-i", "sine=frequency=440:duration=1",
            "-f", "lavfi", "-i", "sine=frequency=880:duration=1",
            "-i", str(ass),
            "-map", "0:v", "-map", "1:a", "-map", "2:a", "-map", "3:s", "-map", "3:s",
            "-metadata:s:s:0", "language=eng", "-metadata:s:s:1", "language=fre",
            "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p",
            "-c:a", "aac", "-c:s", "ass"]
        if with_font:
            cmd += ["-attach", str(font), "-metadata:s:t", "mimetype=application/x-truetype-font"]
        subprocess.run(cmd + ["-f", "matroska", str(video)], check=True)

    engine = create_engine(f"sqlite:///{tmp_path / 'remuxarr.db'}")
    Base.metadata.create_all(engine)
    db = sessionmaker(bind=engine)()
    db.add(AppSetting(key="dry_run_mode", value=json.dumps(False)))
    db.commit()

    write(with_font=False)
    scanner.scan_library(db, [str(library)], force_probe=True)
    assert [q.status for q in db.query(QueueItem)] == ["pending"], "no job queued"

    def scan():
        scanner.scan_library(db, [str(library)], force_probe=False)

    yield SimpleNamespace(db=db, write=write, scan=scan)
    db.close()
    engine.dispose()


def _flags(db):
    from app.database.models import AudioLanguageFlag

    db.expire_all()
    return sorted((f.id, f.stream_index) for f in db.query(AudioLanguageFlag))


def _review_item(db):
    from app.database.models import QueueItem

    db.expire_all()
    return db.query(QueueItem).filter(QueueItem.status == "manual_review").one()


def test_a_scan_that_sends_the_file_to_review_keeps_its_questions(lib):
    """Fonts added to the file make the next scan a font review."""
    from app.api.routes.queue import queue_stats

    before = _flags(lib.db)
    assert len(before) == 2, "fixture no longer flags both audio tracks"

    lib.write(with_font=True)
    lib.scan()

    assert _review_item(lib.db).review_reason == "font_attachments"
    assert _flags(lib.db) == before
    # One file, counted once by Manual Review and twice by Audio Language
    # Review: the three figures the badge adds up.
    assert queue_stats(lib.db) == {
        "manual_review": 1, "language_review": {"audio": 2, "subtitle": 0}}


def test_an_answer_that_leaves_the_file_in_review_keeps_its_questions(lib):
    """
    French subtitles were dropped when the file was asked about, so only the
    English track was in question. Keeping French as well since then makes
    the answer raise a review of the French track straight away.
    """
    from app.api.routes.queue import ReviewApplyRequest, apply_review_decisions
    from app.database.models import MediaFile
    from app.database.session import update_app_setting

    lib.write(with_font=True)
    lib.scan()
    before = _flags(lib.db)
    item = _review_item(lib.db)
    asked = [t["stream_index"] for t in json.loads(item.review_subtitles)]
    file_id = lib.db.query(MediaFile).one().id
    update_app_setting(lib.db, "keep_subtitle_languages", ["eng", "fre"])

    reply = apply_review_decisions(ReviewApplyRequest(
        files=[{"file_id": file_id, "answers": {str(s): "keep" for s in asked}}]), lib.db)

    assert [o["status"] for o in reply["outcomes"]] == ["manual_review"], reply
    assert [t["language"] for t in json.loads(_review_item(lib.db).review_subtitles)] == ["fre"]
    assert _flags(lib.db) == before
