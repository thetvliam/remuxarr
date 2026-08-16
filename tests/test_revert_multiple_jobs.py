"""
Revert points across MULTIPLE jobs on the same file.

The bug this file exists for
----------------------------
Revert points used to be anchored to the job that created them: each one
fingerprinted the file as its own job left it. That breaks the moment a
file is processed twice, and breaks silently.

Job 1 drops the subtitles and records a point. Job 2 fixes an audio
language tag — destroying nothing at all — but rewrites the file, giving
it a new size and mtime. Job 1's point now fails its own sentinel check
forever. The dropped subtitles are still sitting on the recycle volume,
intact and permanently unreachable, and the user is told the file "has
been modified since it was processed" — blaming an outside change for
something Remuxarr did to itself.

A user who changes their language settings and rescans triggers exactly
this across their whole library.

The fix is that a revert point describes the PRISTINE original and is
extended by every later job rather than replaced. test_a_metadata_only_job
_does_not_strand_the_first_jobs_tracks is the direct regression test for
the scenario above; the rest cover what extending has to get right.

Everything here runs real FFmpeg on real files. The failure being guarded
against is a sidecar that looks fine and holds the wrong thing, which
argv assertions cannot see.

Verified by mutation, 8 applied, 8 killed:

  • Existing point ignored, fresh manifest each job → killed, by
                                                      test_a_metadata_only_job_
                                                      does_not_strand_the_first
                                                      _jobs_tracks. That mutant
                                                      IS the original bug, so
                                                      it is the one that
                                                      matters here.
  • Manifest rebuilt from input_path when a point
    exists                                          → killed
  • Previous sidecar not passed as a second input   → killed
  • Manifest version check dropped                  → killed
  • Sources swapped, current file preferred over
    the previous sidecar                            → killed
  • Stale sidecar_index left on a revived stream    → killed
  • Worker inserts a second row instead of updating → killed
  • Superseded sidecar not unlinked                 → killed

Three of those initially SURVIVED, all for the same reason: they live in
combinations our own jobs cannot currently produce, so no end-to-end
sequence reaches them. Rather than contrive a scenario or file them as
equivalent mutants — true today, quietly false the first time matching
changes its mind about a stream — the two decisions were factored into
_plan_sources and _reannotate and are tested directly in
tests/test_revert_capture.py.

No equivalent mutants.
"""
import asyncio
import json
import os
import shutil
import subprocess

import pytest


pytestmark = pytest.mark.skipif(
    shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None,
    reason="ffmpeg/ffprobe not available",
)


def _probe(path):
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-show_streams", "-show_format",
         "-of", "json", str(path)], capture_output=True, text=True)
    assert out.returncode == 0, f"{path} is unreadable"
    return json.loads(out.stdout)


def _summarise(path):
    # codec_name is absent on some attachment streams, so it is fetched
    # rather than indexed — a KeyError here reads as a broken fixture
    # rather than as the missing field it is.
    return [(s["codec_type"], s.get("codec_name"),
             (s.get("tags") or {}).get("language"))
            for s in _probe(path)["streams"]]


