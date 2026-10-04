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

The look-alike tests at the end of this file were added with
revert._pair_pass and check content by packet hash, because their failure
is the right tag on the wrong track. The four that revert failed against
the matching that paired an uneven group in order. Between them they kill
that mutant, the rule being switched off in the exact pass, and the
rejected refinement of skipping streams the sidecar already holds — the
last one only here, via
test_an_earlier_lookalike_does_not_hide_a_later_loss.
test_a_point_that_stored_both_lookalikes_still_matches_exactly is a guard
rather than a mutant's target: it passed before the change too, and
pins that reporting a group lost does not make attach refuse its own file.
Mutation detail for the matching itself is recorded in
test_revert_manifest.py.

The track-order tests at the end of this file came with
ffmpeg._sparse_openings, and look at the written file with ffprobe: how
far back in time does any packet fall, in byte order. All five fail
against the code before it, on FFmpeg 8.1 (the image's build) and on
Ubuntu's 6.1. Mutants run against the suite as it stood first, all
surviving it, all killed now:

  • No second openings at all                    → killed
  • The restore reading sparse streams from the
    main inputs                                  → killed
  • A two-file sidecar not remapping             → killed
  • Only subtitles treated as sparse             → killed
  • Only attachments treated as sparse           → killed
  • Cover art not treated as sparse              → killed

The two-file sidecar mutant is killed here only by
test_a_second_jobs_sidecar_stays_in_order, and the cover-art one only by
test_cover_art_stays_in_order; both are also killed by unit tests on the
command line. A second opening of the wrong file was already killed by
twenty existing tests, and every video and audio stream reopened as well
by three existing tests.

The AC3 Forge tests at the end run the real forge. Three of the four fail
against the forge as it was before capture, with the revert point reading
as "modified since it was processed"; the fourth, a file with no point
gaining none, is a guard and passed before too. Their mutants are recorded
in test_forge_orchestration.

Their fixture first used memory_engine(), whose single shared connection
let the forge's progress writes end the revert-point transaction from
another thread; about one run in twenty failed. It now uses a database
file. The mutants these four tests kill were mapped before and after that
change and are the same: the hook not given to the run, the runner not
forwarding it, the point not recorded, and a capture refusal ignored.
"""
import asyncio
import json
import pathlib
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


def _run_job(lib, ffmpeg_args, *, job_id, extracts=None):
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

    # Which extraction targets do NOT yet exist. Taken before the job, as
    # the worker does, because afterwards every target exists.
    will_create = [p for p in (extracts or []) if not os.path.exists(p)]

    captured, error = asyncio.run(capture(
        input_path=str(lib["path"]), produced_path=str(produced),
        file_id=lib["media"].id, job_id=job_id,
        app_cfg={"revert_enabled": True, "revert_require_point": False},
    ))
    assert error is None, error

    # The swap. Extracted subtitles are staged outputs too, so they reach
    # their final paths HERE — after capture, not before. Writing them
    # earlier is what made the first version of this test pass against
    # code that recorded nothing.
    os.replace(produced, lib["path"])
    for path in (extracts or []):
        with open(path, "w") as f:
            f.write("extracted by the job\n")

    if captured:
        _record_revert_point(lib["media"].id, captured, str(lib["path"]),
                             will_create)
    return captured


def _revert(lib):
    from app.core.revert_restore import restore_revert_point
    from app.database.models import RevertPoint

    lib["db"].expire_all()
    point = lib["db"].query(RevertPoint).one()
    return asyncio.run(restore_revert_point(point.id))


@pytest.fixture
def mp4_with_text_subtitle(tmp_path, monkeypatch):
    """
    An MP4 carrying a mov_text subtitle — the shape that produced an
    unusable revert point.
    """
    from sqlalchemy.orm import sessionmaker

    from app.config import settings as app_settings
    from app.database.models import Base, MediaFile
    import app.database.session as session_mod
    from tests.conftest import memory_engine

    media_dir = tmp_path / "media"; media_dir.mkdir()
    recycle = tmp_path / "recycle"; recycle.mkdir()
    monkeypatch.setattr(app_settings, "RECYCLE_DIR", str(recycle), raising=False)

    subs = tmp_path / "s.srt"
    subs.write_text("1\n00:00:00,000 --> 00:00:01,000\nbonjour\n\n")
    path = media_dir / "Film.mp4"
    subprocess.run(
        ["ffmpeg", "-v", "error", "-y",
         "-f", "lavfi", "-i", "testsrc=size=160x120:rate=10:duration=1",
         "-f", "lavfi", "-i", "sine=frequency=440:duration=1",
         "-i", str(subs),
         "-map", "0:v", "-map", "1:a", "-map", "2:s",
         "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac",
         "-c:s", "mov_text", "-metadata:s:s:0", "language=fre",
         "-f", "mp4", str(path)], check=True)

    engine = memory_engine(); Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine)
    monkeypatch.setattr(session_mod, "SessionLocal", factory)
    import app.core.worker as worker_mod
    monkeypatch.setattr(worker_mod, "SessionLocal", factory)

    db = factory()
    stat = path.stat()
    media = MediaFile(path=str(path), filename="Film.mp4",
                      directory=str(media_dir), size=stat.st_size,
                      mtime=stat.st_mtime, container="mp4", status="processed")
    db.add(media); db.commit()

    return {"db": db, "media": media, "path": path, "recycle": recycle,
            "tmp": tmp_path, "pristine": _summarise(path)}


def test_an_mp4_subtitle_can_actually_be_restored(mp4_with_text_subtitle):
    """
    Matroska cannot hold mov_text, so capture converts it to SubRip on the
    way into the sidecar. Restoring with a flat -c copy then tried to mux
    SubRip back into MP4, which FFmpeg refuses at header write — so the
    revert point existed, listed as restorable, and failed the moment
    anyone used it. Permanently: the point is not consumed on failure, so
    every retry failed identically.

    Routine rather than exotic under shipped defaults.
    keep_subtitle_languages is ["eng"], so any other language is dropped,
    and extract_text_subtitles_to_srt removes every extracted text
    subtitle from the mux.
    """
    _run_job(mp4_with_text_subtitle,
             ["-map", "0:0", "-map", "0:1", "-c", "copy", "-f", "mp4"],
             job_id=1)
    kinds = [k for k, _c, _l in _summarise(mp4_with_text_subtitle["path"])]
    assert "subtitle" not in kinds, "fixture did not drop the subtitle"

    outcome = _revert(mp4_with_text_subtitle)

    assert outcome.success is True, outcome.error
    assert _summarise(mp4_with_text_subtitle["path"]) == \
        mp4_with_text_subtitle["pristine"]


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


def test_extending_a_point_refreshes_its_age(lib):
    """
    created_at is what retention ages against, and an extend rewrites the
    sidecar entirely. Left at the first job's timestamp, a file last
    processed more than revert_retention_days ago gets a fresh sidecar
    that the very next sweep — sixty seconds later — deletes as expired.
    The user has no revert point for work they just did, and nothing in
    the UI says why.
    """
    from datetime import timedelta

    from app.core.timeutil import utcnow_naive
    from app.database.models import RevertPoint

    _run_job(lib, ["-map", "0:0", "-map", "0:1", "-map", "0:2", "-c", "copy"],
             job_id=1)

    # Age the point past any plausible retention window.
    lib["db"].expire_all()
    point = lib["db"].query(RevertPoint).one()
    point.created_at = utcnow_naive() - timedelta(days=90)
    lib["db"].commit()

    _run_job(lib, ["-map", "0:0", "-map", "0:1", "-c", "copy"], job_id=2)

    lib["db"].expire_all()
    refreshed = lib["db"].query(RevertPoint).one().created_at
    assert refreshed > utcnow_naive() - timedelta(minutes=5), (
        f"the extended point still dates from the first job ({refreshed}); "
        f"retention will delete it on the next sweep"
    )


def test_extracted_subtitles_are_removed_when_the_subtitle_comes_back(lib):
    """
    extract_text_subtitles_to_srt writes .srt files next to the media AND
    removes those subtitles from the mux. A revert re-embeds them, so
    leaving the files behind gives the user the same subtitle twice —
    which players list as duplicate tracks.

    Only the file the job created is removed. The other .srt here stands
    in for one Bazarr put there first, which the job overwrote rather than
    created: deleting that would destroy something Remuxarr never made.
    """
    mine = lib["path"].with_suffix(".ger.srt")      # the job will create this
    theirs = lib["path"].with_suffix(".eng.srt")    # already there; overwritten
    theirs.write_text("downloaded by Bazarr")

    _run_job(lib, ["-map", "0:0", "-map", "0:1", "-map", "0:2", "-c", "copy"],
             job_id=1, extracts=[str(mine), str(theirs)])

    outcome = _revert(lib)

    assert outcome.success is True, outcome.error
    assert not mine.exists(), "the extracted subtitle file was left behind"
    assert theirs.exists(), "a subtitle file the job did not create was deleted"
    assert _summarise(lib["path"]) == lib["pristine"]


def test_a_renamed_subtitle_is_still_removed_on_revert(lib):
    """
    Reported after the rename shipped. Subtitle Language Review renames an
    extracted sidecar to carry the language the user chose; the revert
    point still recorded the old name, so the match failed and the revert
    re-embedded the subtitle while leaving the file on disk. The user got
    it twice — the exact duplication that cleanup exists to prevent.

    The rename now follows through into the manifest, keeping the
    fingerprint, which os.rename does not change.
    """
    from app.api.routes._language_review import _rename_extracted_subtitle

    extracted = lib["path"].with_suffix(".und.srt")

    _run_job(lib, ["-map", "0:0", "-map", "0:1", "-map", "0:2", "-c", "copy"],
             job_id=1, extracts=[str(extracted)])

    class _Flag:
        file_id = lib["media"].id
        detected_language = "und"
        extracted_path = str(extracted)

    flag = _Flag()
    renamed = _rename_extracted_subtitle(flag, "eng", lib["db"])
    lib["db"].commit()

    # 2-letter, matching _build_srt_path — the manual rename and
    # the automatic extraction path have to agree on the name.
    assert renamed and renamed.endswith(".en.srt")
    assert not extracted.exists()
    assert pathlib.Path(renamed).exists()

    outcome = _revert(lib)

    assert outcome.success is True, outcome.error
    assert not pathlib.Path(renamed).exists(), (
        "the renamed subtitle was left behind, so it is now embedded AND "
        "on disk"
    )


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


# ── Look-alike tracks: which one survived? ─────────────────────────────────────
#
# The tests above compare languages and codecs, which is what the earlier
# bugs got wrong. The ones below cannot: their failure is the RIGHT
# metadata on the WRONG content — a French slot holding English audio,
# written back with a French tag. So every track carries distinct content
# (a different tone, a different subtitle line) and the check is on the
# packets themselves, which a stream copy carries through unchanged.


def _packets(path, kind):
    """One hash per stream of `kind` ("a" or "s"), over its packets."""
    out = subprocess.run(
        ["ffmpeg", "-v", "error", "-i", str(path), "-map", f"0:{kind}",
         "-c", "copy", "-f", "streamhash", "-hash", "md5", "-"],
        capture_output=True, text=True, check=True).stdout
    return [line.split("=", 1)[1] for line in out.strip().splitlines()]


def _library(tmp_path, monkeypatch, *, audio=(), subtitles=()):
    """
    A file whose audio is `audio` — (frequency, language) pairs — and whose
    subtitles are `subtitles` — (text, language) pairs — with no default
    flag on any of them, so tracks that share a language and codec really
    are identical in everything the matching compares.
    """
    from sqlalchemy.orm import sessionmaker

    from tests.conftest import memory_engine

    from app.config import settings as app_settings
    from app.database.models import Base, MediaFile
    import app.database.session as session_mod
    import app.core.worker as worker_mod

    media_dir = tmp_path / "media"
    media_dir.mkdir()
    recycle = tmp_path / "recycle"
    recycle.mkdir()
    monkeypatch.setattr(app_settings, "RECYCLE_DIR", str(recycle), raising=False)

    cmd = ["ffmpeg", "-v", "error", "-y",
           "-f", "lavfi", "-i", "testsrc=size=160x120:rate=10:duration=1"]
    maps = ["-map", "0:v"]
    meta = []
    n = 1
    for i, (freq, lang) in enumerate(audio):
        cmd += ["-f", "lavfi", "-i", f"sine=frequency={freq}:duration=1"]
        maps += ["-map", f"{n}:a"]
        meta += [f"-disposition:a:{i}", "0"]
        if lang:
            meta += [f"-metadata:s:a:{i}", f"language={lang}"]
        n += 1
    for i, (text, lang) in enumerate(subtitles):
        srt = tmp_path / f"sub{i}.srt"
        srt.write_text(f"1\n00:00:00,000 --> 00:00:01,000\n{text}\n\n")
        cmd += ["-i", str(srt)]
        maps += ["-map", f"{n}:s"]
        meta += [f"-disposition:s:{i}", "0"]
        if lang:
            meta += [f"-metadata:s:s:{i}", f"language={lang}"]
        n += 1

    path = media_dir / "Show.mkv"
    subprocess.run(cmd + maps + meta + [
        "-c:v", "libx264", "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-ac", "2", "-ar", "48000", "-c:s", "srt",
        "-f", "matroska", str(path)], check=True)

    engine = memory_engine()
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine)
    monkeypatch.setattr(session_mod, "SessionLocal", factory)
    monkeypatch.setattr(worker_mod, "SessionLocal", factory)

    db = factory()
    stat = path.stat()
    media = MediaFile(path=str(path), filename="Show.mkv",
                      directory=str(media_dir), size=stat.st_size,
                      mtime=stat.st_mtime, container="mkv", status="processed")
    db.add(media)
    db.commit()

    return {"db": db, "media": media, "path": path, "recycle": recycle,
            "tmp": tmp_path, "pristine": _summarise(path),
            "audio": _packets(path, "a") if audio else [],
            "subtitles": _packets(path, "s") if subtitles else []}


def _assert_pristine_content(lib, outcome):
    assert outcome.success is True, outcome.error
    assert _summarise(outcome.restored_path) == lib["pristine"]
    if lib["audio"]:
        assert _packets(outcome.restored_path, "a") == lib["audio"], (
            "the audio came back with the right tags on the wrong content"
        )
    if lib["subtitles"]:
        assert _packets(outcome.restored_path, "s") == lib["subtitles"], (
            "the subtitles came back with the right tags on the wrong content"
        )


def test_a_retag_and_a_drop_in_one_job_restore_the_right_tracks(tmp_path, monkeypatch):
    """
    The job keeps the untagged English tracks, tags them English, and
    drops the French ones, which share codec and layout. Matching used to
    pair the French originals with the English survivors, store English a
    second time and never store French: the revert succeeded and played
    English in the French slot.
    """
    lib = _library(tmp_path, monkeypatch,
                   audio=[(440, "fre"), (880, None)],
                   subtitles=[("bonjour", "fre"), ("hello", None)])

    _run_job(lib, ["-map", "0:0", "-map", "0:2", "-map", "0:4", "-c", "copy",
                   "-metadata:s:a:0", "language=eng",
                   "-metadata:s:s:0", "language=eng"], job_id=1)

    _assert_pristine_content(lib, _revert(lib))


def test_a_retag_after_a_drop_keeps_the_dropped_tracks(tmp_path, monkeypatch):
    """
    The same loss spread over two jobs, which is the routine shape under
    the shipped defaults: undefined tags are left alone, so the first job
    only drops French, and the tag is fixed later from the review page.

    The first job's revert point was correct. The second destroyed nothing
    — and rebuilt the sidecar without the French tracks, because matching
    paired the stored French original with the re-tagged English survivor.
    """
    lib = _library(tmp_path, monkeypatch,
                   audio=[(440, "fre"), (880, None)],
                   subtitles=[("bonjour", "fre"), ("hello", None)])

    _run_job(lib, ["-map", "0:0", "-map", "0:2", "-map", "0:4", "-c", "copy"],
             job_id=1)
    _run_job(lib, ["-map", "0", "-c", "copy",
                   "-metadata:s:a:0", "language=eng",
                   "-metadata:s:s:0", "language=eng"], job_id=2)

    _assert_pristine_content(lib, _revert(lib))


def test_an_earlier_lookalike_does_not_hide_a_later_loss(tmp_path, monkeypatch):
    """
    The first job cannot tell French from the re-tagged English track, so
    it stores both — including the English one that is still in the file.
    The second drops Spanish, a third look-alike.

    The obvious refinement is to stop looking for tracks the sidecar
    already holds, since they were lost before. That leaves the English
    survivor unclaimed, and Spanish takes it: recorded as still present,
    never stored, and the revert plays English in the Spanish slot.
    """
    lib = _library(tmp_path, monkeypatch,
                   audio=[(440, "fre"), (880, None), (660, "spa")])

    _run_job(lib, ["-map", "0:0", "-map", "0:2", "-map", "0:3", "-c", "copy",
                   "-metadata:s:a:0", "language=eng"], job_id=1)
    _run_job(lib, ["-map", "0:0", "-map", "0:1", "-c", "copy"], job_id=2)

    _assert_pristine_content(lib, _revert(lib))


def test_the_first_of_two_identical_subtitles_can_be_removed(tmp_path, monkeypatch):
    """
    Two English subtitles with no title and no flags — a full track and a
    forced-only one, say — are identical to everything the matching
    compares. A review answer can still remove either: answers are stored
    per track, with look-alikes numbered apart (descriptors_by_stream),
    and analyze_file drops exactly the one named.

    Removing the first used to store the second twice and lose the first.
    """
    lib = _library(tmp_path, monkeypatch,
                   subtitles=[("full subtitles", "eng"), ("forced only", "eng")])

    _run_job(lib, ["-map", "0:0", "-map", "0:2", "-c", "copy"], job_id=1)

    _assert_pristine_content(lib, _revert(lib))


def test_a_point_that_stored_both_lookalikes_still_matches_exactly(tmp_path, monkeypatch):
    """
    Attaching a detached point re-runs the matching against the candidate
    file, and refuses if a track the original had is neither in that file
    nor in the sidecar. Reporting a group of look-alikes lost must not
    make that check refuse the very file the point was captured from —
    which is what a renamed file is, byte for byte.
    """
    from app.core.revert_match import EXACT, assess
    from app.database.models import RevertPoint

    lib = _library(tmp_path, monkeypatch,
                   audio=[(440, "fre"), (880, None)])
    _run_job(lib, ["-map", "0:0", "-map", "0:2", "-c", "copy",
                   "-metadata:s:a:0", "language=eng"], job_id=1)

    lib["db"].expire_all()
    point = lib["db"].query(RevertPoint).one()
    result = assess(point, str(lib["path"]), _probe(lib["path"]))

    assert result.tier == EXACT, result.reasons


# ── Track order in the written file ────────────────────────────────────────────
#
# A restore reads two files, and a later job's sidecar does too. Mixing
# them, FFmpeg used to write a file whose tracks were not in time order:
# subtitles, fonts or a whole audio track sat in a second pass through the
# timeline after the rest, and a player that seeks to a video frame never
# met them — styled subtitles "worked at startup and vanished on seeking".
# See ffmpeg._sparse_openings. These tests look at the file itself: in file
# order, how far does any packet's time ever step backwards?
#
# The fixtures are a minute long. A track out of order by a whole file, or
# by the gap between two subtitle lines, needs that much to show; at a few
# seconds it stays inside FFmpeg's ten-second interleaving window and the
# file comes out in order whatever the code does.

ORDER_SECONDS = 60

ASS_HEADER = """[Script Info]
ScriptType: v4.00+

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Default,Fixture Sans,20,&H00FFFFFF,&H000000FF,&H00000000,&H00000000,0,0,0,0,100,100,0,0,1,2,0,2,10,10,10,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""


def _worst_step_back(path):
    """
    The furthest any packet's time falls behind one written before it, in
    seconds, reading the file in byte order. A well-formed file stays near
    zero; one with a track in a second pass reports most of its length.
    Cover art has no timestamp and is skipped.
    """
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "packet=dts_time,pts_time,pos",
         "-of", "json", str(path)], capture_output=True, text=True, check=True).stdout
    packets = [p for p in json.loads(out).get("packets", [])
               if p.get("pos") not in (None, "N/A")]
    packets.sort(key=lambda p: int(p["pos"]))
    worst, latest = 0.0, float("-inf")
    for p in packets:
        raw = p.get("dts_time")
        if raw in (None, "N/A"):
            raw = p.get("pts_time")
        if raw in (None, "N/A"):
            continue
        t = float(raw)
        worst = max(worst, latest - t)
        latest = max(latest, t)
    return worst


