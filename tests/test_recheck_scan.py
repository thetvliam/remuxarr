"""
The re-check scan: a settings change applied to files already scanned.

A routine scan skips a file whose size and mtime have not changed, so a
change to Keep Audio Languages, say, reached only files scanned afterwards;
every file seen before kept its result until a Force Full Rescan, which runs
ffprobe on every file in the library. Reproduced against a real server
first: with the keep list narrowed from English and French to English, a
routine scan left a seen file's French track in place.

A re-check decides an unchanged file again from the tracks stored when it
was last probed, the way the worker re-decides a waiting job, and probes
only what changed on disk. Before relying on the stored tracks, the decision
from them was compared with the decision from a fresh probe for all 18 files
of tests/sample_library under three different settings: no differences.

These drive the real scan_library and _process_file against a real database
and real files on disk. Only ffprobe is replaced, by a recorder that answers
from a table, so each test can assert whether a file was probed at all.

Confirmed unprotected before this file was written: seven mutations, each
run against the whole 2084-test suite, all seven survived. The flag ignored,
so an unchanged file is still skipped; unchanged files probed anyway; the
stored tracks used for a file that changed on disk; scan_library not passing
the flag on; the route not passing it to the thread; _run_scan not passing
it to scan_library; and Force Full Rescan losing to a re-check. The first
three and the last are killed here; the three hand-ons are killed in
test_scan_library.py and test_scan_routes.py, beside the tests for the
other scan modes.
"""
import json

import pytest

import app.core.scanner as scanner
from app.core.scanner import scan_library
from app.database.models import AppSetting, QueueItem


def _audio(index, language):
    return {"index": index, "codec_type": "audio", "codec_name": "aac",
            "channels": 2, "tags": {"language": language},
            "disposition": {"default": 1 if index == 1 else 0}}


def _probe(*languages, subtitle=None):
    streams = [{"index": 0, "codec_type": "video", "codec_name": "h264"}]
    streams += [_audio(i + 1, lang) for i, lang in enumerate(languages)]
    if subtitle:
        streams.append({"index": len(streams), "codec_type": "subtitle",
                        "codec_name": subtitle, "tags": {"language": "eng"}})
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

    folder = tmp_path / "tv" / "Show"
    folder.mkdir(parents=True)
    probes, probed = {}, []

    def fake_probe(path, *_a, **_kw):
        probed.append(path)
        return probes[path]

    monkeypatch.setattr(scanner, "probe_file", fake_probe)

    def setting(key, value):
        db.merge(AppSetting(key=key, value=json.dumps(value)))
        db.commit()

    def media(name, probe, size=64):
        path = folder / name
        path.write_bytes(b"\0" * size)
        probes[str(path)] = probe
        return str(path)

    def scan(**kwargs):
        probed.clear()
        return scan_library(db, [str(tmp_path / "tv")], **kwargs)

    def items():
        return [(i.status, i.is_dry_run) for i in
                db.query(QueueItem).order_by(QueueItem.id)]

    setting("dry_run_mode", False)
    setting("prefer_mp4_container", False)
    setting("keep_audio_languages", ["eng", "fre"])
    yield type("Lib", (), dict(db=db, setting=staticmethod(setting),
                               media=staticmethod(media), scan=staticmethod(scan),
                               items=staticmethod(items), probes=probes,
                               probed=probed))
    db.close()


def test_a_recheck_applies_a_narrower_setting_without_probing(lib):
    """The unchanged file is queued from its stored tracks; ffprobe never runs."""
    lib.media("Ep1.mkv", _probe("eng", "fre"))
    lib.scan()
    assert lib.items() == [("skipped", False)]

    lib.setting("keep_audio_languages", ["eng"])
    lib.scan(recheck=True)

    assert lib.probed == []
    assert lib.items()[-1] == ("pending", False)


def test_a_routine_scan_still_leaves_the_file_as_it_was(lib):
    """The control: without the re-check, nothing about the file changes."""
    lib.media("Ep1.mkv", _probe("eng", "fre"))
    lib.scan()
    lib.setting("keep_audio_languages", ["eng"])

    lib.scan()

    assert lib.probed == []
    assert lib.items() == [("skipped", False)]


def test_a_file_changed_on_disk_is_probed_during_a_recheck(lib):
    """
    Its stored tracks may no longer describe it. Here the new release has a
    German track where the old had French: decided from the stored tracks,
    the file still needs nothing; probed, the German track goes.
    """
    path = lib.media("Ep1.mkv", _probe("eng", "fre"))
    lib.scan()

    lib.media("Ep1.mkv", _probe("eng", "ger"), size=128)
    lib.scan(recheck=True)

    assert lib.probed == [path]
    assert lib.items()[-1] == ("pending", False)


def test_a_file_held_in_review_is_decided_again(lib):
    lib.setting("image_subtitle_handling", "always_ask")
    lib.media("Ep1.mkv", _probe("eng", subtitle="hdmv_pgs_subtitle"))
    lib.scan()
    assert lib.items() == [("manual_review", False)]

    lib.setting("image_subtitle_handling", "always_keep")
    lib.scan(recheck=True)

    assert lib.probed == []
    assert lib.items() == [("skipped", False)]


def test_a_file_processed_before_gets_a_new_job_and_keeps_its_record(lib):
    """
    Decided: a re-check covers files already processed, as Force Full Rescan
    does. The success record is history and stays.
    """
    lib.media("Ep1.mkv", _probe("eng", "fre"))
    lib.scan()
    done = lib.db.query(QueueItem).one()
    done.status = "success"
    lib.db.commit()

    lib.setting("keep_audio_languages", ["eng"])
    lib.scan(recheck=True)

    assert lib.items() == [("success", False), ("pending", False)]


def test_with_dry_run_on_the_job_a_recheck_queues_is_a_preview(lib):
    lib.media("Ep1.mkv", _probe("eng", "fre"))
    lib.scan()
    lib.setting("dry_run_mode", True)
    lib.setting("keep_audio_languages", ["eng"])

    lib.scan(recheck=True)

    assert lib.items()[-1] == ("pending", True)


def test_force_full_rescan_still_probes_when_a_recheck_is_also_asked_for(lib):
    path = lib.media("Ep1.mkv", _probe("eng", "fre"))
    lib.scan()

    lib.scan(force_probe=True, recheck=True)

    assert lib.probed == [path]