@pytest.fixture
def lib(tmp_path, monkeypatch):
    """A pristine file, a mounted recycle volume, and a live database."""
    from sqlalchemy.orm import sessionmaker

    from tests.conftest import memory_engine

    from app.config import settings as app_settings
    from app.database.models import Base, MediaFile
    import app.database.session as session_mod

    media_dir = tmp_path / "media"
    media_dir.mkdir()
    recycle = tmp_path / "recycle"
    recycle.mkdir()
    monkeypatch.setattr(app_settings, "RECYCLE_DIR", str(recycle), raising=False)

    path = media_dir / "Show.mkv"
    subs = tmp_path / "s.srt"
    subs.write_text("1\n00:00:00,000 --> 00:00:01,000\nhello\n\n")
    subprocess.run(
        ["ffmpeg", "-v", "error", "-y",
         "-f", "lavfi", "-i", "testsrc=size=160x120:rate=10:duration=1",
         "-f", "lavfi", "-i", "sine=frequency=440:duration=1",
         "-f", "lavfi", "-i", "sine=frequency=880:duration=1",
         "-i", str(subs),
         "-map", "0:v", "-map", "1:a", "-map", "2:a", "-map", "3:s",
         "-metadata:s:a:0", "language=eng", "-metadata:s:a:1", "language=fre",
         "-metadata:s:s:0", "language=ger",
         "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", "-c:s", "srt",
         "-f", "matroska", str(path)], check=True)

    engine = memory_engine()
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine)
    monkeypatch.setattr(session_mod, "SessionLocal", factory)
    import app.core.worker as worker_mod
    monkeypatch.setattr(worker_mod, "SessionLocal", factory)

    db = factory()
    stat = path.stat()
    media = MediaFile(path=str(path), filename="Show.mkv",
                      directory=str(media_dir), size=stat.st_size,
                      mtime=stat.st_mtime, container="mkv", status="processed")
    db.add(media)
    db.commit()

    return {"db": db, "media": media, "path": path, "recycle": recycle,
            "tmp": tmp_path, "pristine": _summarise(path)}


def _run_job(lib, ffmpeg_args, *, job_id):
    """
    Do to the file what a real job would, with capture in the same window:
    FFmpeg writes a temp output, capture runs while both files exist, then
    the temp is swapped in and the revert point recorded.
    """
    from app.core.revert_capture import capture
    from app.core.worker import _record_revert_point

    produced = lib["tmp"] / f"job{job_id}.remuxarr_tmp"
    subprocess.run(
        ["ffmpeg", "-v", "error", "-y", "-i", str(lib["path"]),
         *ffmpeg_args, "-f", "matroska", str(produced)], check=True)

    captured, error = asyncio.run(capture(
        input_path=str(lib["path"]), produced_path=str(produced),
        file_id=lib["media"].id, job_id=job_id,
        app_cfg={"revert_enabled": True, "revert_require_point": False},
    ))
    assert error is None, error

    os.replace(produced, lib["path"])
    if captured:
        _record_revert_point(lib["media"].id, captured, str(lib["path"]))
    return captured


def _revert(lib):
    from app.core.revert_restore import restore_revert_point
    from app.database.models import RevertPoint

    lib["db"].expire_all()
    point = lib["db"].query(RevertPoint).one()
    return asyncio.run(restore_revert_point(point.id))


@pytest.fixture
def with_cover_art(tmp_path, monkeypatch):
    """
    An original whose LAST stream is not an attachment.

    Matroska cover art is stored as an image attachment, and FFmpeg's
    demuxer surfaces it as an attached_pic video stream — after every font
    attachment. That ordering is the whole point of this fixture: it is
    the shape that exposed the sidecar index bug.
    """
    from sqlalchemy.orm import sessionmaker

    from app.config import settings as app_settings
    from app.database.models import Base, MediaFile
    import app.database.session as session_mod
    from tests.conftest import memory_engine

    media_dir = tmp_path / "media"; media_dir.mkdir()
    recycle = tmp_path / "recycle"; recycle.mkdir()
    monkeypatch.setattr(app_settings, "RECYCLE_DIR", str(recycle), raising=False)

    cover = tmp_path / "cover.jpg"
    subprocess.run(
        ["ffmpeg", "-v", "error", "-y", "-f", "lavfi",
         "-i", "testsrc=size=320x240:rate=10:duration=1", "-frames:v", "1",
         str(cover)], check=True)
    font = tmp_path / "FontA.otf"
    font.write_bytes(b"FONTDATA" * 32)
    subs = tmp_path / "s.srt"
    subs.write_text("1\n00:00:00,000 --> 00:00:01,000\nhello\n\n")

    path = media_dir / "Anime.mkv"
    subprocess.run(
        ["ffmpeg", "-v", "error", "-y",
         "-f", "lavfi", "-i", "testsrc=size=160x120:rate=10:duration=1",
         "-f", "lavfi", "-i", "sine=frequency=440:duration=1",
         "-i", str(subs),
         "-map", "0:v", "-map", "1:a", "-map", "2:s",
         "-metadata:s:a:0", "language=jpn", "-metadata:s:s:0", "language=eng",
         "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", "-c:s", "srt",
         "-attach", str(font), "-metadata:s:t:0", "mimetype=font/otf",
         "-attach", str(cover), "-metadata:s:t:1", "mimetype=image/jpeg",
         "-f", "matroska", str(path)], check=True)

    kinds = [k for k, _c, _l in _summarise(path)]
    assert kinds[-1] == "video", (
        f"fixture must end with the cover art, not an attachment: {kinds}"
    )

    engine = memory_engine(); Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine)
    monkeypatch.setattr(session_mod, "SessionLocal", factory)
    import app.core.worker as worker_mod
    monkeypatch.setattr(worker_mod, "SessionLocal", factory)

    db = factory()
    stat = path.stat()
    media = MediaFile(path=str(path), filename="Anime.mkv",
                      directory=str(media_dir), size=stat.st_size,
                      mtime=stat.st_mtime, container="mkv", status="processed")
    db.add(media); db.commit()

    return {"db": db, "media": media, "path": path, "recycle": recycle,
            "tmp": tmp_path, "pristine": _summarise(path)}


