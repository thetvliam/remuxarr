"""
Which gate raised a review, recorded rather than inferred.

Two endpoints used to work out why an item was in manual review by looking
at whether review_subtitles was non-null. That was reliable while the
image-subtitle gate was the only trigger that populated it, and both places
said so in their own docstrings — resolve_subtitles_bulk claimed the field
was "exclusively populated by the image-based-subtitle gate", and the
approve endpoint warned that "if a new subtitle-review trigger is ever
added, it must populate this field or this inference breaks again".

The font-attachment gate is that trigger. Its items are flagged subtitles
too, so the old filter collected them, and a bulk resolve would have run
them under image_subtitle_handling: someone with that on always_remove and
font handling on always_keep would have had every anime file converted, the
fonts dropped and the typesetting flattened, by pressing a button that said
it was resolving image subtitles.

QueueItem.review_reason now carries the answer. The gate names itself on
the decision, and every path that raises a review from a decision copies it
onto the row: the queue endpoints, the scanner, and the worker at job
pickup. The scanner and the worker did not at first.

The awkward case is a null reason on a row with flagged tracks. None of
those is a font review, and QueueItem.review_reason lists where they come
from. The image resolver includes them, and the font resolver must not,
because for it they are a guess.

Ten mutants, all confirmed surviving the full suite before this file
existed:

  the record   the image gate not naming itself                killed
               the font gate not naming itself                 killed
               the reason not persisted onto the item          killed
               the reason not cleared when the item leaves     killed
  the scope    the font resolver sweeping in image items       killed
               the image resolver sweeping in font items       killed
               the image resolver dropping unlabelled rows     killed
               the font resolver including unlabelled rows      killed
               the manual_review status filter dropped         killed
               both resolvers reading the same reason           killed

Six more, for the paths that raise reviews outside the queue endpoints, all
confirmed surviving the full suite before the tests for them were written:

  the paths    the scanner not recording the gate on a new row  killed
               the scanner writing the image gate on a new row  killed
               the scanner not updating it on refresh           killed
               the scanner keeping it when the gate names none  killed
               the worker not recording it at pickup            killed
               the worker writing the image gate at pickup      killed

Run from the project root:
    pytest tests/test_review_reason.py -v
"""
import itertools
import json

import pytest

from app.api.routes import queue as queue_routes
from app.core.decision import analyze_file
from tests.conftest import make_file_info, make_track


# ── What the gates record ─────────────────────────────────────────────────────

def _image_sub_tracks():
    return [
        make_track(0, "video", codec="h264"),
        make_track(1, "audio", codec="aac", language="eng", is_default=True),
        make_track(2, "subtitle", codec="hdmv_pgs_subtitle", language="eng"),
    ]


def _font_tracks():
    return [
        make_track(0, "video", codec="hevc"),
        make_track(1, "audio", codec="aac", language="eng", is_default=True),
        make_track(2, "subtitle", codec="ass", language="eng",
                   title="Signs and Songs"),
    ]


def test_the_image_subtitle_gate_names_itself(settings):
    """
    Closes: the image gate leaving review_reason unset.

    Unset means null, and null with a flagged payload is read as an
    image-subtitle review for backward compatibility — so this gate
    failing to name itself is invisible until the day the compatibility
    reading is dropped, and then every one of its items becomes
    unresolvable in bulk.
    """
    decision = analyze_file(
        make_file_info(container="mkv"), _image_sub_tracks(), settings,
    )

    assert decision.is_manual_review is True
    assert decision.review_reason == "image_subtitles"


def test_the_font_gate_names_itself(settings):
    """
    Closes: the font gate leaving review_reason unset — which would put
    its items back in the image resolver's scope through the
    compatibility reading, which is the whole bug this closes.
    """
    decision = analyze_file(
        make_file_info(container="mkv", font_attachments=17),
        _font_tracks(), settings,
    )

    assert decision.is_manual_review is True
    assert decision.review_reason == "font_attachments"


# ── Persisting it ─────────────────────────────────────────────────────────────

@pytest.fixture
def db():
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    from app.database.models import Base

    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)()


_next_id = itertools.count(1)


