#!/usr/bin/env python3
"""
Run a media library through Remuxarr under many settings profiles and report
anything that breaks a rule which should hold whatever the settings are.

This is an exploratory tool, not part of the test suite. It exists because
the decision engine and the worker branch on more settings than a hand-written
expectation table can follow: the first expectation table written for the
generated library went stale in two places within one release cycle, and
neither was a bug. So nothing here says what a file's decision should be.
Each profile is checked against invariants instead:

  1. Every job ends as success, review or skipped, and nothing logs an
     exception. A scan error is expected only on a file ffprobe cannot read.
  2. Every output probes, and its video and audio decode without an error.
  3. Every input stream is in the output, dropped by a planned action or
     extracted to a sidecar, and the chapters survive. Losses the project has
     already decided on are listed separately with where that is recorded.
  4. Every kept audio and subtitle track carries the language the plan gave
     it: the Fix target if there is one, otherwise its own tag.
  5. Each extraction adds exactly one sidecar, and an existing sidecar's text
     survives somewhere unless Existing Subtitle Files is Replace.
  6. Nothing outside a processed file and its sidecars changes: files sent to
     review, skipped or excluded keep every byte.
  7. A full rescan afterwards queues nothing for a file that was processed
     or had nothing to do.
  8. No temp file is left behind, and the source is gone only when the
     container changed.

Every question is answered as the profile's runner choices say, through
the app's own route functions: after the scan and after each queue run,
until none is left. Then:

 16. What the review preview refuses, apply refuses; for an answer it
     accepts, the preview's outcome is what apply did.
 17. A processed file's job plans what its subtitle answers said, and a
     file whose every question was answered is not still waiting.
 18. An applied language is the planned language of its track; an ignored
     one plans none.
 19. After a rescan, nothing answered is asked again.

Then every AC3 Forge candidate is queued through the app's own route
function and the worker runs the Forge jobs:

 20. Every add succeeds, and nothing logs an exception.
 21. The file gains exactly one AC3 5.1 carrying Forge's marker, after the
     last audio track, and every other stream is as it was.
 22. The forged file probes and decodes cleanly.
 23. A full rescan queues nothing for a forged file that had nothing left
     to do before the forge.
 24. With the runner's _forge_undo choice, every add is undone the same
     way, and the file is back to exactly the streams it had before.
 25. No Forge temp file or unfinished Forge job is left, and nothing in
     the library changed but the forged files.

Then, with revert on, every revert point is restored through the app's own
restore_revert_point (a file check 7 found queued again is skipped, as the
revert route refuses one), and:

  9. A file has a revert point exactly when its job lost a track a point
     can hold: anything but an attachment. With revert off, none has one.
 10. Every restore succeeds, onto the original name and extension, and a
     converted output goes away.
 11. The restored file matches the original stream for stream, and an
     MP4's fast-start layout matches too.
 12. The restored file probes and decodes cleanly.
 13. The job's other files are back as they were, and nothing else moved.
 14. A full rescan plans the restored file exactly as it planned the
     original.
 15. The point is spent: no row, no sidecar, no temp file left, and no
     Forge job on the file still marked a success.

A broken rule is a lead, not a verdict. Reproduce it with the real code
before calling it a bug, and turn a real one into a normal test in tests/.

Every profile runs in its own process with its own copy of the library, its
own database, temp folder and recycle folder, all under --out. The app's
DATABASE_PATH, TEMP_DIR and RECYCLE_DIR are pointed there before it is
imported, so a run inside the Remuxarr container leaves the live database,
temp folder and recycle bin alone. Dry run is turned off in the copy, which
is the point: the worker really rewrites the copied files.

Usage (from the repository root, with the FFmpeg the app uses first on PATH):

    tools/testlib/make_test_library.sh /tmp/testlib
    python tools/testlib/explore.py /tmp/testlib
    python tools/testlib/explore.py /tmp/testlib --profile default -j 2
    python tools/testlib/explore.py --list-profiles

Exit status: 0 when every check passed, 1 when anything was found, 2 when a
profile could not run at all.
"""
from __future__ import annotations

import argparse
import concurrent.futures
import hashlib
import itertools
import json
import os
import random
import re
import shutil
import subprocess
import sys
import tempfile
import time
from collections import Counter
from pathlib import Path

HERE = Path(__file__).resolve().parent
DEFAULT_APP = HERE.parent.parent

# Settings every profile gets, whatever else it sets. dry_run_mode defaults
# on, and nothing would be processed with it. scan_paths is filled in per run.
FORCED = {"dry_run_mode": False, "auto_start_jobs": True}

# The settings the profiles vary: every one the decision engine, the job path
# or revert reads that can be set without Sonarr, Radarr or Plex. Left out:
# original_language_audio, which needs an original language from Sonarr or
# Radarr, and max_concurrent_jobs, since jobs here run one at a time.
# Each list starts with the app's default.
VARIED = {
    "prefer_mp4_container":          [True, False],
    "add_faststart_to_mp4":          [True, False],
    "fix_undefined_language":        ["always_leave", "always_fix", "always_ask"],
    "fix_undefined_language_audio":  ["always_leave", "always_fix", "always_ask"],
    "keep_default_audio":            [True, False],
    "keep_forced_subtitles":         [True, False],
    "extract_text_subtitles_to_srt": [True, False],
    "existing_srt_handling":         ["keep_both", "replace", "embedded_first"],
    "image_subtitle_handling":       ["always_ask", "always_keep", "always_remove"],
    "font_attachment_handling":      ["always_ask", "always_keep", "always_remove"],
    "keep_audio_languages":          [["eng"], ["eng", "jpn"], ["fre"]],
    "keep_subtitle_languages":       [["eng"], ["eng", "fre"], ["jpn"]],
    "skip_extras":                   [True, False],
    "revert_enabled":                [True, False],
}

# Choices the runner makes for the user, varied with the settings so every
# setting value meets every answer at least once. Not app settings: they
# are kept out of the settings each profile writes. The first of each is
# the default profile's. See _answer_reviews.
RUNNER_CHOICES = {
    # Every flagged track of a subtitle review (image, styled, encoding)
    # gets this; an answer the app refuses falls back to keep.
    "_subtitle_answer": ["keep", "remove", "extract"],
    # Every audio and subtitle language question: ignore, or this code.
    "_language_answer": ["ignore", "eng", "jpn"],
    # Whether every successful AC3 Forge add is then undone.
    "_forge_undo": [False, True],
}

IMAGE_SUB_CODECS = {"hdmv_pgs_subtitle", "pgssub", "dvd_subtitle", "dvdsub",
                    "dvb_subtitle", "vobsub", "xsub"}

# Losses the project has decided to accept, keyed by stream kind. Each is
# listed in the report rather than counted as a finding, with where the
# decision is written down.
KNOWN_LOSSES = {
    "attachment": "MP4 cannot hold attachments; app/core/ffmpeg.py, the "
                  "attachment map in build_ffmpeg_command",
    "cover": "cover art is not mapped into MP4; app/core/probe.py, "
             "extract_tracks skipping attached_pic",
}


# ── Profiles ────────────────────────────────────────────────────────────────