def test_cover_art_after_attachments_survives_a_round_trip(with_cover_art):
    """
    Reported from a real library, and it corrupted the file quietly.

    sidecar_index is positional — the nth stream mapped becomes output
    stream n — but the Matroska muxer writes every real track first and
    attachments afterwards. Feed it [subtitle, font, cover-art] and it
    writes [subtitle, cover-art, font]. Every index from the first
    attachment onward then pointed at the wrong stream, so the restored
    file had the cover art carrying a font's filename and mimetype, and
    the fonts shifted by one.

    Only files where a NON-attachment stream follows an attachment are
    affected, which is why the earlier samples came back clean: their
    attachments were last. Matroska cover art is exactly that shape.

    Compared as a multiset rather than a sequence, because attachments are
    not ordered tracks in Matroska and the muxer places them after every
    real track regardless of the order they were mapped in. What has to
    survive is that every stream comes back, once, as itself.
    """
    _run_job(with_cover_art,
             ["-map", "0:0", "-map", "0:1", "-c", "copy"], job_id=1)

    outcome = _revert(with_cover_art)

    assert outcome.success is True, outcome.error
    assert (sorted(map(str, _summarise(with_cover_art["path"])))
            == sorted(map(str, with_cover_art["pristine"])))


def test_cover_art_comes_back_as_a_video_stream(with_cover_art):
    """
    A known limitation, pinned so it is recorded rather than discovered.

    Matroska stores cover art as an image ATTACHMENT; FFmpeg's demuxer
    surfaces it as an attached_pic video stream. The sidecar therefore
    holds it as a real video stream, and muxing it back produces a video
    stream rather than an attachment — verified directly, including that
    -disposition attached_pic does not convert it back.

    So a reverted file with cover art gains a still-image video track
    where the original had an attachment. Its filename and mimetype are
    correct and no data is lost. Restoring it properly means extracting
    the image and re-attaching it, which is a second pass over the file in
    the one operation that overwrites the user's media — worth doing
    deliberately, not as a side effect.

    If this test starts failing, FFmpeg has changed and the limitation can
    go.
    """
    _run_job(with_cover_art,
             ["-map", "0:0", "-map", "0:1", "-c", "copy"], job_id=1)
    _revert(with_cover_art)

    covers = [s for s in _streams(with_cover_art["path"])
              if _tag(s, "filename") == "cover.jpg"]

    assert len(covers) == 1, "the cover art did not come back at all"
    assert covers[0]["codec_type"] == "video"
    assert _tag(covers[0], "mimetype") == "image/jpeg"