def _item(db, reason=None, flagged=True, status="manual_review"):
    """
    One queue item in a given review state.

    The path is numbered from a counter, not derived from the arguments:
    two items with the same reason are exactly the case these tests need,
    and media_files.path is unique.
    """
    from app.database.models import MediaFile, QueueItem

    name = f"f{next(_next_id)}.mkv"
    media = MediaFile(path=f"/media/tv/{name}", filename=name,
                      directory="/media/tv", size=1, mtime=1.0)
    db.add(media)
    db.flush()
    item = QueueItem(
        file_id=media.id, status=status, review_reason=reason,
        review_subtitles=json.dumps([{"stream_index": 2}]) if flagged else None,
    )
    db.add(item)
    db.commit()
    return item


def test_the_reason_is_written_onto_the_item(db):
    """
    Closes: the reason computed but never persisted. The decision knows
    it; nothing downstream can see it unless the item carries it.
    """
    from app.core.decision import ProcessingDecision

    item = _item(db, reason=None, flagged=False)
    decision = ProcessingDecision(
        should_process=False, reason="needs a look", is_manual_review=True,
        flagged_subtitles=[{"stream_index": 2}],
        review_reason="font_attachments",
    )

    queue_routes._apply_decision_to_item(db, item, item.media_file, decision)

    assert item.review_reason == "font_attachments"


def test_the_reason_is_cleared_when_the_item_leaves_review(db):
    """
    Closes: the reason left behind when the item resolves.

    A stale reason on a pending or skipped item makes it a candidate for a
    bulk resolver that has no business touching it — the field has to mean
    "in review, for this" or it means nothing.
    """
    from app.core.decision import ProcessingDecision

    item = _item(db, reason="font_attachments")
    decision = ProcessingDecision(
        should_process=True, reason="convert it", is_manual_review=False,
    )

    queue_routes._apply_decision_to_item(db, item, item.media_file, decision)

    assert item.review_reason is None


# ── Every path that raises a review records it ────────────────────────────────
#
# The tests above cover the queue endpoints. The scanner and the worker raise
# reviews on their own, and neither recorded the gate, so every review they
# raised came out null, and a null review with flagged tracks is an
# image-subtitle review to both bulk endpoints whatever had actually fired.
#
# Each test hands the real path a decision naming a gate and checks the row
# carries it. The decision is injected because what is under test is the
# recording, not which gate a given file trips; the gates are pinned above.

def _review(gate, flagged=True):
    from app.core.decision import Action, ProcessingDecision

    msg = f"needs a look ({gate})"
    return ProcessingDecision(
        should_process=False, is_manual_review=True, reason=msg,
        actions=[Action(action_type="flag_manual_review", description=msg)],
        flagged_subtitles=(
            [{"stream_index": 2, "language": "eng", "codec": "ass",
              "is_forced": False, "title": "Signs and Songs"}]
            if flagged else None
        ),
        review_reason=gate,
    )


_PROBE = {
    "format": {"format_name": "matroska,webm", "duration": "1.0"},
    "streams": [
        {"index": 0, "codec_type": "video", "codec_name": "hevc"},
        {"index": 1, "codec_type": "audio", "codec_name": "aac",
         "tags": {"language": "eng"}},
        {"index": 2, "codec_type": "subtitle", "codec_name": "ass",
         "tags": {"language": "eng"}},
    ],
}


def _scan(db, monkeypatch, tmp_path, decision):
    """
    The real scanner from the webhook entry point, with only the probe and
    the decision replaced. Everything between them and the row is the
    production code, including the refresh-in-place on a second call.
    """
    from app.core import scanner

    path = tmp_path / "Show - S01E08.mkv"
    if not path.exists():
        path.write_bytes(b"\0" * 64)
    monkeypatch.setattr(scanner, "probe_file", lambda *_a, **_kw: _PROBE)
    monkeypatch.setattr(scanner, "analyze_file", lambda *_a, **_kw: decision)
    return scanner.queue_single_file(db, str(path))


def test_a_review_the_scanner_raises_records_its_gate(db, monkeypatch, tmp_path):
    """
    Closes: the scanner creating a review row without the gate that raised
    it. Every scan and every webhook comes through here.
    """
    item = _scan(db, monkeypatch, tmp_path, _review("font_attachments"))

    assert item.status == "manual_review"
    assert item.review_reason == "font_attachments"


