"""
Subtitle Language Review API
=============================
GET  /api/subtitle-language-review/         — paginated, searchable list of flagged files
POST /api/subtitle-language-review/apply    — set a language on selected files and reprocess
POST /api/subtitle-language-review/ignore   — confirm selected files are fine left undefined

One real difference from Audio Language Review worth being explicit about:
every row here originates from an UNDEFINED ("und") tag that
fix_undefined_language's "always_ask" mode flagged for a human decision —
there's no "defined but wrong subtitle language" detection the way Audio
Language Review has for audio (see subtitle_language_mismatch's docstring on
ProcessingDecision for why). The resolution flow is identical either way
though: pick the correct language and reprocess, or confirm it's fine to
leave as-is.

ON THE PREVIOUS "MIRRORED DELIBERATELY" NOTE
--------------------------------------------
This module used to carry a full copy of the audio implementation, with a
docstring stating the mirroring was deliberate "rather than sharing an
implementation, since the two operate on genuinely independent MediaFile
columns and flag tables".

The premise is true and the conclusion did not follow. Independent columns and
tables are precisely the things a parameter can carry — they are now four
fields on LanguageReviewKind. What the two copies shared was the part that is
hard and easy to get wrong: the QueueItem handling. Measured by AST with
docstrings stripped, the two were 100 lines each differing by one log message.

The cost showed up in this file's own comments. Its endpoints read "Mirrors the
audio review endpoint exactly; see its docstring", "Mirrors
/api/audio-language-review/apply exactly, including the fix for its
status-filtered active-item delete" — a fix that had to be found once and then
applied here separately, and which the comment openly describes as "a real,
silent bug rather than a harmless simplification". The duplication was already
being maintained by cross-reference; sharing the implementation just makes the
compiler enforce what those comments were asking a human to remember.
"""
from app.api.routes._language_review import (
    LanguageReviewKind,
    build_language_review_router,
)
from app.core.scanner import _load_subtitle_language_overrides
from app.database.models import SubtitleLanguageFlag

SUBTITLE_LANGUAGE_REVIEW = LanguageReviewKind(
    slug           = "subtitle",
    prefix         = "/api/subtitle-language-review",
    tag            = "subtitle-language-review",
    flag_model     = SubtitleLanguageFlag,
    load_overrides = _load_subtitle_language_overrides,
    overrides_attr = "subtitle_language_overrides",
    ignored_attr   = "subtitle_language_ignored",

    list_description = """
Paginated, filterable list of files with a flagged subtitle language
needing a decision. Search matches filename, case-insensitive
substring — e.g. "king of the hill" returns every flagged episode
across every season, ready to select-all and apply in one action.

`language` narrows to a single detected tag, combining with `search`
via AND. Filtering happens server-side because the list is paginated —
narrowing only the loaded page would report fewer matches than exist,
and "select all" would then act on a subset the user believes is
complete. The facet counts honour `search` but deliberately ignore
`language`, so the dropdown keeps showing the alternatives within the
current search instead of collapsing to the selected option.
""".strip(),

    apply_description = """
Set target_language on the flagged subtitle track for every flag in
flag_ids, persist it as an override, and reprocess each affected file
immediately so the correction actually gets written.

Takes FLAG ids, not file ids: one file can have several undefined
subtitle tracks and each needs its own answer. Flags belonging to the
same file are applied together and that file is reprocessed once, so
"applied" counts files, not flags.

The override is committed separately from the reprocess attempt, so the
user's choice sticks even if this particular attempt fails. Existing
ACTIVE QueueItems (pending or manual_review only — never a terminal
historical row) are cleared first so the reprocess is not silently
skipped by the in-progress guard. A file whose job is currently RUNNING
is skipped rather than cleared, and reported as such — the choice is
saved and applies on the next scan.
""".strip(),

    ignore_description = """
Confirm it's fine to leave the current subtitle track undefined for
every file in file_ids. No reprocessing happens: nothing about the
file needs to change, this just permanently stops it being flagged
again on future scans.
""".strip(),
)

router = build_language_review_router(SUBTITLE_LANGUAGE_REVIEW)

# Re-exported under the names they had before the implementation was shared,
# so existing callers and tests that import them directly keep working.
list_flags     = router.handlers.list_flags
apply_language = router.handlers.apply_language
ignore_flags   = router.handlers.ignore_flags
