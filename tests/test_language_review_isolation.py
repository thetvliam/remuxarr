"""
Isolation between the two language-review routers.

The audio and subtitle reviews now share one implementation, parameterised by
LanguageReviewKind. That removes a real maintenance hazard — the two copies
were 100 logic lines each differing by one log message, and their comments
record the same two bugs being fixed twice — but it introduces a new one:
a single wrong field in the config would make one review silently operate on
the other's table or column.

Nothing else would catch that. The existing per-kind tests each exercise one
router in isolation, so a config that pointed both at the same table would
still pass them individually. These tests specifically assert the two do not
touch each other.
"""
import pathlib

import pytest

from app.api.routes._language_review import ApplyRequest, IgnoreRequest
from app.api.routes.audio_language import AUDIO_LANGUAGE_REVIEW
from app.api.routes.subtitle_language import SUBTITLE_LANGUAGE_REVIEW
from app.database.models import AudioLanguageFlag, MediaFile, SubtitleLanguageFlag, Track


@pytest.fixture
def db():
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    from app.database.models import Base

    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)()


@pytest.fixture
def flagged_file(db):
    """One file carrying BOTH an audio and a subtitle flag — the case that
    distinguishes a correct config from one with a field crossed over.

    With the tracks those flags describe, since an answer is stored against
    what a track is.
    """
    mf = MediaFile(path="/m/a.mkv", filename="a.mkv", directory="/m",
                   size=1, mtime=1.0)
    db.add(mf)
    db.commit()
    db.add(Track(file_id=mf.id, stream_index=1, track_type="audio",
                 codec="aac", language="dut"))
    db.add(Track(file_id=mf.id, stream_index=2, track_type="subtitle",
                 codec="subrip", language="und"))
    db.add(AudioLanguageFlag(file_id=mf.id, stream_index=1, detected_language="dut"))
    db.add(SubtitleLanguageFlag(file_id=mf.id, stream_index=2, detected_language="und"))
    db.commit()
    return mf


def _answers(db, media, attr):
    """
    The stored answers of one column, resolved against the file's tracks.

    They are keyed by a track descriptor, so a test reads them the way the
    decision engine does rather than by matching the key format.
    """
    from app.core.scanner import (_load_track_answers, _track_to_dict,
                                  resolve_track_answers)

    tracks = [_track_to_dict(t) for t in
              db.query(Track).filter(Track.file_id == media.id).all()]
    return resolve_track_answers(_load_track_answers(media, attr), tracks)


# ── Config sanity ────────────────────────────────────────────────────────────

def test_the_two_kinds_share_no_field():
    """Every distinguishing field must actually distinguish."""
    a, s = AUDIO_LANGUAGE_REVIEW, SUBTITLE_LANGUAGE_REVIEW
    for field in ("slug", "prefix", "tag", "flag_model",
                  "overrides_attr", "ignored_attr"):
        assert getattr(a, field) != getattr(s, field), (
            f"LanguageReviewKind.{field} is identical for both reviews — "
            "one of them is configured to act on the other's data"
        )


def test_configured_columns_exist_on_mediafile():
    """A typo in overrides_attr/ignored_attr would silently create a new
    attribute on the instance instead of writing the column."""
    for kind in (AUDIO_LANGUAGE_REVIEW, SUBTITLE_LANGUAGE_REVIEW):
        assert hasattr(MediaFile, kind.overrides_attr), kind.overrides_attr
        assert hasattr(MediaFile, kind.ignored_attr), kind.ignored_attr


# ── Behavioural isolation ────────────────────────────────────────────────────

def _real_run(monkeypatch):
    """
    Declare the mode. ignore_flags reads dry_run_mode and DEFAULT_APP_SETTINGS
    ships it True, so a bare session takes the dry-run path and marks nothing.
    These tests are about which table a real ignore touches, not about the
    mode, so they have to state the one they mean.
    """
    import app.api.routes._language_review as lr

    monkeypatch.setattr(lr, "get_app_settings",
                        lambda _db: {"dry_run_mode": False})


def test_audio_ignore_leaves_the_subtitle_flag_alone(db, flagged_file, monkeypatch):
    from app.api.routes.audio_language import ignore_flags

    _real_run(monkeypatch)
    ignore_flags(IgnoreRequest(file_ids=[flagged_file.id]), db)
    db.refresh(flagged_file)

    assert db.query(AudioLanguageFlag).count() == 0
    assert db.query(SubtitleLanguageFlag).count() == 1, \
        "audio ignore deleted the SUBTITLE flag — flag_model is crossed over"
    assert flagged_file.audio_language_ignored is True
    assert flagged_file.subtitle_language_ignored is not True, \
        "audio ignore wrote the SUBTITLE ignored column"


