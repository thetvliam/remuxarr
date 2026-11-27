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

The third case
--------------
That still left a stream the job removed that started before every
stream it kept late by the difference, 0.4 s in the tests here: the
processed file starts at its first kept stream, so moving it back by its
own start moved the kept streams too far, and a TS the job left as one
lost the difference in the processed file itself. An MP4's removed AAC
came back 21 ms late. So capture records when each stream's first packet
was (probe.first_packet_times), and a restore, or a later job's capture,
moves each file so its streams land at those times (ffmpeg.
alignment_offsets). Points recorded before that are restored as they were,
so each of the tests above runs twice: once as a point that recorded the
times, and once as an older point (_as_an_older_point), which must also
never read them.

A subtitle places a file too, but never one the sidecar stored
converted: placed by its SRT, a sidecar holding nothing but an MP4's
mov_text moved back by the gap before the first line, the MP4 beginning
it with an empty sample at 0 and the SRT at the line. A sidecar holding
nothing but a TS's DVB subtitle has to be placed by it, or it comes back
a frame of audio early. And a stream whose first packet has no time places
nothing: in an AVI, MPEG-4 video with B-frames has none on its first
packets, its earliest timed one is a frame late, and placed by it a video
converted to MP4 came back 80 ms late.

Each test compares every stream with the original's, relative to the
video, to within 2 ms (Matroska stores milliseconds): the first packet of
video and audio, every packet of a subtitle or data stream. The test of a
TS whose removed track started first also checks that the restored file
begins where the original did, which a comparison relative to the video
cannot see. The MP4 originals here keep their AAC's encoder delay in the
edit list of the MP4 they are restored to. No test takes a Matroska
original through an MP4 and back: there the delay recorded in the
Matroska (CodecDelay) is lost, and every stream of the restored file
moves by it (21 ms for AAC), a question of its own that recording start
times does not touch.

Three of the tests from before the third case fail against the code
before them, each with the stream off by the amount described: the
subtitles beside fonts and the later job's subtitles by -2.000 s, the data
stream kept in a TS by -0.510 s. The other three passed before them too;
they are there for the mutants below. The five added with the third case
all fail against the code before it: the removed track that started first
by +0.400 s (+0.399 s in the MP4), the later job's German track by
+0.400 s, and the MP4's removed AAC by +0.021 s.
Two came after, with the fix that stopped a converted subtitle placing a
file. The MP4's removed subtitle fails against the code before it, losing
a line with the rest moved back 0.5 s. The TS's removed DVB subtitle
passed there, and failed by -0.021 s against the fix as first proposed,
which let no subtitle place a file. The AVI converted to MP4 came after
that, and fails against the code before it with its audio 0.080 s off
against its video.

Verified by mutation, 19 applied, 19 killed.

The first seven came with the first two cases, each run first against the
suite as it stood before this file, and all seven survived it. Their exact
form was not recorded; they were made again from these descriptions
against the code with recorded times, so what follows for them is a
record of the remade mutants, not baseline evidence. Five are now killed
only by the older-point runs, because with recorded times the files are
placed by those whatever the mutant does to the start time: that is what
the older-point runs are for. Every kill is a stream reported off by the
amount the mutant moves it:

  • Restore without -copyts, as before        → killed, only by the older-
                                                point runs of test_subtitles_
                                                restored_beside_fonts_keep_
                                                their_time (-2.000 s) and
                                                test_a_data_stream_kept_in_a_
                                                ts_keeps_its_time (-0.510 s)
  • Restore with -copyts alone                → killed, only by the older-
                                                point runs of the three tests
                                                that keep a file in MPEG-TS
                                                (-1.400 s)
  • The processed file's first opening moved
    back, its second opening not              → killed, only by both runs of
                                                test_a_data_stream_kept_in_
                                                a_ts_keeps_its_time (+1.400 s)
  • The offset's sign turned                  → killed, by both runs of the
                                                same three (2.800 s) and by
                                                four of the five tests of the
                                                third case
  • A later job's capture without -copyts     → killed, only by the older-
                                                point run of test_a_later_job_
                                                keeps_the_earlier_sidecars_
                                                subtitles_in_time (-2.000 s)
  • A later job's capture with -copyts alone  → killed, only by the older-
                                                point run of test_a_later_job_
                                                on_a_file_that_stays_ts_keeps_
                                                its_time (+1.400 s)
  • A first capture with -copyts alone        → killed, by the older-point
                                                run of every test whose
                                                original is a TS (+1.400 s)

