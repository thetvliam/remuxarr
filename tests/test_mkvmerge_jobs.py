"""
Matroska jobs that go through mkvmerge (ffmpeg.plan_mkvmerge, _run_mkvmerge,
_combined_through_mkvmerge), run for real.

A job keeping a file Matroska goes to mkvmerge only when its output would
hold something FFmpeg drops, and only when nothing measured to change under
mkvmerge can reach it (app/core/mkvtoolnix.py says what, and tests it). Here
the whole path runs: the right tool for each file, what comes out, the date
put back as stored, the subtitles of a job that extracts them written by one
FFmpeg pass beside mkvmerge's file, and every way back to FFmpeg: mkvmerge
exiting with a warning (a Latin-1 subtitle, whose bytes it would replace),
a check after the mux failing, mkvmerge not starting. A refusal by the job's
own checks is not retried.

Every file here is built with FFmpeg and MKVToolNix; the tests skip without
them. CI installs both, the releases the image ships.

Verified by mutation, 19 applied, 17 killed, 2 retired. Run first against
the suite as it stood before this file, two (the job never planning
mkvmerge, and a refusal by the job's checks retried) survived all 3063
tests: no earlier fixture can reach this path, as FFmpeg writes none of
what mkvmerge is used for. Killed now, each by the test for its behaviour:

  • execute_ffmpeg or execute_ffmpeg_combined never asking for mkvmerge
  • a job converting to MP4 sent to mkvmerge
  • the blockers ignored
  • the date not put back
  • each part of the check after the mux removed: languages, flags, the
    date, regions, track order (one mutant each)
  • exit 1 ending the job instead of going back to FFmpeg
  • a failed check ending the job instead
  • a refusal by the job's own checks retried through FFmpeg
  • an mkvmerge that cannot start ending the job
  • the subtitle pass's temps left behind when it fails
  • mkvmerge's progress lines not read
  • the second run's progress not starting where the first ended

Retired: removing the temps after mkvmerge's attempt failed or was thrown
away in an extracting job changed nothing, as the runner removes every
output it was handed; the two calls were deleted.
"""
import asyncio
import json
import logging
import os
import shutil
import subprocess

import pytest

from app.config import settings as app_settings
from app.core import ffmpeg as ff
from app.core import mkvtoolnix as mkv
from app.core.decision import Action, ProcessingDecision

pytestmark = pytest.mark.skipif(
    not (shutil.which("ffmpeg") and shutil.which("ffprobe")
         and shutil.which("mkvmerge") and shutil.which("mkvextract")
         and shutil.which("mkvpropedit")),
    reason="needs FFmpeg and MKVToolNix")

CHAPTERS = """<?xml version="1.0"?>
<Chapters>
 <EditionEntry><EditionFlagDefault>1</EditionFlagDefault>
  <ChapterAtom><ChapterUID>1</ChapterUID><ChapterTimeStart>00:00:00.000</ChapterTimeStart>
   <ChapterDisplay><ChapterString>Part one</ChapterString><ChapLanguageIETF>en</ChapLanguageIETF></ChapterDisplay>
   <ChapterDisplay><ChapterString>Partie un</ChapterString><ChapLanguageIETF>fr</ChapLanguageIETF></ChapterDisplay></ChapterAtom>
 </EditionEntry>
 <EditionEntry>
  <ChapterAtom><ChapterUID>2</ChapterUID><ChapterTimeStart>00:00:00.000</ChapterTimeStart>
   <ChapterDisplay><ChapterString>Cut</ChapterString><ChapLanguageIETF>en</ChapLanguageIETF></ChapterDisplay></ChapterAtom>
 </EditionEntry>
</Chapters>
"""

# Track layout of every source here: the job drops the German audio (2).
TRACKS = [{"stream_index": 0, "track_type": "video"}, {"stream_index": 1, "track_type": "audio"},
          {"stream_index": 2, "track_type": "audio"}, {"stream_index": 3, "track_type": "subtitle"},
          {"stream_index": 4, "track_type": "subtitle"}, {"stream_index": 5, "track_type": "subtitle"}]


def _run(*cmd):
    subprocess.run([str(c) for c in cmd], check=True, capture_output=True)


