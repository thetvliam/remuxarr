"""
Serve the pending release notes to the UI.

RELEASE_NOTES.md holds what has changed since the last merge to main that a
user would notice. The UI shows it once per change, as a dialog, so the
version field below is what makes "once" work: it is a hash of the content,
not a release number. The client stores the hash it last dismissed, and a
dialog reappears exactly when the text differs from the one that was
dismissed.

A hash rather than a version string because nothing here is versioned. The
file is emptied each cycle and refilled, so the same release number would
have to be bumped by hand and would be forgotten — and forgotten means the
release that renamed someone's settings is the one that shows nothing. It
also handles the file being emptied and later refilled with different
content, which a monotonic counter does not.

Parsed here rather than shipped as markdown because the frontend has no
markdown renderer, and adding one to display two headings and a list would
be a dependency bigger than the feature.
"""
import hashlib
import logging
import os
import re

from fastapi import APIRouter

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/release-notes", tags=["release-notes"])

# Repo root, four levels up from app/api/routes/release_notes.py. Resolved
# from __file__ rather than the working directory: uvicorn is started from
# /app in the container but a developer runs it from wherever they happen
# to be, and a notes file that silently fails to load is a release nobody
# is told about.
_NOTES_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(
        os.path.abspath(__file__))))),
    "RELEASE_NOTES.md",
)

_COMMENT = re.compile(r"<!--.*?-->", re.DOTALL)
_HEADING = re.compile(r"^##+\s+(.*\S)\s*$")
_ITEM = re.compile(r"^[-*]\s+(.*\S)\s*$")


def _parse(text: str) -> list[dict]:
    """
    Markdown to sections. HTML comments are removed first, and anything
    before the first heading is dropped.

    Both matter: the file opens with an HTML comment explaining the
    workflow to whoever edits it, and none of that is for users. Dropping
    the pre-heading text alone is not enough, because that comment
    describes the format using the format — a `##` with a `-` beneath it,
    written as an example, would be served as a real release note.

    Unparseable lines are ignored rather than raising. A stray blank line
    or a note someone wrapped in prose should not take out the endpoint —
    the worst case is one entry missing from a dialog, against the whole
    app failing to load.
    """
    # HTML comments stripped before anything else. The workflow notes at
    # the top of the file are a comment, and they contain the words this
    # parser looks for — a heading with a bullet under it, written to
    # explain the format, would otherwise be published to every user as
    # though it were a release note.
    text = _COMMENT.sub("", text)

    sections: list[dict] = []
    for line in text.splitlines():
        heading = _HEADING.match(line)
        if heading:
            sections.append({"title": heading.group(1), "items": []})
            continue
        item = _ITEM.match(line)
        if item and sections:
            sections[-1]["items"].append(item.group(1))

    # A heading with nothing under it says nothing and renders as a stray
    # title, so it is not a section.
    return [s for s in sections if s["items"]]


@router.get("/")
def get_release_notes():
    """
    The pending notes, with a hash identifying this exact set.

    version is None when there is nothing to say — an empty file, a file
    of only comments, or no file at all. The client treats None as "show
    nothing", so the normal state of a cycle with no user-visible changes
    yet is silence.
    """
    try:
        with open(_NOTES_PATH, encoding="utf-8") as handle:
            text = handle.read()
    except FileNotFoundError:
        # Not an error worth a 500. A source checkout without the file, or
        # an image built before it existed, should run — just with nothing
        # to announce.
        logger.debug("No RELEASE_NOTES.md at %s", _NOTES_PATH)
        return {"version": None, "sections": []}
    except OSError:
        logger.exception("Could not read RELEASE_NOTES.md")
        return {"version": None, "sections": []}

    sections = _parse(text)
    if not sections:
        return {"version": None, "sections": []}

    # Hashed over the PARSED content, not the raw file. Editing the
    # workflow comment, fixing its indentation, or changing a line ending
    # would otherwise re-show a dialog every user has already read, and a
    # dialog that reappears for no reason is one they learn to dismiss
    # without reading.
    digest = hashlib.sha256(
        repr(sections).encode("utf-8")
    ).hexdigest()[:12]

    return {"version": digest, "sections": sections}
