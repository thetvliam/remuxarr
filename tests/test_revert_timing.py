"""
A revert puts every track back at its own time.

The bug this file exists for
----------------------------
A restore mixes streams from two files of one timeline, the processed
file and the sidecar, and a later job's capture does the same with the
file it was handed and the previous sidecar. FFmpeg's command line moves
each input's timestamps back by that input's own start time unless told
otherwise, and a sidecar's start time is often not where its timeline
begins: one holding attachments beside its subtitles gets the time of its
first subtitle line. So every track restored from such a sidecar came
back early by that much: the test library's "07 - fonts unused" by 0.5 s,
and a sidecar whose one subtitle track began at 65 s had it restored at
0. A later job cut its sidecar from the previous one the same way, which
stored the shift for good. Nothing saw it: the explorer compares packet
contents, not their times, and so did every revert test here.

Writing these turned up a second case of the same rule. FFmpeg recomputes
an MPEG-TS input's start time from the streams that input reads, and the
restore reads a sparse stream the processed file kept through a second
opening of it that reads nothing else (ffmpeg._sparse_openings), so a
data stream kept in a TS came back early by its own start, 0.51 s here.

The fix keeps the first file's openings moved back by its own start time
as before and every other file at its stored times (ffmpeg._input_args).
The obvious fix, -copyts alone, is wrong too: a job that leaves a file in
MPEG-TS writes it starting about 1.4 s in, and -copyts alone moved that
file's tracks 1.4 s away from the sidecar's. The tests in MPEG-TS are
there for that.

Each test compares every stream with the original's, relative to the
video, to within 2 ms (Matroska stores milliseconds): the first packet of
video and audio, every packet of a subtitle or data stream. No original
here records an encoder delay for its audio: FLAC has none, and MP2 and
AC-3 are only ever in MPEG-TS, which has nowhere to record one (AC-3 in
Matroska carries 256 samples). So the shift a recorded delay causes on
the way back from an MP4, every stream of the restored file moved by the
delay (21 ms for AAC), stays out of these tests; it is a question of its
own.

Not covered, because it is not fixed: a stream the job removed that
started before every stream it kept comes back late by the difference
(ffmpeg._input_args).

Three of these fail against the code before the fix, each with the
stream off by the amount described: the subtitles beside fonts and the
later job's subtitles by -2.000 s, the data stream kept in a TS by
-0.510 s. The other three passed before it too; they are there for the
mutants below.

Verified by mutation, 7 applied, 7 killed. Run first against the suite as
it stood before this file, all seven survived it. Killed now, every time
by a stream reported off by the amount the mutant moves it:

  • Restore without -copyts, as before        → killed, by
                                                test_subtitles_restored_beside_
                                                fonts_keep_their_time (-2.000 s)
                                                and test_a_data_stream_kept_in_
                                                a_ts_keeps_its_time (-0.510 s)
  • Restore with -copyts alone                → killed, by the three tests
                                                that keep a file in MPEG-TS
                                                (-1.400 s)
  • The processed file's first opening moved
    back, its second opening not              → killed, only by
                                                test_a_data_stream_kept_in_
                                                a_ts_keeps_its_time (+1.400 s)
  • The offset's sign turned                  → killed, by the same three
                                                (-2.800 s)
  • A later job's capture without -copyts     → killed, only by
                                                test_a_later_job_keeps_the_
                                                earlier_sidecars_subtitles_in_
                                                time (-2.000 s)
  • A later job's capture with -copyts alone  → killed, only by
                                                test_a_later_job_on_a_file_
                                                that_stays_ts_keeps_its_time
                                                (+1.400 s)
  • A first capture with -copyts alone        → killed, by every test whose
                                                original is a TS (+1.400 s)

No equivalent mutants.
"""
import asyncio
import os
import shutil
import subprocess

import pytest


pytestmark = pytest.mark.skipif(
    shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None,
    reason="ffmpeg/ffprobe not available",
)

