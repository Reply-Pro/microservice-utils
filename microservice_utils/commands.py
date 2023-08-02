from pydantic import BaseModel


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
