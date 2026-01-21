from __future__ import annotations

import json
from datetime import datetime
from typing import Any, Literal, Optional

from pydantic import BaseModel, Field, root_validator, validator


def _validate_iso8601(value: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError("value must be a non-empty ISO8601 string")
    s = value.strip()
    if s.endswith("Z"):
        s = s[:-1] + "+00:00"
    datetime.fromisoformat(s)
    return value


class EventEnvelopeV1(BaseModel):
    name: str
    version: int = Field(default=1)
    event_id: str
    account_id: str
    emitted_at: str
    producer: Literal["omnichannel", "integrations-service"]
    trace_id: str
    correlation_id: str
    payload: dict[str, Any]
    dedupe_key: Optional[str] = None

    class Config:
        frozen = True
        extra = "allow"

    @validator("name", "event_id", "account_id", "trace_id", "correlation_id")
    def _non_empty(cls, value: str) -> str:
        if not isinstance(value, str) or not value.strip():
            raise ValueError("value must be a non-empty string")
        return value

    @validator("version")
    def _version_is_one(cls, value: int) -> int:
        if value != 1:
            raise ValueError("EventEnvelopeV1 only supports version=1")
        return value

    @validator("emitted_at")
    def _emitted_at_iso8601(cls, value: str) -> str:
        return _validate_iso8601(value)

    @root_validator
    def _payload_account_id_matches(cls, values: dict) -> dict:
        payload = values.get("payload")
        account_id = values.get("account_id")
        if isinstance(payload, dict) and "account_id" in payload:
            if payload.get("account_id") != account_id:
                raise ValueError("payload.account_id must match account_id")
        return values

    @classmethod
    def from_published_json(cls, message: bytes) -> "EventEnvelopeV1":
        try:
            json_msg = json.loads(message)
        except Exception as exc:
            raise RuntimeError(
                "Message doesn't have a valid EventEnvelope v1 schema"
            ) from exc

        if not isinstance(json_msg, dict):
            raise RuntimeError("Message doesn't have a valid EventEnvelope v1 schema")

        return cls(**json_msg)

    def to_publishable_json(self) -> bytes:
        return self.json().encode("utf-8")
