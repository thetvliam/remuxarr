"""
Renaming an extracted subtitle when its language is chosen in review.

The gap this closes: an extracted subtitle has been taken OUT of the mux,
so applying a language override has no track left to re-extract under the
corrected name. The reprocess the review triggers cannot reach the file.
It keeps "und" in its filename permanently — and the filename is what
Plex reads, so the correction the user made has no effect at all on the
thing they were correcting.

Why the path is stored rather than derived: by review time the track is
gone from the file, and with it the forced / SDH / dub flags that shaped
the name. Show.und.forced.srt cannot be reconstructed from a stream index.
Guessing would rename a different subtitle.

The rename is deliberately conservative. It replaces one component of the
name and refuses in every case where it cannot be sure:

  * no recorded path, or the file is no longer there
  * the name does not contain the language it was extracted under
  * the target name is already taken — quite possibly by a subtitle the
    user downloaded for exactly this language

and it never raises, because the language override is committed before it
runs. A sidecar that will not rename is a wrong filename; failing the
request would leave the user unsure whether their answer was recorded.

Verified by mutation, 9 applied, 9 killed:

  • Rename skipped entirely                        → killed
  • Suffixes dropped (Show.und.forced → Show.eng)  → killed
  • First matching component replaced, not the
    language one                                    → killed
  • Existing target overwritten                     → killed
  • Missing source treated as renameable            → killed
  • Recorded path ignored, name derived instead     → killed
  • flag.extracted_path not updated after renaming  → killed
  • OSError propagated instead of logged            → killed
  • Rename attempted for an embedded track          → killed
  • A rescan blanking the recorded path             → killed
  • Revert points never updated after a rename      → killed
  • Every created-file entry rewritten, not the
    renamed one                                      → killed
  • The fingerprint rewritten along with the path   → killed
  • The updated manifest not written back           → killed

Dropping the file_id filter is equivalent and recorded rather than
tested: the path match already prevents another file's records being
touched, and extracted subtitle paths derive from the media filename so
two files cannot record the same one. The filter is there to avoid
JSON-parsing every revert point in the bin on every correction. Verified
by applying it alone (passes) and together with a loosened path match
(fails).

One further mutant, dropping the existence check on the source, is
observably identical: os.rename raises FileNotFoundError and the same
handler catches it. The check earns its place by keeping an ordinary
event quiet — a user deleting an extracted subtitle should not produce a
warning — and that is what the test asserts.
"""
import os



class _Flag:
    """Stands in for a SubtitleLanguageFlag row."""

    def __init__(self, extracted_path, detected_language="und", file_id=1):
        self.extracted_path = extracted_path
        self.detected_language = detected_language
        self.stream_index = 2
        self.file_id = file_id


def _rename(flag, lang, db=None):
    from app.api.routes._language_review import _rename_extracted_subtitle

    return _rename_extracted_subtitle(flag, lang, db)


# ── The ordinary case ────────────────────────────────────────────────────────

def test_the_language_component_is_replaced(tmp_path):
    srt = tmp_path / "Show.und.srt"
    srt.write_text("subtitle text")
    flag = _Flag(str(srt))

    result = _rename(flag, "eng")

    assert result == str(tmp_path / "Show.en.srt")
    assert (tmp_path / "Show.en.srt").exists()
    assert not srt.exists()


def test_suffixes_after_the_language_are_kept(tmp_path):
    """
    Show.und.forced.srt has to become Show.en.forced.srt and go on being
    the forced one. Rebuilding the name from the language alone loses the
    suffix, and Plex then stops treating it as forced — a subtitle that
    silently starts appearing over dialogue it should not.
    """
    srt = tmp_path / "Show.und.forced.srt"
    srt.write_text("forced subtitle")
    flag = _Flag(str(srt))

    result = _rename(flag, "eng")

    assert os.path.basename(result) == "Show.en.forced.srt"


def test_only_the_language_component_is_touched(tmp_path):
    """
    A file whose title happens to contain the language code must not have
    the wrong part rewritten. Replacing the first match turns
    und.Chronicles.und.srt into en.Chronicles.und.srt.
    """
    srt = tmp_path / "und.Chronicles.und.srt"
    srt.write_text("subtitle text")
    flag = _Flag(str(srt))

    result = _rename(flag, "eng")

    assert os.path.basename(result) == "und.Chronicles.en.srt"