def _ass(tmp_path, name, starts):
    path = tmp_path / name
    path.write_text(ASS_HEADER + "".join(
        f"Dialogue: 0,0:00:{s:02d}.00,0:00:{s + 3:02d}.00,Default,,0,0,0,,Line at {s}s\n"
        for s in starts))
    return path


def _ordered_library(tmp_path, monkeypatch, *, audio=("jpn", "eng"),
                     subtitle_starts=(5, 20, 35, 50), font=True,
                     cover_art=False, container="mkv"):
    """
    A minute-long file to put through jobs and a revert: video, one audio
    track per language in `audio`, a styled subtitle (unless
    `subtitle_starts` is empty) with a font attached, or instead an MP4
    with cover art. Built by FFmpeg, and checked to be in order itself, so
    a disordered restore cannot be blamed on the fixture.
    """
    from sqlalchemy.orm import sessionmaker

    from tests.conftest import memory_engine

    from app.config import settings as app_settings
    from app.database.models import Base, MediaFile
    import app.database.session as session_mod
    import app.core.worker as worker_mod

    media_dir = tmp_path / "media"
    media_dir.mkdir()
    recycle = tmp_path / "recycle"
    recycle.mkdir()
    monkeypatch.setattr(app_settings, "RECYCLE_DIR", str(recycle), raising=False)

    seconds = ORDER_SECONDS
    cmd = ["ffmpeg", "-v", "error", "-y",
           "-f", "lavfi", "-i", f"testsrc=size=64x36:rate=5:duration={seconds}"]
    maps, meta = ["-map", "0:v"], []
    for i, lang in enumerate(audio):
        cmd += ["-f", "lavfi", "-i",
                f"sine=frequency={440 + 220 * i}:duration={seconds}"]
        maps += ["-map", f"{i + 1}:a"]
        meta += [f"-metadata:s:a:{i}", f"language={lang}"]
    n = len(audio) + 1
    if subtitle_starts:
        cmd += ["-i", str(_ass(tmp_path, "subs.ass", subtitle_starts))]
        maps += ["-map", f"{n}:s"]
        meta += ["-metadata:s:s:0", "language=eng"]
        n += 1
    if cover_art:
        cover = tmp_path / "cover.png"
        subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi",
                        "-i", "color=c=red:size=64x64", "-frames:v", "1",
                        str(cover)], check=True)
        cmd += ["-i", str(cover)]
        maps += ["-map", f"{n}:v"]
        meta += ["-c:v:1", "png", "-disposition:v:1", "attached_pic"]
    if font:
        fake_font = tmp_path / "Fixture.ttf"
        fake_font.write_bytes(b"\0\1\0\0 not a real font, an opaque attachment")
        meta += ["-attach", str(fake_font), "-metadata:s:t:0",
                 "mimetype=application/x-truetype-font"]

    path = media_dir / f"Show.{container}"
    fmt = {"mkv": "matroska", "mp4": "mp4"}[container]
    subprocess.run(cmd + maps + [
        "-c:v:0", "libx264", "-preset", "ultrafast", "-g", "25",
        "-pix_fmt", "yuv420p", "-c:a", "aac", "-ac", "1", "-b:a", "16k",
        "-c:s", "ass" if container == "mkv" else "mov_text", *meta,
        "-f", fmt, str(path)], check=True)
    assert _worst_step_back(path) < 1.0, "the fixture itself is out of order"

    engine = memory_engine()
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine)
    monkeypatch.setattr(session_mod, "SessionLocal", factory)
    monkeypatch.setattr(worker_mod, "SessionLocal", factory)

    db = factory()
    stat = path.stat()
    media = MediaFile(path=str(path), filename=path.name,
                      directory=str(media_dir), size=stat.st_size,
                      mtime=stat.st_mtime, container=container,
                      status="processed")
    db.add(media)
    db.commit()

    return {"db": db, "media": media, "path": path, "recycle": recycle,
            "tmp": tmp_path, "pristine": _summarise(path)}


