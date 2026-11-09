"""
An unanswered subtitle-encoding review, and the scans that decide its file
again.

The worker raises this review when extracting a subtitle to SRT fails on
text that is not UTF-8 (worker._flag_subtitle_encoding_review). The decision
engine never learns of the failure, so each time a scan decided the file
again it planned the same extraction:

  • Re-check Scanned Files sent the review back to the queue, the job
    failed the same way, and the file came back to review. Every Re-check
    went round once more.
  • A review from a gate (the keep list now holding an image subtitle, say)
    replaced the question, and answering that one extracted the track again.

The scanner now leaves the review as it is while its tracks are still in the
file and the new decision would meet the failure again
(scanner._standing_encoding_review). These run real scans against an
in-memory database with ffprobe replaced, and the worker's own function to
raise the review, so each case is followed from the failure to what the
scan leaves.

Verified by mutation, 7 applied 7 killed, each surviving the whole
2314-test suite first:

  • the guard never consulted               → the Re-check test
  • a review decision replacing the
    question                                → the waits-behind test
  • any decision keeping it, extracting
    the tracks or not                       → the extraction-off test
  • the track's codec not compared          → the codec test
  • its language not compared               → the language test
  • a track no longer in the file
    taken as still there                    → the track-gone test
  • the Sonarr and Radarr IDs dropped       → the IDs test
"""
import json

import pytest

import app.core.scanner as scanner
import app.core.worker as worker
from app.api.routes.queue import _answer_subtitle_review
from app.core.scanner import ScanStats, _process_file, scan_library
from app.database.models import AppSetting, MediaFile, QueueItem, Track
from app.database.session import get_app_settings

ENG_SRT = ("subtitle", "subrip", "eng")
FRE_PGS = ("subtitle", "hdmv_pgs_subtitle", "fre")
AUDIO = ("audio", "aac", "eng")


def _probe(*streams):
    """Video at stream 0, then each (type, codec, language) in order."""
    out = [{"index": 0, "codec_type": "video", "codec_name": "h264"}]
    for i, (kind, codec, language) in enumerate(streams, start=1):
        stream = {"index": i, "codec_type": kind, "codec_name": codec,
                  "tags": {"language": language}}
        if kind == "audio":
            stream["channels"] = 2
        out.append(stream)
    return {"format": {"format_name": "matroska,webm", "duration": "1.0"},
            "streams": out}


@pytest.fixture
def lib(tmp_path, monkeypatch):
    """
    Film.mkv: English audio, an English SRT at stream 2 and a French PGS at
    stream 3. Only English subtitles are kept and extraction is on, so the
    first scan queues a job that extracts the SRT; the extraction then
    fails on its encoding, and the file is in review with the question.
    """
    from sqlalchemy.orm import sessionmaker

    from app.database.models import Base
    from tests.conftest import memory_engine

    engine = memory_engine()
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine)
    db = factory()
    monkeypatch.setattr(worker, "SessionLocal", factory)

    root = tmp_path / "films"
    root.mkdir()
    path = root / "Film.mkv"
    path.write_bytes(b"\0" * 64)
    probes = {str(path): _probe(AUDIO, ENG_SRT, FRE_PGS)}
    monkeypatch.setattr(scanner, "probe_file", lambda p, *_a, **_kw: probes[p])

    class Lib:
        @staticmethod
        def set(**values):
            for key, value in values.items():
                db.merge(AppSetting(key=key, value=json.dumps(value)))
            db.commit()

        @staticmethod
        def scan(**kwargs):
            # Nothing this session has read is carried into the scan, as a
            # scan's own session starts empty. Otherwise an item read here
            # stays in the identity map after the scanner bulk-deletes it,
            # and the item queued in its place, given the freed id, warns.
            db.expunge_all()
            scan_library(db, [str(root)], **kwargs)

        @staticmethod
        def replace_file(*streams):
            """The file replaced on disk with these streams after video."""
            probes[str(path)] = _probe(*streams)
            path.write_bytes(b"\0" * 128)

        @staticmethod
        def items():
            db.expire_all()
            return db.query(QueueItem).order_by(QueueItem.id).all()

        @staticmethod
        def review():
            """The review left, as (reason, flagged stream and reason), or None."""
            db.expire_all()
            item = db.query(QueueItem).filter_by(status="manual_review").one_or_none()
            if item is None:
                return None
            return (item.review_reason,
                    [(s["stream_index"], s["reason"])
                     for s in json.loads(item.review_subtitles or "[]")])

    Lib.db, Lib.path = db, str(path)
    Lib.set(dry_run_mode=False, prefer_mp4_container=False,
            scan_paths=[str(root)], keep_subtitle_languages=["eng"])
    Lib.scan(force_probe=True)
    (job,) = Lib.items()
    assert job.status == "pending" and "Extract 1 subtitle" in job.reason
    _fail_on_encoding(db, job, stream_index=2)
    assert Lib.review() == ("subtitle_encoding", [(2, "encoding")])
    yield Lib
    db.close()


