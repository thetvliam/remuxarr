"""
Extracting a subtitle onto a name that already holds a file.

A kept text subtitle is extracted to Movie.en.srt beside the video. That
name was chosen without looking at the folder, so a Movie.en.srt already
there, one Bazarr downloaded or one the user had retimed, was overwritten.
The revert point never held a copy, so nothing could bring it back.
Reproduced with real FFmpeg before this file existed: the Bazarr file's
text was replaced by the embedded track's.

existing_srt_handling now decides. Under "keep_both", the default, the
decision treats what is already in the folder as taken and numbers past
it, the way it already numbered a second English track (Movie.en.2.srt).
Under "replace" the name is used anyway, as before; the setting's
description says a revert cannot bring the replaced file back.

The folder is listed by scanner._file_info_for, the one place every
decision's file input is built, and is read at each decision rather than
stored: Bazarr can add a file at any time, and the decision a job runs is
the one made when it is picked up.

Verified by mutation, 6 applied, 6 killed. The baseline was the suite as
it stood (1950 tests) plus one mechanical change it needed:
test_job_preflight.py pins the exact file input the worker hands the
decision, and gained the folder listing. Four survived that baseline and
are killed here now:

  • keep_both ignores the listing            → the decision tests, the
                                               real job
  • replace numbers past anyway              → replace keeps the name
  • names compared with their case           → differing only by case
  • the decision's own fallback changed
    to replace                               → without the key

Two were already killed by the baseline, so no test here claims them:
_file_info_for passing no listing (the adjusted preflight test; the real
job below kills it too), and the stored default changed to replace
(test_base_settings_match_production_defaults, through the BASE_SETTINGS
entry this setting added).

One proposed mutant was not run. Counting every file in the folder rather
than only this file's subtitles is equivalent: the check is equality with
the one name being built, which another file's name never matches. So the
listing is the whole folder, unfiltered, and there is no filter to mutate.
"""
import asyncio
import hashlib
import json
import os
import shutil
import subprocess
from types import SimpleNamespace

import pytest

from app.core.decision import analyze_file
from tests.conftest import BASE_SETTINGS


def _sub(si, language="eng", codec="subrip"):
    return dict(stream_index=si, track_type="subtitle", codec=codec,
                language=language, channels=None, channel_layout="",
                is_default=False, is_forced=False, is_hearing_impaired=False,
                is_dub=False, title=None)


TRACKS = [
    dict(stream_index=0, track_type="video", codec="h264", language="und",
         channels=None, channel_layout="", is_default=True, is_forced=False,
         is_hearing_impaired=False, is_dub=False, title=None),
    dict(stream_index=1, track_type="audio", codec="aac", language="eng",
         channels=2, channel_layout="stereo", is_default=True, is_forced=False,
         is_hearing_impaired=False, is_dub=False, title=None),
    _sub(2),
]


def _sidecars(folder_entries, settings=BASE_SETTINGS, tracks=TRACKS):
    decision = analyze_file(
        {"path": "/m/Movie.mkv", "container": "mkv",
         "folder_entries": folder_entries},
        tracks, settings)
    return [os.path.basename(a.external_path) for a in decision.actions
            if a.action_type == "extract_subtitle"]


# ── The decision ─────────────────────────────────────────────────────────────

def test_an_existing_subtitle_is_numbered_past():
    assert _sidecars(["/m/Movie.mkv", "/m/Movie.en.srt"]) == ["Movie.en.2.srt"]


def test_two_existing_subtitles_are_both_numbered_past():
    assert _sidecars(["/m/Movie.en.srt", "/m/Movie.en.2.srt"]) == ["Movie.en.3.srt"]


def test_other_files_in_the_folder_leave_the_name_free():
    """Another video's subtitle, and this one's forced subtitle, are not
    the name being built."""
    assert _sidecars(["/m/Other.en.srt", "/m/Movie.en.forced.srt"]) == ["Movie.en.srt"]


def test_a_name_differing_only_by_case_is_taken():
    """On a share that ignores case, writing Movie.en.srt replaces it."""
    assert _sidecars(["/m/Movie.EN.srt"]) == ["Movie.en.2.srt"]


def test_a_language_correction_does_not_rename_onto_an_existing_file():
    """
    An undefined subtitle corrected to English is renamed from Movie.und.srt
    to Movie.en.srt by a second path through the decision. It must number
    past the existing file too.
    """
    settings = BASE_SETTINGS | {"fix_undefined_language": "always_fix",
                                "undefined_language_value": "eng"}
    tracks = TRACKS[:2] + [_sub(2, language="und")]

    assert _sidecars(["/m/Movie.en.srt"], settings, tracks) == ["Movie.en.2.srt"]


def test_replace_keeps_the_name():
    settings = BASE_SETTINGS | {"existing_srt_handling": "replace"}

    assert _sidecars(["/m/Movie.en.srt"], settings) == ["Movie.en.srt"]


