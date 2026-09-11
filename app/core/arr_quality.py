"""
Restoring a file's quality after Remuxarr replaces it.

Sonarr and Radarr parse quality from the filename at import, and neither
carries it across a file replacement. When a job changes a container, the
rescan sees the old file gone and the new one as previously unknown, and
re-parses from scratch. A name with a resolution but no source token falls
back to HDTV at that resolution, so a WEBDL-1080p download becomes
HDTV-1080p.

That is not only a wrong label. Radarr set qualityCutoffNotMet on the
replaced file, which means it considers the movie upgradable and will grab
a replacement on its next search — overwriting the file Remuxarr just
produced. The observed case is in the fixtures behind
tests/test_arr_quality_restore.py.

Nothing has to be captured before the job. Both services keep the original
downloadFolderImported record after the file it describes is gone, with the
quality intact, so the value is read back afterwards from history. That
makes this idempotent and retryable: if it fails, nothing is lost and it
can be run again.

WHAT DECIDES WHETHER THERE IS ANYTHING TO DO
--------------------------------------------
Not a filename comparison. The pre-job path is not available by the time
this runs — _finish_job has already moved MediaFile.path on to the new one
— and reconstructing it would mean a schema change to answer a question
history answers directly.

Instead: if the newest import record describes the file that is live right
now, nothing replaced it and there is nothing to restore. That is exactly
the condition worth acting on, it needs no extra state, and it also covers
a replacement that happened for some reason other than a container change.
The cost is two reads per job for Radarr and three for Sonarr where the
answer turns out to be "nothing to do".

WHY THE EDITOR ENDPOINT
-----------------------
Not PUT /api/v3/moviefile/{id} with a quality-only body. Sonarr accepts
that; Radarr answers 500 with "Nullable object must have a value" from
MovieFileController.SetMovieFile, because it deserialises into a full
resource and dereferences a field the partial body left null. Reading the
resource back and returning it with one field changed works, but costs a
GET and risks writing back a record that has moved on.

The editor endpoints take a partial body by design and both services
accept the same shape, so there is one write path rather than a branch.
Only the field naming the ids differs, and that lives in the descriptor
with everything else that differs.

WHY NOT MATCH HISTORY ON PATH
-----------------------------
Because a path is reused. In the recorded case the movie was an MP4, was
upgraded to an MKV three weeks later, and Remuxarr then remuxed it back to
MP4 at the same path — so that path appears in history as the importedPath
of a release that stopped being on disk in August. Selecting on it restores
a quality three weeks out of date, and because both happened to be
WEBRip-1080p, it would have looked like it worked. Selection is by event
type and date. Path is used only to identify the new file record, which is
the one thing it is reliable for.
"""
import logging
import time
import urllib.error
from dataclasses import dataclass

from app.core.arr_client import arr_get, arr_put

logger = logging.getLogger(__name__)

POLL_DEADLINE = 120.0
POLL_INTERVAL = 3.0
IMPORT_EVENT  = "downloadFolderImported"


@dataclass(frozen=True)
class ArrService:
    """
    The endpoint names that differ between the two services.

    They are not symmetric. Radarr scopes history by movieId and every
    record returned belongs to the file's movie. Sonarr scopes by series,
    so its history mixes in every other episode, and the records belonging
    to this file have to be selected by episodeId — which means asking
    which episodes carry it first. episodes_path is set for Sonarr only.
    """
    name:            str
    entity_label:    str   # what the entity is called in a log line
    files_path:      str
    files_param:     str
    history_path:    str
    history_param:   str
    editor_path:     str
    file_ids_field:  str
    episodes_path:   str | None = None


SONARR = ArrService(
    name           = "Sonarr",
    entity_label   = "series",
    files_path     = "/api/v3/episodefile",
    files_param    = "seriesId",
    history_path   = "/api/v3/history/series",
    history_param  = "seriesId",
    editor_path    = "/api/v3/episodefile/editor",
    file_ids_field = "episodeFileIds",
    episodes_path  = "/api/v3/episode",
)

RADARR = ArrService(
    name           = "Radarr",
    entity_label   = "movie",
    files_path     = "/api/v3/moviefile",
    files_param    = "movieId",
    history_path   = "/api/v3/history/movie",
    history_param  = "movieId",
    editor_path    = "/api/v3/moviefile/editor",
    file_ids_field = "movieFileIds",
)