def _streams(tmp_path, latin1=False, english="eng"):
    """FFmpeg-made video, two audio tracks and three subtitles: es-419,
    es-ES and English (Latin-1 bytes when asked, and tagged `english`,
    which FFmpeg writes as given)."""
    base = tmp_path / "base.mkv"
    subs = []
    for name, text in (("419", "Hola"), ("ES", "Vale"), ("en", "Caf\xe9" if latin1 else "Cafe")):
        path = tmp_path / f"{name}.srt"
        path.write_bytes(f"1\n00:00:00,500 --> 00:00:01,500\n{text}\n\n".encode("latin-1"))
        subs.append(path)
    _run("ffmpeg", "-v", "error", "-f", "lavfi", "-i", "testsrc=d=2:s=160x90:r=24",
         "-f", "lavfi", "-i", "sine=f=440:d=2", "-f", "lavfi", "-i", "sine=f=550:d=2",
         *sum((["-i", s] for s in subs), []),
         "-map", "0", "-map", "1", "-map", "2", "-map", "3", "-map", "4", "-map", "5",
         "-c:v", "libx264", "-preset", "ultrafast", "-c:a", "aac", "-c:s", "copy",
         "-metadata:s:a:0", "language=eng", "-metadata:s:a:1", "language=ger",
         "-metadata:s:s:0", "language=spa", "-metadata:s:s:1", "language=spa",
         "-metadata:s:s:2", f"language={english}",
         "-metadata", "creation_time=2026-01-02T03:04:05.250000Z", base)
    return base


def _regional(path):
    """Give the tracks their regions in place, as a real release has them."""
    _run("mkvpropedit", "-q", path, "--edit", "track:2", "--set", "language-ietf=en-GB",
         "--edit", "track:4", "--set", "language-ietf=es-419",
         "--edit", "track:5", "--set", "language-ietf=es-ES")


def _source(tmp_path, *, latin1=False, date=True, english="eng"):
    """A Matroska that gains through mkvmerge: regional languages and two
    chapter editions. With latin1, written so mkvmerge never rewrites the
    English subtitle's bytes before the job does."""
    base = _streams(tmp_path, latin1, english)
    (tmp_path / "chapters.xml").write_text(CHAPTERS)
    source = tmp_path / "film.mkv"
    if latin1 or english != "eng":
        shutil.copy(base, source)
        _run("mkvpropedit", "-q", source, "--chapters", tmp_path / "chapters.xml")
    else:
        _run("mkvmerge", "-q", *([] if date else ["--no-date"]), "-o", source,
             "--chapters", tmp_path / "chapters.xml", base)
        if date:
            # mkvmerge dates a file to the second it ran, which a job in
            # the same second would match by chance.
            _run("mkvpropedit", "-q", source, "--edit", "info", "--set", "date=2020-01-02T03:04:05Z")
    _regional(source)
    return source


def _decision(*extra):
    drop = Action(action_type="drop_track", description="drop German", track_type="audio",
                  stream_index=2)
    return ProcessingDecision(should_process=True, reason="test", actions=[drop, *extra],
                              target_container="mkv", source_container="mkv")


def _writer(path):
    out = subprocess.run(["mkvmerge", "-J", str(path)], capture_output=True, text=True)
    return json.loads(out.stdout)["container"]["properties"].get("writing_application", "")


def _languages(path):
    return [t.language_ietf for t in mkv.read_matroska(str(path)).tracks]


class Hook:
    """The worker's before_staging, counted; refuses when told to."""
    def __init__(self, refuse=None):
        self.calls, self.refuse = [], refuse

    async def __call__(self, produced):
        self.calls.append(produced)
        return self.refuse


def _execute(source, decision, hook=None, progress=None, job_id=7301):
    return asyncio.run(ff.execute_ffmpeg(
        str(source), str(source), decision, TRACKS, job_id,
        progress_callback=progress, before_staging=hook))


def _temps(job_id):
    return [n for n in os.listdir(app_settings.TEMP_DIR) if n.startswith(f"job_{job_id}")]


# ── The right tool ───────────────────────────────────────────────────────────

