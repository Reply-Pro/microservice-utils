from types import SimpleNamespace
from unittest.mock import patch

import pytest

from microservice_utils.telnyx import TelnyxAPIClient


def _mock_response(payload):
    return SimpleNamespace(
        raise_for_status=lambda: None,
        json=lambda: payload,
    )


def test_fetch_detail_record_returns_payload():
    payload = {
        "data": [
            {
                "id": "rec-123",
                "call_sec": "4",
                "billed_sec": "6",
                "rate": "0.01",
                "cost": "0.06",
                "currency": "USD",
                "call_session_id": "v3:test",
                "started_at": "2025-11-12T13:24:16Z",
                "finished_at": "2025-11-12T13:24:25Z",
            }
        ]
    }

    with patch(
        "microservice_utils.telnyx.client.requests.request",
        return_value=_mock_response(payload),
    ) as mock_request:
        client = TelnyxAPIClient("token", base_url="https://example.com/api")
        record = client.fetch_detail_record("v3:test")

    assert record is not None
    assert record.id == "rec-123"
    assert record.call_sec == 4
    mock_request.assert_called_once_with(
        "GET",
        "https://example.com/api/detail_records",
        params={
            "filter[record_type]": "sip-trunking",
            "filter[telnyx_session_id]": "v3:test",
            "page[size]": 1,
        },
        headers={"Authorization": "Bearer token"},
        timeout=10,
    )


def test_fetch_detail_record_returns_none_when_empty():
    payload = {"data": []}

    with patch(
        "microservice_utils.telnyx.client.requests.request",
        return_value=_mock_response(payload),
    ):
        client = TelnyxAPIClient("token")
        assert client.fetch_detail_record("missing") is None


def test_fetch_recording_prefers_call_leg_id_and_parses_download(monkeypatch):
    payload = {
        "data": [
            {
                "id": "record-1",
                "status": "completed",
                "channels": "dual",
                "source": "call",
                "duration_millis": "11153",
                "download_urls": {"mp3": "https://example.com/audio.mp3"},
                "recording_started_at": "2025-11-06T15:47:39.455400Z",
                "recording_ended_at": "2025-11-06T15:47:50.608561Z",
            }
        ]
    }

    with patch(
        "microservice_utils.telnyx.client.requests.request",
        return_value=_mock_response(payload),
    ) as mock_request:
        client = TelnyxAPIClient("token")
        recording = client.fetch_recording(call_leg_id="leg-1")

    assert recording is not None
    assert recording.download_url == "https://example.com/audio.mp3"
    mock_request.assert_called_once_with(
        "GET",
        "https://api.telnyx.com/v2/recordings",
        params={"page[size]": 1, "filter[call_leg_id]": "leg-1"},
        headers={"Authorization": "Bearer token"},
        timeout=10,
    )


def test_fetch_recording_requires_identifier():
    client = TelnyxAPIClient("token")
    with patch("microservice_utils.telnyx.client.requests.request") as mock_request:
        assert client.fetch_recording() is None
    mock_request.assert_not_called()