def test_subtitle_ignore_leaves_the_audio_flag_alone(db, flagged_file, monkeypatch):
    from app.api.routes.subtitle_language import ignore_flags

    _real_run(monkeypatch)
    ignore_flags(IgnoreRequest(file_ids=[flagged_file.id]), db)
    db.refresh(flagged_file)

    assert db.query(SubtitleLanguageFlag).count() == 0
    assert db.query(AudioLanguageFlag).count() == 1, \
        "subtitle ignore deleted the AUDIO flag — flag_model is crossed over"
    assert flagged_file.subtitle_language_ignored is True
    assert flagged_file.audio_language_ignored is not True


def test_each_list_endpoint_sees_only_its_own_flags(db, flagged_file):
    from app.api.routes.audio_language import list_flags as audio_list
    from app.api.routes.subtitle_language import list_flags as sub_list

    a = audio_list(search="", language="", limit=50, offset=0, db=db)
    s = sub_list(search="", language="", limit=50, offset=0, db=db)

    assert a["total"] == 1 and s["total"] == 1
    # The two flags were seeded with different stream indices and languages,
    # so a crossed-over flag_model shows up here as swapped values.
    assert a["items"][0]["stream_index"] == 1
    assert a["items"][0]["detected_language"] == "dut"
    assert s["items"][0]["stream_index"] == 2
    assert s["items"][0]["detected_language"] == "und"


def test_apply_writes_only_its_own_override_column(db, flagged_file, monkeypatch):
    """
    The apply path commits the override before doing anything else, so this
    asserts the column write in isolation from the reprocess.
    """
    import app.api.routes._language_review as lr

    monkeypatch.setattr(lr, "get_app_settings", lambda _db: {"dry_run_mode": False})
    monkeypatch.setattr(lr.os.path, "exists", lambda _p: True)
    monkeypatch.setattr(lr, "_process_file", lambda *a, **k: None)

    from app.api.routes.audio_language import apply_language
    from app.database.models import AudioLanguageFlag

    # Apply targets a FLAG now, not a file: a file can have several
    # undefined subtitle tracks and each needs its own answer, so a file
    # id can no longer say which one is meant. Audio has one flag per
    # file, so this is the same row either way.
    flag = (db.query(AudioLanguageFlag)
              .filter(AudioLanguageFlag.file_id == flagged_file.id).one())
    apply_language(ApplyRequest(flag_ids=[flag.id], target_language="eng"), db)
    db.refresh(flagged_file)

    assert flagged_file.audio_language_overrides, "audio override not written"
    assert not flagged_file.subtitle_language_overrides, \
        "audio apply wrote the SUBTITLE override column"


def test_applying_consumes_only_the_flag_it_answered(db, monkeypatch, tmp_path):
    """
    A file with three undefined subtitles raises three rows, and answering
    one must leave the other two asking. The row also has to GO once
    answered — rows whose sidecar still exists are deliberately kept
    across a rescan now, so a row left in place would survive every later
    scan and keep asking a question the user has already settled.
    """
    import app.api.routes._language_review as lr
    from app.api.routes.subtitle_language import apply_language
    from app.database.models import MediaFile, SubtitleLanguageFlag

    monkeypatch.setattr(lr, "get_app_settings", lambda _db: {"dry_run_mode": False})
    monkeypatch.setattr(lr, "_process_file", lambda *a, **k: None)

    media_file = tmp_path / "Show.mkv"
    media_file.write_bytes(b"video")
    media = MediaFile(path=str(media_file), filename="Show.mkv",
                      directory=str(tmp_path), size=5, mtime=1.0)
    db.add(media)
    db.commit()

    for stream_index, suffix in ((2, "forced"), (3, "dub"), (4, "sdh")):
        srt = tmp_path / f"Show.und.{suffix}.srt"
        srt.write_text("subtitle")
        db.add(Track(file_id=media.id, stream_index=stream_index,
                     track_type="subtitle", codec="subrip", language="und",
                     title=suffix))
        db.add(SubtitleLanguageFlag(
            file_id=media.id, stream_index=stream_index,
            detected_language="und", extracted_path=str(srt)))
    db.commit()

    answered = (db.query(SubtitleLanguageFlag)
                  .filter(SubtitleLanguageFlag.stream_index == 3).one())
    apply_language(ApplyRequest(flag_ids=[answered.id], target_language="eng"), db)

    remaining = sorted(f.stream_index for f in
                       db.query(SubtitleLanguageFlag).all())
    assert remaining == [2, 4], (
        "answering one track either took the others' questions with it or "
        "left its own behind"
    )
    assert (tmp_path / "Show.en.dub.srt").exists()
    assert (tmp_path / "Show.und.forced.srt").exists()


