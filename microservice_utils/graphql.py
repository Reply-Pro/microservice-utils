from base64 import b64decode, b64encode
from dataclasses import dataclass
from uuid import UUID


def construct_global_id(node: str, uuid: UUID) -> str:
    return b64encode(s=f"{node}:{uuid}".encode(encoding="utf-8")).decode(
        encoding="utf-8"
    )


@dataclass(frozen=True)
class GlobalId:
    node: str
    uuid: UUID


def deconstruct_global_id(global_id: str) -> GlobalId:
    try:
        decoded_global_id = b64decode(s=global_id).decode(encoding="utf-8")
    except UnicodeDecodeError:
        raise RuntimeError("Invalid Global ID")

    decoded_global_id_parts = decoded_global_id.split(":", 1)

    return GlobalId(
        node=decoded_global_id_parts[0],
        uuid=UUID(decoded_global_id_parts[1]),
    )