TOLERANCE = 0.002


def _ffmpeg(*args):
    subprocess.run(["ffmpeg", "-v", "error", "-y", *args], check=True)


def _streams(path):
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "stream=index,codec_type",
         "-of", "csv=p=0", str(path)],
        capture_output=True, text=True, check=True)
    return [line.split(",")[1] for line in out.stdout.split()]


def _packet_times(path):
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "packet=stream_index,pts_time",
         "-of", "csv=p=0", str(path)],
        capture_output=True, text=True, check=True)
    times: dict[int, list[float]] = {}
    for line in out.stdout.split():
        index, pts = line.split(",")[:2]
        if pts != "N/A":
            times.setdefault(int(index), []).append(float(pts))
    return {index: sorted(values) for index, values in times.items()}


def _offsets(original, restored):
    """
    How far each stream of the restored file is from where the original
    had it, relative to the video, as "stream N (type): off by X s" for
    every stream further out than TOLERANCE.
    """
    kinds = _streams(original)
    assert _streams(restored) == kinds, "the restore changed the stream layout"
    before, after = _packet_times(original), _packet_times(restored)
    video = kinds.index("video")
    before_zero, after_zero = before[video][0], after[video][0]
    found = []
    for index, kind in enumerate(kinds):
        if index not in before:
            continue  # an attachment: no packets
        old = [t - before_zero for t in before[index]]
        new = [t - after_zero for t in after.get(index, [])]
        if kind in ("video", "audio"):
            old, new = old[:1], new[:1]
        assert len(new) == len(old), f"stream {index} ({kind}) lost packets"
        worst = max((b - a for a, b in zip(old, new)), key=abs)
        if abs(worst) > TOLERANCE:
            found.append(f"stream {index} ({kind}): off by {worst:+.3f} s")
    return found


@pytest.fixture
def rig(tmp_path, monkeypatch):
    """A recycle volume and a live database, and the steps of a job."""
    from sqlalchemy.orm import sessionmaker

    from tests.conftest import memory_engine

    from app.config import settings as app_settings
    from app.database.models import Base
    import app.core.worker as worker_mod
    import app.database.session as session_mod

    media = tmp_path / "media"
    media.mkdir()
    recycle = tmp_path / "recycle"
    recycle.mkdir()
    monkeypatch.setattr(app_settings, "RECYCLE_DIR", str(recycle), raising=False)

    engine = memory_engine()
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine)
    monkeypatch.setattr(session_mod, "SessionLocal", factory)
    monkeypatch.setattr(worker_mod, "SessionLocal", factory)
    db = factory()
    yield Rig(tmp_path, media, db)
    db.close()


class Rig:
    def __init__(self, tmp, media, db):
        self.tmp, self.media, self.db = tmp, media, db
        self.jobs = 0

    def original(self, name, *ffmpeg_args):
        """Write the original with FFmpeg, keep a copy, and record its row."""
        from app.database.models import MediaFile

        self.path = self.media / name
        _ffmpeg(*ffmpeg_args, str(self.path))
        self.pristine = self.tmp / f"pristine-{name}"
        shutil.copy(self.path, self.pristine)
        stat = self.path.stat()
        self.row = MediaFile(path=str(self.path), filename=name,
                             directory=str(self.media), size=stat.st_size,
                             mtime=stat.st_mtime, container=None,
                             status="processed")
        self.db.add(self.row)
        self.db.commit()

    def job(self, *ffmpeg_args):
        """
        What a job does to the file: FFmpeg writes a temp output, capture
        runs while both exist, the temp replaces the file, and the revert
        point is recorded.
        """
        from app.core.revert_capture import capture
        from app.core.worker import _record_revert_point

        self.jobs += 1
        produced = self.tmp / f"job{self.jobs}.remuxarr_tmp"
        _ffmpeg("-i", str(self.path), *ffmpeg_args, str(produced))
        captured, error = asyncio.run(capture(
            input_path=str(self.path), produced_path=str(produced),
            file_id=self.row.id, job_id=self.jobs,
            app_cfg={"revert_enabled": True, "revert_require_point": False},
        ))
        assert error is None, error
        assert captured is not None, "the job recorded no revert point"
        os.replace(produced, self.path)
        _record_revert_point(self.row.id, captured, str(self.path), [])

    def revert(self):
        """Restore the point, and return how far each stream is out."""
        from app.core.revert_restore import restore_revert_point
        from app.database.models import RevertPoint

        self.db.expire_all()
        point = self.db.query(RevertPoint).one()
        outcome = asyncio.run(restore_revert_point(point.id))
        assert outcome.success, outcome.error
        return _offsets(self.pristine, self.path)