def test_blank_target_language_is_rejected(db):
    from fastapi import HTTPException

    from app.api.routes.subtitle_language import apply_language

    with pytest.raises(HTTPException) as exc:
        apply_language(ApplyRequest(flag_ids=[1], target_language="   "), db)
    assert exc.value.status_code == 400


# ── Flags vs files ───────────────────────────────────────────────────────────
#
# Both endpoints take a list of flags, but nearly everything they do is
# per-FILE: one override blob, one queue-item cleanup, one reprocess, one
# ignore column. Three findings in a row came out of that mismatch, so the
# unit is pinned on both sides here.

def test_ignoring_a_file_clears_every_one_of_its_flags(db, tmp_path, monkeypatch):
    """
    SubtitleLanguageFlag is UNIQUE(file_id, stream_index), so a file with
    three undefined subtitles has three rows. Clearing one left the file on
    the review page it had just been ignored from — and applying a language
    to a survivor sets ignored back to False, silently undoing the ignore.

    The shared router was written against the audio table, which is one row
    per file and where taking the first row happened to be complete. This is
    the audio/subtitle divergence the merge was meant to end, surviving
    inside the merged code.
    """
    from app.api.routes._language_review import IgnoreRequest
    from app.api.routes.subtitle_language import ignore_flags
    from app.database.models import MediaFile, SubtitleLanguageFlag

    _real_run(monkeypatch)
    media = MediaFile(path=str(tmp_path / "S.mkv"), filename="S.mkv",
                      directory=str(tmp_path), size=1, mtime=1.0)
    db.add(media)
    db.commit()
    for stream_index in (2, 3, 4):
        db.add(SubtitleLanguageFlag(file_id=media.id,
                                    stream_index=stream_index,
                                    detected_language="und"))
    db.commit()

    result = ignore_flags(IgnoreRequest(file_ids=[media.id]), db)

    assert db.query(SubtitleLanguageFlag).count() == 0, (
        "the file is still asking about tracks the user just ignored"
    )
    # Files, not rows: the endpoint is given file_ids and the UI compares
    # this number against how many it sent.
    assert result == {"ignored": 1, "dry_run": False}


def test_applying_to_several_flags_of_one_file_reprocesses_it_once(db,
                                                                   monkeypatch,
                                                                   tmp_path):
    """
    Per-flag iteration did per-file work per flag: each pass deleted the
    pending QueueItem the previous pass had just created, then re-ran
    _process_file(force_probe=True). Three ffprobes and three queue churns
    for one file's correction — and on a spun-down array a probe is ~400ms.

    The count matters as much as the work. "applied" feeds a toast that
    says "on N files", so counting flags reported three files where there
    was one.
    """
    import app.api.routes._language_review as lr
    from app.api.routes.subtitle_language import apply_language
    from app.database.models import MediaFile, SubtitleLanguageFlag, Track

    media_file = tmp_path / "Show.mkv"
    media_file.write_bytes(b"video")
    media = MediaFile(path=str(media_file), filename="Show.mkv",
                      directory=str(tmp_path), size=5, mtime=1.0)
    db.add(media)
    db.commit()

    flag_ids = []
    for stream_index in (2, 3, 4):
        db.add(Track(file_id=media.id, stream_index=stream_index,
                     track_type="subtitle", codec="subrip", language="und"))
        flag = SubtitleLanguageFlag(file_id=media.id,
                                    stream_index=stream_index,
                                    detected_language="und")
        db.add(flag)
        db.commit()
        flag_ids.append(flag.id)

    calls = []
    monkeypatch.setattr(lr, "get_app_settings", lambda _db: {"dry_run_mode": False})
    monkeypatch.setattr(lr, "_process_file", lambda *a, **k: calls.append(a[1]))

    result = apply_language(
        ApplyRequest(flag_ids=flag_ids, target_language="eng"), db)

    assert len(calls) == 1, f"reprocessed {len(calls)} times for one file"
    assert result["applied"] == 1, "applied counts files; the toast says files"

    # All three answers still landed — reprocessing once must not mean
    # applying once.
    db.refresh(media)
    assert _answers(db, media, "subtitle_language_overrides") == {
        2: "eng", 3: "eng", 4: "eng",
    }