def test_the_flag_is_updated_to_the_new_path(tmp_path):
    """
    Otherwise the row still names a file that no longer exists, and a
    second correction has nothing to act on.
    """
    srt = tmp_path / "Show.und.srt"
    srt.write_text("subtitle text")
    flag = _Flag(str(srt))

    _rename(flag, "eng")

    assert flag.extracted_path == str(tmp_path / "Show.en.srt")


# ── One row per track ────────────────────────────────────────────────────────

def _flags_db():
    from sqlalchemy.orm import sessionmaker

    from app.database.models import Base, MediaFile
    from tests.conftest import memory_engine

    engine = memory_engine()
    Base.metadata.create_all(engine)
    db = sessionmaker(bind=engine)()
    media = MediaFile(path="/m/Show.mkv", filename="Show.mkv", directory="/m",
                      size=1, mtime=1.0)
    db.add(media)
    db.commit()
    return db, media


def _upsert(db, media, mismatches):
    from types import SimpleNamespace

    from app.core.scanner import _upsert_language_flags

    _upsert_language_flags(db, media, SimpleNamespace(
        audio_language_mismatch=None,
        subtitle_language_mismatches=mismatches))
    db.commit()


def test_every_flagged_track_gets_its_own_row():
    """
    Reported from a real library: a file with three undefined subtitles
    showed one in review. Extraction had written three .srt files, so the
    other two kept "und" in their names with nothing offering to correct
    them.
    """
    from app.database.models import SubtitleLanguageFlag

    db, media = _flags_db()
    _upsert(db, media, [
        {"stream_index": 2, "language": "und",
         "extracted_path": "/m/Show.und.forced.srt"},
        {"stream_index": 3, "language": "und",
         "extracted_path": "/m/Show.und.dub.srt"},
        {"stream_index": 4, "language": "und",
         "extracted_path": "/m/Show.und.sdh.srt"},
    ])

    flags = db.query(SubtitleLanguageFlag).order_by(
        SubtitleLanguageFlag.stream_index).all()
    assert [f.stream_index for f in flags] == [2, 3, 4]
    assert [f.extracted_path for f in flags] == [
        "/m/Show.und.forced.srt", "/m/Show.und.dub.srt", "/m/Show.und.sdh.srt"]


def test_rows_are_matched_by_track_not_replaced_wholesale():
    """
    A rescan updates each track's own row. Matching by file alone would
    rewrite one row three times and leave two behind, or delete and
    recreate them and lose the recorded sidecar paths.
    """
    from app.database.models import SubtitleLanguageFlag

    db, media = _flags_db()
    _upsert(db, media, [
        {"stream_index": 2, "language": "und",
         "extracted_path": "/m/Show.und.forced.srt"},
        {"stream_index": 3, "language": "und",
         "extracted_path": "/m/Show.und.dub.srt"},
    ])
    first_ids = {f.stream_index: f.id
                 for f in db.query(SubtitleLanguageFlag).all()}

    _upsert(db, media, [
        {"stream_index": 2, "language": "und"},
        {"stream_index": 3, "language": "und"},
    ])

    flags = {f.stream_index: f for f in db.query(SubtitleLanguageFlag).all()}
    assert {si: f.id for si, f in flags.items()} == first_ids
    assert flags[2].extracted_path == "/m/Show.und.forced.srt"
    assert flags[3].extracted_path == "/m/Show.und.dub.srt"


def test_a_track_that_is_no_longer_flagged_loses_its_row():
    """
    Answering one track's language resolves it, and its row should go
    while the others stay. Clearing all of them would take the remaining
    questions away with the answer.
    """
    from app.database.models import SubtitleLanguageFlag

    db, media = _flags_db()
    _upsert(db, media, [
        {"stream_index": 2, "language": "und"},
        {"stream_index": 3, "language": "und"},
    ])

    _upsert(db, media, [{"stream_index": 3, "language": "und"}])

    assert [f.stream_index for f in db.query(SubtitleLanguageFlag).all()] == [3]