def _pairwise_profiles() -> list[dict]:
    """
    A small set of profiles in which every pair of values of any two varied
    settings appears together at least once. Greedy and seeded, so the same
    VARIED gives the same profiles; the coverage is checked, not assumed.
    """
    dims = {**VARIED, **RUNNER_CHOICES}
    keys = list(dims)
    order = {k: i for i, k in enumerate(keys)}

    def pair(a, va, b, vb):
        if order[a] > order[b]:
            a, va, b, vb = b, vb, a, va
        return (a, json.dumps(va), b, json.dumps(vb))

    def pairs_of(row):
        return {pair(a, row[a], b, row[b]) for a, b in itertools.combinations(keys, 2)}

    need = {pair(a, va, b, vb)
            for a, b in itertools.combinations(keys, 2)
            for va in dims[a] for vb in dims[b]}
    everything = set(need)
    rng = random.Random(0)
    rows: list[dict] = []
    while need:
        best = None
        seed_pair = min(need)
        for _ in range(60):
            a, va, b, vb = seed_pair
            row = {k: rng.choice(dims[k]) for k in keys}
            row[a], row[b] = json.loads(va), json.loads(vb)
            for k in rng.sample(keys, len(keys)):
                if k in (a, b):
                    continue
                row[k] = max(dims[k], key=lambda v, k=k: sum(
                    pair(k, v, x, row[x]) in need for x in keys if x != k))
            got = pairs_of(row) & need
            if best is None or len(got) > len(best[1]):
                best = (row, got)
        rows.append(best[0])
        need -= best[1]
    covered = set().union(*(pairs_of(r) for r in rows))
    if not everything <= covered:
        raise SystemExit("pairwise profiles do not cover every pair")
    return rows


def all_profiles() -> dict[str, dict]:
    profiles = {"default": {}}
    for i, row in enumerate(_pairwise_profiles(), 1):
        profiles[f"p{i:02d}"] = row
    return profiles


def _check_profile_against_app(settings: dict) -> None:
    """
    update_app_setting stores whatever it is given; only the API checks a
    value. A profile naming a retired setting or value would otherwise run
    as if it meant something, so check it against the app's own schema.
    """
    from app.api.routes.settings import _SCHEMA_BY_KEY
    from app.database.session import DEFAULT_APP_SETTINGS
    for key, value in settings.items():
        if key not in DEFAULT_APP_SETTINGS:
            raise SystemExit(f"profile sets {key!r}, which the app does not have")
        options = (_SCHEMA_BY_KEY.get(key) or {}).get("options")
        if options:
            allowed = [o.get("value", o) if isinstance(o, dict) else o for o in options]
            if value not in allowed:
                raise SystemExit(f"profile sets {key}={value!r}; the app allows {allowed}")


# ── Media helpers ───────────────────────────────────────────────────────────

FFPROBE = os.environ.get("FFPROBE", "ffprobe")
FFMPEG = os.environ.get("FFMPEG", "ffmpeg")


def probe(path: str) -> dict | None:
    r = subprocess.run(
        [FFPROBE, "-v", "error", "-show_streams", "-show_chapters",
         "-show_format", "-of", "json", path],
        capture_output=True, text=True)
    if r.returncode != 0:
        return None
    data = json.loads(r.stdout or "{}")
    return data if data.get("streams") else None


def decode_errors(path: str) -> str:
    r = subprocess.run(
        [FFMPEG, "-nostdin", "-v", "error", "-i", path,
         "-map", "0:v?", "-map", "0:a?", "-f", "null", "-"],
        capture_output=True, text=True)
    return r.stderr.strip() or (f"exit {r.returncode}" if r.returncode else "")


def kind(stream: dict) -> str | None:
    t = stream.get("codec_type")
    if t == "video":
        return "cover" if (stream.get("disposition") or {}).get("attached_pic") else "video"
    if t == "audio":
        return "audio"
    if t == "subtitle":
        return "image-sub" if stream.get("codec_name") in IMAGE_SUB_CODECS else "text-sub"
    if t == "attachment":
        return "attachment"
    return None     # data streams: an MP4 chapter track, for one


def is_mp4(info: dict) -> bool:
    return "mp4" in ((info.get("format") or {}).get("format_name") or "")


