"""
Ask again: forget what was answered for one file and decide it afresh.

Answers to review questions and the three confirmations are stored on the
file's record and applied every time it is decided, with nothing to take
one back short of applying a language. A wrong Remove answered for a
subtitle came back the moment a revert brought the file back: the revert
carries the answers of the tracks it kept, and the next scan applied them.

POST /api/queue/{item_id}/ask-again forgets all five for the item's file
(scanner.forget_answers) and decides the file again now, with a fresh probe
and the current settings, as Re-process does. The details window shows the
button only for a file with something stored, which the queue and history
rows now say in file.has_answers.

These drive the real route function and _process_file against a real
database and real files. Only ffprobe is replaced, by a table keyed by
path, so a test can make it fail.

Confirmed unprotected before this file was written: eight mutations, each
run against the whole 2103-test suite, all eight survived. Forgetting
without deciding again; forgetting only the per-track answers; no refusal
while a job is running; the forgotten answers committed when the file
cannot be read; has_answers blind to the confirmations; has_answers always
false; has_answers always true; and the route holding on to the item while
it is replaced (see the first test). All eight are killed here.
"""
import json
import warnings

import pytest
from fastapi import HTTPException
from sqlalchemy.exc import SAWarning

import app.core.scanner as scanner
from app.api.routes.queue import _serialize, ask_again
from app.core.probe import ProbeError
from app.core.scanner import descriptors_by_stream, extract_tracks, scan_library
from app.database.models import (AppSetting, AudioLanguageFlag, MediaFile,
                                 QueueItem)

CONFIRMATIONS = ("audio_language_ignored", "subtitle_language_ignored",
                 "und_audio_threshold_acknowledged")


def _probe(audio="eng", pgs=True):
    streams = [{"index": 0, "codec_type": "video", "codec_name": "h264"},
               {"index": 1, "codec_type": "audio", "codec_name": "aac",
                "channels": 2, "tags": {"language": audio}}]
    if pgs:
        streams.append({"index": 2, "codec_type": "subtitle",
                        "codec_name": "hdmv_pgs_subtitle",
                        "tags": {"language": "eng"}})
    return {"format": {"format_name": "matroska,webm", "duration": "1.0"},
            "streams": streams}


@pytest.fixture
def lib(tmp_path, monkeypatch):
    from sqlalchemy.orm import sessionmaker

    from app.database.models import Base
    from tests.conftest import memory_engine

    engine = memory_engine()
    Base.metadata.create_all(engine)
    db = sessionmaker(bind=engine)()
    root = tmp_path / "tv"
    (root / "Show").mkdir(parents=True)
    path = str(root / "Show" / "Ep1.mkv")
    probes = {}

    def fake_probe(p, *_a, **_kw):
        if isinstance(probes[p], Exception):
            raise probes[p]
        return probes[p]

    monkeypatch.setattr(scanner, "probe_file", fake_probe)

    for key, value in {"dry_run_mode": False, "prefer_mp4_container": False,
                       "keep_audio_languages": ["eng"],
                       "image_subtitle_handling": "always_ask",
                       "scan_paths": [str(root)]}.items():
        db.merge(AppSetting(key=key, value=json.dumps(value)))
    db.commit()

    class Lib:
        @staticmethod
        def put(probe):
            with open(path, "wb") as f:
                f.write(b"\0" * 64)
            probes[path] = probe

        @staticmethod
        def scan(**kwargs):
            return scan_library(db, [str(root)], **kwargs)

        @staticmethod
        def media():
            db.expire_all()
            return db.query(MediaFile).filter(MediaFile.path == path).one()

        @staticmethod
        def items():
            db.expire_all()
            return [i.status for i in db.query(QueueItem).order_by(QueueItem.id)]

        @staticmethod
        def item(status):
            return db.query(QueueItem).filter(QueueItem.status == status).one()

        @staticmethod
        def answer_remove():
            """Remove on the PGS track, stored as review/apply stores it."""
            media = Lib.media()
            key = descriptors_by_stream(extract_tracks(probes[path]))[2]
            media.subtitle_overrides = json.dumps({key: "remove"})
            db.commit()

        @staticmethod
        def fail_probe():
            probes[path] = ProbeError("unreadable")

    Lib.db = db
    yield Lib
    db.close()