@pytest.mark.parametrize("before, after", [
    ("image_subtitles", "font_attachments"),
    ("font_attachments", None),
], ids=["image-then-fonts", "fonts-then-a-gate-that-names-none"])
def test_a_review_the_scanner_refreshes_takes_the_new_gate(
        db, monkeypatch, tmp_path, before, after):
    """
    Closes: an existing review row keeping the gate it was first raised for.

    The scanner refreshes a review row in place rather than adding a second
    one, and the gate can change between evaluations as the reason can. A
    stale value leaves the row in the scope of a resolver for a gate that
    no longer applies to it.

    The second case is the undefined-audio threshold, which flags no track
    and names no gate. Its null has to replace the old value, not be
    skipped over.
    """
    first = _scan(db, monkeypatch, tmp_path, _review(before))
    again = _scan(db, monkeypatch, tmp_path,
                  _review(after, flagged=after is not None))

    assert again.id == first.id
    assert again.review_reason == after


def test_a_review_the_worker_raises_at_pickup_records_its_gate(
        monkeypatch, tmp_path):
    """
    Closes: the worker sending a job to review without the gate.

    The worker re-decides every pending job when it picks it up, and a job
    whose file now needs review goes there from here, not from the scanner.
    """
    from sqlalchemy.orm import sessionmaker

    from app.core import worker
    from app.database.models import Base, MediaFile, QueueItem
    from tests.conftest import memory_engine

    engine = memory_engine()
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine)
    monkeypatch.setattr(worker, "SessionLocal", factory)
    monkeypatch.setattr(worker, "analyze_file",
                        lambda *_a, **_kw: _review("font_attachments"))

    path = tmp_path / "Show - S01E08.mkv"
    path.write_bytes(b"\0" * 64)
    with factory() as s:
        media = MediaFile(path=str(path), filename=path.name,
                          directory=str(tmp_path), size=64, mtime=1.0,
                          container="mkv", video_codec="hevc")
        s.add(media)
        s.flush()
        job = QueueItem(file_id=media.id, status="pending")
        s.add(job)
        s.commit()
        job_id = job.id

    assert worker._load_job_data(job_id) is None

    with factory() as s:
        job = s.get(QueueItem, job_id)
        assert job.status == "manual_review"
        assert job.review_reason == "font_attachments"


# ── Scoping the bulk resolvers ────────────────────────────────────────────────

def _record_scope(monkeypatch):
    """
    Capture which items a resolver reaches, without running the engine.

    The exception is deliberate: the resolver catches per item and carries
    on, so every candidate is recorded rather than only the first.
    """
    seen = []

    def _explode(db_, media):
        seen.append(media.path)
        raise RuntimeError("not run in this test")

    monkeypatch.setattr(queue_routes, "_build_analysis_inputs", _explode)
    monkeypatch.setattr(queue_routes, "get_app_settings", lambda _db: {})
    return seen


def _scoped_ids(db, monkeypatch, reason, include_unlabelled):
    seen = _record_scope(monkeypatch)
    queue_routes._resolve_review_bulk(
        db, reason, include_unlabelled=include_unlabelled,
    )
    return seen


def test_the_font_resolver_ignores_image_subtitle_items(db, monkeypatch):
    """
    Closes: the font resolver scoped to the wrong reason, and both
    resolvers reading the same one.

    This is the bug. Resolving fonts must not re-run an item whose review
    was about PGS tracks, because it would resolve it under
    font_attachment_handling — a setting that has nothing to say about it.
    """
    font = _item(db, reason="font_attachments")
    _item(db, reason="image_subtitles")

    picked = _scoped_ids(db, monkeypatch, "font_attachments",
                         include_unlabelled=False)

    assert picked == [font.media_file.path]


def test_the_image_resolver_ignores_font_items(db, monkeypatch):
    """
    Closes: the image resolver still collecting font items.

    The direction that loses data: image_subtitle_handling on
    always_remove would convert every anime file, dropping the fonts and
    flattening the styling, from a button that says it resolves image
    subtitles.
    """
    image = _item(db, reason="image_subtitles")
    _item(db, reason="font_attachments")

    picked = _scoped_ids(db, monkeypatch, "image_subtitles",
                         include_unlabelled=True)

    assert picked == [image.media_file.path]


def test_the_image_resolver_still_picks_up_unlabelled_rows(db, monkeypatch):
    """
    Closes: the compatibility reading dropped.

    Every existing install has items in review with a null reason. None of
    them is a font review, and this is the resolver that claims them —
    without this they become permanently unresolvable in bulk, which is
    exactly the backlog the endpoint was built for.
    """
    legacy = _item(db, reason=None, flagged=True)

    picked = _scoped_ids(db, monkeypatch, "image_subtitles",
                         include_unlabelled=True)

    assert picked == [legacy.media_file.path]


