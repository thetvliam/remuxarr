"""
Track titles through every rewrite of an MP4.

In an MP4, FFmpeg reads a track's title as the tag "name", and writing an MP4
it takes a title only from the key "title". A stream copy from MP4 to MP4
therefore dropped every title. Measured with the real command builders and
FFmpeg 8.1 on an MP4 titled "Main Video", "English 5.1", "German Commentary"
and "English SDH": a job that only removed one track, an AC3 Forge add and an
AC3 Forge undo each came out with no titles at all. Matroska to MP4 kept them
(the key is "title" there), and a revert writes its titles explicitly.

Players and Plex show these names, and probe.extract_tracks reads forced,
SDH, dub and commentary hints from them, so a re-check after the loss could
see a track differently.

Now an MP4 output gets each track's title written explicitly: by
build_ffmpeg_command for a job, and by the two forge commands from the titles
load_forge_job_data probes (forge.titles_after_forge).

Verified by mutation, 11 applied 11 killed, each surviving the whole
2362-test suite first:

  jobs    titles never written                    → the dropped-track job
          written for Matroska too                → the Matroska command
          the video's left out                    → the dropped-track job
          the audio's left out                    → the dropped-track job
          the subtitles' left out                 → the dropped-track job
  forge   written for Matroska too                → the Matroska commands
          the add not given them                  → the wiring test
          the undo not given them                 → the wiring test
          no shift after the removed track        → the undo after a job
          the removed track's own title kept      → the undo after a job
          not probed at load                      → the load test
"""
import asyncio
import json
import os
import shutil
import subprocess
from types import SimpleNamespace

import pytest

from app.core.decision import Action, ProcessingDecision
from app.core.ffmpeg import build_ffmpeg_command
from app.core.forge import build_add_ac3_command, build_undo_command, titles_after_forge
from app.core.probe import extract_tracks

needs_ffmpeg = pytest.mark.skipif(
    shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None,
    reason="ffmpeg/ffprobe not available",
)

TITLES = ["Main Video", "English 5.1", "German Commentary", "English SDH"]


def _probe(path):
    out = subprocess.run(["ffprobe", "-v", "error", "-show_streams", "-show_format",
                          "-of", "json", str(path)],
                         capture_output=True, text=True, check=True)
    return json.loads(out.stdout)


def _titles(path):
    return [(t["track_type"], t["title"]) for t in extract_tracks(_probe(path))]


def _make(tmp_path, container="mp4", extra_ffmpeg=()):
    """
    Video, English AAC 5.1, German AAC and an English subtitle, each titled,
    as an MP4 made from Matroska the way a converted release is, or as the
    Matroska itself.
    """
    srt = tmp_path / "s.srt"
    srt.write_text("1\n00:00:00,000 --> 00:00:00,900\nHi\n")
    mkv = tmp_path / "source.mkv"
    subprocess.run([
        "ffmpeg", "-v", "error", "-y",
        "-f", "lavfi", "-i", "testsrc=size=64x36:rate=5:duration=1",
        "-f", "lavfi", "-i", "sine=duration=1",
        "-f", "lavfi", "-i", "sine=frequency=660:duration=1",
        "-i", str(srt),
        "-map", "0:v", "-map", "1:a", "-map", "2:a", "-map", "3:s",
        "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-ac:a:0", "6", "-c:s", "srt",
        "-metadata:s:a:0", "language=eng", "-metadata:s:a:1", "language=ger",
        "-metadata:s:s:0", "language=eng",
        *[arg for i, (spec, title) in enumerate(zip(["v:0", "a:0", "a:1", "s:0"], TITLES))
          for arg in (f"-metadata:s:{spec}", f"title={title}")],
        *extra_ffmpeg, str(mkv)], check=True)
    if container == "mkv":
        return mkv
    mp4 = tmp_path / "Movie.mp4"
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", str(mkv), "-map", "0",
                    "-c", "copy", "-c:s", "mov_text", str(mp4)], check=True)
    return mp4


def _job(source, out, target, actions):
    decision = ProcessingDecision(should_process=True, reason="test",
                                  target_container=target, actions=actions)
    cmd = build_ffmpeg_command(str(source), str(out), decision,
                               extract_tracks(_probe(source)))
    subprocess.run(cmd, check=True, capture_output=True)
    return cmd


