import time

from pydantic import ConfigDict, BaseModel, SerializeAsAny


class Command(BaseModel):
    model_config = ConfigDict(extra="allow")

    @classmethod
    @property
    def name(cls) -> str:
        return cls.__name__


class CommandEnvelope(BaseModel):
    command: str
    parameters: SerializeAsAny[Command]
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
        command = Command.model_validate_json(message)

        return cls(
            command=command.command,
            parameters=command.parameters,
            timestamp=command.timestamp,
        )

    def to_publishable_json(self) -> bytes:
        return self.model_dump_json().encode("utf-8")
