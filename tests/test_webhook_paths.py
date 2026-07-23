"""
Regression tests for Radarr Rename webhook payload handling.

The bug: _radarr_paths read payload["renamedMovieFile"] — a singular
object — but Radarr's Rename event emits "renamedMovieFiles", a LIST
(each element extending WebhookMovieFile, so each carries "path"). The
misnamed field never matched, so Radarr Rename events queued nothing;
Download events still worked via "movieFile". The correctly-handled
Sonarr sibling already read "renamedEpisodeFiles" as an array — that
asymmetry between two functions in the same file was the tell.

Field names and shapes here are taken from Radarr's own source
(WebhookRenamePayload → List<WebhookRenamedMovieFile>, camelCased on the
wire), not guessed. previousPath is included in the rename fixtures
because Radarr really does send it, precisely to assert we DON'T queue
it (the file no longer exists at that path post-rename — queuing it
would probe-fail).

These target the pure path-extraction helpers directly. Run from the
project root:
    pytest tests/ -v
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.api.routes.webhooks import _radarr_paths, _sonarr_paths


# ── Real Radarr payload shapes (camelCase, as serialized on the wire) ──────

def _radarr_rename_payload():
    """A Radarr Rename event with two renamed files, mirroring
    WebhookRenamePayload: Movie + List<WebhookRenamedMovieFile>."""
    return {
        "eventType": "Rename",
        "movie": {"id": 42, "title": "Blade Runner 2049"},
        "renamedMovieFiles": [
            {
                "id": 100,
                "relativePath": "Blade Runner 2049 (2017) Bluray-2160p.mkv",
                "path": "/movies/Blade Runner 2049 (2017)/Blade Runner 2049 (2017) Bluray-2160p.mkv",
                "previousPath": "/movies/Blade Runner 2049 (2017)/old name.mkv",
            },
            {
                "id": 101,
                "relativePath": "Blade Runner 2049 (2017) Bluray-1080p.mkv",
                "path": "/movies/Blade Runner 2049 (2017)/Blade Runner 2049 (2017) Bluray-1080p.mkv",
                "previousPath": "/movies/Blade Runner 2049 (2017)/old name 1080.mkv",
            },
        ],
    }


def _radarr_download_payload():
    return {
        "eventType": "Download",
        "movie": {"id": 42, "title": "Blade Runner 2049"},
        "movieFile": {
            "id": 100,
            "relativePath": "Blade Runner 2049 (2017) Bluray-2160p.mkv",
            "path": "/movies/Blade Runner 2049 (2017)/Blade Runner 2049 (2017) Bluray-2160p.mkv",
        },
    }


# ═══════════════════════════════════════════════════════════════════════════
# The core regression
# ═══════════════════════════════════════════════════════════════════════════

def test_radarr_rename_extracts_new_paths():
    """
    The exact bug: a Rename payload must yield the renamed files' NEW
    paths. Under the old singular-field code this returned [] and the
    rename was silently dropped.
    """
    paths = _radarr_paths(_radarr_rename_payload())
    assert paths == [
        "/movies/Blade Runner 2049 (2017)/Blade Runner 2049 (2017) Bluray-2160p.mkv",
        "/movies/Blade Runner 2049 (2017)/Blade Runner 2049 (2017) Bluray-1080p.mkv",
    ], f"Rename event yielded {paths!r} — the renamed files were not picked up."


def test_radarr_rename_ignores_previous_path():
    """
    previousPath is the pre-rename location; the file no longer exists
    there, so it must never be queued (only "path", the new location,
    is). Guards against a plausible over-fix that grabs both.
    """
    paths = _radarr_paths(_radarr_rename_payload())
    for p in paths:
        assert "old name" not in p, (
            f"previousPath leaked into the queue set: {p!r} — this path "
            "no longer exists on disk after the rename."
        )


def test_radarr_old_singular_field_is_not_read():
    """
    Belt-and-suspenders: a payload carrying ONLY the old misspelled
    singular field must yield nothing — proving the code no longer
    depends on it (and that a real payload, which never contains it,
    can't accidentally match).
    """
    stale_shape = {
        "eventType": "Rename",
        "movie": {"id": 42},
        "renamedMovieFile": {"path": "/movies/should-not-be-read.mkv"},
    }
    assert _radarr_paths(stale_shape) == []


# ═══════════════════════════════════════════════════════════════════════════
# Download path unaffected + dedupe still works
# ═══════════════════════════════════════════════════════════════════════════

def test_radarr_download_still_works():
    """The Download path (movieFile) must be untouched by the fix."""
    assert _radarr_paths(_radarr_download_payload()) == [
        "/movies/Blade Runner 2049 (2017)/Blade Runner 2049 (2017) Bluray-2160p.mkv"
    ]


def test_radarr_dedupes_moviefile_and_rename_overlap():
    """
    A payload with both movieFile and a renamedMovieFiles entry pointing
    at the same final path must yield it once — the existing
    dict.fromkeys dedupe still applies across the newly-read array.
    """
    payload = {
        "eventType": "Download",
        "movie": {"id": 42},
        "movieFile": {"path": "/movies/x/final.mkv"},
        "renamedMovieFiles": [{"path": "/movies/x/final.mkv"}],
    }
    assert _radarr_paths(payload) == ["/movies/x/final.mkv"]


def test_radarr_empty_and_missing_fields_are_safe():
    """Empty renamedMovieFiles, missing fields, and elements without a
    path must not raise and must contribute nothing."""
    assert _radarr_paths({"eventType": "Rename", "renamedMovieFiles": []}) == []
    assert _radarr_paths({"eventType": "Download"}) == []
    assert _radarr_paths(
        {"renamedMovieFiles": [{"id": 1}, {"path": ""}, {"path": "/ok.mkv"}]}
    ) == ["/ok.mkv"]


# ═══════════════════════════════════════════════════════════════════════════
# Sonarr parity — the fix makes the two functions structurally identical
# for renames; pin that so they can't drift apart again
# ═══════════════════════════════════════════════════════════════════════════

def test_sonarr_rename_array_still_extracts_paths():
    """
    The sibling that was always correct. Kept here so the two rename
    handlers are tested side by side — the whole point being that
    they'd diverged.
    """
    payload = {
        "eventType": "Rename",
        "series": {"id": 7},
        "renamedEpisodeFiles": [
            {"path": "/tv/Show/Season 01/Show - S01E01.mkv"},
            {"path": "/tv/Show/Season 01/Show - S01E02.mkv"},
        ],
    }
    assert _sonarr_paths(payload) == [
        "/tv/Show/Season 01/Show - S01E01.mkv",
        "/tv/Show/Season 01/Show - S01E02.mkv",
    ]


def test_both_handlers_treat_rename_as_array():
    """
    Structural parity assertion: feed each handler its own rename-array
    field with two entries and confirm both return two paths. If either
    reverts to reading a singular object, this count drops and the test
    fails.
    """
    r = _radarr_paths(_radarr_rename_payload())
    s = _sonarr_paths({
        "renamedEpisodeFiles": [
            {"path": "/a.mkv"}, {"path": "/b.mkv"},
        ]
    })
    assert len(r) == 2 and len(s) == 2
