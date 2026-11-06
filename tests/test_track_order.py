"""
The two places a file is decided from its stored tracks read them in stream
order.

analyze_file reads position: the forge copy is recognised as the last audio
track, and the FFmpeg command maps streams in the order of the list. The
scanner decides from the probe, which is in stream order. The worker at
pickup (_load_job_data) and an answered review (queue._build_analysis_inputs)
decide from the Track rows, which they read with no ORDER BY, so they relied
on SQLite handing rows back in the order they were inserted. Rows are only
ever inserted in probe order, so that has held; it was never guaranteed.

These insert a file's rows in reverse stream order, the one way to tell an
ordered read from one that happens to come out right, and check the tracks
the decision is given.

Verified by mutation, 2 applied 2 killed, each surviving the whole
2334-test suite first: either read without its ORDER BY.
"""
import json
import os

import pytest
from sqlalchemy.orm import sessionmaker

import app.core.worker as worker
from app.api.routes.queue import _build_analysis_inputs
from app.core.probe import extract_tracks
from app.core.scanner import replace_track_rows
from app.database.models import AppSetting, Base, MediaFile, QueueItem
from tests.conftest import memory_engine

PROBE = {
    "format": {"format_name": "matroska,webm", "duration": "1.0"},
    "streams": [
        {"index": 0, "codec_type": "video", "codec_name": "h264"},
        {"index": 1, "codec_type": "audio", "codec_name": "aac", "channels": 2,
         "tags": {"language": "eng"}, "disposition": {"default": 1}},
        {"index": 2, "codec_type": "audio", "codec_name": "ac3", "channels": 6,
         "tags": {"language": "eng"}},
        {"index": 3, "codec_type": "subtitle", "codec_name": "subrip",
         "tags": {"language": "eng"}},
    ],
}


@pytest.fixture
def db(tmp_path, monkeypatch):
    engine = memory_engine()
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine)
    monkeypatch.setattr(worker, "SessionLocal", factory)
    monkeypatch.setattr(worker, "probe_file", lambda *_a, **_kw: PROBE)
    session = factory()
    path = tmp_path / "Film.mkv"
    path.write_bytes(b"\0" * 64)
    st = os.stat(path)
    session.merge(AppSetting(key="dry_run_mode", value=json.dumps(False)))
    media = MediaFile(path=str(path), filename="Film.mkv", directory=str(tmp_path),
                      container="mkv", size=st.st_size, mtime=st.st_mtime)
    session.add(media)
    session.flush()
    replace_track_rows(session, media.id, list(reversed(extract_tracks(PROBE))))
    session.commit()
    session.media_id = media.id
    yield session
    session.close()


def test_an_answered_review_decides_from_the_tracks_in_stream_order(db):
    media = db.get(MediaFile, db.media_id)

    _file_info, tracks, _kwargs = _build_analysis_inputs(db, media)

    assert [t["stream_index"] for t in tracks] == [0, 1, 2, 3]


def test_a_job_at_pickup_decides_from_the_tracks_in_stream_order(db):
    db.add(QueueItem(file_id=db.media_id, status="processing", reason="queued"))
    db.commit()
    (job_id,) = [i.id for i in db.query(QueueItem)]

    loaded = worker._load_job_data(job_id)

    assert loaded is not None, "the job was not left to run"
    _job, _file, tracks, _cfg, _decision = loaded
    assert [t["stream_index"] for t in tracks] == [0, 1, 2, 3]
