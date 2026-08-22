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
import pytest

from app.api.routes._language_review import ApplyRequest, IgnoreRequest
from app.api.routes.audio_language import AUDIO_LANGUAGE_REVIEW
from app.api.routes.subtitle_language import SUBTITLE_LANGUAGE_REVIEW
from app.database.models import AudioLanguageFlag, MediaFile, SubtitleLanguageFlag


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
    distinguishes a correct config from one with a field crossed over."""
    mf = MediaFile(path="/m/a.mkv", filename="a.mkv", directory="/m",
                   size=1, mtime=1.0)
    db.add(mf)
    db.commit()
    db.add(AudioLanguageFlag(file_id=mf.id, stream_index=1, detected_language="dut"))
    db.add(SubtitleLanguageFlag(file_id=mf.id, stream_index=2, detected_language="und"))
    db.commit()
    return mf


# ── Config sanity ────────────────────────────────────────────────────────────

def test_the_two_kinds_share_no_field():
    """Every distinguishing field must actually distinguish."""
    a, s = AUDIO_LANGUAGE_REVIEW, SUBTITLE_LANGUAGE_REVIEW
    for field in ("slug", "prefix", "tag", "flag_model", "load_overrides",
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

def test_audio_ignore_leaves_the_subtitle_flag_alone(db, flagged_file):
    from app.api.routes.audio_language import ignore_flags

    ignore_flags(IgnoreRequest(file_ids=[flagged_file.id]), db)
    db.refresh(flagged_file)

    assert db.query(AudioLanguageFlag).count() == 0
    assert db.query(SubtitleLanguageFlag).count() == 1, \
        "audio ignore deleted the SUBTITLE flag — flag_model is crossed over"
    assert flagged_file.audio_language_ignored is True
    assert flagged_file.subtitle_language_ignored is not True, \
        "audio ignore wrote the SUBTITLE ignored column"


def test_subtitle_ignore_leaves_the_audio_flag_alone(db, flagged_file):
    from app.api.routes.subtitle_language import ignore_flags

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
    assert (tmp_path / "Show.eng.dub.srt").exists()
    assert (tmp_path / "Show.und.forced.srt").exists()


def test_blank_target_language_is_rejected(db):
    from fastapi import HTTPException

    from app.api.routes.subtitle_language import apply_language

    with pytest.raises(HTTPException) as exc:
        apply_language(ApplyRequest(flag_ids=[1], target_language="   "), db)
    assert exc.value.status_code == 400
