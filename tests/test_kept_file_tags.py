"""
A job or an AC3 Forge run never replaces a file with one that has lost the
file's own tags.

FFmpeg's MP4 muxer writes a fixed list of tags and no others
(mov_write_ilst_box, libavformat/movenc.c, FFmpeg 8.1.3). Measured with a
real job before this file existed: an MP4 tagged the way iTunes and tag
editors tag one, rewritten as an MP4 to drop a track, lost its content
rating ("iTunEXTC"), cast ("iTunMOVI"), a custom tag, its purchase date,
rating and sort name; a Matroska converted to MP4 lost any tag outside that
list. No revert could give them back, as a revert writes an MP4 the same
way, and nothing said they had gone.

Now the staging hook compares the source's tags with the finished output's
while both exist and nothing has been written over
(worker._lost_tags_error, probe.lost_file_tags). Missing or changed, the run
fails with the source untouched and no revert point recorded, and History
says which tags. Names match ignoring case. The tags a muxer writes for
itself, whatever it is given, are passed over (probe.REWRITTEN_TAGS). A
scan does not queue such a file again while its plan is the same, as for
audio that cannot be copied (scanner._held_back); a plan that writes a
Matroska instead is tried.

The fixtures carry tags FFmpeg itself can write but not carry across: an
MP4's tag as a QuickTime metadata key, which an ordinary MP4 rewrite drops,
and a Matroska's own tag, which no MP4 can hold. The real-file tests run
the real scan, worker and Forge.

Verified by mutation, 14 applied, 14 killed. Each was first run against
the whole suite as it stood without this file (2682 tests): 8 survived it.
The other 6 were killed there by real jobs whose files the mutant made
look as if they lost a tag (a GoPro-layout job, a cover kept in an MP4, an
AVI converted to MP4, the unreadable date in test_creation_date.py) and,
for holding a file back whatever its plan, by the uncopyable audio rescan:

  • the check not run for a job             → the MP4 rewrite, the
                                              conversion, the rescan
  • not run for a Forge run                 → Forge refusing
  • a missing tag not noticed               → the comparison, the real
                                              refusals
  • a changed value not noticed             → the comparison, case
  • each of the five passed-over tags
    counted, one mutant each                → that tag; the ordinary MP4
                                              job for the brand, the
                                              compatible brands and the
                                              encoder
  • tags passed over by prefix              → the tags that only resemble
                                              one
  • names matched by case                   → the comparison, the Matroska
                                              kept a Matroska, the rescan
  • an output nothing can read let through  → the unreadable output
  • a refused file queued again             → the rescan
  • held back whatever its plan             → the rescan

The creation date is compared to the second (_same_second). An MP4 holds it
in whole seconds, and a Matroska's date with microseconds came back from a
conversion as .000000 in the same second, refused as lost (issue #61).
Verified by mutation, 4 applied, 4 killed, each surviving the whole suite as
it stood (2833 tests) first:

  • the date compared as written            → kept to the second, and the
                                              fractional conversions in
                                              test_creation_date
  • compared to the minute                  → another second
  • rounded to the nearest second           → .5 and .999999
  • every tag compared to the second        → only the creation date
"""
import asyncio
import hashlib
import json
import subprocess

import pytest

from app.core.probe import REWRITTEN_TAGS, lost_file_tags, probe_file
from tests import test_quicktime_files as _qt
from tests.test_quicktime_files import _WS, needs_ffmpeg

# That file's fixture: a file-backed database every session points at,
# scratch folders and an empty library, with the Forge module. English
# audio is kept, revert points are on, Prefer MP4 is off. Bound by
# assignment, as test_forge_undo_identity.py does: pyflakes reads an
# imported fixture as unused.
rig = _qt.rig


# ── The comparison ───────────────────────────────────────────────────────────

def _tags(**tags):
    return {"format": {"format_name": "mov,mp4,m4a,3gp,3g2,mj2", "tags": tags}}


def test_every_tag_kept_is_no_loss():
    source = _tags(title="A Film", iTunEXTC="mpaa|PG-13|300|", creation_time="2020")

    assert lost_file_tags(source, source) == []


def test_a_missing_tag_and_a_changed_one_are_lost_by_the_sources_names():
    source = _tags(title="A Film", iTunEXTC="mpaa|PG-13|300|", sort_name="Film, A")
    output = _tags(title="Another Film", sort_name="Film, A")

    assert lost_file_tags(source, output) == ["title", "iTunEXTC"]


