import json
import time

from pydantic import BaseModel, create_model


class Command(BaseModel):
    class Config:
        frozen = True

    @classmethod
    @property
    def name(cls) -> str:
        return cls.__name__


class CommandEnvelope(BaseModel):
    command: str
    parameters: Command
    timestamp: int

    @classmethod
    def create(cls, command: Command) -> "CommandEnvelope":
        return cls(
            command=command.name,
            parameters=command,
            timestamp=int(time.time()),
        )

    @classmethod
    def from_published_json(
        cls,
        message: bytes,
    ) -> "CommandEnvelope":
        """Instantiate CommandEnvelope from a received message."""

        json_msg = json.loads(message)

        command = json_msg["command"]
        parameters = json_msg["parameters"]
        command_type = create_model(command, **parameters, __base__=Command)

        return cls(
            command=command,
            parameters=command_type(**parameters),
            timestamp=json_msg["timestamp"],
        )

    def to_publishable_json(self) -> bytes:
        return self.json().encode("utf-8")