def test_a_file_that_gains_goes_through_mkvmerge_and_keeps_what_ffmpeg_drops(tmp_path):
    source = _source(tmp_path)
    made = mkv.read_date_utc(str(source))
    hook = Hook()

    result = _execute(source, _decision(), hook)

    assert result.success, result.error
    assert _writer(source).startswith("mkvmerge")
    # mkvmerge writes a BCP 47 tag beside every old code; the English
    # subtitle's says what its old one does.
    assert _languages(source) == ["und", "en-GB", "es-419", "es-ES", "en"]
    assert len(mkv.read_matroska(str(source)).editions) == 2
    assert mkv.read_date_utc(str(source)) == made
    assert len(hook.calls) == 1


def test_a_file_with_nothing_to_gain_stays_with_ffmpeg(tmp_path):
    source = tmp_path / "plain.mkv"
    shutil.copy(_streams(tmp_path), source)

    result = _execute(source, _decision())

    assert result.success, result.error
    assert _writer(source).startswith("Lavf")


def test_a_source_with_no_date_comes_out_with_none(tmp_path):
    source = _source(tmp_path, date=False)

    assert _execute(source, _decision()).success
    assert _writer(source).startswith("mkvmerge")
    assert mkv.read_date_utc(str(source)) is None


def test_a_language_code_mkvmerge_would_rewrite_keeps_the_file_with_ffmpeg(tmp_path, caplog):
    source = _source(tmp_path, english="en")
    probe = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "5", "-show_entries",
                            "stream_tags=language", "-of", "csv=p=0", str(source)],
                           capture_output=True, text=True).stdout.strip()
    assert probe == "en"

    with caplog.at_level(logging.INFO, logger="app.core.ffmpeg"):
        assert _execute(source, _decision()).success

    assert _writer(source).startswith("Lavf")
    assert "though mkvmerge would keep" in caplog.text


# ── A job that extracts subtitles ────────────────────────────────────────────

def test_an_extracting_job_writes_its_sidecars_with_ffmpeg_and_its_file_with_mkvmerge(tmp_path):
    source = _source(tmp_path)
    srt = tmp_path / "film.en.srt"
    extract = Action(action_type="extract_subtitle", description="extract", track_type="subtitle",
                     stream_index=5, external_path=str(srt))
    seen = []

    result, extracted = asyncio.run(ff.execute_ffmpeg_combined(
        str(source), str(source), _decision(extract), TRACKS, [(5, str(srt))], 7302,
        progress_callback=_run_progress(seen), before_staging=Hook()))

    assert result.success, result.error
    assert extracted[0].success and srt.read_text().strip().endswith("Cafe")
    assert _writer(source).startswith("mkvmerge")
    assert _languages(source) == ["und", "en-GB", "es-419", "es-ES"]
    assert _temps(7302) == []
    assert seen and max(seen) == ff.subprocess_percent(100.0)
    assert seen == sorted(seen)


# ── The ways back to FFmpeg ──────────────────────────────────────────────────

def test_a_latin1_subtitle_falls_back_to_ffmpeg_with_its_bytes_intact(tmp_path, caplog):
    source = _source(tmp_path, latin1=True)
    hook = Hook()

    with caplog.at_level(logging.WARNING, logger="app.core.ffmpeg"):
        result = _execute(source, _decision(), hook, job_id=7303)

    assert result.success, result.error
    assert _writer(source).startswith("Lavf")
    assert "mkvmerge exited 1" in caplog.text and "remuxing with FFmpeg instead" in caplog.text
    raw = subprocess.run(["ffmpeg", "-v", "error", "-i", str(source), "-map", "0:4", "-c", "copy",
                          "-f", "data", "-"], capture_output=True).stdout
    assert b"Caf\xe9" in raw
    assert len(hook.calls) == 1
    assert _temps(7303) == []


def test_a_failed_check_after_the_mux_falls_back_to_ffmpeg(tmp_path, monkeypatch, caplog):
    source = _source(tmp_path)

    def cannot(path, value):
        raise mkv.DateUnreadable("simulated")
    monkeypatch.setattr(mkv, "write_date_utc", cannot)
    hook = Hook()

    with caplog.at_level(logging.WARNING, logger="app.core.ffmpeg"):
        result = _execute(source, _decision(), hook, job_id=7304)

    assert result.success, result.error
    assert _writer(source).startswith("Lavf")
    assert "could not be finished: simulated" in caplog.text
    assert len(hook.calls) == 1
    assert _temps(7304) == []