def test_the_manual_rename_uses_the_same_code_as_the_automatic_path(tmp_path):
    """
    Two paths can set an extracted subtitle's language, and they have to
    produce the same filename.

    _build_srt_path maps the 3-letter ffprobe code to the 2-letter ISO 639-1
    code, because that is what Plex reads. The manual path substituted the
    user's typed code raw, so the same track was Show.en.srt when fixed
    automatically and Show.eng.srt when fixed through review — in the
    function that exists solely to make Plex read the correction.

    It also broke the collision guard: os.path.exists checked a name the
    automatic path can never produce, so a library with Show.en.srt already
    in it gained a second Show.eng.srt rather than refusing.
    """
    import types

    from app.api.routes._language_review import _rename_extracted_subtitle
    from app.core.decision import _build_srt_path

    media = str(tmp_path / "Show S01E01.mkv")
    automatic = _build_srt_path(media, "eng", True, False, False, set())

    extracted = _build_srt_path(media, "und", True, False, False, set())
    pathlib.Path(extracted).write_text("subtitle")

    flag = types.SimpleNamespace(detected_language="und",
                                 extracted_path=extracted, file_id=1)
    manual = _rename_extracted_subtitle(flag, "eng")

    assert manual == automatic, (
        "the manual and automatic fixes name the same track differently"
    )


# ── What the apply endpoint reports ─────────────────────────────────────────
#
# Mutation, 3 applied 3 killed: the stats.errors check inverted, the error
# recorded but applied still incremented, and stats.queued read in place of
# stats.errors. These pin the new check rather than showing prior exposure —
# there was no branch to mutate before it. The baseline is that both tests
# below failed against the unchanged endpoint for the reason each states,
# while the rest of the suite was green.
#
# The inverted mutant is also caught by
# test_applying_to_several_flags_of_one_file_reprocesses_it_once, which
# already pinned the real-run count.


def _flagged_on_disk(db, tmp_path, name="Show.mkv"):
    """
    A flagged file that really exists, so os.stat inside _process_file passes.

    With the track row the flag describes: answers are stored against what a
    track is, so a flag naming a stream the file's tracks do not have is
    reported rather than answered.
    """
    from app.database.models import MediaFile, SubtitleLanguageFlag, Track

    path = tmp_path / name
    path.write_bytes(b"video")
    media = MediaFile(path=str(path), filename=name, directory=str(tmp_path),
                      size=5, mtime=1.0)
    db.add(media)
    db.commit()
    db.add(Track(file_id=media.id, stream_index=2, track_type="subtitle",
                 codec="subrip", language="und"))
    flag = SubtitleLanguageFlag(file_id=media.id, stream_index=2,
                                detected_language="und")
    db.add(flag)
    db.commit()
    return media, flag


def test_a_file_that_cannot_be_probed_is_not_counted_as_applied(
        db, tmp_path, monkeypatch):
    """
    "applied" feeds a toast that says "on N files", so it has to mean files
    that were actually re-evaluated.

    _process_file handles ProbeError itself — it logs, records the error in
    the ScanStats it was handed, and returns — so nothing propagates for the
    endpoint's except block to catch. The count was incremented regardless,
    and the ScanStats carrying the one signal that would have prevented it
    was constructed, passed in, and never read. The result was a clean
    {'applied': 1, 'errors': []} for a file where no queue item was created
    and nothing happened at all.

    Patches probe_file rather than _process_file, so the real handler runs
    and the test cannot drift from what that handler actually records.
    """
    import app.api.routes._language_review as lr
    import app.core.scanner as scanner
    from app.core.probe import ProbeError
    from app.api.routes.subtitle_language import apply_language
    from app.database.models import QueueItem

    _media, flag = _flagged_on_disk(db, tmp_path)

    monkeypatch.setattr(lr, "get_app_settings",
                        lambda _db: {"dry_run_mode": False})
    monkeypatch.setattr(scanner, "probe_file",
                        lambda *a, **k: (_ for _ in ()).throw(ProbeError("no streams")))

    result = apply_language(
        ApplyRequest(flag_ids=[flag.id], target_language="eng"), db)

    assert result["applied"] == 0, (
        "counted a file that was never re-evaluated as applied"
    )
    assert len(result["errors"]) == 1
    assert result["errors"][0]["file_id"] == flag.file_id
    assert db.query(QueueItem).count() == 0