def _fail_on_encoding(db, job, stream_index):
    """What the worker does when the job's extraction fails on encoding."""
    tracks = [{"stream_index": t.stream_index, "track_type": t.track_type,
               "codec": t.codec, "language": t.language,
               "is_forced": t.is_forced, "title": t.title}
              for t in db.query(Track).filter_by(file_id=job.file_id)]
    job.status = "processing"
    db.commit()
    worker._flag_subtitle_encoding_review(job.id, [(stream_index, "/x.srt")], tracks)


def _replaced(lib):
    """The question is gone: no encoding review left on the file."""
    review = lib.review()
    return review is None or review[0] != "subtitle_encoding"


# ── The review stands ────────────────────────────────────────────────────────

def test_recheck_leaves_an_unanswered_encoding_review(lib):
    lib.scan(recheck=True)

    assert lib.review() == ("subtitle_encoding", [(2, "encoding")])
    assert [i.status for i in lib.items()] == ["manual_review"], (
        "Re-check queued the extraction that had just failed")
    lib.db.expire_all()
    assert lib.db.query(MediaFile).one().status == "manual_review"


def test_another_review_waits_behind_it(lib):
    """
    French added to the keep list makes the PGS an image-subtitle question.
    It replaced the encoding question, and answering it extracted the SRT
    again. Answering the encoding question decides the file again, which
    is when the PGS is asked about.
    """
    lib.set(keep_subtitle_languages=["eng", "fre"])

    lib.scan(recheck=True)

    assert lib.review() == ("subtitle_encoding", [(2, "encoding")])


def test_the_sonarr_and_radarr_ids_still_reach_the_review(lib):
    """
    A scan that brings IDs leaves them on the item, as the review branch
    does, or the job that follows the answer finishes without telling the
    service the file has changed.
    """
    _process_file(lib.db, lib.path, get_app_settings(lib.db), force_probe=False,
                  dry_run=False, stats=ScanStats(), sonarr_series_id=42,
                  radarr_movie_id=7, recheck=True)

    (item,) = lib.items()
    assert item.status == "manual_review"
    assert (item.sonarr_series_id, item.radarr_movie_id) == (42, 7)


# ── The review is replaced ───────────────────────────────────────────────────

def test_extraction_switched_off_replaces_it(lib):
    """
    With extraction off the SRT stays embedded, so nothing would fail and
    the question has nothing left to protect.
    """
    lib.set(extract_text_subtitles_to_srt=False)

    lib.scan(recheck=True)

    assert lib.review() is None
    (item,) = lib.items()
    assert item.status == "pending" and "Extract" not in item.reason


def test_a_track_with_another_codec_replaces_it(lib):
    """The file replaced: stream 2 is an ASS track now, extracted as well."""
    lib.replace_file(AUDIO, ("subtitle", "ass", "eng"), FRE_PGS)

    lib.scan()

    assert _replaced(lib)
    (item,) = lib.items()
    assert item.status == "pending" and "Extract 1 subtitle" in item.reason


def test_a_track_in_another_language_replaces_it(lib):
    """The file replaced: stream 2 is a Spanish SRT, kept and extracted."""
    lib.set(keep_subtitle_languages=["eng", "spa"])
    lib.replace_file(AUDIO, ("subtitle", "subrip", "spa"), FRE_PGS)

    lib.scan()

    assert _replaced(lib)
    (item,) = lib.items()
    assert item.status == "pending" and "Extract 1 subtitle" in item.reason


def test_a_track_no_longer_in_the_file_replaces_it(lib):
    """
    The file replaced with a second audio track first, so stream 2 is audio
    and the SRT is stream 3. The French PGS is kept now, so the new decision
    is a review of its own: the question about stream 2 must not stand in
    its way, since stream 2 is no longer a subtitle at all.
    """
    lib.set(keep_subtitle_languages=["eng", "fre"])
    lib.replace_file(AUDIO, ("audio", "aac", "fre"), ENG_SRT, FRE_PGS)

    lib.scan()

    assert lib.review() == ("image_subtitles", [(4, "image")])


# ── Once answered ────────────────────────────────────────────────────────────

def test_once_answered_keep_a_recheck_does_not_extract_it(lib):
    """
    Keep is stored against the track, and the engine leaves a track it has
    an answer for embedded, so nothing needs protecting once answered.
    """
    item = lib.db.query(QueueItem).filter_by(status="manual_review").one()
    media = lib.db.get(MediaFile, item.file_id)
    _answer_subtitle_review(lib.db, item, media, {2: "keep"})
    lib.db.commit()

    lib.scan(recheck=True)

    assert lib.review() is None
    assert all("Extract" not in (i.reason or "") for i in lib.items())