def _drop_german():
    return [Action(action_type="drop_track", description="German",
                   track_type="audio", stream_index=2)]


# ── Jobs ─────────────────────────────────────────────────────────────────────

@needs_ffmpeg
def test_an_mp4_job_keeps_every_title(tmp_path):
    source = _make(tmp_path)
    assert _titles(source) == list(zip(["video", "audio", "audio", "subtitle"], TITLES))

    _job(source, tmp_path / "out.mp4", "mp4", _drop_german())

    assert _titles(tmp_path / "out.mp4") == [
        ("video", "Main Video"), ("audio", "English 5.1"), ("subtitle", "English SDH")]


@needs_ffmpeg
def test_an_mp4_job_that_only_retags_keeps_every_title(tmp_path):
    source = _make(tmp_path)
    retag = [Action(action_type="copy_track", description="tag",
                    track_type="audio", stream_index=2, target_language="spa")]

    _job(source, tmp_path / "out.mp4", "mp4", retag)

    assert [t for _, t in _titles(tmp_path / "out.mp4")] == TITLES


@needs_ffmpeg
def test_matroska_to_mp4_still_keeps_every_title(tmp_path):
    """The SubRip subtitle goes too: MP4 cannot hold one by copy, and a real
    job extracts it or stays Matroska."""
    source = _make(tmp_path, container="mkv")
    drop_subtitle = Action(action_type="drop_track", description="SubRip",
                           track_type="subtitle", stream_index=3)

    _job(source, tmp_path / "out.mp4", "mp4", _drop_german() + [drop_subtitle])

    assert [t for _, t in _titles(tmp_path / "out.mp4")] == ["Main Video", "English 5.1"]


def test_a_matroska_job_writes_no_titles():
    """Matroska keeps them through the copy; nothing needs writing."""
    tracks = [{"stream_index": 0, "track_type": "video", "title": "Main Video"},
              {"stream_index": 1, "track_type": "audio", "title": "English 5.1"}]
    decision = ProcessingDecision(should_process=True, reason="t",
                                  target_container="mkv", actions=[])

    cmd = build_ffmpeg_command("/m/in.mkv", "/m/out.tmp", decision, tracks)

    assert not any(arg.startswith("title=") for arg in cmd)


def test_a_title_is_passed_as_it_is():
    """An argument list, not a shell line: quotes and = need no escaping."""
    tracks = [{"stream_index": 0, "track_type": "audio",
               "title": 'Director\'s "Cut" = 5.1'}]
    decision = ProcessingDecision(should_process=True, reason="t",
                                  target_container="mp4", actions=[])

    cmd = build_ffmpeg_command("/m/in.mp4", "/m/out.tmp", decision, tracks)

    assert cmd[cmd.index("-metadata:s:a:0") + 1] == 'title=Director\'s "Cut" = 5.1'


# ── AC3 Forge ────────────────────────────────────────────────────────────────

@needs_ffmpeg
def test_a_forge_add_and_undo_on_an_mp4_keep_every_title(tmp_path):
    source = _make(tmp_path)
    added = tmp_path / "added.mp4"
    subprocess.run(build_add_ac3_command(
        str(source), str(added), 1, 2, "mp4",
        titles=titles_after_forge(extract_tracks(_probe(source)))),
        check=True, capture_output=True)

    assert _titles(added) == [*zip(["video", "audio", "audio", "subtitle"], TITLES),
                              ("audio", None)]

    undone = tmp_path / "undone.mp4"
    subprocess.run(build_undo_command(
        str(added), str(undone), 2, "mp4",
        titles=titles_after_forge(extract_tracks(_probe(added)), 2)),
        check=True, capture_output=True)

    assert [t for _, t in _titles(undone)] == TITLES


@needs_ffmpeg
@pytest.mark.parametrize("subtitle_title", ["English SDH", None],
                         ids=["titled_subtitle", "untitled_subtitle"])
