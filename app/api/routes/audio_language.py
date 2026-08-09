"""
Audio Language Review API
==========================
GET  /api/audio-language-review/         — paginated, searchable list of flagged files
POST /api/audio-language-review/apply    — set a language on selected files and reprocess
POST /api/audio-language-review/ignore   — confirm selected files are already correct

Distinct from the existing /api/queue/manual-review workflow: a file
flagged here is fully processed and playable the whole time — nothing is
held back waiting for a decision. The flag is purely informational,
surfaced so a human can optionally correct a wrong-but-defined audio
language tag (e.g. an English show mistagged "dut") or confirm the
existing tag is already correct (e.g. anime that's genuinely, correctly
Japanese) at their own pace.

The request handling lives in _language_review.py, shared with the subtitle
counterpart: the two were separate 100-line implementations differing by one
log message, and their comments record the same two bugs being fixed twice
because a fix to one copy did nothing for the other. What is genuinely
specific to audio review stays here, in the descriptions below.
"""
from app.api.routes._language_review import (
    LanguageReviewKind,
    build_language_review_router,
)
from app.core.scanner import _load_audio_language_overrides
from app.database.models import AudioLanguageFlag

AUDIO_LANGUAGE_REVIEW = LanguageReviewKind(
    slug           = "audio",
    prefix         = "/api/audio-language-review",
    tag            = "audio-language-review",
    flag_model     = AudioLanguageFlag,
    load_overrides = _load_audio_language_overrides,
    overrides_attr = "audio_language_overrides",
    ignored_attr   = "audio_language_ignored",

    list_description = """
Paginated, filterable list of files with a flagged audio language
mismatch. Search matches filename, case-insensitive substring — e.g.
"king of the hill" returns every flagged episode across every season,
ready to select-all and apply in one action.

`language` narrows to a single detected tag. The two filters combine
with AND, so "king of the hill" + "dut" gives exactly the episodes of
that show carrying the wrong Dutch tag, leaving any correctly-tagged
ones alone.

Also returns `languages`: every distinct detected tag with a count,
for the filter dropdown. Filtering has to happen on the server because
the list is paginated — narrowing only the loaded page would report
fewer matches than exist, and "select all" would then act on a subset
the user believes is complete.

The counts honour `search` but deliberately ignore `language`. Faceting
on the language filter itself would collapse the dropdown to whichever
option was selected, so there would be no way to switch to another
without clearing first. With search applied and language not, the
dropdown keeps showing the alternatives within the current search.
""".strip(),

    apply_description = """
Set target_language on the flagged track for every file in file_ids,
persist it as an override, and reprocess each file immediately so the
correction actually gets written.

Deletes any existing ACTIVE QueueItem for the file (pending or
manual_review specifically — not any status) before re-running
_process_file with force_probe=True, since the file's bytes haven't
changed on disk and a normal (non-force) evaluation would otherwise
just skip it without ever seeing the new override. A file whose job is
currently RUNNING is skipped rather than cleared, and reported as such
— the choice is still saved and applies on the next scan.
""".strip(),

    ignore_description = """
Confirm the current audio language is correct for every file in
file_ids, despite not matching keep_audio_languages — e.g. anime
that's genuinely, correctly Japanese. No reprocessing happens: nothing
about the file needs to change, this just permanently stops it being
flagged again on future scans.
""".strip(),
)

router = build_language_review_router(AUDIO_LANGUAGE_REVIEW)

# Re-exported under the names they had before the implementation was shared,
# so existing callers and tests that import them directly keep working.
list_flags     = router.handlers.list_flags
apply_language = router.handlers.apply_language
ignore_flags   = router.handlers.ignore_flags
