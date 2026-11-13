"""
Original Language Audio: keep the audio a film or series was made in.

Requested by a user whose library is mostly English but who watches foreign
films and series in their original language with subtitles. Keep Audio
Languages set to English kept the English dub and dropped the original,
since the original is not always the default-flagged track either.

Sonarr and Radarr record each series' and movie's original language. An
import's webhook brings it (stored by the previous commit), and Re-check
Scanned Files and Force Full Rescan ask each service for its whole list,
once per scan, whatever the setting says (arr_lookup.original_languages).
Routine and scheduled scans do not ask. The value reaches every decision
through scanner._file_info_for.

Three settings: off, which is what Remuxarr always did; keep_both, the
original language as well as Keep Audio Languages, and counted as preferred,
so a default-flagged dub is not kept for want of an English track; and
original_only, just the original language when the file has a track in it,
Keep Audio Languages otherwise. The per-file keep set is worked out once in
analyze_file and read by the keep rule, the "has preferred audio" check and
the no-silent-output fallback alike.

Verified by mutation, 15 applied 15 killed. The baseline was the whole
2209-test suite with three mechanical changes this commit forced: the
_process_file substitutes in test_scan_library take the new keyword, as
their docstring asks of a signature change; test_existing_subtitle_files'
stand-in for a MediaFile has the new attribute; and test_job_preflight's
expected file_info has the new key. Fourteen survived it. The setting read
as off was already caught there, by test_settings_schema's rescan-note
test, since the key is in APPLIES_ON_RESCAN and the engine no longer read
it; the tests here catch it as well.

  the rule      the setting read as off                  → every mode test,
                                                           the rescan note
                keep_both behaving as original_only      → keep both with
                                                           English present,
                                                           e2e keep_both
                original_only behaving as keep_both      → original only,
                                                           und kept,
                                                           e2e original_only
                original_only with no original track     → no original track
                the original not counted as preferred    → the requested case
                the fallback reading the plain list      → the requested case,
                                                           the fallback
                the keep rule reading the plain list     → the mode tests,
                                                           e2e both modes
                the stored value not in file_info        → file_info,
                                                           e2e both modes
  the lookup    folders matched by name prefix           → whole folder names
                Container Paths not applied              → translated, Sonarr
                                                           first, one fails
                Radarr asked before Sonarr               → Sonarr first
                a failed request ending the lookup       → one fails
  the scan      asked on routine scans                   → routine scan
                asked with the setting off               → (now the
                                                           behaviour; see
                                                           below)
                the answer not handed to _process_file   → re-check, full,
                                                           e2e both modes

The reason a drop gives, added after a report that Keep original only
dropped English as "not in keep list" with English in the list: 4 applied,
3 killed, all four surviving the suite before these tests.

  the reason    every original_only drop named original  → other languages
                checked against the file's own keep set  → names itself
                the track's language shown as original   → names itself
                the mode check removed                   survives: equivalent,
                                                           as no other mode
                                                           drops a listed
                                                           language

The no-original-track test first had English ahead of German, and the
mutant that applied original_only without the track survived it: the keep
list emptied, and the fallback kept English anyway. German now comes first.

The lookup no longer waits for the setting: a re-check or a full rescan
asks whatever Original Language Audio says, so the original language of a
file already in a library is known with that setting off as well. Two tests
that pinned the old gate were rewritten, the re-check one and the off case
of the real run, which now asserts the language is stored while its file
keeps what off always kept. 3 applied, 3 killed. Putting the gate back is
the old code, so it passes the old suite by construction; both rewritten
tests kill it. Asking on routine scans too, and asking a service that is
switched off, were already killed by the routine-scan test and the
switched-off tests here and in test_post_job_notify, in a run of the suite
with the two rewritten tests deselected, and are not claimed here.

Run from the project root:
    pytest tests/test_original_language_audio.py -v
"""
import asyncio
import json
import logging
import os
import shutil
import subprocess
from types import SimpleNamespace

