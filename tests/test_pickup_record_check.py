"""
A job is decided from the file it will run on, not from a stale record.

Reported from a real server. Re-check Scanned Files queued two files to
"Fix undefined language tag on 1 track" whose audio was tagged English on
disk. Both had been processed by a build from before a job stored its
output's tracks (2f3c521): the record still held the tracks from before
processing, while its size and mtime had been brought up to date, so no scan
had looked again. A re-check decides from the record, and so did the
worker at pickup. Those two were skipped only because a Force Full Rescan
had refreshed their records while they waited. Without it, the job would have
run against tracks the file no longer has.

worker._load_job_data now probes the file at pickup and compares its tracks
with the record as a scan does (scanner._track_shapes). A mismatch cancels
the job and decides the file again from the probe, the way a file that
changes during its job is decided again (_cancel_for_changed_source). The
scan stamp is left for that re-evaluation to compare, so a file unchanged
on disk keeps its answers and one replaced since it was queued loses them.
The same probe supplies the font count for a row that has none.

Answers are observed through audio_language_ignored, a confirmation
forget_answers clears along with them.

Verified by mutation, 7 applied 7 killed. Against the 2250-test suite
less this file, four survived and three were already caught: every job
cancelled (by 49 tests whose jobs then never ran), the stamp compared
instead of the tracks (by 3, two in test_forge_keeps_tracks_current.py,
where the forge rewrites a file and leaves its stamp), and a failed probe
failing the job (by 21 whose dummy files cannot be probed).

  survived, then   no comparison, the job always runs   → stale record,
                                                          replaced
                   the stamp reset before deciding      → stale record
                   the font count probing again         → tracks match
                   the file not decided again           → stale record,
                                                          replaced
  already caught   every job cancelled                  → also tracks
                                                          match, stamp only
                   the stamp compared, not the tracks   → also stale
                                                          record, stamp only
                   a failed probe failing the job       → also probe fails

Run from the project root:
    pytest tests/test_pickup_record_check.py -v
"""
import json
import os

import pytest
from sqlalchemy.orm import sessionmaker

import app.core.scanner as scanner
import app.core.worker as worker
from app.core.probe import ProbeError, extract_tracks
from app.core.scanner import replace_track_rows
from app.database.models import AppSetting, Base, MediaFile, PlannedAction, QueueItem, Track
from tests.conftest import memory_engine


def _probe(*languages):
    streams = [{"index": 0, "codec_type": "video", "codec_name": "h264"}]
    streams += [{"index": i + 1, "codec_type": "audio", "codec_name": "aac",
                 "channels": 2, "tags": {"language": lang},
                 "disposition": {"default": 1 if i == 0 else 0}}
                for i, lang in enumerate(languages)]
    return {"format": {"format_name": "matroska,webm", "duration": "1.0"},
            "streams": streams}


@pytest.fixture
def server(tmp_path, monkeypatch):
    """
    A database the worker and the test share, one file on disk, and an
    ffprobe that answers with `server.on_disk` for both the worker and the
    scan it hands a mismatch to.
    """
    engine = memory_engine()
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine)
    monkeypatch.setattr(worker, "SessionLocal", factory)

    path = tmp_path / "Ep1.mkv"
    path.write_bytes(b"\0" * 64)
    st = os.stat(path)
    state = type("Server", (), {})()
    state.factory, state.path, state.probed = factory, str(path), []
    state.on_disk = None

    def fake_probe(probed_path, *_a, **_kw):
        state.probed.append(probed_path)
        if isinstance(state.on_disk, Exception):
            raise state.on_disk
        return state.on_disk

    monkeypatch.setattr(worker, "probe_file", fake_probe)
    monkeypatch.setattr(scanner, "probe_file", fake_probe)

    def job(recorded, on_disk, *, stamp_changed=False):
        """A claimed job whose record holds `recorded`; the file is `on_disk`."""
        state.on_disk = on_disk
        with factory() as db:
            for key, value in {"dry_run_mode": False, "prefer_mp4_container": False,
                               "keep_audio_languages": ["eng"],
                               "fix_undefined_language_audio": "always_fix"}.items():
                db.merge(AppSetting(key=key, value=json.dumps(value)))
            media = MediaFile(path=state.path, filename="Ep1.mkv",
                              directory=str(tmp_path), container="mkv",
                              size=st.st_size + (1000 if stamp_changed else 0),
                              mtime=st.st_mtime, audio_language_ignored=True)
            db.add(media)
            db.flush()
            replace_track_rows(db, media.id, extract_tracks(recorded))
            item = QueueItem(file_id=media.id, status="processing",
                             reason="Fix undefined language tag on 1 track")
            db.add(item)
            db.commit()
            state.media_id, state.job_id = media.id, item.id
        state.probed.clear()
        return worker._load_job_data(state.job_id)

    state.job = job
    return state