def test_ignoring_a_file_clears_all_of_its_rows():
    """
    Ignore is a per-file decision — "stop asking me about this one" — so
    it has to silence every track, not the first.
    """
    from app.database.models import SubtitleLanguageFlag

    db, media = _flags_db()
    _upsert(db, media, [
        {"stream_index": 2, "language": "und"},
        {"stream_index": 3, "language": "und"},
    ])

    media.subtitle_language_ignored = True
    _upsert(db, media, [
        {"stream_index": 2, "language": "und"},
        {"stream_index": 3, "language": "und"},
    ])

    assert db.query(SubtitleLanguageFlag).count() == 0


# ── Surviving the job that raises it ─────────────────────────────────────────

def test_a_row_survives_the_job_while_its_sidecar_exists(tmp_path):
    """
    The failure that made every other part of this unreachable.

    Extraction removes the track from the mux, so the re-analysis
    _finish_job runs on the OUTPUT sees no subtitle tracks and reports no
    mismatches. Every row was then deleted — the review page emptied
    itself the moment the work finished, and three files named "und" were
    left with nothing offering to correct them.

    "No such track any more" does not mean "no longer a problem" here. It
    means the tag can no longer be fixed in the file and only the filename
    is still correctable, which is when the question matters most.
    """
    from app.database.models import SubtitleLanguageFlag

    srt = tmp_path / "Show.und.forced.srt"
    srt.write_text("extracted")

    db, media = _flags_db()
    _upsert(db, media, [{"stream_index": 2, "language": "und",
                         "extracted_path": str(srt)}])

    # The post-job re-analysis: no subtitle tracks left in the file.
    _upsert(db, media, [])

    assert db.query(SubtitleLanguageFlag).count() == 1


def test_a_row_goes_once_its_sidecar_is_gone(tmp_path):
    """
    The other half. Nothing is left to correct once the file has been
    deleted, so the question should stop being asked rather than sit there
    permanently unanswerable.
    """
    from app.database.models import SubtitleLanguageFlag

    srt = tmp_path / "Show.und.forced.srt"
    srt.write_text("extracted")

    db, media = _flags_db()
    _upsert(db, media, [{"stream_index": 2, "language": "und",
                         "extracted_path": str(srt)}])
    srt.unlink()
    _upsert(db, media, [])

    assert db.query(SubtitleLanguageFlag).count() == 0


def test_an_embedded_track_that_is_resolved_still_loses_its_row(tmp_path):
    """
    A subtitle still in the mux has no sidecar, so the ordinary rule
    applies: no longer flagged means no longer asked. The exception is
    only for tracks that have left the file.
    """
    from app.database.models import SubtitleLanguageFlag

    db, media = _flags_db()
    _upsert(db, media, [{"stream_index": 2, "language": "und"}])
    _upsert(db, media, [])

    assert db.query(SubtitleLanguageFlag).count() == 0


def test_ignoring_a_file_clears_rows_even_with_sidecars(tmp_path):
    """
    Ignore has to win over the survival rule, or a file whose subtitles
    were extracted could never be silenced.
    """
    from app.database.models import SubtitleLanguageFlag

    srt = tmp_path / "Show.und.forced.srt"
    srt.write_text("extracted")

    db, media = _flags_db()
    _upsert(db, media, [{"stream_index": 2, "language": "und",
                         "extracted_path": str(srt)}])

    media.subtitle_language_ignored = True
    _upsert(db, media, [])

    assert db.query(SubtitleLanguageFlag).count() == 0


# ── Keeping the revert point in step ─────────────────────────────────────────

def _revert_point(db, file_id, created_path):
    """A revert point whose job created `created_path`."""
    import json

    from app.database.models import RevertPoint

    point = RevertPoint(
        file_id=file_id, sidecar_path="/recycle/1_1.remuxarr_revert",
        sidecar_size=1, original_path="/m/Show.mkv",
        manifest=json.dumps({
            "version": 2, "streams": [],
            "created_files": [{"path": created_path, "size": 13, "mtime": 1.0}],
        }),
    )
    db.add(point)
    db.commit()
    return point