The other eight came with the third case. Run first against the suite as
it stood before its tests, with the code in place, seven survived; the
sign flipped was already killed there, by the recorded-times runs of the
three tests that keep a file in MPEG-TS, and is recorded as such:

  • The offset alignment_offsets works out
    with its sign flipped                     → already killed by those
                                                three (2.800 s); now also by
                                                four of the five tests of the
                                                third case
  • Each stream's start time recorded and
    read instead of its first packet          → killed, only by the two MP4
                                                tests (+0.021 s)
  • The sidecar never placed                  → killed, only by the two MP4
                                                tests (+0.021 s)
  • The processed file never placed           → killed, by the four tests
                                                with a removed track that
                                                started first (+0.400 s,
                                                +0.378 s in the MP4)
  • The original's start not taken off        → killed, only by test_a_
                                                removed_track_that_started_
                                                first_keeps_its_time_in_a_ts,
                                                the restored file beginning
                                                1.4 s after the original
  • The restore reading packet times for an
    older point                               → killed, by the older-point
                                                run of all six tests from
                                                before the third case
  • A later capture reading packet times for
    an older point                            → killed, only by the older-
                                                point runs of the two later-
                                                job tests from before it
  • A later job's capture not placing its
    files                                     → killed, only by test_a_
                                                later_job_keeps_a_track_that_
                                                started_first_in_time
                                                (+0.400 s)

Three more came with the fix that stopped a converted subtitle placing a
file. Run first against the suite as it stood before its two tests, with
the fix in place, the first two survived and the third was already
killed:

  • A converted subtitle placing a file       → killed, only by test_an_
                                                mp4s_removed_subtitle_keeps_
                                                its_time (a line lost, the
                                                rest moved back 0.5 s)
  • No subtitle placing a file                → killed, only by test_a_tss_
                                                removed_dvb_subtitle_keeps_
                                                its_time (-0.021 s)
  • Audio no longer placing a file            → already killed, by the two
                                                MP4 tests with a removed AAC
                                                (+0.021 s)

A fourth, a subtitle tried before any video or audio, survived against a
first version of that fix that tried video and audio first: a subtitle
left to place a file holds the original's packets either way, one a job
converts being kept in the sidecar as it was (revert.match_streams), so
it gives the same offset. The ordering it broke was removed rather than
kept without a test that could defend it.

One mutant proposed with it is not in that list: the fallback for an
older point removed. With no times recorded there is nothing to place a
file by, so alignment_offsets has no offsets to give whether or not it
checks the manifest's start time first; that check (and returning None)
only keeps a manifest whose times were hand-edited away from failing. The
two mutants that read times for an older point stand in for it, since
reading them is all the code would do differently.

One more came with the AVI: a stream whose first packet has no time
placing its file all the same. Run first against the suite as it stood
before its test, it survived:

  • An untimed first packet still placing     → killed, only by test_an_
    its file                                    avi_converted_to_mp4_keeps_
                                                its_video_in_time (-0.080 s)

No equivalent mutants among the nineteen.
"""
import asyncio
import os
import shutil
import subprocess
import sys

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


def _as_an_older_point(monkeypatch):
    """
    Capture and restore as for a point recorded before points recorded
    when each stream starts: the first capture records no times, and
    neither a later capture nor the restore reads any. Returns the files
    capture asked for times, which a later capture must not add to.
    """
    import app.core.revert_capture as capture_mod
    import app.core.revert_restore as restore_mod

    asked = []

    def none_recorded(path, *args):
        asked.append(path)
        return {}

    def never(*args):
        raise AssertionError("the restore read packet times for a point that recorded none")

    monkeypatch.setattr(capture_mod, "first_packet_times", none_recorded)
    monkeypatch.setattr(restore_mod, "first_packet_times", never)
    return asked


# The tests from before points recorded when each stream starts, run as
# such a point too: a point recorded then is still restored, and a later
# job still adds to it, the way they were before.
_both_kinds_of_point = pytest.mark.parametrize(
    "rig", [False, True], indirect=True, ids=["recorded times", "older point"])


@pytest.fixture
def rig(tmp_path, monkeypatch, request):
    """
    A recycle volume and a live database, and the steps of a job.
    Parametrized True (_both_kinds_of_point), its points are older ones
    (_as_an_older_point).
    """
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
    asked = (_as_an_older_point(monkeypatch)
             if getattr(request, "param", False) else None)
    db = factory()
    yield Rig(tmp_path, media, db, asked)
    db.close()


class Rig:
    def __init__(self, tmp, media, db, asked=None):
        self.tmp, self.media, self.db = tmp, media, db
        self.jobs = 0
        self.asked = asked

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
        if self.asked is not None:
            assert len(self.asked) == 1, (
                "a later capture read packet times for a point that recorded none")
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


@_both_kinds_of_point
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


@_both_kinds_of_point
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


@_both_kinds_of_point
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


@_both_kinds_of_point
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


@_both_kinds_of_point
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


@_both_kinds_of_point
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


def _start(path):
    """When the restored file's video begins, in its own timeline."""
    kinds = _streams(path)
    return _packet_times(path)[kinds.index("video")][0]