def test_a_mkvmerge_that_cannot_start_falls_back_to_ffmpeg(tmp_path, monkeypatch):
    source = _source(tmp_path)
    real = mkv.build_mkvmerge_command
    monkeypatch.setattr(mkv, "build_mkvmerge_command",
                        lambda *a, **k: [str(tmp_path / "gone" / "mkvmerge"), *real(*a, **k)[1:]])

    result = _execute(source, _decision(), job_id=7305)

    assert result.success, result.error
    assert _writer(source).startswith("Lavf")


def test_a_refusal_by_the_jobs_own_checks_is_not_retried(tmp_path):
    source = _source(tmp_path)
    before = source.read_bytes()
    hook = Hook(refuse="the source changed")

    result = _execute(source, _decision(), hook, job_id=7306)

    assert not result.success and result.error == "the source changed"
    assert len(hook.calls) == 1
    assert source.read_bytes() == before
    assert _temps(7306) == []


def _run_progress(seen):
    """Progress from the run itself, not the copy into place after it,
    which reports under its own label."""
    async def progress(p):
        if p.current_action != ff.STAGING_ACTION:
            seen.append(p.percent)
    return progress


def test_mkvmerges_progress_is_reported(tmp_path):
    source = _source(tmp_path)
    seen = []

    assert _execute(source, _decision(), progress=_run_progress(seen)).success
    assert _writer(source).startswith("mkvmerge")
    assert seen and max(seen) == ff.subprocess_percent(100.0)


@pytest.mark.parametrize("start, pct, job", [(0.0, 40.0, 40.0), (50.0, 0.0, 50.0),
                                             (50.0, 40.0, 70.0), (50.0, 100.0, 100.0)])
def test_a_second_run_reports_progress_from_where_the_first_ended(start, pct, job):
    """The subtitle pass of an extracting job is its first half, mkvmerge
    its second."""
    assert ff._from(start, pct) == job


def test_a_conversion_to_mp4_stays_with_ffmpeg(tmp_path):
    """mkvmerge writes only Matroska: a job converting a file that would
    gain must not go to it."""
    source = _source(tmp_path)
    decision = _decision(*(Action(action_type="drop_track", description="drop", track_type="subtitle",
                                  stream_index=i) for i in (3, 4, 5)))
    decision.target_container = "mp4"
    decision.actions.append(Action(action_type="change_container", description="to MP4"))
    # The en-GB audio stays: mkvmerge would keep its region.
    info = mkv.read_matroska(str(source))
    assert mkv.benefits(info, mkv.kept_track_ids(decision, TRACKS), {})
    output = tmp_path / "film.mp4"

    result = asyncio.run(ff.execute_ffmpeg(str(source), str(output), decision, TRACKS, 7307))

    assert result.success, result.error
    assert "mp4" in subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=format_name",
                                    "-of", "csv=p=0", str(output)],
                                   capture_output=True, text=True).stdout


def test_an_extracting_job_that_falls_back_runs_the_combined_pass(tmp_path, caplog):
    """mkvmerge exits 1 over the kept Latin-1 subtitle: the subtitles it was
    to be staged with are thrown away with its file, and the combined run
    does the whole job."""
    source = _source(tmp_path, latin1=True)
    srt = tmp_path / "film.es.srt"
    extract = Action(action_type="extract_subtitle", description="extract", track_type="subtitle",
                     stream_index=3, external_path=str(srt))
    hook = Hook()

    with caplog.at_level(logging.WARNING, logger="app.core.ffmpeg"):
        result, extracted = asyncio.run(ff.execute_ffmpeg_combined(
            str(source), str(source), _decision(extract), TRACKS, [(3, str(srt))], 7308,
            before_staging=hook))

    assert result.success, result.error
    assert extracted[0].success and srt.read_text().strip().endswith("Hola")
    assert _writer(source).startswith("Lavf")
    assert "mkvmerge exited 1" in caplog.text
    assert len(hook.calls) == 1
    assert _temps(7308) == []