def _items(server):
    with server.factory() as db:
        return [(i.status, [a.description for a in
                            db.query(PlannedAction).filter(PlannedAction.queue_item_id == i.id)
                            .order_by(PlannedAction.order)])
                for i in db.query(QueueItem).order_by(QueueItem.id)]


def _media(server):
    with server.factory() as db:
        media = db.get(MediaFile, server.media_id)
        languages = [t.language for t in db.query(Track)
                     .filter(Track.file_id == media.id).order_by(Track.stream_index)]
        return media.size, media.audio_language_ignored, languages


def test_a_stale_record_is_decided_again_from_the_file(server):
    """
    The reported case: the record holds the audio as undefined, the file has
    it as English, and the file is unchanged on disk. The job is cancelled,
    the record refreshed, and the file needs nothing, so it is skipped. It
    keeps its answers, since nothing replaced it.
    """
    assert server.job(_probe("und"), _probe("eng")) is None

    with server.factory() as db:
        job = db.get(QueueItem, server.job_id)
        assert job.status == "cancelled"
        assert "no longer matched the tracks" in job.error_message
    assert [status for status, _ in _items(server)] == ["cancelled", "skipped"]
    size, ignored, languages = _media(server)
    assert languages == ["und", "eng"]
    assert size == os.stat(server.path).st_size
    assert ignored is True


def test_a_file_replaced_before_pickup_is_decided_again_without_its_answers(server):
    """
    Replaced at the same path while the job waited, with a German track the
    record does not have. The new file is queued for what it needs, and the
    answers given for the old one are forgotten, as a scan forgets them.
    """
    assert server.job(_probe("eng"), _probe("eng", "ger"), stamp_changed=True) is None

    items = _items(server)
    assert items[0][0] == "cancelled"
    assert items[1][0] == "pending"
    assert any(d.startswith("Drop audio [ger]") for d in items[1][1])
    _size, ignored, languages = _media(server)
    assert languages == ["und", "eng", "ger"]
    assert ignored is False


def test_a_record_that_matches_runs_with_one_probe(server):
    """
    The everyday case. The job goes ahead, and the one probe also counts the
    fonts of a row that never had them counted.
    """
    tracks = _probe("eng", "ger")

    assert server.job(tracks, tracks) is not None

    assert server.probed == [server.path]
    with server.factory() as db:
        assert db.get(QueueItem, server.job_id).status == "processing"
        assert db.get(MediaFile, server.media_id).font_attachments == 0


def test_a_failed_probe_decides_from_the_record(server):
    """As before this check: FFmpeg reports an unreadable file when it runs."""
    tracks = _probe("eng", "ger")
    assert server.job(tracks, ProbeError("ffprobe failed: share not mounted")) is not None

    with server.factory() as db:
        assert db.get(QueueItem, server.job_id).status == "processing"


def test_a_changed_stamp_over_the_same_tracks_runs(server):
    """The tracks the job decides from are still right, so nothing stops it."""
    tracks = _probe("eng", "ger")

    assert server.job(tracks, tracks, stamp_changed=True) is not None

    with server.factory() as db:
        assert db.get(QueueItem, server.job_id).status == "processing"