def _later(*args):
    """An input that begins 0.4 s after the file does."""
    return ["-itsoffset", "0.4", *args]


def test_a_removed_track_that_started_first_keeps_its_time(rig):
    """
    The German track starts 0.4 s before the video and the English track,
    and the job removes it. The processed file starts at its first kept
    stream, 0.4 s in, and the restore moved it back by that start, so the
    kept tracks moved 0.4 s and the German track came back 0.4 s late
    beside them.
    """
    rig.original("Show.mkv", *_later(*_VIDEO), *_later(*_tone(440)), *_tone(660),
                 "-map", "0", "-map", "1", "-map", "2", *_ENCODE, "-c:a", "flac",
                 "-metadata:s:a:0", "language=eng",
                 "-metadata:s:a:1", "language=ger", "-f", "matroska")
    rig.job("-map", "0:v", "-map", "0:a:0", "-c", "copy", "-f", "matroska")

    assert rig.revert() == []


def test_a_removed_track_that_started_first_keeps_its_time_in_an_mp4(rig):
    """The same in an MP4 left as one, with AAC, whose packets begin
    before the times ffprobe reports for its streams (see the next test)."""
    rig.original("Film.mp4", *_later(*_VIDEO), *_later(*_tone(440)), *_tone(660),
                 "-map", "0", "-map", "1", "-map", "2", *_ENCODE, "-c:a", "aac",
                 "-metadata:s:a:0", "language=eng",
                 "-metadata:s:a:1", "language=ger", "-f", "mp4")
    rig.job("-map", "0:v", "-map", "0:a:0", "-c", "copy", "-f", "mp4")

    assert rig.revert() == []


def test_an_mp4s_removed_aac_keeps_its_time(rig):
    """
    An MP4's AAC begins with a packet of priming that its edit list skips:
    ffprobe reports the stream starting at 0 and its first packet 21 ms
    before. The sidecar keeps that packet, and the track restored from it
    came back 21 ms late. Placed by each stream's start time rather than
    its first packet, it still did (probe.first_packet_times).
    """
    rig.original("Film.mp4", *_VIDEO, *_tone(440), *_tone(660),
                 "-map", "0", "-map", "1", "-map", "2", *_ENCODE, "-c:a", "aac",
                 "-metadata:s:a:0", "language=eng",
                 "-metadata:s:a:1", "language=ger", "-f", "mp4")
    rig.job("-map", "0:v", "-map", "0:a:0", "-c", "copy", "-f", "mp4")

    assert rig.revert() == []


def test_a_removed_track_that_started_first_keeps_its_time_in_a_ts(rig):
    """
    In an MPEG-TS left as one, the processed file itself loses the 0.4 s:
    FFmpeg writes it from 1.4 s whatever its first stream was. The
    restored file also has to begin where the original did, which no
    comparison relative to the video can see.
    """
    rig.original("Recording.ts", *_later(*_VIDEO), *_later(*_tone(440)), *_tone(660),
                 "-map", "0", "-map", "1", "-map", "2", *_ENCODE, "-c:a", "mp2",
                 "-metadata:s:a:0", "language=eng",
                 "-metadata:s:a:1", "language=ger", "-f", "mpegts")
    rig.job("-map", "0:v", "-map", "0:a:0", "-c", "copy", "-f", "mpegts")

    assert rig.revert() == []
    assert abs(_start(rig.path) - _start(rig.pristine)) <= TOLERANCE


def test_a_later_job_keeps_a_track_that_started_first_in_time(rig):
    """
    The first job removes the German track, which starts 0.4 s before the
    rest, and the second the French. The second sidecar mixes the file the
    job was handed, starting 0.4 s in, with the first sidecar, starting at
    the German track. Unless both are placed by the times the point
    recorded, the file handed is moved back by its own start and the
    French track is stored at the German track's time, without the 0.4 s
    between them, so the restore can put back one of the two but not both:
    placed by the French, the German came back 0.4 s late.
    """
    rig.original("Show.mkv", *_later(*_VIDEO), *_later(*_tone(440)),
                 *_later(*_tone(550)), *_tone(660),
                 "-map", "0", "-map", "1", "-map", "2", "-map", "3",
                 *_ENCODE, "-c:a", "flac",
                 "-metadata:s:a:0", "language=eng",
                 "-metadata:s:a:1", "language=fre",
                 "-metadata:s:a:2", "language=ger", "-f", "matroska")
    rig.job("-map", "0:v", "-map", "0:a:0", "-map", "0:a:1", "-c", "copy",
            "-f", "matroska")
    rig.job("-map", "0:v", "-map", "0:a:0", "-c", "copy", "-f", "matroska")

    assert rig.revert() == []


