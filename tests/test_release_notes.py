"""
Release notes endpoint: what the update dialog is told to show.

RELEASE_NOTES.md holds what has changed since the last merge to main that a
user would notice. The UI shows it once per distinct set of notes, so the
version hash is the whole mechanism — the client remembers the hash it
dismissed and shows the dialog again exactly when the content differs.

The failure that matters is silence: a release that renames someone's
settings, shipping with a dialog that says nothing. Everything below is
some form of that.

Verified by mutation, 6 applied, 6 killed:

  • Comment stripping removed                             → killed
  • Empty sections kept, so a bare heading renders        → killed
  • version derived from the raw file rather than parsed  → killed
  • version fixed, so new notes reuse the old hash        → killed
  • A missing file raising instead of returning empty     → killed
  • Empty notes returning a version anyway                → killed

Run from the project root:
    pytest tests/test_release_notes.py -v
"""
import pytest

from app.api.routes.release_notes import _parse
from app.api.routes import release_notes as module


@pytest.fixture
def notes(tmp_path, monkeypatch):
    """Point the endpoint at a notes file this test controls."""
    path = tmp_path / "RELEASE_NOTES.md"

    def write(text):
        path.write_text(text, encoding="utf-8")
        monkeypatch.setattr(module, "_NOTES_PATH", str(path))
        return module.get_release_notes()

    return write


# ── Parsing ──────────────────────────────────────────────────────────────────

def test_headings_and_items_become_sections():
    parsed = _parse("## Changed\n- One.\n- Two.\n\n## Fixed\n- Three.\n")

    assert parsed == [
        {"title": "Changed", "items": ["One.", "Two."]},
        {"title": "Fixed",   "items": ["Three."]},
    ]


def test_the_workflow_comment_is_not_published_to_users():
    """
    The file opens with an HTML comment explaining the format to whoever
    edits it — and it explains the format BY USING IT. A heading with a
    bullet under it, written as an example, is indistinguishable from a
    real entry to a parser that only skips text before the first heading.

    So the users' first sight of this feature would be a dialog telling
    them how to write release notes.
    """
    parsed = _parse(
        "<!--\n"
        "## How To Write These\n"
        "- One line per user-visible change.\n"
        "-->\n"
        "## Changed\n"
        "- A real entry.\n"
    )

    assert parsed == [{"title": "Changed", "items": ["A real entry."]}]


def test_a_heading_with_nothing_under_it_is_not_a_section():
    """A bare title renders as a heading introducing nothing."""
    parsed = _parse("## Changed\n\n## Fixed\n- A fix.\n")

    assert parsed == [{"title": "Fixed", "items": ["A fix."]}]


def test_prose_between_entries_does_not_break_parsing():
    """
    Someone will write a paragraph here. Ignoring the line it cannot parse
    costs one entry; raising costs the endpoint.
    """
    parsed = _parse("## Changed\n- One.\nSome stray prose.\n- Two.\n")

    assert parsed == [{"title": "Changed", "items": ["One.", "Two."]}]


# ── The endpoint ─────────────────────────────────────────────────────────────

def test_notes_are_served_with_a_version(notes):
    body = notes("## Changed\n- Something visible.\n")

    assert body["sections"] == [
        {"title": "Changed", "items": ["Something visible."]}
    ]
    assert body["version"]


def test_an_empty_file_has_no_version_so_no_dialog_is_shown(notes):
    """
    The normal state of a cycle in which nothing user-visible has changed.
    A version here would show an empty dialog on every update.
    """
    body = notes("<!-- workflow notes only -->\n")

    assert body == {"version": None, "sections": []}


def test_a_missing_file_is_not_an_error(notes, tmp_path, monkeypatch):
    """
    A source checkout predating this file, or an older image, should still
    run. Nothing to announce is not a failure.
    """
    monkeypatch.setattr(module, "_NOTES_PATH", str(tmp_path / "absent.md"))

    assert module.get_release_notes() == {"version": None, "sections": []}


def test_different_notes_get_a_different_version(notes):
    """The dialog reappears only when the content actually changed."""
    first = notes("## Changed\n- One.\n")["version"]
    second = notes("## Changed\n- One.\n- Two.\n")["version"]

    assert first != second


def test_editing_the_workflow_comment_does_not_reshow_the_dialog(notes):
    """
    Hashed over the parsed sections, not the raw file. Otherwise fixing a
    typo in the instructions, or a line ending changing, re-shows a dialog
    every user has already read — and a dialog that reappears for no
    reason is one they learn to dismiss without reading.
    """
    before = notes("<!-- short comment -->\n## Changed\n- One.\n")["version"]
    after = notes(
        "<!-- a much longer comment, rewritten entirely -->\n"
        "## Changed\n- One.\n"
    )["version"]

    assert before == after


def test_the_shipped_file_parses(notes):
    """
    The real RELEASE_NOTES.md, not a fixture. It is currently comment-only,
    which must read as "nothing to announce" rather than as a parse
    failure — and if someone adds entries, this proves the file the repo
    actually ships is still readable by the thing that serves it.
    """
    body = module.get_release_notes()

    assert isinstance(body["sections"], list)
    if body["sections"]:
        assert body["version"], "notes present but no version to dismiss"
    else:
        assert body["version"] is None