def _run_job_into(lib, ffmpeg_args, *, job_id, fmt, suffix):
    """
    _run_job for a job whose output is not Matroska, or which changes the
    file's extension the way a conversion to MP4 does.
    """
    from app.core.revert_capture import capture
    from app.core.worker import _record_revert_point

    produced = lib["tmp"] / f"job{job_id}.remuxarr_tmp"
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", str(lib["path"]),
                    *ffmpeg_args, "-f", fmt, str(produced)], check=True)
    captured, error = asyncio.run(capture(
        input_path=str(lib["path"]), produced_path=str(produced),
        file_id=lib["media"].id, job_id=job_id,
        app_cfg={"revert_enabled": True, "revert_require_point": False},
    ))
    assert error is None, error

    final = lib["path"].with_suffix(suffix)
    os.replace(produced, final)
    if final != lib["path"]:
        os.remove(lib["path"])
        lib["path"] = final
        lib["media"].path = str(final)
        lib["media"].filename = final.name
        lib["db"].commit()
    _record_revert_point(lib["media"].id, captured, str(final), [])


def _assert_restored_in_order(lib, outcome):
    assert outcome.success is True, outcome.error
    assert _summarise(outcome.restored_path) == lib["pristine"]
    step = _worst_step_back(outcome.restored_path)
    assert step < 1.0, (
        f"the restored file steps {step:.1f}s back in time — a track was "
        f"written after the rest, where a seeking player never finds it"
    )


