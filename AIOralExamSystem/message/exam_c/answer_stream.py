import time
from typing import Any, Dict, Optional


class AnswerStream:
    """Versioned text stream for the currently active question."""

    def __init__(self, question_id: str):
        self.question_id = str(question_id)
        self.text = ""
        self.version = 0
        self.last_received_at: Optional[float] = None
        self.judging_version: Optional[int] = None

    def append(self, chunk: Any) -> Dict[str, Any]:
        text = str(chunk or "")
        if text:
            self.text += text
            self.version += 1
            self.last_received_at = time.time()
        return self.snapshot()

    def replace(self, text: Any) -> Dict[str, Any]:
        self.text = str(text or "")
        self.version += 1
        self.last_received_at = time.time()
        return self.snapshot()

    def mark_judging(self, version: int) -> None:
        self.judging_version = max(
            int(self.judging_version or 0),
            int(version),
        )

    def has_new_text_after(self, version: int) -> bool:
        return self.version > int(version)

    @property
    def has_text(self) -> bool:
        return bool(self.text.strip())

    def snapshot(self) -> Dict[str, Any]:
        return {
            "question_id": self.question_id,
            "text": self.text,
            "version": self.version,
            "judging_version": self.judging_version,
        }