def _streams(path):
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-show_streams", "-of", "json", str(path)],
        capture_output=True, text=True)
    return json.loads(out.stdout)["streams"]


def _tag(stream, name):
    """
    Case-insensitive tag lookup.

    Matroska stores an attachment's filename as a structural field, which
    FFmpeg reports lowercase. Written back as an ordinary tag it comes out
    uppercase. Matching on case would make this assert the container
    convention rather than the value.
    """
    tags = stream.get("tags") or {}
    return next((v for k, v in tags.items() if k.lower() == name), None)


def test_restored_attachments_keep_their_own_filenames(with_cover_art):
    """
    The visible symptom of the index bug: the cover art came back carrying
    a FONT's filename and mimetype. Stream identity and stream metadata
    have to land on the same stream, and an assumption about stream order
    breaks that quietly rather than loudly — every file still muxed, every
    job still reported success.
    """
    _run_job(with_cover_art,
             ["-map", "0:0", "-map", "0:1", "-c", "copy"], job_id=1)
    _revert(with_cover_art)

    named = {s["codec_type"]: _tag(s, "filename")
             for s in _streams(with_cover_art["path"])
             if _tag(s, "filename")}

    assert named.get("attachment") == "FontA.otf"
    assert named.get("video") == "cover.jpg", (
        f"the cover art came back named {named.get('video')!r}"
    )


@pytest.fixture
def dual_audio(tmp_path, monkeypatch):
    """
    A dual-audio release: Japanese default, English dub, identical codec
    and channel layout. The shape that exposed the matching bug.
    """
    from sqlalchemy.orm import sessionmaker

    from tests.conftest import memory_engine

    from app.config import settings as app_settings
    from app.database.models import Base, MediaFile
    import app.database.session as session_mod

    media_dir = tmp_path / "media"
    media_dir.mkdir()
    recycle = tmp_path / "recycle"
    recycle.mkdir()
    monkeypatch.setattr(app_settings, "RECYCLE_DIR", str(recycle), raising=False)

    path = media_dir / "Spy.mkv"
    subs = tmp_path / "s.srt"
    subs.write_text("1\n00:00:00,000 --> 00:00:01,000\nhello\n\n")
    subprocess.run(
        ["ffmpeg", "-v", "error", "-y",
         "-f", "lavfi", "-i", "testsrc=size=160x120:rate=10:duration=1",
         "-f", "lavfi", "-i", "sine=frequency=440:duration=1",
         "-f", "lavfi", "-i", "sine=frequency=880:duration=1",
         "-i", str(subs),
         "-map", "0:v", "-map", "1:a", "-map", "2:a", "-map", "3:s",
         "-metadata:s:a:0", "language=jpn", "-metadata:s:a:1", "language=eng",
         "-metadata:s:s:0", "language=eng",
         "-disposition:a:0", "default+original", "-disposition:a:1", "dub",
         "-c:v", "libx264", "-pix_fmt", "yuv420p",
         "-c:a", "aac", "-ac", "2", "-ar", "48000", "-c:s", "srt",
         "-f", "matroska", str(path)], check=True)

    engine = memory_engine()
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine)
    monkeypatch.setattr(session_mod, "SessionLocal", factory)
    import app.core.worker as worker_mod
    monkeypatch.setattr(worker_mod, "SessionLocal", factory)

    db = factory()
    stat = path.stat()
    media = MediaFile(path=str(path), filename="Spy.mkv",
                      directory=str(media_dir), size=stat.st_size,
                      mtime=stat.st_mtime, container="mkv", status="processed")
    db.add(media)
    db.commit()

    return {"db": db, "media": media, "path": path, "recycle": recycle,
            "tmp": tmp_path, "pristine": _summarise(path)}