def test_subtitles_and_fonts_from_the_sidecar_stay_in_order(tmp_path, monkeypatch):
    """
    The case that was reported: an MKV with Japanese and English audio,
    styled subtitles and a font is converted to MP4, keeping the English
    audio. MP4 cannot hold the subtitle or the font, so the sidecar gets
    those and the Japanese audio. The revert read the sidecar through to
    the end before the MP4, and the video and English audio came back in a
    second pass after it — subtitles on screen from the start, and gone
    after the first seek, because seeking lands in the video's pass.

    The Japanese audio is what makes it fail. With only sparse streams in
    the sidecar, reading it first costs nothing and the file comes out in
    order anyway.
    """
    lib = _ordered_library(tmp_path, monkeypatch)

    _run_job_into(lib, ["-map", "0:0", "-map", "0:2", "-c", "copy"],
                  job_id=1, fmt="mp4", suffix=".mp4")

    _assert_restored_in_order(lib, _revert(lib))


def test_an_audio_track_from_the_sidecar_stays_in_order(tmp_path, monkeypatch):
    """
    The reverse split: the job dropped the Japanese audio and kept the
    subtitle and font, so the restore reads those from the processed file
    and the audio from the sidecar. The audio was the track left behind.
    """
    lib = _ordered_library(tmp_path, monkeypatch)

    _run_job(lib, ["-map", "0:0", "-map", "0:2", "-map", "0:3",
                   "-map", "0:t", "-c", "copy"], job_id=1)

    _assert_restored_in_order(lib, _revert(lib))