def test_only_the_files_that_were_re_evaluated_are_counted(
        db, tmp_path, monkeypatch):
    """
    The mixed selection, which is the shape the feature is built for: search
    a show name and answer every episode at once. One bad file among fifty
    must not make the other forty-nine uncountable, and must not be counted
    itself.
    """
    import app.api.routes._language_review as lr
    from app.api.routes.subtitle_language import apply_language

    _good_media, good = _flagged_on_disk(db, tmp_path, "Good.mkv")
    bad_media, bad = _flagged_on_disk(db, tmp_path, "Bad.mkv")

    def fake_process(_db, path, _cfg, **kwargs):
        # Mirrors _process_file's own ProbeError branch: record and return.
        if path == bad_media.path:
            kwargs["stats"].errors += 1
            return
        kwargs["stats"].queued += 1

    monkeypatch.setattr(lr, "get_app_settings",
                        lambda _db: {"dry_run_mode": False})
    monkeypatch.setattr(lr, "_process_file", fake_process)

    result = apply_language(
        ApplyRequest(flag_ids=[good.id, bad.id], target_language="eng"), db)

    assert result["applied"] == 1
    assert [e["file_id"] for e in result["errors"]] == [bad_media.id]


# ── Ignore under Dry Run Mode ───────────────────────────────────────────────
#
# Mutation, 3 applied 3 killed: the gate removed, the gate inverted, and a
# real run reporting dry_run True. These pin the new gate rather than showing
# prior exposure — there was no gate to mutate before.
#
# The inverted mutant fails nine tests rather than one, which is the point
# worth recording: every ignore test in the suite was running against a mode
# it never declared, and DEFAULT_APP_SETTINGS ships dry_run_mode True, so a
# bare session now takes the dry-run path. They each say which mode they mean
# now, via _real_run above.


def _ignore_under(db, file_ids, dry_run, monkeypatch, kind="subtitle"):
    import app.api.routes._language_review as lr
    from app.api.routes._language_review import IgnoreRequest

    if kind == "subtitle":
        from app.api.routes.subtitle_language import ignore_flags
    else:
        from app.api.routes.audio_language import ignore_flags

    monkeypatch.setattr(lr, "get_app_settings",
                        lambda _db: {"dry_run_mode": dry_run})
    return ignore_flags(IgnoreRequest(file_ids=file_ids), db)


def test_dry_run_does_not_ignore_anything(db, tmp_path, monkeypatch):
    """
    Ignoring writes no files, so Dry Run Mode's "do NOT modify any files" does
    not obviously reach it. It reaches it because an ignore cannot be undone:
    the column is written True here and False in exactly one other place,
    inside apply_language, and ignoring deletes every flag row for the file
    while the scanner refuses to create new ones for an ignored file. There is
    no endpoint, no setting and no control that clears it, and the one route
    back is closed by the same action that creates the state.

    A one-way door has no business being reachable in the mode that ships on
    and promises nothing will change.
    """
    from app.database.models import MediaFile, SubtitleLanguageFlag

    media = MediaFile(path="/m/a.mkv", filename="a.mkv", directory="/m",
                      size=1, mtime=1.0)
    db.add(media)
    db.commit()
    db.add(SubtitleLanguageFlag(file_id=media.id, stream_index=2,
                                detected_language="und"))
    db.commit()

    result = _ignore_under(db, [media.id], dry_run=True, monkeypatch=monkeypatch)

    db.refresh(media)
    assert media.subtitle_language_ignored is not True, (
        "a dry run marked a file ignored, which nothing can undo"
    )
    assert db.query(SubtitleLanguageFlag).count() == 1
    assert result == {"ignored": 0, "dry_run": True}


def test_a_real_ignore_still_marks_and_clears(db, tmp_path, monkeypatch):
    """
    The positive control. Without it the test above would pass against an
    endpoint that had stopped ignoring in every mode.
    """
    from app.database.models import MediaFile, SubtitleLanguageFlag

    media = MediaFile(path="/m/b.mkv", filename="b.mkv", directory="/m",
                      size=1, mtime=1.0)
    db.add(media)
    db.commit()
    db.add(SubtitleLanguageFlag(file_id=media.id, stream_index=2,
                                detected_language="und"))
    db.commit()

    result = _ignore_under(db, [media.id], dry_run=False, monkeypatch=monkeypatch)

    db.refresh(media)
    assert media.subtitle_language_ignored is True
    assert db.query(SubtitleLanguageFlag).count() == 0
    assert result == {"ignored": 1, "dry_run": False}


