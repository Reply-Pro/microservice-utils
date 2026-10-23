# Shared event primitives

This package provides generic building blocks for event-driven services: an event base class, a
JSON envelope, and Google Cloud Pub/Sub publisher and subscriber adapters. Services build their
domain events (user presence among them) on top of these. The package does not know what any
event means or which topic belongs to which domain.

## Events and the envelope (`microservice_utils/events.py`)

- `Event`: a pydantic model. Its `name` is the class name. Extra fields are allowed.
- `register_event(cls)`: a decorator that adds an event class to a module-level registry, keyed
  by its `name`.
- `EventEnvelope`: the message that goes on the wire.

`EventEnvelope.create(event).to_publishable_json()` produces UTF-8 JSON bytes of this shape:

```json
{"event_type": "PaymentSubmitted", "timestamp": 1642620000, "data": {"confirmation_id": "..."}}
```

`timestamp` is the creation time in whole seconds since the epoch.

`EventEnvelope.from_published_json(message_bytes)` reads it back:

- it raises `RuntimeError` when `event_type` or `data` is missing;
- it raises `RuntimeError` when `event_type` is not in the registry, unless it is called with
  `allow_unregistered_events=True`;
- `data` comes back as a plain `Event` carrying the received fields, **not** as an instance of
  the registered class. The registry only decides whether the message is accepted. Field types
  are therefore not re-validated (a UUID arrives as a string), and a consumer that needs the
  typed model must build it from `data` itself.

A typical consumer dispatches on `event_type`:

```python
envelope = EventEnvelope.from_published_json(message.data)
for handler in EVENT_HANDLERS.get(envelope.event_type, []):
    handler(envelope.data)
```

`microservice_utils/events_v1.py` defines a different, stricter envelope (`EventEnvelopeV1`)
with its own fields. It is separate from the envelope above and is not covered here.

## Pub/Sub adapters (`microservice_utils/google_cloud/adapters/pubsub.py`)

Both adapters take the GCP project id and an optional `prepend_value`, usually the environment
name. Callers pass short, friendly names and the adapter builds the full resource name:

| Call | Friendly name | Resource name with `prepend_value="staging"` |
| --- | --- | --- |
| `Publisher.publish` | `accounts.users` | `projects/<project>/topics/staging.accounts.users` |
| `Subscriber.subscribe` | `accounts.users.billing` | `projects/<project>/subscriptions/staging.accounts.users.billing` |

Without `prepend_value` the friendly name is used as is.

`Publisher.publish(topic_name, data, **attributes)`:

- waits for Pub/Sub to accept the message and returns its message id;
- retries up to 3 attempts with exponential backoff, then raises `PublishError`;
- batches at most 250 messages per request.

`Subscriber.subscribe(subscription_name, handler, max_messages=50,
await_callbacks_on_shutdown=False, **kwargs)`:

- opens a streaming pull with flow control limited to `max_messages` outstanding messages;
- can be called several times, once per subscription; `wait_for_shutdown()` blocks on all of
  them and `shutdown()` cancels them all;
- with `await_callbacks_on_shutdown=True`, `shutdown()` waits for in-flight handlers to finish.
  The Pub/Sub default (`False`) abandons them, so a handler that acknowledges before finishing
  its work loses that message on a restart. Long-lived workers that get redeployed usually want
  it on.

## Ownership boundary

Change these in the service that owns them, not here:

- the schema of a specific event;
- what a status or any other domain value means;
- which topic or subscription a service uses, and how messages are routed between services.

Change this package only for behaviour every consumer should share: the envelope format, name
formatting, retries and subscriber lifecycle. Any such change reaches every service that
upgrades the package, so keep it backwards compatible.

## Tests

- `tests/test_events.py`: event names, envelope creation and parsing, unregistered event types.
- `tests/google_cloud/test_pubsub.py`: subscriber options and shutdown.
