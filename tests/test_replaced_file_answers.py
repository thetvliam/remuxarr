"""
Answers forgotten when a file is replaced by something other than Remuxarr.

Answers to review questions and the three confirmations (Confirm correct on
an audio or subtitle language mismatch, and on the Undefined Audio Track
Threshold) are stored on the file's record, and they stayed there when
Sonarr or Radarr replaced the file at the same path. The new release was
then decided by answers given for the old one. Reproduced against a real
server first: a Keep given for two English subtitle tracks kept a new
release's German ones without asking, and Confirm correct on a Japanese
track stopped a German release from being flagged.

A scan that probes a file now forgets all five when the file changed on
disk and its tracks differ from the stored ones (scanner._replaced_on_disk).
Remuxarr's own jobs, reverts and AC3 forge runs store the tracks of the
file they wrote, so they do not.

These drive the real scan_library, _process_file and queue_single_file
against a real database and real files on disk. Only ffprobe is replaced,
by a table keyed by path, so a test can change what a file holds.

Confirmed unprotected before this file was written: nine mutations, each
run against the whole 2095-test suite, all nine survived. Never
forgetting; forgetting without the file having changed on disk; forgetting
without its tracks having changed; language left out of the comparison;
the -1 re-evaluate marker not counted as a change; and each of the four
kinds of answer (the three answer columns together, then each
confirmation) left in place. All nine are killed here.
"""
import json

import pytest

import app.core.scanner as scanner
from app.core.scanner import queue_single_file, replace_track_rows, scan_library
from app.database.models import AppSetting, MediaFile

ALL_FIVE = {
    "subtitle_overrides":               '{"a": "keep"}',
    "audio_language_overrides":         '{"b": "eng"}',
    "subtitle_language_overrides":      '{"c": "eng"}',
    "audio_language_ignored":           True,
    "subtitle_language_ignored":        True,
    "und_audio_threshold_acknowledged": True,
}
FORGOTTEN = {k: (False if isinstance(v, bool) else None) for k, v in ALL_FIVE.items()}


def _probe(*audio, subtitle="eng"):
    streams = [{"index": 0, "codec_type": "video", "codec_name": "h264"}]
    streams += [{"index": i + 1, "codec_type": "audio", "codec_name": "aac",
                 "channels": 2, "tags": {"language": lang}}
                for i, lang in enumerate(audio)]
    streams.append({"index": len(streams), "codec_type": "subtitle",
                    "codec_name": "ass", "tags": {"language": subtitle,
                                                  "title": "Full"}})
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
    folder = root / "Show"
    folder.mkdir(parents=True)
    probes = {}
    monkeypatch.setattr(scanner, "probe_file", lambda path, *_a, **_kw: probes[path])

    for key, value in {"dry_run_mode": False, "prefer_mp4_container": False,
                       "keep_audio_languages": ["eng", "jpn"],
                       "scan_paths": [str(root)]}.items():
        db.merge(AppSetting(key=key, value=json.dumps(value)))
    db.commit()

    class Lib:
        path = str(folder / "Ep1.mkv")

        @staticmethod
        def put(probe, size):
            """Write the file (size picks its bytes) and what ffprobe reports."""
            (folder / "Ep1.mkv").write_bytes(b"\0" * size)
            probes[Lib.path] = probe

        @staticmethod
        def scan(**kwargs):
            return scan_library(db, [str(root)], **kwargs)

        @staticmethod
        def media():
            db.expire_all()
            return db.query(MediaFile).filter(MediaFile.path == Lib.path).one()

        @staticmethod
        def answer():
            """Store all five, as answers and confirmations would."""
            media = Lib.media()
            for key, value in ALL_FIVE.items():
                setattr(media, key, value)
            db.commit()

        @staticmethod
        def stored():
            media = Lib.media()
            return {key: getattr(media, key) for key in ALL_FIVE}

    Lib.db = db
    yield Lib
    db.close()


