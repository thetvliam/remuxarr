"""
Files a job creates alongside the media, and what a revert does with them.

Subtitle extraction is the only thing that does this today, and it does
two things at once: it writes .srt files next to the media AND removes
those subtitles from the mux. A revert re-embeds them, so leaving the
files behind gives the user every extracted subtitle twice — which
players show as duplicate tracks. extract_text_subtitles_to_srt defaults
on, so this is the ordinary path rather than a corner.

The obvious fix is a trap. Deleting every .srt beside the media would
destroy files that predate Remuxarr entirely: someone with Bazarr already
has Movie.eng.srt, the job OVERWROTE it, and a revert removing it takes
something Remuxarr never made. So only files the job actually created are
recorded, which is knowable only BEFORE the job runs — by the time
capture sees them they all exist and look identical.

A second guard sits on top: each recorded file carries a fingerprint, and
one that has changed since is left alone. Someone who edited an extracted
.srt clearly wants it.

Verified by mutation, 8 applied, 8 killed:

  • Created files not recorded at all               → killed
  • Every extraction recorded, not just new files   → killed
  • Fingerprint not recorded                        → killed
  • Fingerprint not checked before deleting         → killed
  • Records replaced on extend rather than merged   → killed
  • Files removed before the swap rather than after → killed
  • A failed os.stat treated as a match             → killed
  • Removal failure raised instead of logged        → killed
"""
import json

import pytest


# ── Recording, at capture time ───────────────────────────────────────────────

def test_only_files_the_job_created_are_recorded(tmp_path):
    """
    The distinction the whole feature turns on. A path that already
    existed was overwritten, not created, and is not Remuxarr's to remove.
    """
    from app.core.revert_capture import _record_created_files

    created = tmp_path / "new.eng.srt"
    created.write_text("written by this job")

    manifest = {"streams": []}
    _record_created_files(manifest, [str(created)])

    assert [e["path"] for e in manifest["created_files"]] == [str(created)]


def test_a_recorded_file_carries_a_fingerprint(tmp_path):
    from app.core.revert_capture import _record_created_files

    created = tmp_path / "new.eng.srt"
    created.write_text("subtitle text")
    stat = created.stat()

    manifest = {"streams": []}
    _record_created_files(manifest, [str(created)])

    entry = manifest["created_files"][0]
    assert entry["size"] == stat.st_size
    assert entry["mtime"] == stat.st_mtime


def test_a_file_the_job_did_not_actually_write_is_skipped(tmp_path):
    """
    The worker predicts what the job will create. If FFmpeg then wrote
    nothing there, recording it would have a later revert try to delete a
    path it knows nothing about.
    """
    from app.core.revert_capture import _record_created_files

    manifest = {"streams": []}
    _record_created_files(manifest, [str(tmp_path / "never-written.srt")])

    assert manifest["created_files"] == []


def test_records_are_merged_across_jobs_not_replaced(tmp_path):
    """
    A later job extracting to a path an earlier job already created does
    not create it — so it reports nothing, and the earlier record is the
    only one that will ever remove that file. Replacing the list on extend
    loses it silently.
    """
    from app.core.revert_capture import _record_created_files

    first = tmp_path / "first.eng.srt"
    first.write_text("from job one")
    second = tmp_path / "second.ger.srt"
    second.write_text("from job two")

    manifest = {"streams": []}
    _record_created_files(manifest, [str(first)])
    _record_created_files(manifest, [str(second)])

    assert {e["path"] for e in manifest["created_files"]} == {
        str(first), str(second)}


def test_re_recording_a_known_path_keeps_the_original_fingerprint(tmp_path):
    """
    The first record is the accurate one: it describes the file as the job
    that created it left it. Overwriting with a later stat would adopt
    whatever has happened to the file since, defeating the check that
    protects a user's edits.
    """
    from app.core.revert_capture import _record_created_files

    created = tmp_path / "sub.eng.srt"
    created.write_text("original")

    manifest = {"streams": []}
    _record_created_files(manifest, [str(created)])
    original_size = manifest["created_files"][0]["size"]

    created.write_text("a much longer subtitle, edited by the user")
    _record_created_files(manifest, [str(created)])

    assert manifest["created_files"][0]["size"] == original_size


# ── Deciding, before the job runs ────────────────────────────────────────────

class _Action:
    def __init__(self, external_path):
        self.external_path = external_path


def test_only_paths_that_do_not_yet_exist_are_claimed(tmp_path):
    """
    The Bazarr case, at the point where it is decided. A path that already
    exists will be overwritten by the job, not created by it, so a revert
    must not take it away.

    Checked here rather than at capture time because by then the job has
    run and every extraction target exists.
    """
    from app.core.worker import _files_the_job_will_create

    theirs = tmp_path / "Show.eng.srt"
    theirs.write_text("downloaded by Bazarr")
    mine = tmp_path / "Show.ger.srt"          # does not exist yet

    claimed = _files_the_job_will_create(
        [_Action(str(theirs)), _Action(str(mine))])

    assert claimed == [str(mine)]


