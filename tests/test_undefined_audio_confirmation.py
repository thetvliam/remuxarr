"""
Confirm correct on a file's untagged audio, and Clear, end to end.

Every audio flag on an untagged track has the origin "undefined" now, raised
by Always Ask, or by Always Fix for the tracks it will not guess at. Confirm
correct on one is recorded on und_audio_threshold_acknowledged, the switch
the retired Undefined Audio Track Threshold used (the name was kept so the
column did not have to change), and Clear acknowledged on the Review page
takes it back.

These run a real scan against an in-memory database with ffprobe replaced,
then the routes the Review page calls, then the scan again, so each answer
is followed to what the next scan does with it. The switch-level mutants
(the answer written on the mismatch switch, the acknowledgement ignored) are
recorded where they were first killed, in test_audio_language_review and
test_decision; this file is the one path through all of it.
"""
import json

import pytest

import app.core.scanner as scanner
from app.api.routes._language_review import IgnoreRequest
from app.api.routes.audio_language import ignore_flags
from app.api.routes.queue import ClearAcknowledgedRequest, clear_acknowledged
from app.core.scanner import scan_library
from app.database.models import AppSetting, AudioLanguageFlag, MediaFile


def _probe(*audio_languages):
    streams = [{"index": 0, "codec_type": "video", "codec_name": "h264"}]
    for i, language in enumerate(audio_languages, start=1):
        stream = {"index": i, "codec_type": "audio", "codec_name": "aac",
                  "channels": 2}
        if language:
            stream["tags"] = {"language": language}
        streams.append(stream)
    return {"format": {"format_name": "matroska,webm", "duration": "1.0"},
            "streams": streams}


@pytest.fixture
def lib(tmp_path, monkeypatch):
    """
    One MKV with two untagged audio tracks, Always Ask on audio, and the
    container left alone, so nothing but the language questions is in play.
    """
    from sqlalchemy.orm import sessionmaker

    from app.database.models import Base
    from tests.conftest import memory_engine

    engine = memory_engine()
    Base.metadata.create_all(engine)
    db = sessionmaker(bind=engine)()
    root = tmp_path / "films"
    root.mkdir()
    path = root / "Film.mkv"
    path.write_bytes(b"\0" * 64)
    probes = {str(path): _probe(None, None)}
    monkeypatch.setattr(scanner, "probe_file", lambda p, *_a, **_kw: probes[p])

    for key, value in {"dry_run_mode": False, "prefer_mp4_container": False,
                       "fix_undefined_language_audio": "always_ask",
                       "scan_paths": [str(root)]}.items():
        db.merge(AppSetting(key=key, value=json.dumps(value)))
    db.commit()

    class Lib:
        @staticmethod
        def scan(**kwargs):
            scan_library(db, [str(root)], **kwargs)

        @staticmethod
        def media():
            db.expire_all()
            return db.query(MediaFile).one()

        @staticmethod
        def flags():
            db.expire_all()
            return [(f.stream_index, f.origin)
                    for f in db.query(AudioLanguageFlag).order_by(AudioLanguageFlag.stream_index)]

    Lib.db = db
    Lib.scan(force_probe=True)
    yield Lib
    db.close()


def test_confirm_correct_answers_the_untagged_tracks_and_clear_asks_again(lib):
    assert lib.flags() == [(1, "undefined"), (2, "undefined")]
    file_id = lib.media().id

    ignore_flags(IgnoreRequest(file_ids=[file_id]), lib.db)

    media = lib.media()
    assert media.und_audio_threshold_acknowledged is True
    assert media.audio_language_ignored is not True, (
        "an untagged-track answer was recorded on the language-mismatch switch")
    assert lib.flags() == []

    lib.scan(recheck=True)
    assert lib.flags() == [], "the next scan asked about the confirmed tracks again"

    reply = clear_acknowledged(ClearAcknowledgedRequest(file_ids=[file_id]), lib.db)
    assert reply["cleared"] == 1

    lib.scan()
    assert lib.flags() == [(1, "undefined"), (2, "undefined")]