def test_an_undo_after_a_job_puts_each_title_on_its_own_track(tmp_path, subtitle_title):
    """
    A job after the add writes video, audio, then subtitles, so the AC3 sits
    before the subtitle; the undo removes it and the subtitle moves down one.
    Given a title of its own here, the AC3's must go with it, not onto the
    subtitle that takes its place: with the subtitle untitled, nothing else
    would cover it.
    """
    source = _make(tmp_path)
    added = tmp_path / "added.mp4"
    subprocess.run(build_add_ac3_command(str(source), str(added), 1, 2, "mp4"),
                   check=True, capture_output=True)
    rewritten = tmp_path / "rewritten.mp4"
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", str(added),
                    "-map", "0:v", "-map", "0:a", "-map", "0:s", "-c", "copy",
                    *[arg for i, title in enumerate(TITLES[:3] + ["AC3 Track"])
                      for arg in ((f"-metadata:s:{i}", f"title={title}"))],
                    "-metadata:s:4", f"title={subtitle_title or ''}", str(rewritten)], check=True)
    tracks = extract_tracks(_probe(rewritten))
    assert [(t["track_type"], t["title"]) for t in tracks][3:] == [
        ("audio", "AC3 Track"), ("subtitle", subtitle_title)]

    undone = tmp_path / "undone.mp4"
    subprocess.run(build_undo_command(str(rewritten), str(undone), 2, "mp4",
                                      titles=titles_after_forge(tracks, 2)),
                   check=True, capture_output=True)

    assert _titles(undone) == list(zip(["video", "audio", "audio", "subtitle"],
                                       TITLES[:3] + [subtitle_title]))


def test_the_forge_commands_write_no_titles_for_matroska():
    titles = {0: "Main Video", 1: "English 5.1"}

    add = build_add_ac3_command("/m/in.mkv", "/m/t.tmp", 1, 1, "mkv", titles=titles)
    undo = build_undo_command("/m/in.mkv", "/m/t.tmp", 1, "mkv", titles=titles)

    assert not any(arg.startswith("title=") for arg in add + undo)


@needs_ffmpeg
def test_a_forge_job_loads_the_titles_of_the_file(tmp_path, monkeypatch):
    from sqlalchemy.orm import sessionmaker

    import app.core.forge as forge
    from app.database.models import Ac3ForgeJob, Base, MediaFile
    from tests.conftest import memory_engine

    source = _make(tmp_path)
    engine = memory_engine()
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine)
    monkeypatch.setattr(forge, "SessionLocal", factory)
    with factory() as db:
        media = MediaFile(path=str(source), filename=source.name,
                          directory=str(tmp_path), container="mp4",
                          size=os.path.getsize(source), mtime=0.0)
        db.add(media)
        db.flush()
        job = Ac3ForgeJob(file_id=media.id, status="processing", is_undo=False,
                          aac_stream_index=1, audio_track_count=2)
        db.add(job)
        db.commit()
        job_id = job.id

    data = forge.load_forge_job_data(job_id)

    assert data is not None
    assert data["titles"] == dict(enumerate(TITLES))


@pytest.mark.parametrize("is_undo", [False, True], ids=["add", "undo"])
def test_the_worker_hands_the_titles_to_the_forge_command(monkeypatch, is_undo):
    import app.core.forge as forge
    import app.core.worker as worker

    titles = {0: "Main Video", 1: "English 5.1"}
    monkeypatch.setattr(forge, "load_forge_job_data", lambda _id: {
        "job_id": 1, "file_id": 1, "is_undo": is_undo, "file_path": "/m/Movie.mp4",
        "filename": "Movie.mp4", "container": "mp4", "aac_stream_index": 1,
        "audio_track_count": 1, "undo_audio_output_index": 1, "original_size": 1,
        "job_timeout_minutes": 1, "add_faststart": False, "app_cfg": {},
        "titles": titles})
    given = {}

    class Stop(Exception):
        pass

    def capture(**kwargs):
        given.update(kwargs)
        raise Stop()

    monkeypatch.setattr(forge, "build_undo_command" if is_undo else "build_add_ac3_command",
                        capture)
    monkeypatch.setattr(forge, "finish_forge_job", lambda *a, **k: None)
    monkeypatch.setattr(forge, "load_forge_final_state", lambda _id: None)

    class _WS:
        async def broadcast_json(self, *_a, **_k):
            pass

    async def go():
        try:
            await worker._run_forge_job(1, _WS(), asyncio.get_running_loop())
        except Stop:
            pass
    asyncio.run(go())

    assert given.get("titles") == titles
    assert SimpleNamespace(**given).container == "mp4"