def test_names_match_ignoring_case():
    """A Matroska keeps its tags in capitals, an MP4 in lower case."""
    assert lost_file_tags(_tags(TITLE="A Film"), _tags(title="A Film")) == []
    assert lost_file_tags(_tags(title="A Film"), _tags(TITLE="A Film")) == []
    assert lost_file_tags(_tags(TITLE="A Film"), _tags(title="a film")) == ["TITLE"]


def test_a_tag_only_the_output_has_is_no_concern():
    assert lost_file_tags(_tags(title="A Film"), _tags(title="A Film", genre="Drama")) == []


@pytest.mark.parametrize("key", ["major_brand", "minor_version", "compatible_brands",
                                 "encoder", "software"])
def test_what_a_muxer_writes_for_itself_is_passed_over(key):
    assert lost_file_tags(_tags(**{key: "theirs"}), _tags(**{key: "ours"})) == []
    assert lost_file_tags(_tags(**{key: "theirs"}), _tags()) == []
    assert lost_file_tags(_tags(**{key.upper(): "theirs"}), _tags()) == []


def test_the_rewritten_tags_are_the_five_seen_rewritten():
    assert REWRITTEN_TAGS == {"major_brand", "minor_version", "compatible_brands",
                              "encoder", "software"}


@pytest.mark.parametrize("key", ["encoder_settings", "software_version", "major_brand_x",
                                 "encoded_by", "com.apple.quicktime.software"])
def test_a_tag_that_only_resembles_one_is_not_passed_over(key):
    assert lost_file_tags(_tags(**{key: "x"}), _tags()) == [key]


@pytest.mark.parametrize("source, output", [
    (None, None), ({}, {}), ({"format": {}}, {"format": {"tags": {}}}),
])
def test_a_file_with_no_tags_loses_none(source, output):
    assert lost_file_tags(source, output) == []


# ── The creation date, kept to the second ────────────────────────────────────

@pytest.mark.parametrize("fraction", ["460353", "500000", "999999"])
def test_a_creation_date_kept_to_the_second_is_kept(fraction):
    """
    An MP4 holds the date in whole seconds, and FFmpeg's MP4 muxer drops the
    fraction rather than rounding it. A Matroska's date carries microseconds,
    as mkvmerge writes it, and came back from a conversion to MP4 as .000000
    in the same second: every such conversion was refused as losing the date
    (issue #61). .5 and .999999 fail a rule that rounds to the nearest
    second.
    """
    source = _tags(creation_time=f"2026-11-20T08:00:15.{fraction}Z")
    assert lost_file_tags(source, _tags(creation_time="2026-11-20T08:00:15.000000Z")) == []


@pytest.mark.parametrize("output", [
    _tags(creation_time="2026-11-20T08:00:16.000000Z"),
    _tags(creation_time="2026-11-20T08:01:15.000000Z"),
    _tags(),
], ids=["another second", "another minute", "no date"])
def test_a_creation_date_not_kept_to_the_second_is_lost(output):
    source = _tags(creation_time="2026-11-20T08:00:15.460353Z")
    assert lost_file_tags(source, output) == ["creation_time"]


def test_a_creation_date_that_is_not_a_date_is_compared_as_written():
    """FFmpeg passes over a date it cannot read; there is no second to match."""
    assert lost_file_tags(_tags(creation_time="last spring"),
                          _tags(creation_time="last spring")) == []
    assert lost_file_tags(_tags(creation_time="last spring"),
                          _tags(creation_time="2026-11-20T08:00:15.000000Z")) == ["creation_time"]


def test_only_the_creation_date_is_compared_to_the_second():
    """Another tag that holds a date is compared as written."""
    source = _tags(date="2026-11-20T08:00:15.460353Z")
    assert lost_file_tags(source, _tags(date="2026-11-20T08:00:15.000000Z")) == ["date"]


# ── The check ────────────────────────────────────────────────────────────────

@needs_ffmpeg
def test_an_output_nothing_can_read_stops_the_run(tmp_path):
    """A tag cannot be shown kept on a file nothing could read."""
    import app.core.worker as worker

    source = _file(tmp_path / "Film.mp4", "mp4")
    broken = tmp_path / "out.tmp"
    broken.write_bytes(b"not a media file")

    error = asyncio.run(worker._lost_tags_error(str(source), str(broken)))

    assert error.startswith("Could not check that the file keeps its own tags, "
                            "so it has been left as it was: ")
    assert asyncio.run(worker._lost_tags_error(str(source), str(source))) is None