def test_the_dropped_audio_track_is_the_one_stored(dual_audio):
    """
    Reported from a real library. Keeping English and dropping Japanese
    makes FFmpeg promote English to default, since the default track was
    the one removed — so the kept track stops matching the original
    exactly, through no decision of ours.

    Both audio tracks then fell to a pass that ignored language, where
    Japanese claimed the match by coming first in the file. The sidecar
    stored English, still present in the processed file, and the Japanese
    audio was gone for good. The sidecar had the right stream count and a
    plausible size throughout.
    """
    _run_job(dual_audio, ["-map", "0:0", "-map", "0:2", "-c", "copy"], job_id=1)

    languages = [lang for kind, _codec, lang in _summarise(dual_audio["path"])
                 if kind == "audio"]
    assert languages == ["eng"], "fixture did not drop the Japanese track"

    outcome = _revert(dual_audio)

    assert outcome.success is True, outcome.error
    restored = [(k, lang) for k, _c, lang in _summarise(dual_audio["path"])
                if k == "audio"]
    assert restored == [("audio", "jpn"), ("audio", "eng")], (
        "the Japanese track was not restored — the sidecar stored the wrong one"
    )


# ── The regression ───────────────────────────────────────────────────────────

def test_a_metadata_only_job_does_not_strand_the_first_jobs_tracks(lib):
    """
    The exact reported scenario: drop subtitles, then fix an audio
    language tag. The second job destroys nothing but rewrites the file,
    which under the old design invalidated the first job's point forever
    and left its subtitles unreachable on disk.
    """
    _run_job(lib, ["-map", "0:0", "-map", "0:1", "-map", "0:2", "-c", "copy"],
             job_id=1)
    assert "subtitle" not in [k for k, _c, _l in _summarise(lib["path"])]

    _run_job(lib, ["-map", "0", "-c", "copy",
                   "-metadata:s:a:1", "language=deu"], job_id=2)

    outcome = _revert(lib)

    assert outcome.success is True, outcome.error
    assert _summarise(lib["path"]) == lib["pristine"], (
        "the subtitles dropped by job 1 were not restored"
    )


def test_the_metadata_change_itself_is_reverted(lib):
    """
    The other half of the same scenario. A re-tag leaves no trace in any
    sidecar — no track was removed — so only the manifest remembers what
    the tag used to say. If revert copied metadata through instead of
    rewriting it from the manifest, this would be a partial undo that
    looked complete: the dropped tracks back, the re-tag still applied.
    """
    _run_job(lib, ["-map", "0:0", "-map", "0:1", "-map", "0:2", "-c", "copy"],
             job_id=1)
    _run_job(lib, ["-map", "0", "-c", "copy",
                   "-metadata:s:a:0", "language=deu"], job_id=2)

    _revert(lib)

    languages = [lang for kind, _codec, lang in _summarise(lib["path"])
                 if kind == "audio"]
    assert "deu" not in languages, (
        f"the job's language tag survived the revert: {languages}"
    )
    assert languages == [lang for kind, _c, lang in lib["pristine"]
                         if kind == "audio"]


def test_a_metadata_only_job_refreshes_the_fingerprint(lib):
    """
    The mechanism behind the test above. The point must end up describing
    the file as the LATEST job left it, or its sentinel refuses.
    """
    from app.database.models import RevertPoint

    _run_job(lib, ["-map", "0:0", "-map", "0:1", "-map", "0:2", "-c", "copy"],
             job_id=1)
    _run_job(lib, ["-map", "0", "-c", "copy",
                   "-metadata:s:a:1", "language=deu"], job_id=2)

    lib["db"].expire_all()
    point = lib["db"].query(RevertPoint).one()
    stat = lib["path"].stat()

    assert point.processed_size == stat.st_size
    assert point.processed_mtime == stat.st_mtime


# ── Extending ────────────────────────────────────────────────────────────────