import pytest

from app.core.decision import analyze_file
from conftest import BASE_SETTINGS, make_file_info, make_track


def _mode(mode, keep=("eng",)):
    return dict(BASE_SETTINGS, keep_audio_languages=list(keep),
                original_language_audio=mode, prefer_mp4_container=False)


def _film(original="fre"):
    return make_file_info(path="/m/Film (2020)/Film.mkv", container="mkv",
                          original_language=original)


def _dropped(decision):
    return sorted(a.stream_index for a in decision.actions
                  if a.track_type == "audio" and a.action_type == "drop_track")


def _requested():
    """The case in the request: a French original, a German dub that is the
    default track, and no English track at all."""
    return [make_track(0, "video", codec="h264"),
            make_track(1, "audio", codec="aac", language="ger", is_default=True),
            make_track(2, "audio", codec="aac", language="fre")]


# ── The rule ─────────────────────────────────────────────────────────────────

def test_off_is_what_remuxarr_always_did():
    """The default dub is kept for want of an English track, the original goes."""
    assert _dropped(analyze_file(_film(), _requested(), _mode("off"))) == [2]


def test_keep_both_keeps_the_original_and_not_the_default_dub():
    """
    The case the request describes. The original language counts as
    preferred, so Always Keep Default Audio Track has nothing to rescue.
    """
    assert _dropped(analyze_file(_film(), _requested(), _mode("keep_both"))) == [1]


def test_keep_both_also_keeps_keep_audio_languages():
    tracks = [make_track(0, "video", codec="h264"),
              make_track(1, "audio", codec="aac", language="eng", is_default=True),
              make_track(2, "audio", codec="aac", language="fre"),
              make_track(3, "audio", codec="aac", language="ger")]

    assert _dropped(analyze_file(_film(), tracks, _mode("keep_both"))) == [3]


def test_original_only_drops_the_dub_in_keep_audio_languages():
    tracks = [make_track(0, "video", codec="h264"),
              make_track(1, "audio", codec="aac", language="eng", is_default=True),
              make_track(2, "audio", codec="aac", language="fre")]

    assert _dropped(analyze_file(_film(), tracks, _mode("original_only"))) == [1]


def test_original_only_with_no_original_track_keeps_audio_languages():
    """
    A file holding only dubs keeps what Keep Audio Languages keeps, rather
    than being left to the no-silent-output fallback, which keeps the first
    audio track whatever it is. German comes first here so that the two
    differ: with English first, a rule that emptied the keep list was
    rescued by the fallback picking English anyway, and passed.
    """
    tracks = [make_track(0, "video", codec="h264"),
              make_track(1, "audio", codec="aac", language="ger"),
              make_track(2, "audio", codec="aac", language="eng")]

    assert _dropped(analyze_file(_film(), tracks, _mode("original_only"))) == [1]


# ── The reason a drop gives ──────────────────────────────────────────────────

def _miraculous():
    """The layout of the report that showed the wrong reason: English, the
    French original and a German dub, with English in Keep Audio Languages."""
    return [make_track(0, "video", codec="h264"),
            make_track(1, "audio", codec="eac3", language="eng"),
            make_track(2, "audio", codec="eac3", language="fre"),
            make_track(3, "audio", codec="eac3", language="ger")]


def _reasons(decision):
    return {a.stream_index: a.description.split(" — ", 1)[1] for a in decision.actions
            if a.track_type == "audio" and a.action_type == "drop_track"}


def test_original_only_names_itself_for_a_language_in_the_list():
    """English is in Keep Audio Languages, so "not in keep list" pointed at
    a setting that already listed it."""
    reasons = _reasons(analyze_file(_film(), _miraculous(), _mode("original_only")))

    assert reasons[1] == ("not the original language [fre] "
                          "(Original Language Audio: Keep original only)")


