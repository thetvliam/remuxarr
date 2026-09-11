"""
The embedded font count, from the probe to the gate.

The font gate in decision.py worked, and test_font_attachment_review.py
proved it thoroughly — on file_info dicts built by hand with
font_attachments already set. Production never built one like that. The
count was computed by probe.extract_format_info() and then dropped:
analyze_file() had three callers, the scanner, the worker at job pickup
and queue.py's re-evaluations, each building its own dict, and none added
the new key. So the gate never fired. Always Ask sent nothing to review,
Always Keep converted the file anyway, and every test was green.

Reproduced on a real MKV with an ASS track and an attached font, through
the real webhook path: all three settings queued the same conversion.

The fix is a stored count (MediaFile.font_attachments) and one builder
(scanner._file_info_for) that every caller uses. These tests go through
the real callers rather than the gate, because the gate was never the
problem. The probe is replaced with the JSON shape ffprobe really
produces for an attachment — filename and mimetype tags, which is what
the counter reads — and everything after it is production code.

test_sample_library.py could not have caught this either, and still
cannot: its records were parsed from `ffmpeg -i` dumps by a parser that
keeps only language and title tags, so every attachment in them has
empty tags and counts as 0. Three of its files carry attachments beside
ASS tracks, two of them fonts by codec. Two snapshot as converted to MP4,
and none as a font review.

Fifteen mutants, all run against the full suite before any test here
existed. Three were already dead. The builder dropping the key and the
worker building its own dict were killed by
test_job_preflight.py::test_the_decision_is_recomputed_from_the_stored_file_and_tracks,
which pins the exact dict and had to be updated in the same change. A
pickup probe failure propagating was killed by 17 existing tests, and
only incidentally: their rows are null and their files are dummies, so
they reach the probe and it fails. The other twelve survived:

  survived, then   the scanner not storing it on a new row          killed
                   the scanner not storing it on an existing row    killed
                   the scanner building its own dict                killed
                   queue.py building its own dict                   killed
                   the pickup probe never running                   killed
                   the pickup probe's answer not stored             killed
                   the pickup probe running when already stored     killed
                   a pickup probe failure recorded as no fonts      killed
                   the post-job refresh not storing it              killed
                   a revert not storing it                          killed
                   the migration entry missing                      killed
                   the migration giving existing rows 0             killed

The last four are killed in test_job_finalisation.py,
test_revert_execution.py and test_schema_migration.py, next to the
harness each writer already had.

Run from the project root:
    pytest tests/test_font_attachment_pipeline.py -v
"""
import json

import pytest

from app.api.routes import queue as queue_routes
from app.core import scanner, worker
from app.core.probe import ProbeError
from app.database.models import Base, MediaFile, QueueItem, Track
from tests.conftest import memory_engine


def _probe(fonts):
    """
    ffprobe's JSON for an anime-style MKV: English audio, one ASS track,
    and `fonts` font attachments carrying the tags a real one carries.
    """
    streams = [
        {"index": 0, "codec_type": "video", "codec_name": "hevc"},
        {"index": 1, "codec_type": "audio", "codec_name": "aac",
         "channels": 2, "tags": {"language": "eng"},
         "disposition": {"default": 1}},
        {"index": 2, "codec_type": "subtitle", "codec_name": "ass",
         "tags": {"language": "eng", "title": "Signs and Songs"}},
    ]
    streams += [
        {"index": 3 + n, "codec_type": "attachment", "codec_name": "ttf",
         "tags": {"filename": f"Font{n}.ttf",
                  "mimetype": "application/x-truetype-font"}}
        for n in range(fonts)
    ]
    return {"format": {"format_name": "matroska,webm", "duration": "1.0"},
            "streams": streams}


@pytest.fixture
def factory(monkeypatch):
    """
    A session factory the test and the worker share. The worker imports
    SessionLocal by value, so it is patched on the worker itself.
    """
    from sqlalchemy.orm import sessionmaker

    engine = memory_engine()
    Base.metadata.create_all(engine)
    made = sessionmaker(bind=engine)
    monkeypatch.setattr(worker, "SessionLocal", made)
    return made


@pytest.fixture
def episode(tmp_path):
    path = tmp_path / "Saiki K. - S01E08.mkv"
    path.write_bytes(b"\0" * 64)
    return str(path)


# ── The scanner ───────────────────────────────────────────────────────────────

def _scan(db, monkeypatch, path, probe):
    monkeypatch.setattr(scanner, "probe_file", lambda *_a, **_kw: probe)
    return scanner.queue_single_file(db, path)


def test_the_scanner_hands_the_gate_the_count_it_probed(
        factory, monkeypatch, episode):
    """
    Closes: the reported bug, at the scanner, which every scan and every
    webhook goes through.

    Under the default, Always Ask, a file whose styled subtitles use
    embedded fonts goes to review. Before the fix it was queued for
    conversion like any other MKV, because the count never left the probe.
    """
    with factory() as db:
        item = _scan(db, monkeypatch, episode, _probe(fonts=2))

        assert item.status == "manual_review"
        assert item.review_reason == "font_attachments"
        assert item.media_file.font_attachments == 2


