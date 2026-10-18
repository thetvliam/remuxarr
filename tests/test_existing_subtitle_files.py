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

Embedded first came next: the decision keeps the name and the worker moves
the file holding it to the next free number just before the swap, on both
execution paths. Verified by mutation, 12 applied, 11 killed, 1 equivalent.
The baseline was the suite after the setting's first two options, plus one
mechanical change: test_subtitle_extraction_routing.py's fake extraction
names its keywords exactly and gained before_swap. All 12 survived it.
Killed here unless the routing module is named:

  • no move at all                           → takes the name, and both
                                               routing tests
  • the job's own targets not avoided        → does not take a name the
                                               job writes
  • the two-pass extraction given no hook,
    and execute_subtitle_extraction not
    passing it on (two mutants)              → both routing tests
  • the freed name not recorded as created   → takes the name (its revert),
                                               routing cancel test
  • the remux moving this job's own two-pass
    output                                   → routing, this path too
  • a cancel not putting the old file back   → routing cancel test
  • a numbered target not numbered from its
    base                                     → a name the job is writing
  • a failed move not put back               → puts back what it moved
  • a name holding nothing not skipped       → holding nothing
  • the disk ignored when numbering          → a name the job is writing

Equivalent: making room before the mid-job change check rather than after.
A cancel puts back whatever was moved, so the order cannot be observed. The
avoided-targets mutant first survived a scenario whose targets came in
ascending order, where moving the old file on again when the next target
finds it repairs the first move; the test now uses the order a relabelled
undefined track produces, Movie.en.2.srt before Movie.en.srt.

A subtitle arriving while the job runs came last. Under keep_both it stops
the job through the same check as a changed source, and the re-decided job
numbers past it; and a cancelled job now removes only the subtitles it
wrote, where it used to remove every name it had planned, which deleted a
file Bazarr had put at one of them (reproduced first). Verified by
mutation, 6 applied, 6 killed. Five survived the suite before these tests
and are killed here or in test_subtitle_extraction_routing.py: the arrival
check removed (the keep_both tests on both paths), applied under replace
too (replace carries on), the cleanup removing every planned name (does
not delete a subtitle it did not write, and both keep_both tests), and on
the two-pass path the extraction's hook not checking and its refusal
recorded as a failure (the routing arrival test). The sixth, this job's
own two-pass subtitle counted as an arrival, was already killed by
test_source_changed_mid_job.py's refused-remux test, which then reports
the wrong reason, so no test here claims it.

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


def _video(path, *, subtitles, languages=None):
    """A real MKV with English audio and `subtitles` SubRip tracks, English
    unless `languages` says otherwise, each saying FROM THE RELEASE and its
    number."""
    languages = languages or ["eng"] * subtitles
    cmd = ["ffmpeg", "-v", "error", "-y",
           "-f", "lavfi", "-i", "testsrc=size=160x120:rate=10:duration=1",
           "-f", "lavfi", "-i", "sine=frequency=440:duration=1"]
    for n in range(subtitles):
        srt = f"{path}.{n}.input.srt"
        with open(srt, "w") as f:
            f.write(f"1\n00:00:00,000 --> 00:00:00,900\nFROM THE RELEASE {n + 1}\n")
        cmd += ["-i", srt]
    cmd += ["-map", "0:v", "-map", "1:a", "-metadata:s:a:0", "language=eng"]
    for n in range(subtitles):
        cmd += ["-map", f"{n + 2}:s", f"-metadata:s:s:{n}", f"language={languages[n]}"]
    cmd += ["-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p",
            "-c:a", "aac", "-c:s", "srt", str(path)]
    subprocess.run(cmd, check=True)
    for n in range(subtitles):
        os.remove(f"{path}.{n}.input.srt")
    return path


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

    video = _video(library / "Movie.mkv", subtitles=1)
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
    assert "FROM THE RELEASE 1" in extracted.read_text()

    with lib.Session() as db:
        point_id = db.query(RevertPoint).one().id
    outcome = asyncio.run(restore_revert_point(point_id))

    assert outcome.success, outcome.error
    assert not extracted.exists(), "the job's own subtitle file was left behind"
    assert hashlib.sha256(lib.bazarr.read_bytes()).hexdigest() == lib.bazarr_sha


# ── Embedded first ───────────────────────────────────────────────────────────
#
# The decision keeps the name and the job moves whatever holds it to the next
# free number just before the swap (worker._move_aside), on both execution
# paths. The two-pass path is covered in test_subtitle_extraction_routing.py,
# whose rig drives it; these drive the real combined path.

