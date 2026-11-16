"""The mirror's transport: what it copies, and that it never gets in the caller's way.

A client talks to a fake Novu through ``MirroringTransport``, as the Novu SDK does.
"""

import json
import threading
import time

import httpx
import pytest

from microservice_utils.novu.mirror import NovuMirror
from tests.novu.fakes import FakeEngine, FakeNovu

NOVU = "https://api.novu.co"
MIRROR_URL = "https://engine.test"


@pytest.fixture
def novu():
    return FakeNovu()


@pytest.fixture
def engine():
    return FakeEngine()


@pytest.fixture
def mirror(engine):
    """A mirror that sends in the caller's thread, so a test sees the copy at once."""
    return NovuMirror(
        MIRROR_URL,
        "engine-key",
        background=False,
        transport=httpx.MockTransport(engine.handle),
    )


def client(novu, mirror):
    return httpx.Client(
        base_url=NOVU,
        headers={"Authorization": "ApiKey novu-key"},
        transport=httpx.MockTransport(novu.handle),
        event_hooks={"response": [mirror.on_response]},
    )


def trigger_body(**extra):
    return {"name": "welcome", "to": ["u1"], "payload": {"name": "Ana"}, **extra}


# --- what is copied --------------------------------------------------------------


def test_a_trigger_novu_accepted_is_copied_with_the_transaction_id_novu_gave_it(
    novu, engine, mirror
):
    client(novu, mirror).post("/v1/events/trigger", json=trigger_body())

    [copy] = engine.requests
    assert copy.method == "POST"
    assert str(copy.url) == f"{MIRROR_URL}/v1/events/trigger"
    assert copy.headers["authorization"] == "ApiKey engine-key"  # never Novu's key
    body = json.loads(copy.content)
    assert body["name"] == "welcome" and body["payload"] == {"name": "Ana"}
    assert body["transactionId"] == "novu-tx-1"  # the same notification on both sides


def test_a_trigger_with_its_own_transaction_id_keeps_it(novu, engine, mirror):
    client(novu, mirror).post(
        "/v1/events/trigger", json=trigger_body(transactionId="acct:abc")
    )

    assert engine.bodies()[0]["transactionId"] == "acct:abc"


def test_each_event_of_a_bulk_trigger_gets_its_own_transaction_id(novu, engine, mirror):
    events = [trigger_body(), trigger_body(transactionId="mine")]
    client(novu, mirror).post("/v1/events/trigger/bulk", json={"events": events})

    [copy] = engine.requests
    assert copy.url.path == "/v1/events/trigger/bulk"
    assert [e["transactionId"] for e in json.loads(copy.content)["events"]] == [
        "novu-tx-1",
        "mine",
    ]


def test_subscriber_changes_and_cancels_are_copied(novu, engine, mirror):
    novu_client = client(novu, mirror)
    novu_client.post(
        "/v2/subscribers", json={"subscriberId": "u1", "email": "a@example.com"}
    )
    novu_client.patch("/v2/subscribers/u1", json={"firstName": "Ana"})
    novu_client.delete("/v2/subscribers/u1")
    novu_client.delete("/v1/events/trigger/acct:abc")

    assert [(r.method, r.url.path) for r in engine.requests] == [
        ("POST", "/v2/subscribers"),
        ("PATCH", "/v2/subscribers/u1"),
        ("DELETE", "/v2/subscribers/u1"),
        ("DELETE", "/v1/events/trigger/acct:abc"),
    ]
    assert engine.bodies()[0]["email"] == "a@example.com"


def test_reading_the_activity_feed_is_not_copied(novu, engine, mirror):
    response = client(novu, mirror).get("/v1/notifications", params={"page": 0})

    assert response.status_code == 200
    assert engine.requests == []


def test_what_novu_refused_is_not_copied_and_the_caller_gets_the_refusal(
    novu, engine, mirror
):
    novu.status = 422

    response = client(novu, mirror).post("/v1/events/trigger", json=trigger_body())

    assert response.status_code == 422
    assert engine.requests == []


# --- never in the way --------------------------------------------------------------


def test_the_caller_gets_exactly_what_novu_answered(novu, engine, mirror):
    response = client(novu, mirror).post("/v1/events/trigger", json=trigger_body())

    assert response.json() == {
        "data": {
            "acknowledged": True,
            "status": "processed",
            "transactionId": "novu-tx-1",
        }
    }


