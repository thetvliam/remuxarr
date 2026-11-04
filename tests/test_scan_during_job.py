"""
A scan that reaches a file while its job runs, or while one waits.

_process_file decides from the file's stored tracks or a fresh probe, and
while a job is running both describe the file as it was before the job.
Reproduced with a Re-check during a real job before this file existed:

  • a review raised then named its tracks by their pre-job stream numbers.
    The job dropped an audio track, the styled subtitle the card showed as
    stream 3 became stream 2, stream 3 became the French SRT, and "remove"
    on the card was stored against the French SRT;
  • a skip marked the file "skipped" while the job was processing it;
  • work the job was not doing reached the queue branch's in-progress check
    and was dropped, and the job then recorded the file's new size and
    mtime, so no delta scan looked at it again.

Now nothing is recorded while a job runs: the file is marked
(MediaFile.reevaluate_after_job) and decided again from a fresh probe once
the job has succeeded.

A waiting job is the other half. A Re-check that turned a queued
conversion into a skip left the job pending beside the skipped row, and
its pickup decided the same and left a second one. The skip branch now
supersedes pending jobs, as the review branch already did.

Verified by mutation, 6 applied, 6 killed. Each was run against the full
suite (2268 tests) before these tests existed. Four survived it and are
killed here:

  • no running-job guard                   → the skip, review and more-work
                                             runs
  • the guard not marking the file         → the same three
  • the guard placed after the review
    branch, so only skips and queueing
    are held back                          → the review run
  • the skip branch not superseding a
    waiting job                            → the waiting-conversion run

Two were already killed by tests elsewhere, and are recorded rather than
claimed: the guard matching waiting jobs as well as running ones (four
tests in test_ask_again, test_font_attachment_pipeline and
test_review_keeps_language_flags), and the guard not handing the running
job the Sonarr and Radarr IDs (test_post_job_notify's processing cases).
"""
import json
import subprocess

from tests import test_forge_keeps_tracks_current as _kept
from tests.test_forge_keeps_tracks_current import (
    ENGLISH_51, _drop_french, _queue, _run_queued, _streams, _video, needs_ffmpeg,
)
from tests.test_forge_undo_identity import _source

# That file's fixture: a file-backed database the scanner and the worker
# share, and a library Movie.mkv. Bound by assignment for the reason
# test_forge_undo_identity gives.
lib = _kept.lib


def _set(lib, **settings):
    from app.database.models import AppSetting

    with lib.Session() as db:
        for key, value in settings.items():
            db.merge(AppSetting(key=key, value=json.dumps(value)))
        db.commit()


def _state(lib):
    """The file's status, its reevaluate mark, and (status, review_subtitles)
    of each of its queue items in order."""
    from app.database.models import MediaFile, QueueItem

    with lib.Session() as db:
        media = db.query(MediaFile).one()
        items = [(q.status, json.loads(q.review_subtitles) if q.review_subtitles else None)
                 for q in db.query(QueueItem).order_by(QueueItem.id)]
        return media.status, media.reevaluate_after_job, items


def _recheck(lib):
    """Re-check Scanned Files over the library, as the scan route runs it."""
    from app.core.scanner import scan_library

    with lib.Session() as db:
        scan_library(db, [str(lib.video.parent)], recheck=True)


def _during_the_job(lib, monkeypatch, step):
    """
    Run `step` once, inside the job, just before FFmpeg starts: after the
    worker has claimed it and decided it, while the file is still the one
    the job read. Returns the state `step` left, as seen then.
    """
    seen = []
    for name in ("execute_ffmpeg", "execute_ffmpeg_combined"):
        real = getattr(lib.worker, name)

        async def wrapped(*args, _real=real, **kwargs):
            if not seen:
                step()
                seen.append(_state(lib))
            return await _real(*args, **kwargs)

        monkeypatch.setattr(lib.worker, name, wrapped)
    return seen


def _queue_dropping_french(lib, audio):
    """The file with `audio` and a French SRT, queued to drop French audio."""
    _video(lib.video, audio=audio)
    _queue(lib)
    _drop_french(lib, "keep_audio_languages")
    assert _queue(lib) is not None, "dropping French audio queued nothing"


@needs_ffmpeg
def test_a_recheck_that_skips_a_waiting_conversion_takes_it_out_of_the_queue(lib):
    """Queued to become MP4; Prefer MP4 is turned off and the file re-checked."""
    _source(lib.video, [("aac", "eng", 2, None)])
    _set(lib, prefer_mp4_container=True)
    assert _queue(lib) is not None

    _set(lib, prefer_mp4_container=False)
    _recheck(lib)

    assert _state(lib) == ("skipped", False, [("skipped", None)])
    assert lib.worker._claim_next() is None, "a job is still waiting for the file"