def test_the_image_resolver_leaves_subtitle_encoding_reviews_alone(monkeypatch):
    """
    Closes: resolving subtitle items in bulk re-queuing an encoding failure.

    The worker raises this review when FFmpeg cannot decode a text subtitle
    as UTF-8, and re-deciding the file cannot see that. Reproduced on a real
    MKV with a Latin-1 SRT track: the image resolver reported the review
    resolved and put it back to pending with the extraction that had just
    failed, which fails the same way and raises the same review. The review
    here comes from the real writer, so this breaks if it stops naming
    itself.
    """
    from sqlalchemy.orm import sessionmaker

    from app.core import worker
    from app.database.models import Base, MediaFile, QueueItem, Track
    from tests.conftest import memory_engine

    engine = memory_engine()
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine)
    monkeypatch.setattr(worker, "SessionLocal", factory)

    with factory() as s:
        media = MediaFile(path="/m/Movie.mkv", filename="Movie.mkv",
                          directory="/m", size=1, mtime=1.0,
                          container="mkv", video_codec="h264")
        s.add(media)
        s.flush()
        for index, kind, codec in [(0, "video", "h264"), (1, "audio", "aac"),
                                   (2, "subtitle", "subrip")]:
            s.add(Track(file_id=media.id, stream_index=index,
                        track_type=kind, codec=codec,
                        language="und" if kind == "video" else "eng",
                        is_default=(index == 1)))
        job = QueueItem(file_id=media.id, status="processing")
        s.add(job)
        s.commit()
        job_id = job.id

    worker._flag_subtitle_encoding_review(
        job_id, [(2, "/m/Movie.en.srt")],
        [{"stream_index": 2, "track_type": "subtitle",
          "codec": "subrip", "language": "eng"}],
    )

    with factory() as s:
        result = queue_routes.resolve_subtitles_bulk(s)
        assert s.get(QueueItem, job_id).status == "manual_review"
        assert result == {"resolved": 0, "still_unresolved": 0, "errors": []}


def test_the_font_resolver_leaves_unlabelled_rows_alone(db, monkeypatch):
    """
    Closes: the font resolver including unlabelled rows.

    For the image resolver a null reason is a safe inference. For this one
    it is a guess about a row whose origin was never recorded, and guessing
    wrong converts files the review was protecting.
    """
    _item(db, reason=None, flagged=True)

    picked = _scoped_ids(db, monkeypatch, "font_attachments",
                         include_unlabelled=False)

    assert picked == []


def test_only_items_actually_in_review_are_resolved(db, monkeypatch):
    """
    Closes: the manual_review status filter dropped.

    A completed item keeps its history; re-running the engine over it
    would drag it back into the queue.
    """
    review = _item(db, reason="font_attachments")
    _item(db, reason="font_attachments", status="success")

    picked = _scoped_ids(db, monkeypatch, "font_attachments",
                         include_unlabelled=False)

    assert picked == [review.media_file.path]


# ── The endpoints, not just the helper ────────────────────────────────────────

def test_the_font_endpoint_asks_for_font_items(db, monkeypatch):
    """
    Closes: the font endpoint passing the wrong reason to the shared
    helper.

    The helper is generic, so every one of its own tests passes whatever
    reason it is given and cannot notice an endpoint asking for the wrong
    one. This is the layer the bug lives in — a one-word difference at a
    call site, with the whole loss of typesetting behind it.
    """
    font = _item(db, reason="font_attachments")
    _item(db, reason="image_subtitles")
    seen = _record_scope(monkeypatch)

    queue_routes.resolve_fonts_bulk(db=db)

    assert seen == [font.media_file.path]


def test_the_subtitle_endpoint_asks_for_image_items(db, monkeypatch):
    """
    Closes: the image endpoint passing the wrong reason, and dropping the
    unlabelled rows it is the only endpoint entitled to claim.
    """
    image = _item(db, reason="image_subtitles")
    legacy = _item(db, reason=None, flagged=True)
    _item(db, reason="font_attachments")
    seen = _record_scope(monkeypatch)

    queue_routes.resolve_subtitles_bulk(db=db)

    assert sorted(seen) == sorted([image.media_file.path,
                                   legacy.media_file.path])