def test_original_only_still_says_not_in_keep_list_for_other_languages():
    """German is in neither list, so the old reason is still the true one."""
    reasons = _reasons(analyze_file(_film(), _miraculous(), _mode("original_only")))

    assert reasons[3] == "not in keep list"


def test_off_gives_the_reason_it_always_gave():
    reasons = _reasons(analyze_file(_film(), _miraculous(), _mode("off")))

    assert reasons == {2: "not in keep list", 3: "not in keep list"}


def test_no_original_language_known_is_off():
    assert _dropped(analyze_file(_film(original=None), _requested(),
                                 _mode("keep_both"))) == [2]


def test_undefined_audio_is_still_kept():
    tracks = [make_track(0, "video", codec="h264"),
              make_track(1, "audio", codec="aac", language="und"),
              make_track(2, "audio", codec="aac", language="eng"),
              make_track(3, "audio", codec="aac", language="fre")]

    assert _dropped(analyze_file(_film(), tracks, _mode("original_only"))) == [2]


def test_the_fallback_sees_the_original_as_kept(caplog):
    """
    A file whose only audio is the original, untagged by any default flag:
    kept by the rule, so the no-silent-output fallback must not fire and
    log its "no usable audio track" warning. The fallback restates the keep
    rule's terms, and reading the plain list it would think nothing
    survives.
    """
    tracks = [make_track(0, "video", codec="h264"),
              make_track(1, "audio", codec="aac", language="fre")]

    with caplog.at_level(logging.WARNING, logger="app.core.decision"):
        decision = analyze_file(_film(), tracks, _mode("keep_both"))

    assert _dropped(decision) == []
    assert not [r for r in caplog.records if "No usable audio track" in r.getMessage()]


def test_the_stored_value_reaches_file_info(tmp_path):
    from app.core.scanner import _file_info_for

    media = SimpleNamespace(path=str(tmp_path / "Film.mkv"), container="mkv",
                            video_codec="h264", font_attachments=0, unwritable_data="",
                            und_audio_threshold_acknowledged=False,
                            original_language="fre")

    assert _file_info_for(media)["original_language"] == "fre"


# ── The lookup ───────────────────────────────────────────────────────────────

_CFG = {
    "sonarr_enabled": True, "sonarr_url": "http://sonarr", "sonarr_api_key": "s",
    "sonarr_path_prefix_local": "/data/tv", "sonarr_path_prefix_remote": "/tv",
    "radarr_enabled": True, "radarr_url": "http://radarr", "radarr_api_key": "r",
    "radarr_path_prefix_local": "/data/movies", "radarr_path_prefix_remote": "/movies",
}


def _language(name, language_id=2):
    return {"id": language_id, "name": name}


@pytest.fixture
def services(monkeypatch):
    """Stub both services' lists; record each request by url."""
    import app.core.arr_lookup as arr_lookup

    lists = {"http://sonarr": [], "http://radarr": []}
    asked = []

    def fake_get(url, api_key, path, params=None):
        asked.append((url, path))
        answer = lists[url]
        if isinstance(answer, Exception):
            raise answer
        return answer

    monkeypatch.setattr(arr_lookup, "arr_get", fake_get)
    return SimpleNamespace(lists=lists, asked=asked, lookup=arr_lookup.original_languages)


def test_a_file_is_matched_through_the_container_paths(services):
    services.lists["http://sonarr"] = [
        {"path": "/tv/Show", "originalLanguage": _language("French")}]

    for_path = services.lookup(_CFG)

    assert for_path("/data/tv/Show/Season 01/Show - S01E01.mkv") == "fre"


def test_a_folder_matches_on_whole_folder_names(services):
    services.lists["http://sonarr"] = [
        {"path": "/tv/Show", "originalLanguage": _language("French")}]

    assert services.lookup(_CFG)("/data/tv/Show 2/Season 01/E01.mkv") is None