def _mode(lib, value):
    from app.database.models import AppSetting
    with lib.Session() as db:
        db.merge(AppSetting(key="existing_srt_handling", value=json.dumps(value)))
        db.commit()


def test_a_file_is_moved_to_the_next_free_number(tmp_path):
    from app.core.worker import _move_aside

    (tmp_path / "Movie.en.srt").write_text("old")

    moved = _move_aside([str(tmp_path / "Movie.en.srt")], set())

    assert moved == [(str(tmp_path / "Movie.en.srt"), str(tmp_path / "Movie.en.2.srt"))]
    assert (tmp_path / "Movie.en.2.srt").read_text() == "old"


def test_a_name_the_job_is_writing_is_skipped(tmp_path):
    """The job writes Movie.en.srt and Movie.en.2.srt, so the old file goes
    to .3, and a numbered target is numbered from its own base."""
    from app.core.worker import _move_aside

    (tmp_path / "Movie.en.srt").write_text("old")
    (tmp_path / "Movie.en.2.srt").write_text("older")
    targets = {str(tmp_path / n).casefold() for n in ("Movie.en.srt", "Movie.en.2.srt")}

    moved = _move_aside([str(tmp_path / "Movie.en.srt"),
                         str(tmp_path / "Movie.en.2.srt")], targets)

    assert [new.rsplit("/", 1)[1] for _old, new in moved] == ["Movie.en.3.srt",
                                                              "Movie.en.4.srt"]


def test_a_name_holding_nothing_is_left_alone(tmp_path):
    from app.core.worker import _move_aside

    assert _move_aside([str(tmp_path / "Movie.en.srt")], set()) == []


def test_a_failed_move_puts_back_what_it_moved(tmp_path, monkeypatch):
    from app.core import worker

    (tmp_path / "Movie.en.srt").write_text("first")
    (tmp_path / "Movie.fr.srt").write_text("second")
    real_rename = os.rename
    calls = []

    def rename(src, dst):
        calls.append(src)
        if src.endswith("Movie.fr.srt"):
            raise PermissionError("read-only")
        real_rename(src, dst)
    monkeypatch.setattr(worker.os, "rename", rename)

    with pytest.raises(OSError):
        worker._move_aside([str(tmp_path / "Movie.en.srt"),
                            str(tmp_path / "Movie.fr.srt")], set())

    monkeypatch.setattr(worker.os, "rename", real_rename)
    assert sorted(p.name for p in tmp_path.iterdir()) == ["Movie.en.srt", "Movie.fr.srt"]


@needs_ffmpeg
def test_the_embedded_subtitle_takes_the_name_and_the_old_file_is_kept(lib):
    from app.core.revert_restore import restore_revert_point
    from app.database.models import RevertPoint

    _mode(lib, "embedded_first")
    _run_one(lib)

    assert "FROM THE RELEASE 1" in lib.bazarr.read_text()
    moved = lib.library / "Movie.en.2.srt"
    assert hashlib.sha256(moved.read_bytes()).hexdigest() == lib.bazarr_sha, (
        "the file that held the name was not kept under the next number")

    # What the setting's description says a revert does.
    with lib.Session() as db:
        point_id = db.query(RevertPoint).one().id
    outcome = asyncio.run(restore_revert_point(point_id))

    assert outcome.success, outcome.error
    assert not lib.bazarr.exists(), "the extracted file was left at the name"
    assert hashlib.sha256(moved.read_bytes()).hexdigest() == lib.bazarr_sha


@needs_ffmpeg
def test_the_old_file_does_not_take_a_name_the_job_writes(lib):
    """
    The job writes two English subtitles, and Movie.en.2.srt comes first:
    the undefined track before the English one is relabelled to English
    after the English one has taken Movie.en.srt. The old Movie.en.srt is
    moved before either is written, so it must skip .2 as well, or the job
    writes the relabelled subtitle over it. In the other order the move
    happens to come out right anyway: the second target, finding the old
    file at .2, moves it on to .3.
    """
    from app.database.models import AppSetting

    lib.video.unlink()
    _video(lib.video, subtitles=2, languages=["und", "eng"])
    _mode(lib, "embedded_first")
    with lib.Session() as db:
        for key, value in {"fix_undefined_language": "always_fix",
                           "undefined_language_value": "eng",
                           "undefined_language_mode": "all_undefined",
                           "keep_undefined_subtitles": True}.items():
            db.merge(AppSetting(key=key, value=json.dumps(value)))
        db.commit()

    _run_one(lib)

    assert "FROM THE RELEASE 2" in lib.bazarr.read_text()
    assert "FROM THE RELEASE 1" in (lib.library / "Movie.en.2.srt").read_text()
    assert hashlib.sha256((lib.library / "Movie.en.3.srt").read_bytes()).hexdigest() \
        == lib.bazarr_sha, "the file that held the name was overwritten"