def _await_file_record(
    service, base_url, api_key, entity_id, path, deadline, interval,
):
    """
    Poll until a file record reports the path the job produced.

    This is what tells us the rescan finished — the *arr queues it as a
    command and runs it in its own time — and it is where the new file's
    id comes from. Matched on the full path: a basename match would pick
    the same episode number in a different season.

    Returns None once the deadline passes, rather than waiting on a
    service that may be down.
    """
    end = time.monotonic() + deadline
    while True:
        records = arr_get(
            base_url, api_key, service.files_path,
            {service.files_param: entity_id},
        ) or []

        for record in records:
            if record.get("path") == path:
                return record

        if time.monotonic() >= end:
            return None
        time.sleep(interval)


def _history_for_file(service, base_url, api_key, entity_id, file_id):
    """Every history record that belongs to the given file's entity."""
    records = arr_get(
        base_url, api_key, service.history_path,
        {service.history_param: entity_id},
    ) or []

    if service.episodes_path is None:
        return records

    episodes = arr_get(
        base_url, api_key, service.episodes_path, {"seriesId": entity_id},
    ) or []
    on_this_file = {
        e.get("id") for e in episodes if e.get("episodeFileId") == file_id
    }
    return [r for r in records if r.get("episodeId") in on_this_file]


def _quality_of_the_import(records, file_id):
    """
    The quality of the newest import, unless that import is the file that
    is live right now — in which case nothing replaced it and there is
    nothing to put back.

    The check is against the newest record only, not a filter across all
    of them. Excluding the live file and taking the next one down looks
    equivalent and is not: in the recorded case it restores the release
    that was superseded three weeks earlier, because that import is still
    sitting in history underneath.

    Deletion records are excluded by event type, not by hoping they sort
    late: they are written after the import they describe and they carry a
    quality field of their own, so reading the newest record of any type
    reads a deletion.
    """
    imports = [r for r in records if r.get("eventType") == IMPORT_EVENT]
    if not imports:
        return None

    newest = max(imports, key=lambda r: r.get("date") or "")
    if str((newest.get("data") or {}).get("fileId")) == str(file_id):
        return None

    return newest.get("quality")


def restore_quality(
    service, base_url, api_key, entity_id, path,
    deadline=POLL_DEADLINE, interval=POLL_INTERVAL,
) -> bool:
    """
    Put the imported quality back on the file now at *path*.

    Returns whether anything was written. Raises nothing of its own — the
    HTTP client propagates failures and the callers in sonarr.py and
    radarr.py log them, because a job whose file is already on disk should
    not be recorded as failed over a metadata write.
    """
    record = _await_file_record(
        service, base_url, api_key, entity_id, path, deadline, interval,
    )
    if record is None:
        logger.warning(
            "%s: no file record for %s after %.0fs — quality not restored",
            service.name, path, deadline,
        )
        return False

    file_id = record.get("id")
    quality = _quality_of_the_import(
        _history_for_file(service, base_url, api_key, entity_id, file_id),
        file_id,
    )
    if quality is None:
        logger.debug(
            "%s: nothing to restore for %s — the live file is the one that "
            "was imported, or it never was",
            service.name, path,
        )
        return False

    arr_put(
        base_url, api_key, service.editor_path,
        {service.file_ids_field: [file_id], "quality": quality},
    )
    logger.info(
        "%s: restored quality %s on file %s (%s)",
        service.name,
        (quality.get("quality") or {}).get("name", "?"),
        file_id, path,
    )
    return True


def restore_quality_best_effort(
    service: ArrService, base_url: str, api_key: str, entity_id: int,
    path: str, log: logging.Logger,
) -> None:
    """
    restore_quality for the worker's post-job hook, where it must not fail.

    The file is already on disk and correct when this runs, so a failed
    metadata write must not mark the job failed. It is logged at error
    rather than swallowed, because what it leaves behind is a file the
    service has flagged qualityCutoffNotMet and may replace.

    One copy for both services. sonarr.py and radarr.py each carried this
    body, identical but for three words; Sonarr's never ran in a test, and
    neither did Radarr's two fallback branches.

    log is the caller's logger, so each service's lines keep the module
    name they have always shown in the log view.
    """
    try:
        restore_quality(service, base_url, api_key, entity_id, path)
    except urllib.error.HTTPError as exc:
        # The body is where the reason actually is: a quality-only PUT to
        # the per-file endpoint answers 500 with the exception and the
        # controller line that threw it, while code and reason say only
        # "Internal Server Error". Truncated because a stack trace is the
        # usual payload and the first line is the part that identifies it.
        try:
            detail = exc.read().decode("utf-8", "replace")[:500]
        except Exception:
            detail = "<no body>"
        log.error(
            "%s: quality restore HTTP %d for %s %d (%s): %s — %s",
            service.name, exc.code, service.entity_label, entity_id, path,
            exc.reason, detail,
        )
    except Exception:
        log.exception(
            "%s: quality restore failed for %s %d (%s)",
            service.name, service.entity_label, entity_id, path,
        )