def test_a_subtitle_track_that_ends_early_stays_in_order(tmp_path, monkeypatch):
    """
    No font here: a subtitle track alone is enough. Its last line is ten
    seconds in, and after it the file supplying it was read to the end
    while the other waited.
    """
    lib = _ordered_library(tmp_path, monkeypatch, subtitle_starts=(2, 7),
                           font=False)

    _run_job(lib, ["-map", "0:0", "-map", "0:2", "-map", "0:3", "-c", "copy"],
             job_id=1)

    _assert_restored_in_order(lib, _revert(lib))


def test_cover_art_stays_in_order(tmp_path, monkeypatch):
    """
    Cover art in an MP4 is a video stream holding one picture, and to the
    reading order it is as sparse as a font: once its single packet is out
    it never catches up.
    """
    lib = _ordered_library(tmp_path, monkeypatch, subtitle_starts=(),
                           font=False, cover_art=True, container="mp4")

    _run_job_into(lib, ["-map", "0:0", "-map", "0:2", "-map", "0:3",
                        "-c", "copy"], job_id=1, fmt="mp4", suffix=".mp4")

    _assert_restored_in_order(lib, _revert(lib))


def test_a_second_jobs_sidecar_stays_in_order(tmp_path, monkeypatch):
    """
    The first job drops the Japanese audio. The second drops the English
    audio and the subtitle, so its sidecar mixes the old sidecar's audio
    with the file's audio, subtitle and font — two inputs again. The
    sidecar itself came out out of order, and a restore from it could not
    put that right.
    """
    from app.database.models import RevertPoint

    lib = _ordered_library(tmp_path, monkeypatch)

    _run_job(lib, ["-map", "0:0", "-map", "0:2", "-map", "0:3",
                   "-map", "0:t", "-c", "copy"], job_id=1)
    _run_job(lib, ["-map", "0:0", "-map", "0:t", "-c", "copy"], job_id=2)

    lib["db"].expire_all()
    sidecar = lib["db"].query(RevertPoint).one().sidecar_path
    step = _worst_step_back(sidecar)
    assert step < 1.0, f"the second job's sidecar steps {step:.1f}s back in time"

    _assert_restored_in_order(lib, _revert(lib))