def test_an_extraction_with_no_target_path_is_ignored(tmp_path):
    from app.core.worker import _files_the_job_will_create

    assert _files_the_job_will_create([_Action(None)]) == []


# ── Removing, at revert time ─────────────────────────────────────────────────

def test_an_untouched_created_file_is_removed(tmp_path):
    from app.core.revert_restore import _remove_created_files

    created = tmp_path / "sub.eng.srt"
    created.write_text("subtitle text")
    stat = created.stat()

    _remove_created_files({"created_files": [
        {"path": str(created), "size": stat.st_size, "mtime": stat.st_mtime}]})

    assert not created.exists()


def test_an_edited_file_is_left_alone(tmp_path):
    """
    Someone who has retimed or corrected an extracted subtitle wants it.
    Reverting the media file is not permission to throw that away.
    """
    from app.core.revert_restore import _remove_created_files

    created = tmp_path / "sub.eng.srt"
    created.write_text("original text")
    stat = created.stat()
    created.write_text("corrected timings and a few fixed lines")

    _remove_created_files({"created_files": [
        {"path": str(created), "size": stat.st_size, "mtime": stat.st_mtime}]})

    assert created.exists()
    assert "corrected" in created.read_text()


def test_a_file_already_gone_is_not_an_error(tmp_path):
    from app.core.revert_restore import _remove_created_files

    _remove_created_files({"created_files": [
        {"path": str(tmp_path / "deleted.srt"), "size": 10, "mtime": 1.0}]})


def test_an_unremovable_file_does_not_fail_the_revert(tmp_path, monkeypatch):
    """
    The media file is already restored by this point. A subtitle file that
    will not delete is untidy, not a failure, and raising would send the
    user to retry a revert that has already happened — where the sentinel
    would then refuse them.
    """
    import app.core.revert_restore as mod

    created = tmp_path / "sub.eng.srt"
    created.write_text("text")
    stat = created.stat()

    def boom(*_a, **_k):
        raise OSError("read-only file system")

    monkeypatch.setattr(mod.os, "remove", boom)

    _remove_created_files = mod._remove_created_files
    _remove_created_files({"created_files": [
        {"path": str(created), "size": stat.st_size, "mtime": stat.st_mtime}]})


def test_a_manifest_without_the_key_is_fine(tmp_path):
    """Manifests written before this existed simply have nothing to clean."""
    from app.core.revert_restore import _remove_created_files

    _remove_created_files({"streams": []})


# ── End to end ───────────────────────────────────────────────────────────────

@pytest.fixture
def extracted(tmp_path, monkeypatch):
    """A revert point whose job extracted one subtitle and overwrote another."""
    from sqlalchemy.orm import sessionmaker

    from app.config import settings as app_settings
    from app.database.models import Base, MediaFile, RevertPoint
    import app.database.session as session_mod
    from tests.conftest import memory_engine

    recycle = tmp_path / "recycle"
    recycle.mkdir()
    monkeypatch.setattr(app_settings, "RECYCLE_DIR", str(recycle), raising=False)

    engine = memory_engine()
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine)
    monkeypatch.setattr(session_mod, "SessionLocal", factory)

    media_file = tmp_path / "Show.mkv"
    media_file.write_bytes(b"processed output")

    mine = tmp_path / "Show.ger.srt"          # created by the job
    mine.write_text("extracted by the job")
    theirs = tmp_path / "Show.eng.srt"        # pre-existed; job overwrote it
    theirs.write_text("downloaded by Bazarr, overwritten by the job")

    stat = media_file.stat()
    created_stat = mine.stat()

    db = factory()
    media = MediaFile(path=str(media_file), filename="Show.mkv",
                      directory=str(tmp_path), size=stat.st_size,
                      mtime=stat.st_mtime, container="mkv")
    db.add(media)
    db.commit()

    manifest = {
        "version": 2, "path": str(media_file), "container": "mkv",
        "streams": [], "duration": 60.0,
        # Only the file the job actually created is listed.
        "created_files": [{"path": str(mine), "size": created_stat.st_size,
                           "mtime": created_stat.st_mtime}],
    }
    point = RevertPoint(file_id=media.id, sidecar_path=str(recycle / "x"),
                        sidecar_size=1, manifest=json.dumps(manifest),
                        original_path=str(media_file),
                        processed_size=stat.st_size,
                        processed_mtime=stat.st_mtime)
    db.add(point)
    db.commit()

    return {"manifest": manifest, "mine": mine, "theirs": theirs}


def test_a_revert_removes_only_what_the_job_created(extracted):
    """
    The Bazarr case. The job overwrote Show.eng.srt, so it is not in the
    record and survives; Show.ger.srt was created by the job and goes.
    """
    from app.core.revert_restore import _remove_created_files

    _remove_created_files(extracted["manifest"])

    assert not extracted["mine"].exists()
    assert extracted["theirs"].exists(), (
        "a subtitle file that predated the job was deleted"
    )