def test_two_destructive_jobs_still_restore_the_pristine_original(lib):
    """
    Neither input alone has everything: the French track job 2 dropped is
    still in the file it was handed, while the subtitles job 1 dropped
    exist only in the previous sidecar.
    """
    _run_job(lib, ["-map", "0:0", "-map", "0:1", "-map", "0:2", "-c", "copy"],
             job_id=1)
    _run_job(lib, ["-map", "0:0", "-map", "0:1", "-c", "copy"], job_id=2)
    assert len(_summarise(lib["path"])) == 2

    outcome = _revert(lib)

    assert outcome.success is True, outcome.error
    assert _summarise(lib["path"]) == lib["pristine"]


def test_there_is_only_ever_one_revert_point_per_file(lib):
    from app.database.models import RevertPoint

    _run_job(lib, ["-map", "0:0", "-map", "0:1", "-map", "0:2", "-c", "copy"],
             job_id=1)
    _run_job(lib, ["-map", "0:0", "-map", "0:1", "-c", "copy"], job_id=2)

    lib["db"].expire_all()
    assert lib["db"].query(RevertPoint).count() == 1


def test_the_superseded_sidecar_is_removed(lib):
    """
    Extending writes a new sidecar rather than appending to the old one,
    so the old file has to go — otherwise every reprocessed file leaves a
    permanent copy of its own history on the volume.
    """
    first = _run_job(lib, ["-map", "0:0", "-map", "0:1", "-map", "0:2",
                           "-c", "copy"], job_id=1)
    second = _run_job(lib, ["-map", "0:0", "-map", "0:1", "-c", "copy"],
                      job_id=2)

    assert first.sidecar_path != second.sidecar_path
    assert not os.path.exists(first.sidecar_path), "superseded sidecar left behind"
    assert os.path.exists(second.sidecar_path)


def test_the_manifest_still_describes_the_pristine_original(lib):
    """
    Rebuilt from input_path on the second job, it would describe the
    already-processed file — and reverting would only ever undo the most
    recent job while the earlier losses stayed gone.
    """
    from app.database.models import RevertPoint

    _run_job(lib, ["-map", "0:0", "-map", "0:1", "-map", "0:2", "-c", "copy"],
             job_id=1)
    _run_job(lib, ["-map", "0:0", "-map", "0:1", "-c", "copy"], job_id=2)

    lib["db"].expire_all()
    manifest = json.loads(lib["db"].query(RevertPoint).one().manifest)

    kinds = [s["type"] for s in manifest["streams"]]
    assert kinds == ["video", "audio", "audio", "subtitle"], (
        "the manifest no longer describes the four-stream original"
    )


def test_surviving_streams_carry_no_stale_sidecar_index(lib):
    """
    Every capture rewrites the sidecar, so an index from the previous one
    points somewhere arbitrary. Restore prefers the sidecar when both
    annotations are present, so a stale one silently sources a track from
    the wrong place.
    """
    from app.database.models import RevertPoint

    _run_job(lib, ["-map", "0:0", "-map", "0:1", "-map", "0:2", "-c", "copy"],
             job_id=1)
    _run_job(lib, ["-map", "0", "-c", "copy"], job_id=2)

    lib["db"].expire_all()
    manifest = json.loads(lib["db"].query(RevertPoint).one().manifest)

    for stream in manifest["streams"]:
        if stream.get("processed_index") is not None:
            assert stream.get("sidecar_index") is None, (
                f"stream {stream['index']} survives but still claims a "
                f"sidecar slot"
            )


def test_three_jobs_still_restore_the_pristine_original(lib):
    """Extending has to compose, not just work once."""
    _run_job(lib, ["-map", "0:0", "-map", "0:1", "-map", "0:2", "-c", "copy"],
             job_id=1)
    _run_job(lib, ["-map", "0", "-c", "copy",
                   "-metadata:s:a:1", "language=deu"], job_id=2)
    _run_job(lib, ["-map", "0:0", "-map", "0:1", "-c", "copy"], job_id=3)

    outcome = _revert(lib)

    assert outcome.success is True, outcome.error
    assert _summarise(lib["path"]) == lib["pristine"]