# ── AC3 Forge and revert points ──────────────────────────────────────────────
#
# The forge rewrites the file in place, so it goes through revert capture the
# way a job does. These run the real forge — _process_next_forge, its
# command builders, FFmpeg — against a file a job has already processed.


class _QuietSocket:
    async def broadcast_json(self, payload):
        pass


@pytest.fixture
def forge_lib(tmp_path, monkeypatch):
    """
    Ten seconds of video with Japanese and English AAC 5.1, the layout the
    forge adds an AC3 track to, registered with revert capture switched on.
    """
    from sqlalchemy.orm import sessionmaker

    from sqlalchemy import create_engine

    from app.config import settings as app_settings
    from app.database.models import Base, MediaFile
    from app.database.session import update_app_setting
    import app.core.forge as forge_mod
    import app.core.worker as worker_mod
    import app.database.session as session_mod

    media_dir = tmp_path / "media"
    media_dir.mkdir()
    recycle = tmp_path / "recycle"
    recycle.mkdir()
    monkeypatch.setattr(app_settings, "RECYCLE_DIR", str(recycle), raising=False)

    path = media_dir / "Show.mkv"
    subprocess.run([
        "ffmpeg", "-v", "error", "-y",
        "-f", "lavfi", "-i", "testsrc=size=64x36:rate=5:duration=10",
        "-f", "lavfi", "-i", "sine=frequency=440:duration=10",
        "-f", "lavfi", "-i", "sine=frequency=880:duration=10",
        "-map", "0:v", "-map", "1:a", "-map", "2:a",
        "-metadata:s:a:0", "language=jpn", "-metadata:s:a:1", "language=eng",
        "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-ac", "6", "-b:a", "64k",
        "-f", "matroska", str(path)], check=True)

    # A database file, not memory_engine(). A forge run writes its progress
    # from executor threads while the run's own bookkeeping writes from
    # another, and memory_engine() hands every thread the same connection:
    # one thread's commit then ends another's transaction mid-flight. That
    # failed _record_revert_point about one run in twenty, with "cannot
    # commit - no transaction is active" — after the update had in fact been
    # committed by the other thread, so its cleanup deleted the sidecar the
    # point now named. A file gives each thread its own connection, as the
    # app's own engine does.
    engine = create_engine(f"sqlite:///{tmp_path / 'remuxarr.db'}",
                           connect_args={"check_same_thread": False,
                                         "timeout": 30})
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine)
    for module in (session_mod, worker_mod, forge_mod):
        monkeypatch.setattr(module, "SessionLocal", factory)

    # The forge reads its settings from the database, not from a dict
    # handed in the way _run_job's capture gets one.
    db = factory()
    update_app_setting(db, "revert_enabled", True)

    # Plex is told about forge rewrites; nothing to tell here.
    monkeypatch.setattr(worker_mod, "_load_forge_plex_notify_data", lambda _id: None)
    monkeypatch.setattr(worker_mod, "_pick_temp_dir", lambda _p: str(tmp_path))

    stat = path.stat()
    media = MediaFile(path=str(path), filename="Show.mkv",
                      directory=str(media_dir), size=stat.st_size,
                      mtime=stat.st_mtime, container="mkv", status="processed")
    db.add(media)
    db.commit()
    return {"db": db, "media": media, "path": path, "recycle": recycle,
            "tmp": tmp_path}