def test_a_rescan_replaces_a_stale_count(factory, monkeypatch, episode):
    """
    Closes: an existing row keeping the count from its first probe.

    The file at a path is replaced whenever a better release arrives, and
    the new one can carry fonts the old one did not. The row is updated in
    place, so every probed field has to be written on that branch as well.
    """
    with factory() as db:
        _scan(db, monkeypatch, episode, _probe(fonts=0))
        again = _scan(db, monkeypatch, episode, _probe(fonts=3))

        assert again.media_file.font_attachments == 3
        assert again.status == "manual_review"


# ── The queue endpoints ───────────────────────────────────────────────────────

def test_resolving_image_subtitles_then_asks_about_the_fonts(factory):
    """
    Closes: queue.py re-deciding without the count.

    decision.py puts the image-subtitle gate first, and says a file with
    both is asked about fonts on the evaluation after its image subtitles
    are resolved. That evaluation is this endpoint's, so without the count
    the second question was never asked: resolving the PGS track moved the
    file straight to conversion.
    """
    with factory() as db:
        media = MediaFile(path="/m/Show.mkv", filename="Show.mkv",
                          directory="/m", size=1, mtime=1.0,
                          container="mkv", video_codec="hevc",
                          font_attachments=2)
        db.add(media)
        db.flush()
        for index, kind, codec in [(0, "video", "hevc"), (1, "audio", "aac"),
                                   (2, "subtitle", "hdmv_pgs_subtitle"),
                                   (3, "subtitle", "ass")]:
            db.add(Track(file_id=media.id, stream_index=index,
                         track_type=kind, codec=codec,
                         language="und" if kind == "video" else "eng",
                         is_default=(index == 1)))
        item = QueueItem(file_id=media.id, status="manual_review",
                         review_reason="image_subtitles",
                         review_subtitles=json.dumps([{"stream_index": 2}]))
        db.add(item)
        db.commit()

        queue_routes.resolve_subtitles(
            item.id, queue_routes.SubtitleOverridesRequest(overrides={2: "remove"}),
            db,
        )

        db.refresh(item)
        assert item.status == "manual_review"
        assert item.review_reason == "font_attachments"


# ── The worker at job pickup ──────────────────────────────────────────────────

def _pending_job(factory, path, font_attachments):
    with factory() as db:
        media = MediaFile(path=path, filename="Saiki K. - S01E08.mkv",
                          directory="/m", size=64, mtime=1.0,
                          container="mkv", video_codec="hevc",
                          font_attachments=font_attachments)
        db.add(media)
        db.flush()
        for index, kind, codec in [(0, "video", "hevc"), (1, "audio", "aac"),
                                   (2, "subtitle", "ass")]:
            db.add(Track(file_id=media.id, stream_index=index,
                         track_type=kind, codec=codec,
                         language="und" if kind == "video" else "eng",
                         is_default=(index == 1)))
        job = QueueItem(file_id=media.id, status="pending")
        db.add(job)
        db.commit()
        return media.id, job.id


def test_a_row_never_counted_is_counted_at_pickup(factory, monkeypatch, episode):
    """
    Closes: every file already in the library before the count was stored.

    Those rows are null, and a normal scan skips unchanged files, so they
    could stay null indefinitely and the gate would read them as font-free.
    The worker counts them when it picks the job up, which is the point
    where the answer decides what happens to the file, and keeps the result.
    """
    monkeypatch.setattr(worker, "probe_file", lambda *_a, **_kw: _probe(fonts=2))
    media_id, job_id = _pending_job(factory, episode, font_attachments=None)

    assert worker._load_job_data(job_id) is None

    with factory() as db:
        job = db.get(QueueItem, job_id)
        assert job.status == "manual_review"
        assert job.review_reason == "font_attachments"
        assert db.get(MediaFile, media_id).font_attachments == 2


def test_a_stored_count_is_not_probed_again(factory, monkeypatch, episode):
    """
    Closes: the pickup probe running on every job.

    It exists for rows that were never counted. Running it for a row that
    already has an answer, including 0, is an ffprobe per job for a
    number already in hand.
    """
    probed = []
    monkeypatch.setattr(worker, "probe_file",
                        lambda path, *_a, **_kw: probed.append(path) or _probe(0))
    _, job_id = _pending_job(factory, episode, font_attachments=0)

    worker._load_job_data(job_id)

    assert probed == []


def test_a_failed_count_is_left_to_try_again(factory, monkeypatch, episode):
    """
    Closes: a probe failure recorded as "no fonts".

    A probe can fail on a share that has not mounted, or a file still
    being copied. Storing 0 then would mark the file font-free for good, and the one
    retry that would have found its fonts would never happen. Null says
    "not counted yet", so the next pickup tries again. The job itself goes
    on as it would have before the count existed.
    """
    def unreadable(*_a, **_kw):
        raise ProbeError("ffprobe failed: share not mounted")

    monkeypatch.setattr(worker, "probe_file", unreadable)
    media_id, job_id = _pending_job(factory, episode, font_attachments=None)

    assert worker._load_job_data(job_id) is not None

    with factory() as db:
        assert db.get(MediaFile, media_id).font_attachments is None