def test_a_subtitle_pass_that_fails_ends_the_job_as_a_combined_run_would(tmp_path):
    """
    The Latin-1 English subtitle is the one extracted here: FFmpeg cannot
    decode it to SRT, so the subtitle pass fails with FFmpeg's own error,
    which the worker turns into a review (_is_subtitle_encoding_failure),
    as it does for a combined run. Nothing is left behind, and the source
    is untouched.
    """
    from app.core.worker import _is_subtitle_encoding_failure

    source = _source(tmp_path, latin1=True)
    before = source.read_bytes()
    srt = tmp_path / "film.en.srt"
    extract = Action(action_type="extract_subtitle", description="extract", track_type="subtitle",
                     stream_index=5, external_path=str(srt))

    result, extracted = asyncio.run(ff.execute_ffmpeg_combined(
        str(source), str(source), _decision(extract), TRACKS, [(5, str(srt))], 7310,
        before_staging=Hook()))

    assert not result.success and not extracted[0].success
    assert _is_subtitle_encoding_failure(result.error), result.error
    assert not srt.exists()
    assert source.read_bytes() == before
    assert _temps(7310) == []


# ── The check after the mux ──────────────────────────────────────────────────
#
# A net behind the blockers: whatever mkvmerge wrote that the plan did not
# expect sends the job to FFmpeg. The blockers keep every case measured from
# reaching it, so it is tested here on its own, with a real mkvmerge output
# and a plan that disagrees with it in one way at a time.

def _plan_and_output(tmp_path):
    source = _source(tmp_path)
    decision = _decision()
    plan = asyncio.run(ff.plan_mkvmerge(str(source), decision, TRACKS, 7309))
    assert plan is not None
    out = tmp_path / "out.mkv"
    _run(*mkv.build_mkvmerge_command(str(source), str(out), decision, TRACKS,
                                     keep_date=plan.date_utc is not None))
    return plan, out


def _with_stream(plan, index, **changes):
    import copy
    probe = copy.deepcopy(plan.probe)
    stream = next(s for s in probe["streams"] if s["index"] == index)
    for key, value in changes.items():
        stream.setdefault(key, {}).update(value)
    return probe


def test_the_check_passes_what_mkvmerge_wrote_as_planned(tmp_path):
    plan, out = _plan_and_output(tmp_path)
    assert ff._finish_mkvmerge_output(plan, str(out)) is None
    assert mkv.read_date_utc(str(out)) == plan.date_utc


def test_the_check_catches_a_language_written_differently(tmp_path):
    from dataclasses import replace
    plan, out = _plan_and_output(tmp_path)
    plan = replace(plan, probe=_with_stream(plan, 5, tags={"language": "en"}))
    assert ff._finish_mkvmerge_output(plan, str(out)) == "writes track 5's language as eng, not en"


@pytest.mark.parametrize("flag", ["default", "forced"])
def test_the_check_catches_a_flag_changed(tmp_path, flag):
    from dataclasses import replace
    plan, out = _plan_and_output(tmp_path)
    was = next(s for s in plan.probe["streams"] if s["index"] == 4)["disposition"][flag]
    plan = replace(plan, probe=_with_stream(plan, 4, disposition={flag: 1 - was}))
    assert ff._finish_mkvmerge_output(plan, str(out)) == f"changes track 4's {flag} flag"


def test_the_check_catches_a_date_that_differs(tmp_path):
    from dataclasses import replace
    plan, out = _plan_and_output(tmp_path)
    plan = replace(plan, date_utc=plan.date_utc + 1_000_000_000)
    problem = ff._finish_mkvmerge_output(plan, str(out))
    assert problem.startswith("is dated ") and ", not " in problem


def test_the_check_catches_a_region_lost(tmp_path):
    from dataclasses import replace
    plan, out = _plan_and_output(tmp_path)
    tracks = tuple(replace(t, language_ietf="en-US") if t.id == 5 else t for t in plan.info.tracks)
    plan = replace(plan, info=replace(plan.info, tracks=tracks))
    assert ff._finish_mkvmerge_output(plan, str(out)) == "loses track 5's language en-US"


def test_the_check_catches_the_tracks_out_of_order(tmp_path):
    from dataclasses import replace
    plan, out = _plan_and_output(tmp_path)
    plan = replace(plan, kept=[0, 3, 1, 4, 5])
    assert ff._finish_mkvmerge_output(plan, str(out)) \
        == "does not hold the tracks the job keeps, in its order"