def test_sonarr_is_asked_about_a_path_first(services):
    cfg = dict(_CFG, radarr_path_prefix_local="/data/tv", radarr_path_prefix_remote="/tv")
    services.lists["http://sonarr"] = [
        {"path": "/tv/Show", "originalLanguage": _language("French")}]
    services.lists["http://radarr"] = [
        {"path": "/tv/Show", "originalLanguage": _language("Japanese", 8)}]

    assert services.lookup(cfg)("/data/tv/Show/E01.mkv") == "fre"


def test_one_service_failing_leaves_the_other(services, caplog):
    services.lists["http://sonarr"] = ConnectionError("refused")
    services.lists["http://radarr"] = [
        {"path": "/movies/Film (2020)", "originalLanguage": _language("Korean", 21)}]

    with caplog.at_level(logging.WARNING, logger="app.core.arr_lookup"):
        for_path = services.lookup(_CFG)

    assert for_path("/data/movies/Film (2020)/Film.mkv") == "kor"
    assert any("sonarr" in r.getMessage() for r in caplog.records)


def test_a_service_switched_off_is_not_asked(services):
    services.lookup(dict(_CFG, sonarr_enabled=False))

    assert [url for url, _path in services.asked] == ["http://radarr"]


def test_an_entry_naming_no_language_gives_none(services):
    services.lists["http://sonarr"] = [
        {"path": "/tv/Show", "originalLanguage": _language("Unknown", 0)}]

    assert services.lookup(_CFG)("/data/tv/Show/E01.mkv") is None


# ── Which scans ask ──────────────────────────────────────────────────────────

@pytest.fixture
def scanning(tmp_path, monkeypatch):
    """scan_library over two files, with _process_file and the lookup stubbed."""
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    from app.core import scanner
    from app.database.models import AppSetting, Base

    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    db = sessionmaker(bind=engine)()
    library = tmp_path / "tv"
    library.mkdir()
    for name in ("A.mkv", "B.mkv"):
        (library / name).write_text("not really a video")

    handed: list = []
    lookups: list = []

    def fake_process(db, path, app_cfg, force_probe, dry_run, stats,
                     sonarr_series_id=None, radarr_movie_id=None, recheck=False,
                     original_language=None, from_scan=False):
        handed.append(original_language)

    def fake_lookup(cfg):
        lookups.append(cfg.get("original_language_audio"))
        return lambda path: "fre"

    monkeypatch.setattr(scanner, "_process_file", fake_process)
    monkeypatch.setattr(scanner, "original_languages", fake_lookup)

    def setting(mode):
        db.merge(AppSetting(key="original_language_audio", value=json.dumps(mode)))
        db.commit()

    def scan(**kwargs):
        return scanner.scan_library(db, [str(library)], **kwargs)

    return SimpleNamespace(scan=scan, setting=setting, handed=handed, lookups=lookups)


@pytest.mark.parametrize("kind", [{"recheck": True}, {"force_probe": True}])
def test_a_recheck_and_a_full_rescan_ask_once_and_hand_on_the_answer(scanning, kind):
    scanning.setting("keep_both")

    scanning.scan(**kind)

    assert scanning.lookups == ["keep_both"], "asked once per scan, not per file"
    assert scanning.handed == ["fre", "fre"]


def test_a_routine_scan_does_not_ask(scanning):
    scanning.setting("keep_both")

    scanning.scan()

    assert scanning.lookups == []
    assert scanning.handed == [None, None]


def test_a_recheck_asks_with_the_setting_off_too(scanning):
    """
    It used to ask only while Original Language Audio was on, which left the
    original language unknown for files already in a library otherwise.
    Stored while the setting is off, it changes no audio decision: the
    real run below shows that for the off case.
    """
    scanning.setting("off")

    scanning.scan(recheck=True)

    assert scanning.lookups == ["off"], "asked once per scan, not per file"
    assert scanning.handed == ["fre", "fre"]


# ── With real FFmpeg ─────────────────────────────────────────────────────────

needs_ffmpeg = pytest.mark.skipif(
    shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None,
    reason="ffmpeg/ffprobe not available",
)


