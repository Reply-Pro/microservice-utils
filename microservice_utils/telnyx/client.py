import logging
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Optional

import requests

logger = logging.getLogger("microservice_utils.telnyx")
DEFAULT_BASE_URL = "https://api.telnyx.com/v2"


def _to_float(value: Any) -> Optional[float]:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _to_int(value: Any) -> Optional[int]:
    try:
        return int(value)
    except (TypeError, ValueError):
        try:
            return int(float(value))
        except (TypeError, ValueError):
            return None


def _parse_dt(raw: Optional[str]) -> Optional[datetime]:
    if not raw:
        return None
    try:
        if raw.endswith("Z"):
            raw = raw[:-1] + "+00:00"
        return datetime.fromisoformat(raw)
    except ValueError:
        logger.warning("[TELNYX] unable to parse datetime value: %s", raw)
        return None


@dataclass
class TelnyxDetailRecord:
    """
    Represents a Telnyx Call Detail Record (CDR) returned by the /detail_records API.

    This object contains the billing and duration information of a call leg/session,
    including the billed seconds, rate, cost, and timestamps. It is used for enriching
    a voice Action with financial and duration metadata after the call ends.
    """

    id: str
    call_sec: Optional[float]
    billed_sec: Optional[float]
    rate: Optional[float]
    cost: Optional[float]
    currency: Optional[str]
    call_session_id: Optional[str]
    started_at: Optional[str]
    finished_at: Optional[str]

    @classmethod
    def from_api(cls, payload: dict[str, Any]) -> "TelnyxDetailRecord":
        return cls(
            id=payload.get("id"),
            call_sec=_to_float(payload.get("call_sec")),
            billed_sec=_to_float(payload.get("billed_sec")),
            rate=_to_float(payload.get("rate")),
            cost=_to_float(payload.get("cost")),
            currency=payload.get("currency"),
            call_session_id=payload.get("telnyx_session_id"),
            started_at=payload.get("started_at"),
            finished_at=payload.get("finished_at"),
        )

    def to_attributes(self) -> dict[str, Any]:
        data = {
            "call_leg_id": self.id,
            "call_session_id": self.call_session_id,
            "currency": self.currency,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
        }
        if self.call_sec is not None:
            data["call_sec"] = self.call_sec
        if self.billed_sec is not None:
            data["billed_sec"] = self.billed_sec
        if self.rate is not None:
            data["rate"] = self.rate
        if self.cost is not None:
            data["cost"] = self.cost
        return data


def _extract_download_url(download_urls: dict[str, Any]) -> Optional[str]:
    for key in ("mp3", "wav", "ogg"):
        url = download_urls.get(key)
        if url:
            return url
    return None


@dataclass
class TelnyxRecordingInfo:
    """
    Represents metadata about a call recording returned
    by the Telnyx /recordings API.

    Includes basic information such as recording status,
    audio channels, recording duration,
    timestamps, and the downloadable URL for audio playback. Used to enrich a completed
    voice Action with recording information.
    """

    id: str
    status: Optional[str]
    channels: Optional[str]
    source: Optional[str]
    duration_millis: Optional[int]
    download_url: Optional[str]
    recording_started_at: Optional[datetime]
    recording_ended_at: Optional[datetime]

    @classmethod
    def from_api(cls, payload: dict[str, Any]) -> "TelnyxRecordingInfo":
        download_url = _extract_download_url(payload.get("download_urls") or {})
        return cls(
            id=payload.get("id"),
            status=payload.get("status"),
            channels=payload.get("channels"),
            source=payload.get("source"),
            duration_millis=_to_int(payload.get("duration_millis")),
            download_url=download_url,
            recording_started_at=_parse_dt(payload.get("recording_started_at")),
            recording_ended_at=_parse_dt(payload.get("recording_ended_at")),
        )


class TelnyxAPIClient:
    def __init__(
        self,
        api_key: Optional[str],
        *,
        base_url: str = DEFAULT_BASE_URL,
        timeout: int = 10,
    ) -> None:
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout

    def _request(
        self, method: str, path: str, params: dict[str, Any]
    ) -> Optional[dict]:
        if not self.api_key:
            logger.warning("[TELNYX] API key is not configured")
            return None

        url = f"{self.base_url}{path}"
        headers = {"Authorization": f"Bearer {self.api_key}"}
        try:
            response = requests.request(
                method,
                url,
                params=params,
                headers=headers,
                timeout=self.timeout,
            )
            response.raise_for_status()
        except requests.RequestException as exc:
            logger.warning("[TELNYX] API request failed: %s", exc)
            return None

        try:
            return response.json()
        except ValueError as exc:
            logger.warning("[TELNYX] Invalid JSON response: %s", exc)
            return None

    def fetch_detail_record(self, call_session_id: str) -> Optional[TelnyxDetailRecord]:
        params = {
            "filter[record_type]": "sip-trunking",
            "filter[telnyx_session_id]": call_session_id,
            "page[size]": 1,
        }
        payload = self._request("GET", "/detail_records", params)
        if not payload:
            return None

        data = (payload.get("data") or [])[:1]
        if not data:
            return None

        record = TelnyxDetailRecord.from_api(data[0])
        if not record.id:
            logger.warning(
                "[TELNYX] detail_records response missing id for telnyx_session_id=%s",
                call_session_id,
            )
            return None

        return record

    def fetch_recording(
        self,
        *,
        call_leg_id: Optional[str] = None,
        call_session_id: Optional[str] = None,
    ) -> Optional[TelnyxRecordingInfo]:
        if not call_leg_id and not call_session_id:
            return None

        params: dict[str, Any] = {"page[size]": 1}
        if call_leg_id:
            params["filter[call_leg_id]"] = call_leg_id
        if call_session_id:
            params["filter[call_session_id]"] = call_session_id

        payload = self._request("GET", "/recordings", params)
        if not payload:
            return None

        data = (payload.get("data") or [])[:1]
        if not data:
            return None

        record = TelnyxRecordingInfo.from_api(data[0])
        if not record.id:
            return None

        return record