@needs_ffmpeg
def test_a_move_that_fails_fails_the_job_and_replaces_nothing(lib, monkeypatch):
    def refuse(paths, avoid):
        raise PermissionError("read-only folder")
    monkeypatch.setattr(lib.worker, "_move_aside", refuse)
    _mode(lib, "embedded_first")

    _run_one(lib)

    from app.database.models import QueueItem
    with lib.Session() as db:
        job = db.query(QueueItem).one()
        status, error = job.status, job.error_message or ""
    assert status == "failed"
    assert "Could not move an existing subtitle file aside" in error
    assert hashlib.sha256(lib.bazarr.read_bytes()).hexdigest() == lib.bazarr_sha
    assert lib.video.exists(), "the source was replaced although the job failed"


# ── A subtitle that arrives while the job runs ───────────────────────────────
#
# Bazarr reacts to the same import that queues the job, so a subtitle can
# land at an extraction name mid-job. Under keep_both that stops the job
# like any other change on disk, and the re-decided job numbers past it.
# The other two modes deal with it at the swap. These drive the combined
# path; the two-pass path is in test_subtitle_extraction_routing.py.

def _while_ffmpeg_runs(lib, monkeypatch, *, change_source=False):
    """Bazarr's Movie.en.srt appears during the combined pass, and the
    source is optionally replaced at the same time."""
    lib.bazarr.unlink()
    real = lib.worker.execute_ffmpeg_combined

    async def combined(**kwargs):
        lib.bazarr.write_text("1\n00:00:00,000 --> 00:00:00,900\nFROM BAZARR\n")
        if change_source:
            with open(lib.video, "ab") as f:
                f.write(b"\0")
        return await real(**kwargs)
    monkeypatch.setattr(lib.worker, "execute_ffmpeg_combined", combined)


def _jobs(lib):
    from app.database.models import PlannedAction, QueueItem
    with lib.Session() as db:
        return [(job.status, job.error_message or "",
                 [a.description for a in db.query(PlannedAction)
                  .filter(PlannedAction.queue_item_id == job.id)])
                for job in db.query(QueueItem).order_by(QueueItem.id)]


@needs_ffmpeg
def test_a_subtitle_arriving_mid_job_stops_it_under_keep_both(lib, monkeypatch):
    _while_ffmpeg_runs(lib, monkeypatch)

    _run_one(lib)

    jobs = _jobs(lib)
    assert jobs[0][0] == "cancelled", jobs
    assert "Movie.en.srt appeared beside it" in jobs[0][1]
    assert hashlib.sha256(lib.bazarr.read_bytes()).hexdigest() == lib.bazarr_sha
    assert jobs[1][0] == "pending", "the file was not decided again"
    assert any(d.endswith("Movie.en.2.srt") for d in jobs[1][2]), jobs[1][2]


@needs_ffmpeg
def test_replace_carries_on_when_a_subtitle_arrives(lib, monkeypatch):
    _mode(lib, "replace")
    _while_ffmpeg_runs(lib, monkeypatch)

    _run_one(lib)

    assert _jobs(lib)[0][0] == "success"
    assert "FROM THE RELEASE 1" in lib.bazarr.read_text()


@needs_ffmpeg
def test_a_cancelled_job_does_not_delete_a_subtitle_it_did_not_write(lib, monkeypatch):
    """
    Reproduced before this change: the job is cancelled because its source
    was replaced, and its cleanup removed every name it had planned to
    write, including the subtitle Bazarr had just put at one of them.
    Under replace, so the arrival itself is not what stops the job.
    """
    _mode(lib, "replace")
    _while_ffmpeg_runs(lib, monkeypatch, change_source=True)

    _run_one(lib)

    assert _jobs(lib)[0][0] == "cancelled"
    assert hashlib.sha256(lib.bazarr.read_bytes()).hexdigest() == lib.bazarr_sha, (
        "the cancelled job deleted a subtitle another program wrote")
