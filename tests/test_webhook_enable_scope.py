"""
What sonarr_enabled / radarr_enabled actually gate.

Neither flag has ever been read by the webhook endpoints. Both are read in
exactly one place, worker._load_post_job_data, where they gate the OUTBOUND
RescanSeries / RescanMovie call after a job finishes. An inbound webhook is
accepted, debounced, probed and queued whatever they are set to.

The settings descriptions used to say the opposite — "When enabled, Remuxarr
accepts On Import / On Upgrade webhooks from Sonarr and calls Sonarr's
RescanSeries after each job completes" — and the first half of that was never
true. The descriptions were corrected rather than the code, because both flags
DEFAULT TO FALSE: gating the endpoints would silently stop queueing for every
install that has the webhook configured in Sonarr but has never turned the
toggle on, and the symptom would be a webhook that reports success while
nothing happens. Reachability of the port is already the only precondition for
every other endpoint in this app, so gating this one buys no security either.

These tests exist because that is a decision, not an accident, and the next
person to notice the gap should find it recorded and executable rather than
re-derive it and "fix" it. If the endpoints are ever genuinely meant to be
gated, these are the tests to change, deliberately, in that commit.
"""
import asyncio

import pytest

from app.api.routes import webhooks
from app.config import settings as app_settings


class _FakeRequest:
    """Only .json() is used by either handler."""

    def __init__(self, payload):
        self._payload = payload

    async def json(self):
        return self._payload


@pytest.fixture
def queued(monkeypatch):
    """
    Capture what reaches the queue, and collapse the debounce window so the
    timer fires within the test rather than ten seconds later.

    _queue_sync is the seam: everything past it (probe, decision, DB write)
    belongs to the scanner and is covered elsewhere. What is under test here
    is only whether the handler gets that far.
    """
    calls = []
    monkeypatch.setattr(app_settings, "WEBHOOK_DEBOUNCE_SECONDS", 0.01)
    monkeypatch.setattr(
        webhooks, "_queue_sync",
        lambda path, series_id=None, radarr_movie_id=None: calls.append(path),
    )
    # Path translation reads settings from the database; the prefixes are
    # empty in every configuration this test cares about, so short-circuit it
    # and keep the test to the one question it is asking.
    monkeypatch.setattr(
        webhooks, "_resolve_translated_path_sync",
        lambda path, series_id=None, radarr_movie_id=None: path,
    )
    return calls


def _drain(handler, payload, calls):
    async def driver():
        response = await handler(_FakeRequest(payload))
        # The debounce schedules the queue call on a timer; give it room to
        # fire before asserting on what was queued.
        await asyncio.sleep(0.2)
        return response

    return asyncio.run(driver()), calls


SONARR_PAYLOAD = {
    "eventType":   "Download",
    "series":      {"id": 42},
    "episodeFile": {"path": "/media/tv/Show/S01E01.mkv"},
}

RADARR_PAYLOAD = {
    "eventType": "Download",
    "movie":     {"id": 7},
    "movieFile": {"path": "/media/movies/Film.mkv"},
}


def test_a_sonarr_webhook_is_accepted_with_the_integration_disabled(queued):
    response, calls = _drain(webhooks.sonarr_webhook, SONARR_PAYLOAD, queued)
    assert response == {"status": "accepted", "files": 1}
    assert calls == ["/media/tv/Show/S01E01.mkv"]


def test_a_radarr_webhook_is_accepted_with_the_integration_disabled(queued):
    response, calls = _drain(webhooks.radarr_webhook, RADARR_PAYLOAD, queued)
    assert response == {"status": "accepted", "files": 1}
    assert calls == ["/media/movies/Film.mkv"]


def test_neither_handler_reads_the_enable_flags():
    """
    The behaviour above is a consequence of the flags being absent from this
    module entirely, so assert that directly. A handler that started consulting
    them would still pass the two tests above if it happened to default to
    accepting, and this catches the change at the point it is made.
    """
    import inspect

    source = inspect.getsource(webhooks)
    assert "sonarr_enabled" not in source
    assert "radarr_enabled" not in source
