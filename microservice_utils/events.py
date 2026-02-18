import logging
import logging
import time
import typing

from pydantic import (
    ConfigDict,
    BaseModel,
    SerializeAsAny,
)

logger = logging.getLogger("django")

# Used by EventEnvelope
EventRegistry = typing.Dict[str, typing.Type["Event"]]
_event_registry: EventRegistry = {}


class Event(BaseModel):
    model_config = ConfigDict(extra="allow")

    @classmethod
    @property
    def name(cls) -> str:
        return cls.__name__


class EventEnvelope(BaseModel):
    """Use this class to publish events via messaging (e.g. Pub/Sub)"""

    event_type: str
    timestamp: int
    data: SerializeAsAny[Event]
    model_config = ConfigDict(extra="allow")

    @classmethod
    def create(cls, event: Event) -> "EventEnvelope":
        return cls(
            event_type=event.name,
            timestamp=int(time.time()),
            data=event,
        )

    @classmethod
    def from_published_json(
        cls,
        message: bytes,
        allow_unregistered_events: bool = False,
        **kwargs: typing.Any,
    ) -> "EventEnvelope":
        """Instantiate EventEnvelope from a received message (e.g. from Pub/Sub).
        This facilitates using Event instances in worker handler registries e.g.:

        event_envelope = EventEnvelope.from_published_json(message.data)

        EVENT_HANDLERS = {
            events.PaymentSubmitted: [handle_payment_submitted],
        }

        handlers = EVENT_HANDLERS.get(event_envelope.event_type, [])

        for handler in handlers:
            handler(event_envelope.data)
        """
        event = Event.model_validate_json(message).model_dump()
        try:
            event_type = event["event_type"]
        except KeyError:
            raise RuntimeError("Message doesn't have a valid EventEnvelope schema")

        try:
            data = event["data"]
        except KeyError:
            raise RuntimeError("Message doesn't have a valid EventEnvelope schema")

        if event_type not in get_registered_events() and not allow_unregistered_events:
            raise RuntimeError(
                f"Received message with unknown event type {event_type!r}"
            )

        return cls(
            event_type=event_type,
            data=data,
            timestamp=event.get("timestamp"),
        )

    def to_publishable_json(self) -> bytes:
        return self.model_dump_json().encode("utf-8")


def register_event(event: typing.Type[Event]):
    logger.debug("Registered event %s", event)
    _event_registry[event.name] = event

    return event


def get_registered_events() -> EventRegistry:
    return _event_registry
