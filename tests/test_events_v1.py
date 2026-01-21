import pytest

from microservice_utils.events_v1 import EventEnvelopeV1


def _valid_envelope(**overrides):
    payload = {"account_id": "acc_1", "call_control_id": "ccid_1"}
    data = {
        "name": "TelnyxWebhookReceived",
        "version": 1,
        "event_id": "evt_1",
        "account_id": "acc_1",
        "emitted_at": "2026-01-08T15:30:00Z",
        "producer": "integrations-service",
        "trace_id": "tr_1",
        "correlation_id": "corr_1",
        "payload": payload,
    }
    data.update(overrides)
    return data


def test_event_envelope_v1_valid_payload_roundtrip():
    envelope = EventEnvelopeV1(**_valid_envelope(extra_field="ok"))
    assert envelope.name == "TelnyxWebhookReceived"
    assert envelope.version == 1
    assert envelope.payload["account_id"] == "acc_1"
    assert envelope.to_publishable_json().startswith(b"{")


def test_event_envelope_v1_payload_account_id_mismatch():
    data = _valid_envelope(payload={"account_id": "acc_2"})
    with pytest.raises(ValueError):
        EventEnvelopeV1(**data)


def test_event_envelope_v1_rejects_wrong_version():
    with pytest.raises(ValueError):
        EventEnvelopeV1(**_valid_envelope(version=2))


def test_event_envelope_v1_rejects_bad_timestamp():
    with pytest.raises(ValueError):
        EventEnvelopeV1(**_valid_envelope(emitted_at="not-a-date"))


def test_event_envelope_v1_from_published_json():
    raw = EventEnvelopeV1(**_valid_envelope()).to_publishable_json()
    parsed = EventEnvelopeV1.from_published_json(raw)
    assert parsed.event_id == "evt_1"


def test_event_envelope_v1_from_published_json_invalid():
    with pytest.raises(RuntimeError):
        EventEnvelopeV1.from_published_json(b"not-json")