def test_a_replaced_release_forgets_every_answer_and_confirmation(lib):
    lib.put(_probe("eng", "jpn"), 64)
    lib.scan()
    lib.answer()

    lib.put(_probe("eng", "ger"), 128)
    lib.scan()

    assert lib.stored() == FORGOTTEN


def test_a_release_differing_only_in_language_is_still_a_replacement(lib):
    """
    The case from the real server: the same track shapes with German
    subtitles where the old file had English. Every field an answer is keyed
    by is the same, so without the language the two look identical.
    """
    lib.put(_probe("eng", subtitle="eng"), 64)
    lib.scan()
    lib.answer()

    lib.put(_probe("eng", subtitle="ger"), 128)
    lib.scan()

    assert lib.stored() == FORGOTTEN


def test_an_import_through_the_webhook_path_forgets_too(lib):
    """queue_single_file, which a Sonarr or Radarr import reaches."""
    lib.put(_probe("eng", "jpn"), 64)
    lib.scan()
    lib.answer()

    lib.put(_probe("eng", "ger"), 128)
    queue_single_file(lib.db, lib.path)

    assert lib.stored() == FORGOTTEN


def test_a_changed_file_with_the_same_tracks_keeps_its_answers(lib):
    """Touched or remuxed by another tool, but nothing was answered differently."""
    lib.put(_probe("eng", "jpn"), 64)
    lib.scan()
    lib.answer()

    lib.put(_probe("eng", "jpn"), 128)
    lib.scan()

    assert lib.stored() == ALL_FIVE


def test_a_full_rescan_keeps_answers_when_an_unchanged_file_reads_differently(lib):
    """
    Force Full Rescan probes every file. A Remuxarr update that changes how
    tracks are read would make every stored track list differ from its
    probe; the file not having changed on disk is what keeps the answers.
    """
    lib.put(_probe("eng", "jpn"), 64)
    lib.scan()
    lib.answer()

    lib.put(_probe("eng", "ger"), 64)   # same bytes, read differently
    lib.scan(force_probe=True)

    assert lib.stored() == ALL_FIVE


def test_an_out_of_date_stamp_with_matching_tracks_keeps_its_answers(lib):
    """
    The AC3 forge stores the tracks of the file it rewrote but leaves its
    size and mtime as they were, so the next scan sees a change on disk and
    tracks that match.
    """
    lib.put(_probe("eng", "jpn"), 64)
    lib.scan()
    lib.answer()
    media = lib.media()
    media.mtime -= 3600
    lib.db.commit()

    lib.scan()

    assert lib.stored() == ALL_FIVE


def test_a_reverted_file_keeps_the_answers_carried_back_to_it(lib):
    """
    A revert puts the original back, stores the tracks it probed from it
    (revert_restore._apply) and writes the -1 re-evaluate marker. The next
    scan finds the tracks it stored.
    """
    lib.put(_probe("eng", "ger"), 128)   # the processed file
    lib.scan()
    lib.answer()

    lib.put(_probe("eng", "jpn"), 64)    # the original, restored
    media = lib.media()
    replace_track_rows(lib.db, media.id, scanner.extract_tracks(_probe("eng", "jpn")))
    media.size, media.mtime = -1, -1.0
    lib.db.commit()

    lib.scan()

    assert lib.stored() == ALL_FIVE


def test_a_file_replaced_after_its_job_was_cancelled_is_still_caught(lib):
    """
    Cancel, dismiss and clear write the -1 marker. A replacement landing
    after one of them is a replacement all the same.
    """
    lib.put(_probe("eng", "jpn"), 64)
    lib.scan()
    lib.answer()
    media = lib.media()
    media.size, media.mtime = -1, -1.0
    lib.db.commit()

    lib.put(_probe("eng", "ger"), 64)
    lib.scan()

    assert lib.stored() == FORGOTTEN