class _WS:
    async def broadcast_json(self, *args, **kwargs):
        pass


def _audio_languages(path):
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "a",
         "-show_entries", "stream_tags=language", "-of", "json", str(path)],
        capture_output=True, text=True, check=True)
    return [(s.get("tags") or {}).get("language") for s in json.loads(out.stdout)["streams"]]


@needs_ffmpeg
@pytest.mark.parametrize("mode, languages", [
    ("off",           ["eng"]),
    ("keep_both",     ["eng", "fre"]),
    ("original_only", ["fre"]),
])
def test_a_full_rescan_with_sonarr_keeps_what_each_setting_says(tmp_path, monkeypatch,
                                                                 mode, languages):
    """
    An English dub, the default, and the French original, with Keep Audio
    Languages English and Sonarr saying the series is French. A full rescan
    asks Sonarr, stores the language, decides, and the job writes the file.
    """
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    import app.core.arr_lookup as arr_lookup
    import app.core.worker as worker
    import app.database.session as session_module
    from app.config import settings as app_settings
    from app.core import scanner
    from app.database.models import AppSetting, Base, MediaFile, QueueItem

    engine = create_engine(f"sqlite:///{tmp_path / 'remuxarr.db'}",
                           connect_args={"check_same_thread": False, "timeout": 30})
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine)
    monkeypatch.setattr(worker, "SessionLocal", factory)
    monkeypatch.setattr(session_module, "SessionLocal", factory)
    monkeypatch.setattr(app_settings, "TEMP_DIR", str(tmp_path / "tmp"))
    monkeypatch.setattr(app_settings, "RECYCLE_DIR", str(tmp_path / "recycle"))
    (tmp_path / "recycle").mkdir()

    show = tmp_path / "tv" / "Show"
    (show / "Season 01").mkdir(parents=True)
    video = show / "Season 01" / "Show - S01E01.mkv"
    subprocess.run([
        "ffmpeg", "-v", "error", "-y",
        "-f", "lavfi", "-i", "testsrc=size=64x36:rate=5:duration=1",
        "-f", "lavfi", "-i", "sine=frequency=440:duration=1",
        "-f", "lavfi", "-i", "sine=frequency=880:duration=1",
        "-map", "0:v", "-map", "1:a", "-map", "2:a",
        "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p", "-c:a", "aac",
        "-metadata:s:a:0", "language=eng", "-disposition:a:0", "default",
        "-metadata:s:a:1", "language=fre", "-disposition:a:1", "0",
        "-f", "matroska", str(video)], check=True)
    monkeypatch.setattr(arr_lookup, "arr_get", lambda *_a, **_kw: [
        {"path": str(show), "originalLanguage": {"id": 2, "name": "French"}}])

    with factory() as db:
        for key, value in (("dry_run_mode", False), ("prefer_mp4_container", False),
                           ("keep_audio_languages", ["eng"]),
                           ("original_language_audio", mode),
                           ("sonarr_enabled", True), ("sonarr_url", "http://sonarr"),
                           ("sonarr_api_key", "key")):
            db.merge(AppSetting(key=key, value=json.dumps(value)))
        db.commit()
        scanner.scan_library(db, [str(tmp_path / "tv")], force_probe=True)
        stored = db.query(MediaFile).one().original_language
        pending = [q.id for q in db.query(QueueItem).filter(QueueItem.status == "pending")]

    # Stored whatever the setting says. With it off, the audio the job leaves
    # below is still what off always kept.
    assert stored == "fre", "the original language was not stored"

    async def run_next():
        job_id = worker._claim_next()
        await worker._run_and_broadcast(job_id, _WS(), asyncio.get_running_loop())
    if pending:
        asyncio.run(run_next())

    with factory() as db:
        path = db.query(MediaFile).one().path
    engine.dispose()
    assert os.path.exists(path)
    assert _audio_languages(path) == languages