def _answered_and_waiting(lib):
    """A file in review, answered Remove, now waiting with that job."""
    lib.put(_probe())
    lib.scan()
    assert lib.items() == ["manual_review"]
    lib.answer_remove()
    lib.scan(recheck=True)
    assert lib.items() == ["pending"]


def test_a_waiting_job_decided_by_an_answer_goes_back_to_review(lib):
    """
    Without a warning: the waiting job is bulk-deleted and the review item
    that replaces it gets its id, and while the route still held the old
    object the two collided in the session (SAWarning, "Identity map already
    had an identity"). The suite runs without warnings, so this one fails.
    """
    _answered_and_waiting(lib)

    with warnings.catch_warnings():
        warnings.simplefilter("error", SAWarning)
        out = ask_again(lib.item("pending").id, db=lib.db)

    assert lib.items() == ["manual_review"]
    assert lib.media().subtitle_overrides is None
    assert out["status"] == "manual_review"


def test_a_success_record_stays_in_history(lib):
    """What a revert leaves behind, and where the button is pressed for it."""
    _answered_and_waiting(lib)
    lib.item("pending").status = "success"
    lib.db.commit()

    ask_again(lib.item("success").id, db=lib.db)

    assert lib.items() == ["success", "manual_review"]


def test_confirm_correct_is_taken_back_and_the_mismatch_flagged_again(lib):
    lib.put(_probe(audio="jpn", pgs=False))
    lib.scan()
    media = lib.media()
    lib.db.query(AudioLanguageFlag).delete()
    for attr in CONFIRMATIONS:
        setattr(media, attr, True)
    item = lib.db.query(QueueItem).first()
    lib.db.commit()

    ask_again(item.id, db=lib.db)

    media = lib.media()
    assert [getattr(media, a) for a in CONFIRMATIONS] == [False, False, False]
    assert lib.db.query(AudioLanguageFlag).filter(
        AudioLanguageFlag.file_id == media.id).count() == 1


def test_refused_while_the_files_job_is_running(lib):
    """The running job decided from the answers; it finishes with them."""
    _answered_and_waiting(lib)
    lib.item("pending").status = "processing"
    lib.db.commit()

    with pytest.raises(HTTPException) as exc:
        ask_again(lib.item("processing").id, db=lib.db)

    assert exc.value.status_code == 409
    assert lib.media().subtitle_overrides is not None


def test_nothing_is_forgotten_when_the_file_cannot_be_read(lib):
    _answered_and_waiting(lib)
    lib.fail_probe()

    with pytest.raises(HTTPException) as exc:
        ask_again(lib.item("pending").id, db=lib.db)

    assert exc.value.status_code == 400
    assert lib.media().subtitle_overrides is not None
    assert lib.items() == ["pending"]


def test_an_unknown_item_is_a_404(lib):
    with pytest.raises(HTTPException) as exc:
        ask_again(999, db=lib.db)
    assert exc.value.status_code == 404


@pytest.mark.parametrize("attr, value", [
    ("subtitle_overrides", '{"a": "keep"}'),
    ("audio_language_overrides", '{"a": "eng"}'),
    ("subtitle_language_overrides", '{"a": "eng"}'),
    ("audio_language_ignored", True),
    ("subtitle_language_ignored", True),
    ("und_audio_threshold_acknowledged", True),
])
def test_rows_say_the_file_has_answers(lib, attr, value):
    lib.put(_probe(pgs=True))
    lib.scan()
    item = lib.db.query(QueueItem).first()
    assert _serialize(item)["file"]["has_answers"] is False

    setattr(lib.media(), attr, value)
    lib.db.commit()

    assert _serialize(item)["file"]["has_answers"] is True