def test_a_rename_is_followed_into_the_revert_point(tmp_path):
    """
    Reported after the rename shipped. A revert removes the subtitle files
    its job created, matching them by the path recorded at capture — so
    renaming one without telling the revert point means the match fails,
    the revert re-embeds the subtitle, and the sidecar stays. The user
    ends up with it twice, which is the exact duplication that cleanup
    exists to prevent.
    """
    import json

    from sqlalchemy.orm import sessionmaker

    from app.database.models import Base, RevertPoint
    from tests.conftest import memory_engine

    engine = memory_engine()
    Base.metadata.create_all(engine)
    db = sessionmaker(bind=engine)()

    srt = tmp_path / "Show.und.srt"
    srt.write_text("subtitle text")
    _revert_point(db, 1, str(srt))

    new_path = _rename(_Flag(str(srt)), "eng", db)
    db.commit()

    manifest = json.loads(db.query(RevertPoint).one().manifest)
    assert [e["path"] for e in manifest["created_files"]] == [new_path]


def test_the_fingerprint_survives_the_rename(tmp_path):
    """
    os.rename preserves size and mtime, so the recorded fingerprint is
    still accurate — and still protects a file the user has edited since.
    Rewriting it here would adopt whatever the file looks like now and
    quietly disarm that check.
    """
    import json

    from sqlalchemy.orm import sessionmaker

    from app.database.models import Base, RevertPoint
    from tests.conftest import memory_engine

    engine = memory_engine()
    Base.metadata.create_all(engine)
    db = sessionmaker(bind=engine)()

    srt = tmp_path / "Show.und.srt"
    srt.write_text("subtitle text")
    _revert_point(db, 1, str(srt))

    _rename(_Flag(str(srt)), "eng", db)
    db.commit()

    entry = json.loads(db.query(RevertPoint).one().manifest)["created_files"][0]
    assert entry["size"] == 13
    assert entry["mtime"] == 1.0


def test_only_the_renamed_entry_is_rewritten(tmp_path):
    """
    A job that extracts three subtitles records three created files, and
    review corrects them one at a time. Rewriting every entry to the path
    of whichever was just renamed would collapse all three onto one name —
    so a revert would remove that one and leave the other two behind, with
    the manifest claiming otherwise.
    """
    import json

    from sqlalchemy.orm import sessionmaker

    from app.database.models import Base, RevertPoint
    from tests.conftest import memory_engine

    engine = memory_engine()
    Base.metadata.create_all(engine)
    db = sessionmaker(bind=engine)()

    renaming = tmp_path / "Show.und.srt"
    renaming.write_text("subtitle text")
    other = tmp_path / "Show.ger.srt"
    other.write_text("german subtitle")

    point = RevertPoint(
        file_id=1, sidecar_path="/recycle/1_1.remuxarr_revert", sidecar_size=1,
        original_path="/m/Show.mkv",
        manifest=json.dumps({"version": 2, "streams": [], "created_files": [
            {"path": str(renaming), "size": 13, "mtime": 1.0},
            {"path": str(other), "size": 15, "mtime": 2.0},
        ]}))
    db.add(point)
    db.commit()

    new_path = _rename(_Flag(str(renaming)), "eng", db)
    db.commit()

    paths = [e["path"] for e in
             json.loads(db.query(RevertPoint).one().manifest)["created_files"]]
    assert paths == [new_path, str(other)]


def test_another_files_revert_point_is_not_touched(tmp_path):
    """
    Filtered by file_id. Without that, correcting one subtitle rewrites
    the created-file records of every revert point in the bin.
    """
    import json

    from sqlalchemy.orm import sessionmaker

    from app.database.models import Base, RevertPoint
    from tests.conftest import memory_engine

    engine = memory_engine()
    Base.metadata.create_all(engine)
    db = sessionmaker(bind=engine)()

    srt = tmp_path / "Show.und.srt"
    srt.write_text("subtitle text")
    _revert_point(db, 1, str(srt))
    other = _revert_point(db, 2, "/m/Other.und.srt")

    _rename(_Flag(str(srt), file_id=1), "eng", db)
    db.commit()

    untouched = json.loads(db.get(RevertPoint, other.id).manifest)
    assert untouched["created_files"][0]["path"] == "/m/Other.und.srt"


# ── Surviving a rescan ───────────────────────────────────────────────────────