def _forge(lib, *, undo=False):
    """Queue one forge job for the file and run it through the real worker step."""
    from app.database.models import Ac3ForgeJob
    import app.core.worker as worker_mod

    audio = [s for s in _probe(lib["path"])["streams"] if s["codec_type"] == "audio"]
    english = next(s for s in audio if s["codec_name"] == "aac"
                   and (s.get("tags") or {}).get("language") == "eng")
    job = Ac3ForgeJob(file_id=lib["media"].id, is_undo=undo,
                      status="undo_pending" if undo else "pending",
                      aac_stream_index=english["index"],
                      audio_track_count=len(audio))
    lib["db"].add(job)
    lib["db"].commit()

    assert asyncio.run(worker_mod._process_next_forge(_QuietSocket())) is True
    lib["db"].expire_all()
    return lib["db"].get(Ac3ForgeJob, job.id)


def _codecs(path):
    return [(s["codec_type"], s["codec_name"],
             (s.get("tags") or {}).get("language"))
            for s in _probe(path)["streams"]]


def test_forging_after_a_job_keeps_the_revert_point_usable(forge_lib):
    """
    The forge adds an AC3 track to a file a job processed. It used to leave
    the revert point as it was, fingerprinting a file that no longer
    existed, so the entry read "modified since it was processed" and could
    never be used.

    A revert now gives back the true original — without the forged AC3,
    which is not part of it.
    """
    from app.core.revert_restore import revert_blocked_reason
    from app.database.models import RevertPoint

    lib = forge_lib
    pristine = _codecs(lib["path"])
    _run_job(lib, ["-map", "0:0", "-map", "0:2", "-c", "copy"], job_id=1)

    assert _forge(lib).status == "success"
    assert ("audio", "ac3", "eng") in _codecs(lib["path"]), "the forge added nothing"

    point = lib["db"].query(RevertPoint).one()
    assert revert_blocked_reason(point, str(lib["path"])) is None

    outcome = _revert(lib)
    assert outcome.success is True, outcome.error
    assert _codecs(outcome.restored_path) == pristine