def sha(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def inventory(root: Path) -> dict[str, str]:
    return {p.relative_to(root).as_posix(): sha(p)
            for p in sorted(root.rglob("*")) if p.is_file()}


# ── One profile, in its own process ─────────────────────────────────────────

def run_child(spec_path: str) -> None:
    spec = json.loads(Path(spec_path).read_text())
    scratch = Path(spec["scratch"])
    lib = scratch / "library"
    shutil.copytree(spec["library"], lib, symlinks=False)
    (scratch / "recycle").mkdir()

    # Before the app is imported: its settings object reads these once.
    os.environ["REMUXARR_DATABASE_PATH"] = str(scratch / "remuxarr.db")
    os.environ["REMUXARR_TEMP_DIR"] = str(scratch / "tmp")
    os.environ["REMUXARR_RECYCLE_DIR"] = str(scratch / "recycle")
    sys.path.insert(0, spec["app"])

    import asyncio
    import logging

    class Collect(logging.Handler):
        def __init__(self):
            super().__init__(logging.WARNING)
            self.records = []

        def emit(self, record):
            self.records.append({
                "logger": record.name,
                "level": record.levelname,
                "message": record.getMessage()[:500],
                "exception": bool(record.exc_info),
            })

    collect = Collect()
    logging.getLogger().addHandler(collect)
    # The app's own log, to run.log, for reading a finding back.
    to_log = logging.StreamHandler(sys.stderr)
    to_log.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
    logging.getLogger().addHandler(to_log)
    logging.getLogger().setLevel(logging.INFO)

    from app.config import settings as app_paths
    for name, want in (("DATABASE_PATH", scratch / "remuxarr.db"),
                       ("TEMP_DIR", scratch / "tmp"),
                       ("RECYCLE_DIR", scratch / "recycle")):
        if Path(getattr(app_paths, name)) != want:
            raise SystemExit(f"{name} is {getattr(app_paths, name)}, not the scratch copy")

    from app.api.ws_manager import ws_manager
    from app.core import scanner, worker
    from app.core.langcodes import canonical
    from app.database import session as S
    from app.database.models import MediaFile, PlannedAction, QueueItem

    app_settings = {k: v for k, v in spec["settings"].items() if k not in RUNNER_CHOICES}
    choices = {k: spec["settings"].get(k, v[0]) for k, v in RUNNER_CHOICES.items()}
    settings = {**FORCED, **app_settings, "scan_paths": [str(lib)]}
    _check_profile_against_app(app_settings)

    before = inventory(lib)
    probes_before = {rel: probe(str(lib / rel)) for rel in before}
    # For check 11: whether each MP4 starts with its moov, before any job.
    from app.core.probe import is_faststart_mp4
    faststart_before = {r: is_faststart_mp4(str(lib / r)) for r, info in probes_before.items()
                        if info is not None and is_mp4(info)}

    S.init_db()
    timings = {}
    with S.SessionLocal() as db:
        for key, value in settings.items():
            S.update_app_setting(db, key, value)
        t = time.monotonic()
        scan = scanner.scan_library(db, [str(lib)], force_probe=True)
        timings["scan"] = round(time.monotonic() - t, 2)
        source_of = {m.id: m.path for m in db.query(MediaFile)}

    async def drain():
        loop = asyncio.get_running_loop()
        ran = 0
        while (job_id := await loop.run_in_executor(None, worker._claim_next)) is not None:
            await worker._run_and_broadcast(job_id, ws_manager, loop)
            ran += 1
        return ran

    findings: list[dict] = []
    known: list[dict] = []
    files: list[dict] = []

    def rel(path: str) -> str:
        try:
            return Path(path).relative_to(lib).as_posix()
        except ValueError:
            return path

    def find(check: int, path: str, detail: str):
        # FFmpeg's messages carry addresses that differ every run; without
        # them, one cause in several profiles groups as one line.
        detail = re.sub(r" @ 0x[0-9a-f]+", "", detail)
        findings.append({"check": check, "file": rel(path), "detail": detail})

    # Every question the scan raised answered, then the queue, then any new
    # question (a job can raise one, as an undecodable subtitle does) and
    # the queue again, until none comes up. Answered before the first run,
    # as a user can: a question answered after its file's job starts a
    # second job on the processed file, which the per-job checks below are
    # not built to judge (a file with two is marked in summary.json).
    t = time.monotonic()
    answered: dict[int, dict] = {}
    _answer_reviews(S, choices, answered, find, canonical)
    ran = asyncio.run(drain())
    for _ in range(4):
        if not _answer_reviews(S, choices, answered, find, canonical):
            break
        ran += asyncio.run(drain())
    timings["jobs"] = round(time.monotonic() - t, 2)

    after = inventory(lib)

    # 1. Outcomes.
    unreadable = sum(1 for r in before if probes_before[r] is None
                     and (lib / r).suffix.lower() in _MEDIA_SUFFIXES)
    if scan.errors > unreadable:
        find(1, str(lib), f"{scan.errors} scan error(s), but only {unreadable} "
                          "file(s) that ffprobe itself cannot read")
    for rec in collect.records:
        if rec["exception"]:
            find(1, "", f"exception logged by {rec['logger']}: {rec['message']}")

    allowed_changes: set[str] = set()
    with S.SessionLocal() as db:
        cfg = S.get_app_settings(db)
        for media in db.query(MediaFile).order_by(MediaFile.id):
            source = source_of.get(media.id, media.path)
            jobs = db.query(QueueItem).filter(QueueItem.file_id == media.id) \
                     .order_by(QueueItem.id).all()
            entry = {"id": media.id, "file": rel(source), "status": media.status,
                     "jobs": [j.status for j in jobs]}
            if media.id in answered:
                entry["answered"] = answered[media.id]
            files.append(entry)
            for j in jobs:
                if j.status in ("success", "manual_review", "skipped"):
                    continue
                error = j.error_message or j.reason or ""
                taken = _refused_name(error)
                if (j.status == "failed" and taken
                        and rel(str(Path(source).parent / taken)) in before):
                    known.append({"file": rel(source), "lost": "job refused",
                                  "why": f"{taken} already existed: app/core/worker.py, "
                                         "_output_name_taken"})
                    entry["refused"] = True
                    continue
                find(1, source, f"job {j.status}: {error[:300]}")
            done = [j for j in jobs if j.status == "success"]
            if not done:
                continue
            job = done[-1]
            # A question raised by a job and answered after it starts a
            # second job on the processed file. The checks below judge one
            # job against the original, so they leave such a file out.
            several = len(done) > 1
            if several:
                entry["several_jobs"] = len(done)
            actions = db.query(PlannedAction).filter(PlannedAction.queue_item_id == job.id) \
                        .order_by(PlannedAction.order).all()
            entry["output"] = rel(media.path)
            entry["actions"] = [f"{a.action_type}:{a.stream_index}" for a in actions]
            entry["source"] = source
            entry["plan"] = _plan_of(actions)
            _check_processed(find, known, lib, source, media.path, actions,
                             probes_before.get(rel(source)), before, after,
                             cfg, canonical, allowed_changes, entry, several)
            if not several:
                _check_answers_carried_out(find, source, actions, entry.get("answered"),
                                           canonical, probes_before.get(rel(source)))
        _check_answered_left_review(find, db, files, source_of)

    # 6. Nothing else changed.
    for path in sorted(set(before) | set(after)):
        if path in allowed_changes:
            continue
        if before.get(path) != after.get(path):
            what = ("appeared" if path not in before else
                    "disappeared" if path not in after else "changed")
            find(6, str(lib / path), f"{what}, outside any processed file")

    # 8. Temp files: one line per folder, the names in the result file.
    tmp = scratch / "tmp"
    temp_left = {"temp folder": sorted(p.name for p in tmp.rglob("*") if p.is_file())
                 if tmp.exists() else []}
    for p in sorted(lib.rglob("*.remuxarr_tmp")):
        temp_left.setdefault(p.parent.relative_to(lib).as_posix() or ".", []).append(p.name)
    for where, names in temp_left.items():
        if names:
            find(8, where, f"{len(names)} temp file(s) left behind")

    # 7. A full rescan queues nothing for a file that was processed or had
    # nothing to do. A failed job is left out: a full scan decides every
    # file again, so it queues a failed one again, which is how it is retried.
    settled = {e["id"] for e in files
               if e["jobs"] and e["jobs"][-1] in ("success", "skipped")}
    with S.SessionLocal() as db:
        known_jobs = {q.id for q in db.query(QueueItem)}
        rescan = scanner.scan_library(db, [str(lib)], force_probe=True)
        for q in db.query(QueueItem).filter(QueueItem.status == "pending"):
            if q.id not in known_jobs and q.file_id in settled:
                media = db.get(MediaFile, q.file_id)
                find(7, media.path, f"queued again by a rescan: {q.reason or ''}")
        # 19. Answers stick: the rescan asks nothing already answered.
        _check_answers_stick(find, db, files, source_of)

    forge = _forge_round_trips(find, lib, scratch, files, rel, collect,
                               choices["_forge_undo"])

    reverts = _revert_round_trips(
        find, known, lib, scratch, cfg, files, before, probes_before,
        faststart_before, rel, collect,
    )

    result = {
        "profile": spec["name"], "settings": spec["settings"],
        "reverts": reverts,
        "forge": forge,
        "scan": {k: getattr(scan, k) for k in ("total", "queued", "manual_review",
                                                "skipped", "errors", "excluded")},
        "rescan_queued": rescan.queued, "jobs_run": ran, "timings": timings,
        "findings": findings, "known_losses": known,
        "temp_left": {k: v for k, v in temp_left.items() if v},
        "choices": choices,
        "several_jobs": [e["file"] for e in files if e.get("several_jobs")],
        "files": [{k: v for k, v in e.items() if k not in ("id", "source", "plan", "touched")}
                  for e in files],
        "logged_errors": [r for r in collect.records
                          if r["level"] in ("ERROR", "CRITICAL") and not r["exception"]],
    }
    Path(spec["result"]).write_text(json.dumps(result, indent=1))


# ── AC3 Forge (checks 20 to 25) ──────────────────────────────────────────────

def _forge_round_trips(find, lib, scratch, files, rel, collect, undo: bool) -> dict:
    """
    Queue every Forge candidate through the app's own route function, run
    the Forge jobs through the worker, and check the result (20 to 23);
    with `undo`, undo every add the same way and check that (24). Then 25.
    Returns counts for summary.json.
    """
    import asyncio

    from fastapi import HTTPException

    from app.api.routes.forge import QueueForgeRequest, add_to_queue, undo_job
    from app.api.ws_manager import ws_manager
    from app.core import scanner, worker
    from app.core.forge import FORGE_AC3_HANDLER_NAME, get_candidates
    from app.database import session as S
    from app.database.models import Ac3ForgeJob, MediaFile, QueueItem

    async def run_forge_jobs():
        while await worker._process_next_forge(ws_manager):
            pass

    first_record = len(collect.records)
    untouched_before = inventory(lib)
    added: dict[int, dict] = {}
    with S.SessionLocal() as db:
        for item in get_candidates(db, limit=10000)["items"]:
            media = db.get(MediaFile, item["id"])
            pre = probe(media.path)
            try:
                job = add_to_queue(QueueForgeRequest(file_id=media.id), db)
            except HTTPException as exc:
                find(20, media.path, f"a Forge candidate was refused: {exc.detail}")
                continue
            added[media.id] = {"job": job["id"], "path": media.path, "pre": pre}
    asyncio.run(run_forge_jobs())

    forged = []
    with S.SessionLocal() as db:
        for file_id, j in added.items():
            job = db.get(Ac3ForgeJob, j["job"])
            # 20. Every add succeeds.
            if job.status != "success":
                find(20, j["path"], f"Forge add {job.status}: {(job.error_message or '')[:200]}")
                continue
            forged.append(file_id)
            post = probe(j["path"])
            # 22. Readable.
            if post is None:
                find(22, j["path"], "the forged file does not probe")
                continue
            errors = decode_errors(j["path"])
            if errors:
                find(22, j["path"], f"decoding the forged file: {errors[:300]}")
            if j["pre"] is None:
                continue
            # 21. Every stream as it was, plus one marked AC3 5.1 after the
            # last audio track.
            marked = [i for i, st in enumerate(post["streams"])
                      if st.get("codec_name") == "ac3" and st.get("channels") == 6
                      and {k.lower(): v for k, v in (st.get("tags") or {}).items()}
                      .get("handler_name") == FORGE_AC3_HANDLER_NAME]
            if len(marked) != 1:
                find(21, j["path"], f"{len(marked)} AC3 5.1 tracks carry the Forge marker")
                continue
            ac3 = post["streams"][marked[0]]
            audio_before = sum(1 for st in j["pre"]["streams"] if st.get("codec_type") == "audio")
            audio_at = [st["index"] for st in post["streams"]
                        if st.get("codec_type") == "audio"].index(ac3["index"])
            if audio_at != audio_before:
                find(21, j["path"], f"the AC3 is audio track {audio_at}, "
                                    f"not {audio_before} after the last")
            rest = [_signature(st) for i, st in enumerate(post["streams"]) if i != marked[0]]
            was = [_signature(st) for st in j["pre"]["streams"]]
            if rest != was:
                find(21, j["path"], "beside the AC3: " + _first_difference(
                    was, rest, now="after the add", was="before"))
            j["post"] = post

    # 23. A rescan queues nothing for a forged file that had nothing left to
    # do before the forge.
    settled = {e["id"] for e in files
               if e["jobs"] and e["jobs"][-1] in ("success", "skipped")}
    with S.SessionLocal() as db:
        queued = {q.file_id for q in db.query(QueueItem)
                  .filter(QueueItem.status.in_(("pending", "processing")))}
        known_jobs = {q.id for q in db.query(QueueItem)}
        scanner.scan_library(db, [str(lib)], force_probe=True)
        for q in db.query(QueueItem).filter(QueueItem.status == "pending"):
            if (q.id not in known_jobs and q.file_id in forged
                    and q.file_id in settled and q.file_id not in queued):
                find(23, added[q.file_id]["path"],
                     f"queued by a rescan after the forge: {q.reason or ''}")

    # 24. An undo gives back exactly the streams from before the add.
    undone = 0
    if undo and forged:
        with S.SessionLocal() as db:
            for file_id in forged:
                try:
                    undo_job(added[file_id]["job"], db)
                except HTTPException as exc:
                    find(24, added[file_id]["path"], f"the undo was refused: {exc.detail}")
        asyncio.run(run_forge_jobs())
        with S.SessionLocal() as db:
            for file_id in forged:
                j = added[file_id]
                job = db.get(Ac3ForgeJob, j["job"])
                if job.status != "undone":
                    find(24, j["path"], f"Forge undo {job.status}: {(job.error_message or '')[:200]}")
                    continue
                undone += 1
                post = probe(j["path"])
                if post is None:
                    find(24, j["path"], "the undone file does not probe")
                    continue
                errors = decode_errors(j["path"])
                if errors:
                    find(24, j["path"], f"decoding the undone file: {errors[:300]}")
                if j["pre"] is not None:
                    was = [_signature(st) for st in j["pre"]["streams"]]
                    now = [_signature(st) for st in post["streams"]]
                    if now != was:
                        find(24, j["path"], _first_difference(
                            was, now, now="after the undo", was="before the add"))

    # 25. Nothing left over: no Forge temp file, no job still on its way,
    # and nothing in the library changed but the forged files.
    tmp = scratch / "tmp"
    left = [p.name for p in tmp.rglob("*") if p.is_file()] if tmp.exists() else []
    left += [p.name for p in lib.rglob("*.forge_tmp")]
    if left:
        find(25, "temp folder", f"{len(left)} temp file(s) left behind by Forge")
    with S.SessionLocal() as db:
        busy = db.query(Ac3ForgeJob).filter(Ac3ForgeJob.status.in_(
            ("pending", "processing", "undo_pending"))).count()
    if busy:
        find(25, "", f"{busy} Forge job(s) never finished")
    mine = {rel(j["path"]) for j in added.values()}
    now_inventory = inventory(lib)
    for p in sorted(set(untouched_before) | set(now_inventory)):
        if p not in mine and untouched_before.get(p) != now_inventory.get(p):
            find(25, str(lib / p), "changed by AC3 Forge, which only touches the forged file")
    for rec in collect.records[first_record:]:
        if rec["exception"]:
            find(20, "", f"exception logged by {rec['logger']}: {rec['message']}")
    return {"candidates": len(added), "forged": len(forged), "undone": undone}


# ── Review answers (checks 16 to 19) ────────────────────────────────────────

def _language_routes():
    """The audio and subtitle language review's apply and ignore functions."""
    from app.api.routes import audio_language, subtitle_language
    from app.database.models import AudioLanguageFlag, SubtitleLanguageFlag
    out = []
    for kind, router, flag in (("audio", audio_language.router, AudioLanguageFlag),
                               ("subtitle", subtitle_language.router, SubtitleLanguageFlag)):
        ends = {r.path.rsplit("/", 1)[-1]: r.endpoint for r in router.routes
                if "POST" in getattr(r, "methods", ())}
        out.append((kind, flag, ends["apply"], ends["ignore"]))
    return out


def _answer_reviews(S, choices, answered, find, canonical) -> int:
    """
    Answer every question waiting, through the app's own route functions
    called with a session, which is the request path less HTTP. Returns how
    many were answered; a question answered once is not answered again.

    Subtitle reviews go through the preview first and then apply, and the
    two are held to each other (check 16). Language questions get ignore or
    the profile's code.
    """
    from app.api.routes._language_review import ApplyRequest, IgnoreRequest
    from app.api.routes.queue import (
        ReviewApplyRequest, ReviewFileDecision, _flagged_entries,
        apply_review_decisions, preview_review_decisions,
    )
    from app.database.models import QueueItem

    count = 0
    with S.SessionLocal() as db:
        items = db.query(QueueItem).filter(QueueItem.status == "manual_review") \
                  .order_by(QueueItem.id).all()
        for item in items:
            flagged = _flagged_entries(item)
            if not flagged:
                continue
            record = answered.setdefault(item.file_id, {})
            if set(flagged) <= set(record.get("subtitle", {})):
                continue
            path = item.media_file.path
            choice = choices["_subtitle_answer"]
            want = {si: choice for si in flagged}

            def ask(answers):
                body = ReviewApplyRequest(files=[ReviewFileDecision(
                    file_id=item.file_id, answers=answers)])
                return body, preview_review_decisions(body, db)

            body, preview = ask(want)
            if preview["errors"]:
                refused = preview["errors"][0]["error"]
                # 16. What the preview refuses, apply refuses.
                applied = apply_review_decisions(body, db)
                if not applied["errors"]:
                    find(16, path, f"the preview refused {choice} but apply took it: "
                                   f"{refused[:160]}")
                    record["subtitle"] = want
                    count += 1
                    continue
                record.setdefault("refused", []).append(f"{choice}: {refused[:160]}")
                want = {si: "keep" for si in flagged}
                body, preview = ask(want)
                if preview["errors"]:
                    find(16, path, f"the preview refused keep as well: "
                                   f"{preview['errors'][0]['error'][:160]}")
                    continue
            applied = apply_review_decisions(body, db)
            if applied["errors"]:
                find(16, path, f"the preview accepted {sorted(set(want.values()))} but apply "
                               f"refused: {applied['errors'][0]['error'][:160]}")
                continue
            # 16. And for an accepted answer, the preview's outcome is the
            # outcome apply produced.
            said, did = preview["outcomes"][0], applied["outcomes"][0]
            if said["still_in_review"] != (did["status"] == "manual_review") or \
                    said["will_process"] != (did["status"] == "pending"):
                find(16, path, f"the preview said will_process={said['will_process']}, "
                               f"still_in_review={said['still_in_review']}; apply left the "
                               f"file {did['status']}")
            record["subtitle"] = {**record.get("subtitle", {}), **want}
            record["preview"] = said
            count += 1

        language = choices["_language_answer"]
        for kind, flag_model, apply_route, ignore_route in _language_routes():
            by_file: dict[int, list] = {}
            for flag in db.query(flag_model).order_by(flag_model.id):
                by_file.setdefault(flag.file_id, []).append(flag)
            for file_id, flags in by_file.items():
                record = answered.setdefault(file_id, {})
                done = record.setdefault(f"{kind}_language", {})
                new = [f for f in flags if str(f.stream_index) not in done]
                if not new:
                    continue
                for f in new:
                    done[str(f.stream_index)] = language
                if language == "ignore":
                    result = ignore_route(IgnoreRequest(file_ids=[file_id]), db)
                else:
                    result = apply_route(ApplyRequest(flag_ids=[f.id for f in new],
                                                      target_language=language), db)
                errors = result.get("errors") if isinstance(result, dict) else None
                if errors:
                    find(18, str(file_id), f"{kind} language {language} not applied: "
                                           f"{str(errors[0])[:160]}")
                count += 1
    return count


def _check_answers_carried_out(find, source, actions, answers, canonical, original):
    """
    17 and 18: a processed file's job plans what its answers said. Keep
    means neither dropped nor extracted; remove, dropped; extract,
    extracted. An applied language is the planned target of its track,
    unless the track is dropped or already carries it; ignore plans no new
    language.
    """
    if not answers:
        return
    tag = {s["index"]: canonical((s.get("tags") or {}).get("language")) or "und"
           for s in (original or {}).get("streams", [])}
    by_stream = {a.stream_index: a for a in actions if a.stream_index is not None}
    meant = {"keep": None, "remove": "drop_track", "extract": "extract_subtitle"}
    for si, choice in (answers.get("subtitle") or {}).items():
        a = by_stream.get(int(si))
        kind = a.action_type if a is not None else None
        if choice == "keep":
            if kind in ("drop_track", "extract_subtitle"):
                find(17, source, f"stream {si} answered keep, planned {kind}")
        elif kind != meant[choice]:
            find(17, source, f"stream {si} answered {choice}, planned {kind}")
    for kind in ("audio", "subtitle"):
        for si, choice in (answers.get(f"{kind}_language") or {}).items():
            a = by_stream.get(int(si))
            if a is None or a.action_type == "drop_track":
                continue
            if a.action_type == "extract_subtitle":
                # An extracted subtitle's language is its file name's, which
                # the job page shows as "Extract subtitle [lang]"; the row
                # has no column for it.
                m = re.match(r"Extract subtitle \[([a-z]+)\]", a.description or "")
                target = canonical(m.group(1)) if m else None
            else:
                target = canonical(a.target_language) if a.target_language else None
            if choice == "ignore":
                # A two-letter tag written in its three-letter form is not a
                # new language, and neither is "und" for a tag that says
                # "undefined" in words.
                was = tag.get(int(si))
                if target and target != was and {target, was} != {"und", "undefined"}:
                    find(18, source, f"{kind} stream {si} ignored, planned language {target}")
            elif target is None and tag.get(int(si)) == canonical(choice):
                continue
            elif target != canonical(choice):
                find(18, source, f"{kind} stream {si} answered {choice}, planned "
                                 f"language {target}")


def _check_answered_left_review(find, db, files, source_of):
    """17: a file whose every question was answered is not still waiting."""
    from app.api.routes.queue import _flagged_entries
    from app.database.models import QueueItem
    for e in files:
        subtitle = (e.get("answered") or {}).get("subtitle")
        if not subtitle:
            continue
        item = db.query(QueueItem).filter(QueueItem.file_id == e["id"]) \
                 .order_by(QueueItem.id.desc()).first()
        if item is not None and item.status == "manual_review":
            again = set(_flagged_entries(item)) & {int(s) for s in subtitle}
            if again:
                find(17, source_of.get(e["id"], e["file"]),
                     f"still in review for answered stream(s) {sorted(again)}")


def _check_answers_stick(find, db, files, source_of):
    """19: after a rescan, nothing answered is asked again."""
    from app.api.routes.queue import _flagged_entries
    from app.database.models import AudioLanguageFlag, QueueItem, SubtitleLanguageFlag
    for e in files:
        answers = e.get("answered") or {}
        path = source_of.get(e["id"], e["file"])
        subtitle = {int(s) for s in answers.get("subtitle") or {}}
        if subtitle:
            item = db.query(QueueItem).filter(QueueItem.file_id == e["id"]) \
                     .order_by(QueueItem.id.desc()).first()
            if item is not None and item.status == "manual_review":
                again = set(_flagged_entries(item)) & subtitle
                if again:
                    find(19, path, f"a rescan asked again about stream(s) {sorted(again)}")
        for kind, model in (("audio", AudioLanguageFlag), ("subtitle", SubtitleLanguageFlag)):
            asked = {int(s) for s in answers.get(f"{kind}_language") or {}}
            if not asked:
                continue
            again = {f.stream_index for f in db.query(model).filter(model.file_id == e["id"])}
            again &= asked
            if again:
                find(19, path, f"a rescan asked the {kind} language of stream(s) "
                               f"{sorted(again)} again")


def _plan_of(actions) -> list[list]:
    """A job's planned actions, as check 14 compares them."""
    return [[a.action_type, a.track_type, a.stream_index, a.target_language]
            for a in actions]


def _signature(stream: dict) -> tuple:
    """What a revert promises to put back about one stream (check 11)."""
    tags = {k.lower(): v for k, v in (stream.get("tags") or {}).items()}
    flags = sorted(k for k, v in (stream.get("disposition") or {}).items() if v == 1)
    # FFmpeg reads an MP4 track's title back as "name" (forge._title_args).
    title = tags.get("title") or tags.get("name")
    return (stream.get("codec_type"), stream.get("codec_name"), tags.get("language"),
            title, tags.get("filename"), tags.get("mimetype"), tuple(flags))


def _is_matroska_cover(sig: tuple) -> bool:
    return sig[0] == "video" and sig[4] is not None and "attached_pic" in sig[6]


def _without_attached_pic(sig: tuple) -> tuple:
    return sig[:6] + (tuple(f for f in sig[6] if f != "attached_pic"),)


def _revert_round_trips(find, known, lib, scratch, cfg, files, before,
                        probes_before, faststart_before, rel, collect) -> int:
    """
    Phase 2: restore every revert point through the app's own
    restore_revert_point, then check the round trip (checks 9 to 15).
    Returns how many points were restored.
    """
    import asyncio

    from app.api.routes.queue import _flagged_entries
    from app.api.routes.revert import _ACTIVE_QUEUE_STATES
    from app.core import scanner
    from app.core.probe import is_faststart_mp4
    from app.core.revert_restore import restore_revert_point
    from app.database import session as S
    from app.database.models import Ac3ForgeJob, PlannedAction, QueueItem, RevertPoint

    enabled = bool(cfg.get("revert_enabled"))
    by_id = {e["id"]: e for e in files}
    with S.SessionLocal() as db:
        points = [(p.id, p.file_id, p.sidecar_path, p.manifest)
                  for p in db.query(RevertPoint).order_by(RevertPoint.id)]
    with_point = {file_id for _, file_id, _, _ in points}

    # 9. A point exactly when one should be.
    for e in files:
        processed = bool(e["jobs"]) and e["jobs"][-1] == "success"
        has = e["id"] in with_point
        if not enabled:
            if has:
                find(9, e["file"], "a revert point was recorded with revert off")
        elif processed and "lost_tracks" in e and e["lost_tracks"] != has:
            why = _point_unavailable(collect, e["source"]) if e["lost_tracks"] else None
            if why:
                known.append({"file": e["file"], "lost": "no revert point",
                              "why": f"capture reported it unavailable ({why}); "
                                     "app/core/revert_capture.py, Two kinds of "
                                     "\"no revert point\""})
            else:
                find(9, e["file"], "a track was lost but no revert point was recorded"
                     if e["lost_tracks"] else
                     "a revert point was recorded though no track was lost")
        elif has and not processed:
            find(9, e["file"], f"a revert point for a file whose job ended {e['jobs'][-1:]}")
    if not points:
        return 0

    pre_revert = inventory(lib)
    first_record = len(collect.records)
    reverted = []
    for point_id, file_id, sidecar, manifest in points:
        e = by_id.get(file_id)
        if e is None or "source" not in e:
            continue
        source = e["source"]
        # For check 14: answered tracks the revert brings back from the
        # sidecar. Their answers do not come back with them, by design.
        from_sidecar = {s["index"] for s in json.loads(manifest or "{}").get("streams", [])
                        if s.get("processed_index") is None}
        e["answers_dropped"] = sorted(from_sidecar & _answered_streams(e))
        # The revert route refuses a file that is queued, and check 7's
        # rescan can have queued it again; that is reported there.
        with S.SessionLocal() as db:
            queued = db.query(QueueItem).filter(
                QueueItem.file_id == file_id,
                QueueItem.status.in_(_ACTIVE_QUEUE_STATES)).first()
        if queued:
            e["revert_skipped"] = f"queued ({queued.status}), which the revert route refuses"
            continue
        outcome = asyncio.run(restore_revert_point(point_id))

        # 10. The restore succeeds, onto the original name.
        if not outcome.success:
            find(10, source, f"revert failed: {(outcome.error or '')[:300]}")
            continue
        reverted.append(e)
        if outcome.restored_path != source:
            find(10, source, f"restored to {rel(outcome.restored_path)}, not the original name")
        output = str(lib / e["output"])
        if output != source and os.path.exists(output):
            find(10, source, f"the processed {e['output']} is still there after the revert")

        # 15. The point is spent.
        with S.SessionLocal() as db:
            left = db.query(RevertPoint).filter(RevertPoint.file_id == file_id).count()
        if left:
            find(15, source, f"{left} revert point(s) still recorded after the revert")
        if os.path.exists(sidecar):
            find(15, source, "the sidecar is still in the recycle folder")
        # An AC3 forged after processing is not part of the original, so the
        # revert takes it away and settles its job (forge.settle_forge_jobs).
        with S.SessionLocal() as db:
            still = db.query(Ac3ForgeJob).filter(Ac3ForgeJob.file_id == file_id,
                                                 Ac3ForgeJob.status == "success").count()
        if still:
            find(15, source, "a Forge job still says success after the revert")

        restored = outcome.restored_path if os.path.exists(outcome.restored_path or "") else None
        info = probe(restored) if restored else None
        # 12. Readable.
        if info is None:
            find(12, source, "the restored file does not probe")
            continue
        errors = decode_errors(restored)
        if errors:
            find(12, source, f"decoding the restored file: {errors[:300]}")

        # 11. Stream for stream, the original.
        original = probes_before.get(rel(source))
        if original is None:
            continue
        want = [_signature(s) for s in original["streams"]]
        got = [_signature(s) for s in info["streams"]]
        if want != got:
            # A Matroska cover comes back as a video track, which can also
            # move it ahead of the attachments that followed it.
            covers_only = sorted((_without_attached_pic(w) if _is_matroska_cover(w) else w
                                  for w in want), key=repr) == sorted(got, key=repr)
            if covers_only:
                known.append({"file": rel(source), "lost": "cover comes back as video",
                              "why": "README, Reverting a processed file: Two "
                                     "limitations worth knowing"})
            else:
                find(11, source, _first_difference(want, got))
        # The layout too, where the README's "exact original state" can
        # differ without a stream changing: an MP4's moov before or after
        # its media data.
        was = faststart_before.get(rel(source))
        if was is not None:
            now_fs = is_faststart_mp4(restored)
            if was != now_fs:
                find(11, source, f"fast-start {'on' if was else 'off'} in the original, "
                                 f"{'on' if now_fs else 'off'} after the revert")

    for rec in collect.records[first_record:]:
        if rec["exception"]:
            find(10, "", f"exception logged by {rec['logger']}: {rec['message']}")

    # 13. Everything else is back as it was. The restored media differs in
    # bytes (check 11 judges it); the job's other paths must match the
    # original library, and nothing else may have moved since the reverts
    # began.
    now = inventory(lib)
    mode = cfg.get("existing_srt_handling")
    mine: set[str] = set()
    for e in reverted:
        source_rel = rel(e["source"])
        mine.update(e.get("touched", []))
        if source_rel not in now:
            find(13, e["source"], "the restored file is not at its original path")
        sidecars_before = {p: before[p] for p in e.get("touched", [])
                           if p != source_rel and p in before}
        for p in e.get("touched", []):
            if p == source_rel or before.get(p) == now.get(p):
                continue
            if p in sidecars_before and mode == "replace":
                known.append({"file": source_rel, "lost": f"{Path(p).name} stays replaced",
                              "why": "README, Reverting a processed file: Extracted "
                                     "subtitle files"})
                continue
            if mode == "embedded_first" and p not in before and \
                    now.get(p) in sidecars_before.values():
                known.append({"file": source_rel, "lost": f"{Path(p).name} stays moved",
                              "why": "README, Reverting a processed file: Extracted "
                                     "subtitle files"})
                continue
            if mode == "embedded_first" and p in sidecars_before and p not in now and \
                    sidecars_before[p] in now.values():
                continue    # the moved file, counted on its new name above
            what = ("left behind" if p not in before else
                    "missing" if p not in now else "changed")
            find(13, e["source"], f"{Path(p).name} {what} after the revert")
    for p in sorted(set(pre_revert) | set(now)):
        if p not in mine and pre_revert.get(p) != now.get(p):
            find(13, str(lib / p), "changed by a revert of another file")

    tmp = scratch / "tmp"
    if tmp.exists() and any(p.is_file() for p in tmp.rglob("*")):
        find(15, "temp folder", "temp file(s) left behind after the reverts")

    # 14. A rescan decides each restored file as it decided the original.
    with S.SessionLocal() as db:
        queued_before = {q.id for q in db.query(QueueItem)}
        scanner.scan_library(db, [str(lib)], force_probe=True)
        for e in reverted:
            fresh = [q for q in db.query(QueueItem).filter(QueueItem.file_id == e["id"])
                     .order_by(QueueItem.id) if q.id not in queued_before]
            if not fresh:
                find(14, e["source"], "a rescan did not queue the restored file again")
                continue
            actions = db.query(PlannedAction).filter(
                PlannedAction.queue_item_id == fresh[-1].id
            ).order_by(PlannedAction.order).all()
            dropped = set(e.get("answers_dropped", []))
            plan = _plan_of(actions)
            if fresh[-1].status == "manual_review":
                asked = set(_flagged_entries(fresh[-1])) or dropped
                if dropped and asked <= dropped:
                    known.append({"file": rel(e["source"]), "lost": "answer asked again",
                                  "why": _ANSWERS_FROM_SIDECAR})
                else:
                    find(14, e["source"], "a rescan made the restored file manual_review")
            elif fresh[-1].status != "pending":
                find(14, e["source"], f"a rescan made the restored file {fresh[-1].status}")
            elif plan != e["plan"]:
                differs = _differing_streams(plan, e["plan"])
                if dropped and differs - {None} and differs - {None} <= dropped:
                    known.append({"file": rel(e["source"]), "lost": "answer not carried back",
                                  "why": _ANSWERS_FROM_SIDECAR})
                else:
                    find(14, e["source"], f"a rescan plans {plan}, "
                                          f"the original was planned {e['plan']}")
    return len(reverted)


def _point_unavailable(collect, source: str) -> str | None:
    """
    Why capture went ahead without a revert point for `source`, if it said
    so: the UNAVAILABLE case revert_capture documents, which with Require
    Revert Point off lets the job proceed. The reason is cut at its first
    colon-separated detail, so one cause groups as one line.
    """
    marker = f"proceeding without a revert point for {source} \u2014 "
    for rec in collect.records:
        if rec["logger"] == "app.core.revert_capture" and marker in rec["message"]:
            reason = rec["message"].split(marker, 1)[1]
            return re.sub(r" @ 0x[0-9a-f]+", "", reason).splitlines()[0][:160]
    return None


_ANSWERS_FROM_SIDECAR = ("a track the revert brings back from the sidecar has its "
                         "question asked again: app/core/revert_restore.py, _apply; "
                         "look-alike tracks are stored whole, app/core/revert.py, "
                         "match_streams")


def _answered_streams(e: dict) -> set[int]:
    answers = e.get("answered") or {}
    return {int(s) for key in ("subtitle", "audio_language", "subtitle_language")
            for s in answers.get(key) or {}}


def _differing_streams(a: list, b: list) -> set:
    """Stream indices whose planned actions differ between two plans."""
    def by_stream(plan):
        out: dict = {}
        for action in plan:
            out.setdefault(action[2], []).append(action)
        return out
    x, y = by_stream(a), by_stream(b)
    return {k for k in set(x) | set(y) if x.get(k) != y.get(k)}


def _first_difference(want: list, got: list, now: str = "after the revert",
                      was: str = "in the original") -> str:
    if len(want) != len(got):
        extra = Counter(g[0] for g in got) - Counter(w[0] for w in want)
        missing = Counter(w[0] for w in want) - Counter(g[0] for g in got)
        what = ", ".join([f"+{n} {k}" for k, n in sorted(extra.items())]
                         + [f"-{n} {k}" for k, n in sorted(missing.items())])
        return f"{len(got)} streams {now}, {len(want)} {was} ({what})"
    for i, (w, g) in enumerate(zip(want, got)):
        if w != g:
            return f"stream {i} is {g}, was {w}"
    return "streams differ"


def _refused_name(error: str) -> str | None:
    """The file named by worker._output_name_taken's refusal, or None."""
    marker = " already exists beside "
    return error.split(marker, 1)[0] if marker in error else None


_MEDIA_SUFFIXES = {".mkv", ".mp4", ".m4v", ".mov", ".avi", ".ts", ".m2ts",
                   ".wmv", ".flv", ".webm"}


def _check_processed(find, known, lib, source, output, actions, info_before,
                     before, after, cfg, canonical, allowed_changes, entry, several=False):
    out_dir = Path(output).parent
    stem = Path(output).stem
    prefix = f"{out_dir.relative_to(lib).as_posix()}/{stem}." if out_dir != lib else f"{stem}."
    srt_before = {p: h for p, h in before.items() if p.startswith(prefix) and p.endswith(".srt")}
    srt_after = {p: h for p, h in after.items() if p.startswith(prefix) and p.endswith(".srt")}
    touched = {Path(p).relative_to(lib).as_posix() for p in (source, output)}
    touched.update(srt_before, srt_after)
    allowed_changes.update(touched)
    entry["touched"] = sorted(touched)

    by_stream = {a.stream_index: a for a in actions if a.stream_index is not None}
    types = {a.action_type for a in actions}

    # 8. The source goes only when the container changed (by this job).
    if several:
        pass
    elif "change_container" in types:
        if source != output and os.path.exists(source):
            find(8, source, "container changed but the source is still there")
    elif source != output:
        find(8, source, f"no container change, yet the output is {Path(output).name}")
    if not os.path.exists(output):
        find(2, source, f"the output {Path(output).name} does not exist")
        return

    # 2. The output is readable.
    info_after = probe(output)
    if info_after is None:
        find(2, source, f"the output {Path(output).name} does not probe")
        return
    errors = decode_errors(output)
    if errors:
        find(2, source, f"decoding {Path(output).name}: {errors[:300]}")
    if info_before is None:
        return

    # For check 9: did the job lose a stream a revert point can hold? An
    # attachment cannot be held on its own, and a data stream is an MP4's
    # chapter track, which the muxer writes afresh.
    held = ("video", "cover", "audio", "text-sub", "image-sub")
    was = Counter(kind(s) for s in info_before["streams"])
    now = Counter(kind(s) for s in info_after["streams"])
    entry["lost_tracks"] = any(was[k] > now[k] for k in held)
    if several:
        return

    # 3. Every stream accounted for.
    expected: list[tuple[str, dict]] = []
    for s in info_before["streams"]:
        k = kind(s)
        if k is None:
            continue
        a = by_stream.get(s["index"])
        if a is not None and a.action_type in ("drop_track", "extract_subtitle"):
            continue
        expected.append((k, s))
    got = Counter(kind(s) for s in info_after["streams"] if kind(s))
    want = Counter(k for k, _ in expected)
    for k in sorted(set(got) | set(want)):
        missing = want[k] - got[k]
        if missing > 0:
            if k in KNOWN_LOSSES and is_mp4(info_after):
                known.append({"file": Path(source).relative_to(lib).as_posix(),
                              "lost": f"{missing} {k}", "why": KNOWN_LOSSES[k]})
            else:
                find(3, source, f"{missing} {k} stream(s) lost with no planned action")
        elif missing < 0:
            find(3, source, f"{-missing} {k} stream(s) appeared")
    ch_before = len(info_before.get("chapters") or [])
    ch_after = len(info_after.get("chapters") or [])
    if ch_before != ch_after:
        find(3, source, f"chapters {ch_before} -> {ch_after}")

    # 4. Languages as planned, matched in order within each kind.
    def lang(s):
        return canonical((s.get("tags") or {}).get("language")) or "und"
    for k in ("audio", "text-sub", "image-sub"):
        planned = []
        for kk, s in expected:
            if kk != k:
                continue
            a = by_stream.get(s["index"])
            target = canonical(a.target_language) if a is not None and a.target_language else None
            planned.append(target or lang(s))
        actual = [lang(s) for s in info_after["streams"] if kind(s) == k]
        if len(planned) == len(actual) and planned != actual:
            find(4, source, f"{k} languages {actual}, planned {planned}")

    # 5. Sidecars.
    extracts = sum(1 for a in actions if a.action_type == "extract_subtitle")
    new_text = Counter(srt_after.values()) - Counter(srt_before.values())
    if sum(new_text.values()) != extracts:
        find(5, source, f"{extracts} extraction(s) planned, "
                        f"{sum(new_text.values())} new sidecar text(s) on disk")
    mode = cfg.get("existing_srt_handling")
    if mode != "replace":
        lost = Counter(srt_before.values()) - Counter(srt_after.values())
        if lost:
            find(5, source, f"{sum(lost.values())} existing sidecar(s) lost under {mode}")
    for p in set(srt_after) - set(srt_before):
        if not (lib / p).stat().st_size:
            find(5, source, f"empty sidecar {Path(p).name}")


# ── The parent: run the profiles, report ────────────────────────────────────

def run_profile(name, settings, args) -> dict:
    scratch = Path(args.out).resolve() / name
    if scratch.exists():
        shutil.rmtree(scratch)
    scratch.mkdir(parents=True)
    spec = {"name": name, "settings": settings, "library": str(Path(args.library).resolve()),
            "app": str(Path(args.app).resolve()), "scratch": str(scratch),
            "result": str(scratch / "result.json")}
    (scratch / "spec.json").write_text(json.dumps(spec, indent=1))
    t = time.monotonic()
    with open(scratch / "run.log", "w") as log:
        try:
            r = subprocess.run([sys.executable, "-I", __file__, "--child", str(scratch / "spec.json")],
                               stdout=log, stderr=subprocess.STDOUT, timeout=args.timeout)
            code = r.returncode
        except subprocess.TimeoutExpired:
            code = "timeout"
    took = round(time.monotonic() - t, 1)
    result_file = scratch / "result.json"
    if code != 0 or not result_file.exists():
        return {"profile": name, "settings": settings, "crashed": code, "seconds": took,
                "log": str(scratch / "run.log")}
    result = json.loads(result_file.read_text())
    result["seconds"] = took
    if not result["findings"] and not args.keep:
        for p in scratch.iterdir():
            if p.name not in ("result.json", "run.log", "spec.json"):
                shutil.rmtree(p) if p.is_dir() else p.unlink()
    return result


def main() -> int:
    if len(sys.argv) == 3 and sys.argv[1] == "--child":
        run_child(sys.argv[2])
        return 0

    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("library", nargs="?", help="folder of media to copy and process")
    ap.add_argument("--app", default=str(DEFAULT_APP), help="Remuxarr repository root (default: %(default)s)")
    ap.add_argument("--out", default=os.path.join(tempfile.gettempdir(), "remuxarr-testlib-runs"),
                    help="where each profile's copy and results go (default: %(default)s)")
    ap.add_argument("--profile", action="append", help="run only this profile (repeatable)")
    ap.add_argument("--list-profiles", action="store_true")
    ap.add_argument("-j", "--jobs", type=int, default=1, help="profiles run at once")
    ap.add_argument("--timeout", type=int, default=900, help="seconds per profile")
    ap.add_argument("--keep", action="store_true",
                    help="keep every profile's processed copy, not only those with findings")
    args = ap.parse_args()

    profiles = all_profiles()
    if args.list_profiles:
        for name, s in profiles.items():
            print(name, json.dumps(s) if s else "(app defaults)")
        return 0
    if not args.library:
        ap.error("library is required")
    chosen = args.profile or list(profiles)
    unknown = [p for p in chosen if p not in profiles]
    if unknown:
        ap.error(f"unknown profile(s): {', '.join(unknown)}")

    print(f"{len(chosen)} profile(s), {args.jobs} at a time, into {Path(args.out).resolve()}")
    results = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=max(1, args.jobs)) as pool:
        futures = {pool.submit(run_profile, n, profiles[n], args): n for n in chosen}
        for fut in concurrent.futures.as_completed(futures):
            r = fut.result()
            results.append(r)
            if "crashed" in r:
                print(f"  {r['profile']:8} could not run ({r['crashed']}); see {r['log']}")
            else:
                print(f"  {r['profile']:8} {r['jobs_run']:3} jobs  {len(r['findings']):3} finding(s)  {r['seconds']}s")
    results.sort(key=lambda r: list(profiles).index(r["profile"]))

    # Group identical findings across profiles, so one cause reads as one line.
    grouped: dict[tuple, list[str]] = {}
    for r in results:
        for f in r.get("findings", []):
            grouped.setdefault((f["check"], f["file"], f["detail"]), []).append(r["profile"])
    known: dict[tuple, list[str]] = {}
    for r in results:
        for k in r.get("known_losses", []):
            known.setdefault((k["file"], k["lost"], k["why"]), []).append(r["profile"])

    print()
    if grouped:
        print(f"Findings ({len(grouped)} distinct):")
        for (check, file, detail), names in sorted(grouped.items()):
            print(f"  [{check}] {file}: {detail}")
            print(f"        in {', '.join(names)}")
    else:
        print("No findings.")
    if known:
        print(f"\nAccepted losses ({len(known)} distinct, not counted):")
        for (file, lost, why), names in sorted(known.items()):
            print(f"  {file}: {lost} ({why}) in {len(names)} profile(s)")

    summary = Path(args.out).resolve() / "summary.json"
    summary.write_text(json.dumps(results, indent=1))
    print(f"\nPer-profile results: {summary}")
    if any("crashed" in r for r in results):
        return 2
    return 1 if grouped else 0


if __name__ == "__main__":
    sys.exit(main())
