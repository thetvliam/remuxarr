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
 15. The point is spent: no row, no sidecar, no temp file left.

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
    keys = list(VARIED)
    order = {k: i for i, k in enumerate(keys)}

    def pair(a, va, b, vb):
        if order[a] > order[b]:
            a, va, b, vb = b, vb, a, va
        return (a, json.dumps(va), b, json.dumps(vb))

    def pairs_of(row):
        return {pair(a, row[a], b, row[b]) for a, b in itertools.combinations(keys, 2)}

    need = {pair(a, va, b, vb)
            for a, b in itertools.combinations(keys, 2)
            for va in VARIED[a] for vb in VARIED[b]}
    everything = set(need)
    rng = random.Random(0)
    rows: list[dict] = []
    while need:
        best = None
        seed_pair = min(need)
        for _ in range(60):
            a, va, b, vb = seed_pair
            row = {k: rng.choice(VARIED[k]) for k in keys}
            row[a], row[b] = json.loads(va), json.loads(vb)
            for k in rng.sample(keys, len(keys)):
                if k in (a, b):
                    continue
                row[k] = max(VARIED[k], key=lambda v, k=k: sum(
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

    settings = {**FORCED, **spec["settings"], "scan_paths": [str(lib)]}
    _check_profile_against_app(spec["settings"])

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

    t = time.monotonic()
    ran = asyncio.run(drain())
    timings["jobs"] = round(time.monotonic() - t, 2)

    after = inventory(lib)
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
            actions = db.query(PlannedAction).filter(PlannedAction.queue_item_id == job.id) \
                        .order_by(PlannedAction.order).all()
            entry["output"] = rel(media.path)
            entry["actions"] = [f"{a.action_type}:{a.stream_index}" for a in actions]
            entry["source"] = source
            entry["plan"] = _plan_of(actions)
            _check_processed(find, known, lib, source, media.path, actions,
                             probes_before.get(rel(source)), before, after,
                             cfg, canonical, allowed_changes, entry)

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

    reverts = _revert_round_trips(
        find, known, lib, scratch, cfg, files, before, probes_before,
        faststart_before, rel, collect,
    )

    result = {
        "profile": spec["name"], "settings": spec["settings"],
        "reverts": reverts,
        "scan": {k: getattr(scan, k) for k in ("total", "queued", "manual_review",
                                                "skipped", "errors", "excluded")},
        "rescan_queued": rescan.queued, "jobs_run": ran, "timings": timings,
        "findings": findings, "known_losses": known,
        "temp_left": {k: v for k, v in temp_left.items() if v},
        "files": [{k: v for k, v in e.items() if k not in ("id", "source", "plan", "touched")}
                  for e in files],
        "logged_errors": [r for r in collect.records
                          if r["level"] in ("ERROR", "CRITICAL") and not r["exception"]],
    }
    Path(spec["result"]).write_text(json.dumps(result, indent=1))


def _plan_of(actions) -> list[list]:
    """A job's planned actions, as check 14 compares them."""
    return [[a.action_type, a.track_type, a.stream_index, a.target_language]
            for a in actions]


def _signature(stream: dict) -> tuple:
    """What a revert promises to put back about one stream (check 11)."""
    tags = {k.lower(): v for k, v in (stream.get("tags") or {}).items()}
    flags = sorted(k for k, v in (stream.get("disposition") or {}).items() if v == 1)
    return (stream.get("codec_type"), stream.get("codec_name"), tags.get("language"),
            tags.get("title"), tags.get("filename"), tags.get("mimetype"), tuple(flags))


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

    from app.api.routes.revert import _ACTIVE_QUEUE_STATES
    from app.core import scanner
    from app.core.probe import is_faststart_mp4
    from app.core.revert_restore import restore_revert_point
    from app.database import session as S
    from app.database.models import PlannedAction, QueueItem, RevertPoint

    enabled = bool(cfg.get("revert_enabled"))
    by_id = {e["id"]: e for e in files}
    with S.SessionLocal() as db:
        points = [(p.id, p.file_id, p.sidecar_path)
                  for p in db.query(RevertPoint).order_by(RevertPoint.id)]
    with_point = {file_id for _, file_id, _ in points}

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
    for point_id, file_id, sidecar in points:
        e = by_id.get(file_id)
        if e is None or "source" not in e:
            continue
        source = e["source"]
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
            covers_only = (len(want) == len(got) and all(
                w == g or (_is_matroska_cover(w) and _without_attached_pic(w) == g)
                for w, g in zip(want, got)))
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
            if fresh[-1].status != "pending":
                find(14, e["source"], f"a rescan made the restored file {fresh[-1].status}")
            elif _plan_of(actions) != e["plan"]:
                find(14, e["source"], f"a rescan plans {_plan_of(actions)}, "
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


def _first_difference(want: list, got: list) -> str:
    if len(want) != len(got):
        return f"{len(got)} streams after the revert, {len(want)} in the original"
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
                     before, after, cfg, canonical, allowed_changes, entry):
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

    # 8. The source goes only when the container changed.
    if "change_container" in types:
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