def test_undoing_a_forged_track_the_original_had_keeps_it_restorable(forge_lib):
    """
    Forged first, processed second: the AC3 was there when the job ran, so
    the revert point counts it as part of the original. Undoing the forge
    destroys it, and it used to be destroyed for good — the point never
    stored it, and its fingerprint no longer matched anyway.
    """
    from app.database.models import RevertPoint

    lib = forge_lib
    assert _forge(lib).status == "success"
    before_the_job = _codecs(lib["path"])
    assert ("audio", "ac3", "eng") in before_the_job

    # The job drops the Japanese audio and keeps everything else.
    _run_job(lib, ["-map", "0", "-map", "-0:1", "-c", "copy"], job_id=1)

    assert _forge(lib, undo=True).status == "undone"
    assert ("audio", "ac3", "eng") not in _codecs(lib["path"])

    assert lib["db"].query(RevertPoint).count() == 1
    outcome = _revert(lib)
    assert outcome.success is True, outcome.error
    assert _codecs(outcome.restored_path) == before_the_job


def test_forging_a_file_with_no_revert_point_creates_none(forge_lib):
    """Adding a track loses nothing, so there is nothing to keep."""
    from app.database.models import RevertPoint

    lib = forge_lib
    assert _forge(lib).status == "success"

    assert lib["db"].query(RevertPoint).count() == 0
    assert os.listdir(lib["recycle"]) == []


def test_a_forge_run_that_cannot_keep_a_revert_point_is_refused(forge_lib, monkeypatch):
    """
    With "require a revert point" on, a job that cannot store what it would
    destroy does not run. A forge run is held to the same rule: here the
    recycle volume is gone, so the undo stops before touching the file.
    """
    from app.config import settings as app_settings
    from app.database.session import update_app_setting

    lib = forge_lib
    assert _forge(lib).status == "success"
    _run_job(lib, ["-map", "0", "-map", "-0:1", "-c", "copy"], job_id=1)
    before = lib["path"].read_bytes()

    update_app_setting(lib["db"], "revert_require_point", True)
    monkeypatch.setattr(app_settings, "RECYCLE_DIR",
                        str(lib["tmp"] / "not-mounted"), raising=False)

    job = _forge(lib, undo=True)

    assert job.status == "undo_failed"
    assert lib["path"].read_bytes() == before, "the file was rewritten anyway"
