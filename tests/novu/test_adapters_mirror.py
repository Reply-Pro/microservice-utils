"""The Novu adapters with the mirror: switched on by its settings, and wired into the
real Novu SDK so that every call through it is covered."""

import json

import httpx
import pytest
from novu_py import BulkTriggerEventDto, TriggerEventRequestDto
from novu_py.models import NovuError

from microservice_utils.novu import adapters
from microservice_utils.novu.adapters import Notifier, SubscriberManager
from microservice_utils.novu.mirror import NovuMirror
from tests.novu.fakes import FakeEngine, FakeNovu

MIRROR_URL = "https://engine.test"


@pytest.fixture
def novu(monkeypatch):
    fake = FakeNovu()
    monkeypatch.setattr(
        adapters, "_novu_transport", lambda: httpx.MockTransport(fake.handle)
    )
    return fake


@pytest.fixture
def engine():
    return FakeEngine()


@pytest.fixture
def mirror(engine, monkeypatch):
    """A mirror that sends in the caller's thread, so a test sees the copy at once."""
    instance = NovuMirror(
        MIRROR_URL,
        "engine-key",
        background=False,
        transport=httpx.MockTransport(engine.handle),
    )
    monkeypatch.setattr(
        adapters, "mirror_from_settings", lambda url=None, api_key=None: instance
    )
    return instance


def trigger(**extra):
    return TriggerEventRequestDto(
        workflow_id="welcome", to=["u1"], payload={"a": 1}, **extra
    )


# --- through the real SDK --------------------------------------------------------


def test_a_notification_sent_through_the_notifier_reaches_the_mirror(
    novu, engine, mirror
):
    Notifier("novu-key").send_notification("welcome", ["u1"], {"name": "Ana"})

    [copy] = engine.requests
    assert copy.url.path == "/v1/events/trigger"
    body = json.loads(copy.content)
    assert body["payload"] == {"name": "Ana"}
    assert body["transactionId"] == "novu-tx-1"


def test_a_direct_sdk_call_is_copied_and_its_answer_reaches_the_caller(
    novu, engine, mirror
):
    """omnichannel's notifier calls self.api.trigger itself and reads the answer."""
    notifier = Notifier("novu-key")
    result = notifier.api.trigger(
        trigger_event_request_dto=trigger(transaction_id="acct:abc")
    ).result

    assert result.transaction_id == "acct:abc" and result.acknowledged is True
    assert engine.bodies()[0]["transactionId"] == "acct:abc"


def test_bulk_triggers_and_subscribers_are_copied(novu, engine, mirror):
    Notifier("novu-key").send_notifications(
        BulkTriggerEventDto(events=[trigger(), trigger()])
    )
    manager = SubscriberManager("novu-key")
    manager.subscribe_user("4b1e1f6e-7f3b-4a43-9a51-6f0b9d2c1a11", "a@example.com")
    manager.unsubscribe_collaborator("nonusercollaborator:b@example.com")

    assert [(r.method, r.url.path) for r in engine.requests] == [
        ("POST", "/v1/events/trigger/bulk"),
        ("POST", "/v2/subscribers"),
        ("DELETE", "/v2/subscribers/nonusercollaborator:b@example.com"),
    ]


def test_the_activity_feed_is_read_from_novu_only(novu, engine, mirror):
    Notifier("novu-key").get_notifications(page=0)

    assert engine.requests == []


def test_a_novu_error_still_reaches_the_caller(novu, engine, mirror):
    novu.status = 422  # not retried by the SDK

    with pytest.raises(NovuError):
        Notifier("novu-key").send_notification("welcome", ["u1"], {})

    assert engine.requests == []


def test_every_novu_client_of_a_process_shares_one_http_client(novu, mirror):
    """Services build a Notifier per injection; the SDK never closes a given client."""
    first, second = Notifier("novu-key"), SubscriberManager("novu-key")

    assert first.api.sdk_configuration.client is second.api.sdk_configuration.client


# --- switched on by its settings ------------------------------------------------------


def test_without_a_mirror_the_novu_client_is_built_exactly_as_before(monkeypatch):
    monkeypatch.setattr(
        adapters, "mirror_from_settings", lambda url=None, api_key=None: None
    )
    built = []
    monkeypatch.setattr(
        adapters, "Novu", lambda *args, **kwargs: built.append((args, kwargs))
    )

    Notifier("novu-key")
    SubscriberManager("novu-key")

    assert built == [(("novu-key",), {}), (("novu-key",), {})]


def test_without_settings_there_is_no_mirror(monkeypatch):
    monkeypatch.delenv("NOVU_MIRROR_URL", raising=False)
    monkeypatch.delenv("NOVU_MIRROR_API_KEY", raising=False)

    assert adapters.mirror_from_settings() is None


def test_both_settings_are_needed(monkeypatch):
    monkeypatch.setenv("NOVU_MIRROR_URL", MIRROR_URL)
    monkeypatch.delenv("NOVU_MIRROR_API_KEY", raising=False)

    assert adapters.mirror_from_settings() is None


def test_the_settings_switch_it_on_with_one_mirror_per_process(monkeypatch):
    monkeypatch.setenv("NOVU_MIRROR_URL", MIRROR_URL)
    monkeypatch.setenv("NOVU_MIRROR_API_KEY", "engine-key")

    first, second = adapters.mirror_from_settings(), adapters.mirror_from_settings()

    assert isinstance(first, NovuMirror)
    assert first is second  # one queue and one thread, however many clients
    first.close(timeout=1)


def test_explicit_arguments_win_over_the_environment(monkeypatch):
    monkeypatch.setenv("NOVU_MIRROR_URL", "https://from-env.test")
    monkeypatch.setenv("NOVU_MIRROR_API_KEY", "env-key")

    explicit = adapters.mirror_from_settings(url=MIRROR_URL, api_key="explicit-key")

    assert explicit.url == MIRROR_URL
    explicit.close(timeout=1)


def test_an_argument_and_an_environment_value_are_never_paired(monkeypatch):
    """Otherwise a test URL could receive the production engine's key."""
    monkeypatch.setenv("NOVU_MIRROR_URL", "https://production-engine.test")
    monkeypatch.setenv("NOVU_MIRROR_API_KEY", "production-key")

    assert adapters.mirror_from_settings(url="https://other.test") is None
    assert adapters.mirror_from_settings(api_key="other-key") is None


def test_with_the_mirror_on_novu_still_goes_through_the_environment_proxy(
    monkeypatch, mirror
):
    """httpx picks HTTPS_PROXY only when it chooses the transport itself."""
    monkeypatch.setenv("HTTPS_PROXY", "http://proxy.internal:3128")
    monkeypatch.setattr(adapters, "_http_clients", {})

    client = Notifier("novu-key").api.sdk_configuration.client
    as_before = httpx.Client(follow_redirects=True)

    assert {p.pattern for p in client._mounts} == {p.pattern for p in as_before._mounts}
    # httpx mounts a proxy transport for https:// only when it read HTTPS_PROXY.
    assert "https://" in {p.pattern for p in client._mounts}
