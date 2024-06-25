from datetime import date
from uuid import UUID

import pytest
from freezegun import freeze_time
from pydantic.errors import ConfigError

from microservice_utils import events


@events.register_event
class FakePaymentInitiated(events.Event):
    pass


@events.register_event
class FakePaymentSubmitted(events.Event):
    confirmation_id: UUID
    type: str


@events.register_event
class FakeNotificationSent(events.Event):
    pass


@events.register_event
class FakeReviewPosted(events.Event):
    content: str
    date: date
    permalink: str
    rating: int
    raw_rating: str
    summary: str


@pytest.mark.parametrize(
    "event,expected_event_name",
    [
        (FakePaymentInitiated, "FakePaymentInitiated"),
        (FakeNotificationSent, "FakeNotificationSent"),
        (FakeReviewPosted, "FakeReviewPosted"),
    ],
)
def test_event(event, expected_event_name):
    assert event.name == expected_event_name


@freeze_time("2022-01-19 19:20+00:00")
def test_event_envelope():
    """This test verifies that EventEnvelope creates the correct message schema for
    publishing events as messages via Pub/Sub, etc"""
    event = FakeReviewPosted(
        content="This place is cool",
        date=date(2022, 1, 18),
        permalink="https://fakereviews.com/abc",
        rating=4,
        raw_rating="4",
        summary="Cool place",
    )

    # Create message that can be published and verify
    message = events.EventEnvelope.create(event)

    assert message.event_type == "FakeReviewPosted"
    assert message.timestamp == 1642620000
    assert message.data == event

    # Check publishable json
    # Should look something like this:
    # {"event_type": "FakeReviewPosted", "timestamp": 1642620000,
    # "data": {"content": "This place is cool", etc }}
    publishable = message.to_publishable_json()

    assert isinstance(publishable, bytes)
    assert event.json().encode("utf-8") in publishable


def test_event_envelope_from_published_json():
    """Test reconstituting an EventEnvelope and Event from bytes"""
    raw_received_message = b"""
    {"event_type": "FakePaymentSubmitted", "timestamp": 1642620000, "data":
    {"confirmation_id": "11c6a57c-c2b5-4aca-8676-56b215da28bd", "type": "CC" }}
    """

    expected_event = FakePaymentSubmitted(
        confirmation_id="11c6a57c-c2b5-4aca-8676-56b215da28bd", type="CC"
    )

    message = events.EventEnvelope.from_published_json(raw_received_message)

    assert message.event_type == "FakePaymentSubmitted"
    assert message.timestamp == 1642620000
    assert message.data == expected_event


@pytest.mark.parametrize(
    "payload",
    [
        b"""{"timestamp": 1642620000, "data": {"type": "Something" }}""",
        b"""{"event_type": "FakePaymentSubmitted", "timestamp": 1642620000 }""",
    ],
)
def test_event_envelope_from_published_json_invalid_schema(payload):
    with pytest.raises(RuntimeError):
        events.EventEnvelope.from_published_json(payload)


def test_event_envelope_from_published_json_unregistered_event_type():
    raw_received_message = b"""
    {"event_type": "UnregisteredEventOccurred", "timestamp": 1642620000, "data":
    {"trace_id": "11c6a57c-c2b5-4aca-8676-56b215da28bd", "status": {"sys": "ok"} }}
    """

    # By default, we expect an exception if an unregistered event is received
    with pytest.raises(RuntimeError):
        events.EventEnvelope.from_published_json(raw_received_message)

    # But we can allow unregistered events
    message = events.EventEnvelope.from_published_json(
        raw_received_message, allow_unregistered_events=True
    )

    assert message.event_type == "UnregisteredEventOccurred"
    assert message.timestamp == 1642620000

    # Schema is unknown for unregistered events so the data is a dict and nested data
    # will be dicts instead of model instances
    assert message.data == {
        "trace_id": "11c6a57c-c2b5-4aca-8676-56b215da28bd",
        "status": {"sys": "ok"},
    }


@pytest.mark.parametrize(
    "raw_received_message",
    [
        # Missing event type
        b"""{"timestamp": 1642620000, "data": {"status": "ok"}}""",
        # Missing data
        b"""{"timestamp": 1642620000, "event_type": "UnregisteredEventOccurred"}""",
    ],
)
def test_event_envelope_from_published_json_unregistered_event_type_bad_schema(
    raw_received_message,
):
    """Test that an exception is raised if the enveloped messaged doesn't have an
    event type"""

    with pytest.raises(RuntimeError):
        events.EventEnvelope.from_published_json(
            raw_received_message, allow_unregistered_events=True
        )


@pytest.fixture
def raw_received_message_with_null() -> bytes:
    return b"""
    {"event_type": "UnknownEvent", "timestamp": 1666663140, "data": {
    "id": "0c3e52ef-28d7-4bec-b20f-480930473f63", "status": null}}
    """


def test_event_envelope_from_published_json_unregistered_event_type_null(
    raw_received_message_with_null,
):
    """By default, pydantic.create_model will not handle null. So, by default, we'll
    set any null value to False to appease create_model"""

    message = events.EventEnvelope.from_published_json(
        raw_received_message_with_null, allow_unregistered_events=True
    )
    event = message.data

    assert isinstance(event.id, str)
    assert isinstance(event.status, bool)
    assert event.status is False


def test_event_envelope_from_published_json_unregistered_event_type_null_dont_handle(
    raw_received_message_with_null,
):
    """Allow user to specify the default pydantic.create_model functionality"""

    with pytest.raises(ConfigError):
        events.EventEnvelope.from_published_json(
            raw_received_message_with_null,
            allow_unregistered_events=True,
            handle_none_values=False,
        )