def test_an_unreachable_engine_changes_nothing_for_the_caller(novu, engine, mirror):
    engine.down = True

    response = client(novu, mirror).post("/v1/events/trigger", json=trigger_body())

    assert response.status_code == 201
    assert len(novu.requests) == 1


def test_a_slow_engine_does_not_make_the_caller_wait(novu, engine):
    engine.hold = threading.Event()
    background = NovuMirror(
        MIRROR_URL, "engine-key", transport=httpx.MockTransport(engine.handle)
    )

    started = time.monotonic()
    client(novu, background).post("/v1/events/trigger", json=trigger_body())
    waited = time.monotonic() - started
    try:
        assert waited < 1, f"the caller waited {waited:.1f}s for the engine"
        assert engine.requests == []  # still on its way
    finally:
        engine.hold.set()
    background.close(timeout=5)

    assert len(engine.requests) == 1


def test_when_too_much_is_waiting_new_copies_are_dropped_not_waited_for(novu, engine):
    engine.hold = threading.Event()
    small = NovuMirror(
        MIRROR_URL,
        "engine-key",
        transport=httpx.MockTransport(engine.handle),
        queue_size=2,
    )
    novu_client = client(novu, small)

    started = time.monotonic()
    for _ in range(10):
        novu_client.post("/v1/events/trigger", json=trigger_body())
    assert time.monotonic() - started < 1
    engine.hold.set()
    small.close(timeout=5)

    assert len(novu.requests) == 10  # Novu got every one
    assert (
        len(engine.requests) <= 3
    )  # one in flight plus a full queue; the rest dropped
    assert small.dropped >= 7


# --- nothing personal in the logs ----------------------------------------------------


def test_a_refused_copy_is_logged_without_the_path_or_the_answer(
    novu, mirror, monkeypatch, caplog
):
    """A subscriber id can be an email address, and an answer can echo a payload."""

    def refuse(request):
        return httpx.Response(404, text="no subscriber ana@example.com, reset=SECRET")

    monkeypatch.setattr(
        mirror,
        "_client",
        httpx.Client(base_url=MIRROR_URL, transport=httpx.MockTransport(refuse)),
    )
    with caplog.at_level("WARNING"):
        client(novu, mirror).delete(
            "/v2/subscribers/nonusercollaborator:ana@example.com"
        )

    logged = caplog.text
    assert "subscriber delete" in logged and "404" in logged
    assert "ana@example.com" not in logged and "SECRET" not in logged


def test_a_copy_given_up_on_is_logged_without_the_path(
    novu, engine, monkeypatch, caplog
):
    engine.down = True
    retrying = NovuMirror(
        MIRROR_URL,
        "engine-key",
        background=False,
        transport=httpx.MockTransport(engine.handle),
        attempts=1,
    )
    with caplog.at_level("WARNING"):
        client(novu, retrying).delete(
            "/v2/subscribers/nonusercollaborator:ana@example.com"
        )

    assert "subscriber delete" in caplog.text
    assert "ana@example.com" not in caplog.text


def test_an_unexpected_error_is_logged_without_the_path(
    novu, mirror, monkeypatch, caplog
):
    def explode(item, retry=False):
        raise ValueError(f"bad url {item.path}")

    monkeypatch.setattr(mirror, "_send", explode)
    with caplog.at_level("WARNING"):
        response = client(novu, mirror).delete("/v2/subscribers/ana@example.com")

    assert response.status_code == 200  # the caller never sees it
    assert "ValueError" in caplog.text and "ana@example.com" not in caplog.text


class BrokenBody(httpx.SyncByteStream):
    """A body that times out while it is read, as a dropped connection does."""

    def __iter__(self):
        raise httpx.ReadTimeout("the body never came")
        yield b""  # pragma: no cover


def test_a_body_that_fails_to_read_fails_the_caller_as_it_would_without_the_mirror(
    engine, mirror
):
    """The SDK retries a timeout; it must get the timeout, not another error."""

    def novu_with_broken_body(request):
        return httpx.Response(201, stream=BrokenBody())

    with_mirror = httpx.Client(
        base_url=NOVU,
        transport=httpx.MockTransport(novu_with_broken_body),
        event_hooks={"response": [mirror.on_response]},
    )

    with pytest.raises(httpx.ReadTimeout):
        with_mirror.post("/v1/events/trigger", json=trigger_body())
    assert engine.requests == []
