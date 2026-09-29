import time
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict


class MessageDirection(str, Enum):
    EXTERNAL = "external"
    INTERNAL = "internal"


@dataclass(frozen=True)
class Envelope:
    message_id: str
    direction: MessageDirection
    type: str
    payload: Dict[str, Any] = field(default_factory=dict)
    created_at: float = field(default_factory=time.time)

    def event(self) -> Dict[str, Any]:
        return {"type": self.type, **dict(self.payload)}


def make_message_id() -> str:
    return uuid.uuid4().hex[:16]


def normalize_envelope(
    message: Dict[str, Any],
    direction: MessageDirection = MessageDirection.EXTERNAL,
) -> Envelope:
    if not isinstance(message, dict):
        raise ValueError("MESSAGE_MUST_BE_DICT")

    event_type = str(message.get("type") or "").strip().lower()
    if not event_type:
        raise ValueError("MESSAGE_TYPE_REQUIRED")

    payload = message.get("payload")
    if payload is None:
        payload = {
            key: value
            for key, value in message.items()
            if key not in {"type", "message_id"}
        }
    if not isinstance(payload, dict):
        raise ValueError("MESSAGE_PAYLOAD_MUST_BE_DICT")

    return Envelope(
        message_id=str(message.get("message_id") or make_message_id()),
        direction=direction,
        type=event_type,
        payload=dict(payload),
    )