# ── The language code the endpoint accepts ──────────────────────────────────
#
# Mutation, 5 applied 5 killed: the check removed, fullmatch loosened to
# search, the upper bound widened, the lower bound dropped, and digits
# admitted. Each of the last three dies to exactly one parametrized case,
# which is what the case list is for — a single "bad input" example would
# have left three of them alive.
#
# These call apply_language directly, unlike the pagination-bound tests in
# test_subtitle_language_review.py which need TestClient. The difference is
# where the check lives: a Query() constraint is applied by FastAPI while it
# parses a request and a direct call skips it, but this validation is in the
# function body and runs either way.


def _apply_lang(db, flag_id, value, monkeypatch):
    import app.api.routes._language_review as lr
    from app.api.routes._language_review import ApplyRequest
    from app.api.routes.subtitle_language import apply_language

    monkeypatch.setattr(lr, "get_app_settings",
                        lambda _db: {"dry_run_mode": False})
    monkeypatch.setattr(lr, "_process_file", lambda *a, **k: None)
    return apply_language(
        ApplyRequest(flag_ids=[flag_id], target_language=value), db)


def _one_flag(db, tmp_path):
    """
    A real file on disk. apply_language checks for it and reports "File no
    longer exists on disk" otherwise, so a made-up path takes the error branch
    and never reaches the code under test. The flagged track is stored too,
    for the same reason: a flag naming a stream the file's tracks do not have
    is reported rather than answered.
    """
    from app.database.models import MediaFile, SubtitleLanguageFlag, Track

    path = tmp_path / "v.mkv"
    path.write_bytes(b"video")
    media = MediaFile(path=str(path), filename="v.mkv", directory=str(tmp_path),
                      size=5, mtime=1.0)
    db.add(media)
    db.commit()
    db.add(Track(file_id=media.id, stream_index=2, track_type="subtitle",
                 codec="subrip", language="und"))
    flag = SubtitleLanguageFlag(file_id=media.id, stream_index=2,
                                detected_language="und")
    db.add(flag)
    db.commit()
    return media, flag


@pytest.mark.parametrize("value", [
    "english",          # the spelt-out name, the likeliest typo of the lot
    "en-GB",            # a locale rather than a language
    "e",                # too short to be either standard
    "../../etc/passwd", # not reachable as a traversal, but no business here
    "eng eng",
    "3ng",
])
def test_a_language_code_that_is_not_one_is_refused(db, value, tmp_path, monkeypatch):
    """
    The field behind this is a free-text box with placeholder "eng" and no
    pattern, so anything typed into it was accepted, persisted as an override,
    written into the extracted subtitle's filename and handed to FFmpeg as
    -metadata:s:a:N language=... . That filename is what Plex reads.

    Nothing here was exploitable — the value goes in as an argv element, not
    through a shell, and a path separator makes os.rename fail rather than
    escape. It is the ordinary typo that does the damage, quietly.
    """
    from fastapi import HTTPException

    _media, flag = _one_flag(db, tmp_path)

    with pytest.raises(HTTPException) as caught:
        _apply_lang(db, flag.id, value, monkeypatch)

    assert caught.value.status_code == 400


def test_an_empty_language_is_still_refused(db, tmp_path, monkeypatch):
    """Unchanged, and kept so the new check cannot swallow the old one."""
    from fastapi import HTTPException

    _media, flag = _one_flag(db, tmp_path)

    with pytest.raises(HTTPException) as caught:
        _apply_lang(db, flag.id, "   ", monkeypatch)

    assert caught.value.status_code == 400


@pytest.mark.parametrize("value,stored", [
    ("eng", "eng"),     # ISO 639-2, what the scanner writes
    ("en", "en"),       # ISO 639-1, equally valid
    ("  ENG ", "eng"),  # still stripped and lowercased
    ("cym", "cym"),     # not in ISO_639_2_TO_1, which is a 49-entry
                        # convenience map rather than the standard — a
                        # whitelist would have refused Welsh
])
def test_a_real_language_code_is_accepted(db, value, stored, tmp_path, monkeypatch):
    media, flag = _one_flag(db, tmp_path)

    result = _apply_lang(db, flag.id, value, monkeypatch)

    assert result["applied"] == 1
    db.refresh(media)
    assert _answers(db, media, "subtitle_language_overrides") == {2: stored}