def test_without_the_key_both_are_kept():
    """
    Production always supplies the key: get_app_settings fills every
    missing one from the defaults. This pins the decision's own fallback
    for callers that build settings by hand.
    """
    settings = {k: v for k, v in BASE_SETTINGS.items()
                if k != "existing_srt_handling"}

    assert _sidecars(["/m/Movie.en.srt"], settings) == ["Movie.en.2.srt"]


# ── Where the listing comes from ─────────────────────────────────────────────

def test_the_decision_input_lists_the_folder(tmp_path):
    from app.core.scanner import _file_info_for

    (tmp_path / "Movie.mkv").write_bytes(b"x")
    (tmp_path / "Movie.en.srt").write_text("bazarr")
    media = SimpleNamespace(path=str(tmp_path / "Movie.mkv"), container="mkv",
                            video_codec="h264", font_attachments=0,
                            und_audio_threshold_acknowledged=False)

    assert sorted(_file_info_for(media)["folder_entries"]) == [
        str(tmp_path / "Movie.en.srt"), str(tmp_path / "Movie.mkv")]


def test_a_folder_that_cannot_be_listed_lists_nothing(tmp_path):
    from app.core.scanner import _folder_entries

    assert _folder_entries(str(tmp_path / "gone" / "Movie.mkv")) == []


# ── A real job ───────────────────────────────────────────────────────────────

needs_ffmpeg = pytest.mark.skipif(
    shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None,
    reason="needs real ffmpeg and ffprobe",
)


class _WS:
    async def broadcast_json(self, payload):
        pass


@pytest.fixture
def lib(tmp_path, monkeypatch):
    """
    A file-backed database every session in the job's path points at,
    scratch temp and recycle directories, and a library holding Movie.mkv
    with an English SubRip track and a Movie.en.srt Bazarr put there.
    """
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    import app.core.worker as worker
    import app.database.session as session_module
    from app.config import settings as app_settings
    from app.database.models import AppSetting, Base

    engine = create_engine(f"sqlite:///{tmp_path / 'remuxarr.db'}",
                           connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine)
    monkeypatch.setattr(worker, "SessionLocal", factory)
    monkeypatch.setattr(session_module, "SessionLocal", factory)
    monkeypatch.setattr(app_settings, "TEMP_DIR", str(tmp_path / "tmp"))
    monkeypatch.setattr(app_settings, "RECYCLE_DIR", str(tmp_path / "recycle"))
    (tmp_path / "recycle").mkdir()
    library = tmp_path / "library"
    library.mkdir()
    with factory() as db:
        db.merge(AppSetting(key="dry_run_mode", value=json.dumps(False)))
        db.merge(AppSetting(key="revert_enabled", value=json.dumps(True)))
        db.commit()

    video = library / "Movie.mkv"
    embedded = tmp_path / "embedded.srt"
    embedded.write_text("1\n00:00:00,000 --> 00:00:00,900\nFROM THE RELEASE\n")
    subprocess.run(
        ["ffmpeg", "-v", "error", "-y",
         "-f", "lavfi", "-i", "testsrc=size=160x120:rate=10:duration=1",
         "-f", "lavfi", "-i", "sine=frequency=440:duration=1", "-i", str(embedded),
         "-map", "0:v", "-map", "1:a", "-map", "2:s",
         "-metadata:s:a:0", "language=eng", "-metadata:s:s:0", "language=eng",
         "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p",
         "-c:a", "aac", "-c:s", "srt", str(video)], check=True)
    bazarr = library / "Movie.en.srt"
    bazarr.write_text("1\n00:00:00,000 --> 00:00:00,900\nFROM BAZARR\n")

    yield SimpleNamespace(Session=factory, worker=worker, library=library,
                          video=video, bazarr=bazarr,
                          bazarr_sha=hashlib.sha256(bazarr.read_bytes()).hexdigest())
    engine.dispose()


def _run_one(lib):
    from app.core import scanner

    with lib.Session() as db:
        item = scanner.queue_single_file(db, str(lib.video))
        assert item is not None and item.status == "pending", "nothing was queued"

    async def go():
        job_id = lib.worker._claim_next()
        await lib.worker._run_and_broadcast(job_id, _WS(), asyncio.get_running_loop())
    asyncio.run(go())


@needs_ffmpeg
def test_a_real_job_leaves_the_existing_subtitle_alone(lib):
    from app.core.revert_restore import restore_revert_point
    from app.database.models import RevertPoint

    _run_one(lib)

    assert hashlib.sha256(lib.bazarr.read_bytes()).hexdigest() == lib.bazarr_sha, (
        "the subtitle that was already there was overwritten")
    extracted = lib.library / "Movie.en.2.srt"
    assert "FROM THE RELEASE" in extracted.read_text()

    with lib.Session() as db:
        point_id = db.query(RevertPoint).one().id
    outcome = asyncio.run(restore_revert_point(point_id))

    assert outcome.success, outcome.error
    assert not extracted.exists(), "the job's own subtitle file was left behind"
    assert hashlib.sha256(lib.bazarr.read_bytes()).hexdigest() == lib.bazarr_sha