def test_an_mp4s_removed_subtitle_keeps_its_time(rig):
    """
    FFmpeg's MP4 muxer fills the gaps between mov_text lines with empty
    samples, so this subtitle begins with one at 0, while the sidecar
    stores it as SRT, which begins at its first line. With nothing but the
    subtitle in the sidecar, placing the sidecar by it moved it back by
    the gap, 0.5 s, and the restored subtitle lost a line. Only video and
    audio place a file now (ffmpeg.alignment_offsets).
    """
    rig.original("Film.mp4", *_VIDEO, *_tone(440),
                 "-i", _subtitles(rig, (0.5, 1.5), (2.0, 3.0)),
                 "-map", "0", "-map", "1", "-map", "2", *_ENCODE, "-c:a", "aac",
                 "-c:s", "mov_text", "-metadata:s:s:0", "language=eng", "-f", "mp4")
    # What makes this case: the subtitle's first packet is the empty one
    # at 0, half a second before its first line.
    assert _packet_times(rig.pristine)[2][:2] == pytest.approx([0.0, 0.5])
    rig.job("-map", "0:v", "-map", "0:a", "-c", "copy", "-f", "mp4")

    assert rig.revert() == []


_MAKE_PGS = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                         "tools", "testlib", "make_pgs.py")


def _dvb_subtitles(rig):
    """
    Two lines of DVB bitmap subtitle. FFmpeg can neither draw text as a
    bitmap subtitle nor encode PGS, so they are decoded from the PGS the
    explorer's own generator writes (tools/testlib/make_pgs.py), as the
    explorer's library makes its DVB recording.
    """
    sup, mks = rig.tmp / "subs.sup", rig.tmp / "subs.mks"
    subprocess.run([sys.executable, _MAKE_PGS, str(sup), "160", "120", "2"], check=True)
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-fix_sub_duration", "-i", str(sup),
                    "-c:s", "dvbsub", "-f", "matroska", str(mks)], check=True)
    return str(mks)


def test_a_tss_removed_dvb_subtitle_keeps_its_time(rig):
    """
    A sidecar holding nothing but a TS's DVB subtitle has no video or
    audio to place it by. FFmpeg cut it with the input moved back by the
    subtitle's own start, which in an MPEG-TS it corrects to the streams
    read, not by the original's, which the AAC sets a frame earlier. Not
    placed by its subtitle, the sidecar kept that difference and the
    subtitle came back early by it (ffmpeg.alignment_offsets).
    """
    rig.original("Recording.ts", *_VIDEO, *_tone(440), "-i", _dvb_subtitles(rig),
                 "-map", "0", "-map", "1", "-map", "2:s:0", *_ENCODE,
                 "-c:a", "aac", "-c:s", "copy", "-metadata:s:s:0", "language=eng",
                 "-f", "mpegts")
    # What makes this case: the audio begins before the subtitle does.
    times = _packet_times(rig.pristine)
    assert times[1][0] < times[2][0]
    rig.job("-map", "0:v", "-map", "0:a", "-c", "copy", "-f", "mpegts")

    assert rig.revert() == []


def test_an_avi_converted_to_mp4_keeps_its_video_in_time(rig):
    """
    FFmpeg gives no time to the first packets of an AVI's MPEG-4 video with
    B-frames, so its earliest timed packet is a frame after its first
    frame, while in the MP4 the job writes every packet has one. Placed by
    those, the processed file moved a frame and more, and the restored
    video came back 80 ms late. A stream whose first packet has no time
    places nothing now, and the audio places the file
    (probe.first_packet_times).
    """
    rig.original("Old.avi", *_VIDEO, *_tone(440), *_tone(660),
                 "-map", "0", "-map", "1", "-map", "2",
                 "-c:v", "mpeg4", "-bf", "2", "-q:v", "4", "-c:a", "libmp3lame",
                 "-metadata:s:a:0", "language=eng", "-metadata:s:a:1", "language=ger",
                 "-f", "avi")
    # What makes this case: the video's first packet carries no time.
    first = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0",
                            "-read_intervals", "%+#1", "-show_entries", "packet=pts_time",
                            "-of", "csv=p=0", str(rig.pristine)],
                           capture_output=True, text=True, check=True).stdout.split()
    assert first[:1] == ["N/A"]
    rig.job("-map", "0:v", "-map", "0:a:0", "-c", "copy", "-f", "mp4")

    assert rig.revert() == []