def test_a_rescan_does_not_blank_the_recorded_path(tmp_path, monkeypatch):
    """
    The path is recorded once, when the extraction happens. Every rescan
    afterwards produces NO extract action for that track — it is no longer
    in the mux — so a plain assignment would overwrite the one record of
    where the sidecar went with None, and the review would lose the
    ability to rename it.

    This is the ordinary sequence, not an edge case: process the file,
    then let any later scan touch it.
    """
    from types import SimpleNamespace

    from sqlalchemy.orm import sessionmaker

    from app.core.scanner import _upsert_language_flags
    from app.database.models import Base, MediaFile, SubtitleLanguageFlag
    from tests.conftest import memory_engine

    engine = memory_engine()
    Base.metadata.create_all(engine)
    db = sessionmaker(bind=engine)()

    media = MediaFile(path="/m/Show.mkv", filename="Show.mkv", directory="/m",
                      size=1, mtime=1.0)
    db.add(media)
    db.commit()

    # The scan that extracted it knows the path.
    _upsert_language_flags(db, media, SimpleNamespace(
        audio_language_mismatch=None,
        subtitle_language_mismatches=[{"stream_index": 2, "language": "und",
                                       "extracted_path": "/m/Show.und.srt"}]))
    db.commit()

    # A later scan: the track is gone from the mux, so no path this time.
    _upsert_language_flags(db, media, SimpleNamespace(
        audio_language_mismatch=None,
        subtitle_language_mismatches=[{"stream_index": 2, "language": "und"}]))
    db.commit()

    flag = db.query(SubtitleLanguageFlag).one()
    assert flag.extracted_path == "/m/Show.und.srt", (
        "the rescan forgot where the subtitle was extracted to"
    )


# ── Refusing ─────────────────────────────────────────────────────────────────

def test_an_embedded_track_has_nothing_to_rename(tmp_path):
    """
    A subtitle still in the mux has no sidecar; correcting its tag is
    enough on its own, and the reprocess handles that.
    """
    assert _rename(_Flag(None), "eng") is None


def test_a_missing_file_is_not_renamed(tmp_path, caplog):
    """
    Someone deleting an extracted subtitle is an ordinary thing to do, not
    a fault. Without the existence check the rename is attempted anyway,
    fails, and is caught by the same handler — the outcome is identical
    and every deleted sidecar logs a warning about a file the user removed
    on purpose.
    """
    import logging

    with caplog.at_level(logging.WARNING):
        assert _rename(_Flag(str(tmp_path / "gone.und.srt")), "eng") is None

    assert not [r for r in caplog.records if r.levelno >= logging.WARNING], (
        "a deliberately deleted subtitle was reported as a problem"
    )


def test_a_name_without_the_language_is_left_alone(tmp_path):
    """
    If the recorded name does not carry the language it was extracted
    under, this is not the file we think it is — or the naming scheme has
    changed underneath us. Either way, guessing which component to rewrite
    would rename it wrongly.
    """
    srt = tmp_path / "Show.srt"
    srt.write_text("subtitle text")

    assert _rename(_Flag(str(srt)), "eng") is None
    assert srt.exists()


def test_an_existing_target_is_never_overwritten(tmp_path):
    """
    Something is already called Show.en.srt — quite possibly a subtitle
    the user downloaded for exactly this language. Renaming over it would
    destroy a file nobody asked to replace, to fix a filename.
    """
    srt = tmp_path / "Show.und.srt"
    srt.write_text("extracted")
    theirs = tmp_path / "Show.en.srt"
    theirs.write_text("downloaded by Bazarr")

    assert _rename(_Flag(str(srt)), "eng") is None
    assert srt.exists()
    assert theirs.read_text() == "downloaded by Bazarr"


def test_choosing_the_language_it_already_has_is_a_no_op(tmp_path):
    srt = tmp_path / "Show.und.srt"
    srt.write_text("subtitle text")

    assert _rename(_Flag(str(srt)), "und") is None
    assert srt.exists()


def test_a_failed_rename_does_not_raise(tmp_path, monkeypatch):
    """
    The language override is committed before this runs and is the part
    that matters. Raising here would report the whole correction as failed
    and leave the user unsure whether their answer was recorded.
    """
    import app.api.routes._language_review as mod

    srt = tmp_path / "Show.und.srt"
    srt.write_text("subtitle text")

    def boom(*_a, **_k):
        raise OSError("read-only file system")

    monkeypatch.setattr(mod.os, "rename", boom)

    assert _rename(_Flag(str(srt)), "eng") is None
    assert srt.exists()