def _font(rig):
    font = rig.tmp / "font.ttf"
    font.write_bytes(b"not really a font, which a sidecar does not mind")
    return ["-attach", str(font), "-metadata:s:t", "mimetype=font/ttf"]


def _subtitles(rig, *lines):
    """A SubRip file with one line at each (start, end), in seconds."""
    srt = rig.tmp / "subs.srt"
    cues = []
    for number, (start, end) in enumerate(lines, 1):
        stamp = [f"00:00:{int(t):02d},{int(round(t % 1 * 1000)):03d}" for t in (start, end)]
        cues.append(f"{number}\n{stamp[0]} --> {stamp[1]}\nline {number}\n")
    srt.write_text("\n".join(cues))
    return str(srt)


_VIDEO = ["-f", "lavfi", "-i", "testsrc=size=160x120:rate=25:duration=4"]


def _tone(hz):
    return ["-f", "lavfi", "-i", f"sine=frequency={hz}:sample_rate=48000:duration=4"]


_ENCODE = ["-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p"]


def test_subtitles_restored_beside_fonts_keep_their_time(rig):
    """
    The job removes a subtitle track and the fonts, so the sidecar holds
    those alone and starts at the first subtitle line, 2 s in. The
    restore put the line at 0, 2 s early.
    """
    subs = _subtitles(rig, (2.0, 3.0), (3.2, 3.8))
    rig.original("Show.mkv", *_VIDEO, *_tone(440), "-i", subs,
                 "-map", "0", "-map", "1", "-map", "2", *_font(rig),
                 *_ENCODE, "-c:a", "flac", "-c:s", "srt",
                 "-metadata:s:a:0", "language=eng",
                 "-metadata:s:s:0", "language=ger", "-f", "matroska")
    rig.job("-map", "0:v", "-map", "0:a", "-c", "copy", "-f", "matroska")

    assert rig.revert() == []


def test_a_file_that_stays_ts_keeps_its_tracks_in_time(rig):
    """
    A job that leaves a file in MPEG-TS writes it starting about 1.4 s in,
    and the sidecar starts at 0. -copyts alone kept both, and the track
    restored from the sidecar came back 1.4 s early.
    """
    rig.original("Recording.ts", *_VIDEO, *_tone(440), *_tone(660),
                 "-map", "0", "-map", "1", "-map", "2", *_ENCODE, "-c:a", "mp2",
                 "-metadata:s:a:0", "language=eng",
                 "-metadata:s:a:1", "language=fre", "-f", "mpegts")
    rig.job("-map", "0:v", "-map", "0:a:0", "-c", "copy", "-f", "mpegts")

    assert rig.revert() == []