# ── Real files ───────────────────────────────────────────────────────────────

def _set(rig, **settings):
    from app.database.models import AppSetting

    with rig.Session() as db:
        for key, value in settings.items():
            db.merge(AppSetting(key=key, value=json.dumps(value)))
        db.commit()


def _file(path, muxer, *, tag=None, audio=("eng", "ger"), channels=2, extra=()):
    """
    A file with one audio track per language and the title "A Film";
    `tag` adds one more tag of the file's own, as a QuickTime metadata key
    in an MP4, which an ordinary MP4 rewrite drops. `extra` is more FFmpeg
    output options.
    """
    cmd = ["ffmpeg", "-v", "error", "-y",
           "-f", "lavfi", "-i", "testsrc=size=64x36:rate=10:duration=1"]
    for n, _ in enumerate(audio):
        cmd += ["-f", "lavfi", "-i", f"sine=frequency={440 + 110 * n}:duration=1"]
    cmd += ["-map", "0:v"]
    for n, language in enumerate(audio):
        cmd += ["-map", f"{n + 1}:a", f"-metadata:s:a:{n}", f"language={language}"]
    cmd += ["-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p",
            "-c:a", "aac", "-ac", str(channels), "-metadata", "title=A Film"]
    if tag:
        cmd += ["-metadata", f"{tag[0]}={tag[1]}"]
        if muxer == "mp4":
            cmd += ["-movflags", "+use_metadata_tags"]
    subprocess.run(cmd + list(extra) + ["-f", muxer, str(path)], check=True)
    return path


def _digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _audio(path):
    return [s["tags"]["language"] for s in probe_file(str(path))["streams"]
            if s["codec_type"] == "audio"]


def _file_tags(path):
    return {k.lower(): v for k, v in
            (probe_file(str(path))["format"].get("tags") or {}).items()}


def _scan_and_run(rig):
    """Scan the library and run every job it queued. Returns the jobs'
    (status, error) and the scan's stats."""
    from app.core import scanner
    from app.database.models import QueueItem

    with rig.Session() as db:
        stats = scanner.scan_library(db, [str(rig.library)], force_probe=True)

    async def go():
        while (job_id := rig.worker._claim_next()) is not None:
            await rig.worker._run_and_broadcast(job_id, _WS(), asyncio.get_running_loop())
    asyncio.run(go())

    with rig.Session() as db:
        jobs = [(q.status, q.error_message) for q in
                db.query(QueueItem).order_by(QueueItem.id)]
    return jobs, stats


def _leftovers(rig):
    """Anything a run left beside the file, and every revert point."""
    from app.database.models import RevertPoint

    with rig.Session() as db:
        points = db.query(RevertPoint).count()
    return sorted(p.name for p in rig.library.iterdir()), points


@needs_ffmpeg
def test_an_mp4_rewrite_that_would_lose_a_tag_leaves_the_file_as_it_was(rig):
    """The German track is to go, and the MP4 muxer cannot write the
    file's purchase date back."""
    source = _file(rig.library / "Film.mp4", "mp4", tag=("purchase_date", "2020-01-01"))
    before = _digest(source)
    assert _file_tags(source)["purchase_date"] == "2020-01-01", "fixture"

    jobs, _ = _scan_and_run(rig)

    assert len(jobs) == 1 and jobs[0][0] == "failed"
    assert jobs[0][1] == (f"{rig.worker.LOST_TAGS} It would lose: purchase_date.")
    assert _digest(source) == before
    assert _leftovers(rig) == (["Film.mp4"], 0)


@needs_ffmpeg
def test_a_conversion_to_mp4_that_would_lose_a_tag_leaves_the_file_as_it_was(rig):
    """A Matroska's own tag, its case as Matroska keeps it, which no MP4
    can hold. Its title an MP4 can hold, and is not listed."""
    _set(rig, prefer_mp4_container=True)
    source = _file(rig.library / "Film.mkv", "matroska", tag=("RELEASE_GROUP", "NTb"),
                   audio=("eng",))
    before = _digest(source)

    jobs, _ = _scan_and_run(rig)

    assert [s for s, _ in jobs] == ["failed"]
    assert jobs[0][1].endswith("It would lose: RELEASE_GROUP.")
    assert _digest(source) == before
    assert _leftovers(rig) == (["Film.mkv"], 0)