@needs_ffmpeg
def test_a_recheck_during_a_job_records_nothing_until_it_ends(lib, monkeypatch):
    """
    French audio is put back while the job drops it. The re-check would skip
    the file as it was; that is recorded after the job, from the file the
    job left, which then needs nothing either.
    """
    _set(lib, extract_text_subtitles_to_srt=False)
    _queue_dropping_french(lib, [ENGLISH_51, ("fre", 2, None)])

    def put_french_back():
        _set(lib, keep_audio_languages=["eng", "fre"])
        _recheck(lib)

    seen = _during_the_job(lib, monkeypatch, put_french_back)
    assert _run_queued(lib) == ("success", None)

    assert seen == [("queued", True, [("processing", None)])], \
        "the re-check recorded something while the job ran"
    assert _state(lib) == ("skipped", False, [("success", None), ("skipped", None)])


def _styled_file(path):
    """
    Video, English and French AAC, an English ASS track, a French SRT and a
    font attachment: a font review asks about the ASS.
    """
    folder = path.parent
    ass, srt, font = folder / "in.ass", folder / "in.srt", folder / "F.ttf"
    ass.write_text("[Script Info]\nScriptType: v4.00+\n\n[V4+ Styles]\n"
                   "Format: Name, Fontname, Fontsize\nStyle: Default,Arial,20\n\n"
                   "[Events]\nFormat: Layer, Start, End, Style, Text\n"
                   "Dialogue: 0,0:00:00.00,0:00:01.00,Default,hello\n")
    srt.write_text("1\n00:00:00,000 --> 00:00:00,900\nbonjour\n")
    font.write_bytes(b"\x00\x01\x02\x03" * 256)
    subprocess.run([
        "ffmpeg", "-v", "error", "-y",
        "-f", "lavfi", "-i", "testsrc=size=64x36:rate=5:duration=2",
        "-f", "lavfi", "-i", "sine=frequency=440:duration=2",
        "-f", "lavfi", "-i", "sine=frequency=660:duration=2",
        "-i", str(ass), "-i", str(srt),
        "-map", "0:v", "-map", "1:a", "-map", "2:a", "-map", "3:s", "-map", "4:s",
        "-metadata:s:a:0", "language=eng", "-metadata:s:a:1", "language=fre",
        "-metadata:s:s:0", "language=eng", "-metadata:s:s:1", "language=fre",
        "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-c:s:0", "ass", "-c:s:1", "srt",
        "-attach", str(font), "-metadata:s:t", "mimetype=application/x-truetype-font",
        "-f", "matroska", str(path)], check=True)
    for scratch in (ass, srt, font):
        scratch.unlink()


@needs_ffmpeg
def test_a_review_raised_by_a_recheck_during_a_job_names_the_tracks_after_it(lib, monkeypatch):
    """
    The reproduction. Font reviews are turned on mid-job, and the re-check
    would have asked about the ASS as stream 3, the French SRT's number once
    the job has dropped French audio. Asked after the job, it is stream 2,
    and removing it removes the ASS.
    """
    from app.api.routes.queue import (ReviewApplyRequest, ReviewFileDecision,
                                      apply_review_decisions)
    from app.database.models import MediaFile

    _set(lib, extract_text_subtitles_to_srt=False, font_attachment_handling="always_keep")
    _styled_file(lib.video)
    _queue(lib)
    _drop_french(lib, "keep_audio_languages")
    assert _queue(lib) is not None

    def ask_about_fonts():
        _set(lib, font_attachment_handling="always_ask")
        _recheck(lib)

    seen = _during_the_job(lib, monkeypatch, ask_about_fonts)
    assert _run_queued(lib) == ("success", None)
    assert [s[1] for s in _streams(lib.video)] == ["h264", "aac", "ass", "subrip", "ttf"]

    assert seen == [("queued", True, [("processing", None)])]
    status, marked, items = _state(lib)
    assert (status, marked) == ("manual_review", False)
    assert [(s, [(f["stream_index"], f["codec"]) for f in flagged or []])
            for s, flagged in items] == [("success", []), ("manual_review", [(2, "ass")])]

    with lib.Session() as db:
        file_id = db.query(MediaFile).one().id
        result = apply_review_decisions(ReviewApplyRequest(
            files=[ReviewFileDecision(file_id=file_id, answers={2: "remove"})]), db)
        assert result["errors"] == []
        answers = json.loads(db.query(MediaFile).one().subtitle_overrides)
    assert [(json.loads(key)[1], choice) for key, choice in answers.items()] == [("ass", "remove")]


@needs_ffmpeg
def test_work_a_recheck_finds_during_a_job_is_done_after_it(lib, monkeypatch):
    """
    French subtitles are dropped from the keep list while the job drops
    French audio. The job was decided before; the subtitle goes in a job
    queued once it ends.
    """
    _set(lib, extract_text_subtitles_to_srt=False)
    _queue_dropping_french(lib, [ENGLISH_51, ("fre", 2, None)])

    def drop_french_subtitles():
        _set(lib, keep_subtitle_languages=["eng"])
        _recheck(lib)

    seen = _during_the_job(lib, monkeypatch, drop_french_subtitles)
    assert _run_queued(lib) == ("success", None)
    assert seen == [("queued", True, [("processing", None)])]
    assert _state(lib)[:2] == ("queued", False)

    assert _run_queued(lib) == ("success", None)
    assert [s[1] for s in _streams(lib.video)] == ["h264", "aac"]