def test_a_data_stream_kept_in_a_ts_keeps_its_time(rig):
    """
    The data stream stays in the processed file, and as a sparse stream
    the restore reads it through a second opening of that file
    (ffmpeg._sparse_openings), which has to be moved back by the file's
    start time like the first. It was moved back by the data stream's own
    start instead, FFmpeg's correction of an MPEG-TS input's start time
    counting only what that opening reads, and came back 0.51 s early.
    A subtitle muxed into MPEG-TS by FFmpeg is written as a data stream,
    as a recording's teletext or EPG often is.
    """
    subs = _subtitles(rig, (0.5, 1.5), (2.0, 2.5))
    rig.original("Recording.ts", *_VIDEO, *_tone(440), *_tone(660), "-i", subs,
                 "-map", "0", "-map", "1", "-map", "2", "-map", "3",
                 *_ENCODE, "-c:a", "mp2", "-c:s", "srt",
                 "-metadata:s:a:0", "language=eng",
                 "-metadata:s:a:1", "language=fre", "-f", "mpegts")
    assert _streams(rig.path)[3] == "data"
    rig.job("-map", "0:v", "-map", "0:a:0", "-map", "0:d", "-c", "copy",
            "-f", "mpegts")

    assert rig.revert() == []


def test_a_later_job_keeps_the_earlier_sidecars_subtitles_in_time(rig):
    """
    The first job stores a subtitle track and the fonts, the second an
    audio track, and its sidecar is cut from the file it was handed and
    the first sidecar together. The first sidecar's start time, its first
    subtitle line, was taken off its subtitles there, so the new sidecar
    stored them 2 s early, beyond any restore's reach.
    """
    subs = _subtitles(rig, (2.0, 3.0), (3.2, 3.8))
    rig.original("Show.mkv", *_VIDEO, *_tone(440), *_tone(660), "-i", subs,
                 "-map", "0", "-map", "1", "-map", "2", "-map", "3", *_font(rig),
                 *_ENCODE, "-c:a", "flac", "-c:s", "srt",
                 "-metadata:s:a:0", "language=eng",
                 "-metadata:s:a:1", "language=fre",
                 "-metadata:s:s:0", "language=ger", "-f", "matroska")
    rig.job("-map", "0:v", "-map", "0:a", "-c", "copy", "-f", "matroska")
    rig.job("-map", "0:v", "-map", "0:a:0", "-c", "copy", "-f", "matroska")

    assert rig.revert() == []


def test_a_later_job_on_a_file_that_stays_ts_keeps_its_time(rig):
    """
    The second job is handed an MPEG-TS starting about 1.4 s in, and the
    first sidecar starts at 0. The track it removes has to be moved back
    by the file's start time on its way into the new sidecar, or it is
    stored 1.4 s late beside the first sidecar's.
    """
    rig.original("Recording.ts", *_VIDEO, *_tone(440), *_tone(660), *_tone(880),
                 "-map", "0", "-map", "1", "-map", "2", "-map", "3",
                 *_ENCODE, "-c:a", "mp2",
                 "-metadata:s:a:0", "language=eng",
                 "-metadata:s:a:1", "language=fre",
                 "-metadata:s:a:2", "language=ger", "-f", "mpegts")
    rig.job("-map", "0:v", "-map", "0:a:0", "-map", "0:a:1", "-c", "copy",
            "-f", "mpegts")
    rig.job("-map", "0:v", "-map", "0:a:0", "-c", "copy", "-f", "mpegts")

    assert rig.revert() == []


def test_a_ts_converted_to_mp4_keeps_its_tracks_in_time(rig):
    """
    The commonest way a recording reaches a revert: converted to MP4, with
    a track removed. The first capture reads one file, the original, and
    is cut the way the job cuts it, so its command is left as it was;
    with -copyts it would keep the original's 1.4 s start while the job's
    output starts at 0.
    """
    rig.original("Recording.ts", *_VIDEO, *_tone(440), *_tone(660),
                 "-map", "0", "-map", "1", "-map", "2", *_ENCODE, "-c:a", "ac3",
                 "-metadata:s:a:0", "language=eng",
                 "-metadata:s:a:1", "language=fre", "-f", "mpegts")
    rig.job("-map", "0:v", "-map", "0:a:0", "-c", "copy", "-f", "mp4")

    assert rig.revert() == []