@needs_ffmpeg
def test_a_matroska_left_a_matroska_keeps_the_tag_and_is_processed(rig):
    source = _file(rig.library / "Film.mkv", "matroska", tag=("RELEASE_GROUP", "NTb"))

    jobs, _ = _scan_and_run(rig)

    assert [s for s, _ in jobs] == ["success"]
    assert _audio(source) == ["eng"]
    assert _file_tags(source)["release_group"] == "NTb"
    assert _file_tags(source)["title"] == "A Film"


@needs_ffmpeg
def test_an_ordinary_mp4_job_is_not_refused(rig):
    """
    A file as HandBrake writes one, its own brand and encoder. The job
    rewrites both, as every job does, and keeps the title: the German
    track goes as before.
    """
    source = _file(rig.library / "Film.mp4", "mp4",
                   extra=["-brand", "mp42", "-metadata", "encoding_tool=HandBrake 1.6"])
    before = _file_tags(source)
    assert (before["major_brand"], before["encoder"]) == ("mp42", "HandBrake 1.6"), "fixture"

    jobs, _ = _scan_and_run(rig)

    assert [s for s, _ in jobs] == ["success"]
    assert _audio(source) == ["eng"]
    after = _file_tags(source)
    assert after["title"] == "A Film"
    for key in ("major_brand", "compatible_brands", "encoder"):
        assert after[key] != before[key], f"{key} was not rewritten: the test shows nothing"


@needs_ffmpeg
def test_a_refused_file_is_not_queued_again_until_its_plan_changes(rig):
    """
    A scan with the same plan would run the same job to the same refusal.
    With Prefer MP4 off the plan is another, which keeps the Matroska and
    its tag, and the file is queued and processed.
    """
    _set(rig, prefer_mp4_container=True)
    source = _file(rig.library / "Film.mkv", "matroska", tag=("RELEASE_GROUP", "NTb"))
    jobs, _ = _scan_and_run(rig)
    assert [s for s, _ in jobs] == ["failed"], "fixture"

    jobs, stats = _scan_and_run(rig)
    assert [s for s, _ in jobs] == ["failed"]
    assert stats.held == 1 and stats.queued == 0

    _set(rig, prefer_mp4_container=False)
    jobs, stats = _scan_and_run(rig)
    assert [s for s, _ in jobs] == ["failed", "success"]
    assert stats.held == 0 and stats.queued == 1
    assert _audio(source) == ["eng"]
    assert _file_tags(source)["release_group"] == "NTb"


@needs_ffmpeg
def test_forge_that_would_lose_a_tag_leaves_the_file_as_it_was(rig):
    from app.core import scanner
    from app.database.models import Ac3ForgeJob, MediaFile

    source = _file(rig.library / "Film.mp4", "mp4", tag=("purchase_date", "2020-01-01"),
                   audio=("eng",), channels=6)
    before = _digest(source)

    with rig.Session() as db:
        scanner.scan_library(db, [str(rig.library)], force_probe=True)
        job_id = rig.forge.queue_forge_job(db, db.query(MediaFile).one().id).id
    assert asyncio.run(rig.worker._process_next_forge(_WS())) is True

    with rig.Session() as db:
        job = db.get(Ac3ForgeJob, job_id)
        assert job.status == "failed"
        assert job.error_message == f"{rig.worker.LOST_TAGS} It would lose: purchase_date."
    assert _digest(source) == before


@needs_ffmpeg
def test_forge_on_a_file_that_keeps_its_tags_runs(rig):
    from app.core import scanner
    from app.database.models import Ac3ForgeJob, MediaFile

    source = _file(rig.library / "Film.mp4", "mp4", audio=("eng",), channels=6)

    with rig.Session() as db:
        scanner.scan_library(db, [str(rig.library)], force_probe=True)
        job_id = rig.forge.queue_forge_job(db, db.query(MediaFile).one().id).id
    assert asyncio.run(rig.worker._process_next_forge(_WS())) is True

    with rig.Session() as db:
        assert db.get(Ac3ForgeJob, job_id).status == "success"
    assert [s["codec_name"] for s in probe_file(str(source))["streams"]
            if s["codec_type"] == "audio"] == ["aac", "ac3"]
