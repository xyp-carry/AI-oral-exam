from threading import Lock
from typing import Any


class ExamPipelineRegistry:
    """Tracks pipelines owned by this process, including ones being built."""

    def __init__(self) -> None:
        self._lock = Lock()
        self._entries: dict[str, tuple[str, str, Any | None]] = {}

    def register(self, pipeline_id: str, exam_id: str, user_id: str) -> None:
        with self._lock:
            if pipeline_id in self._entries:
                raise ValueError("PIPELINE_ID_ALREADY_REGISTERED")
            self._entries[pipeline_id] = (exam_id, user_id, None)

    def bind_task(self, pipeline_id: str, task: Any) -> None:
        with self._lock:
            exam_id, user_id, _ = self._entries[pipeline_id]
            self._entries[pipeline_id] = (exam_id, user_id, task)

    def is_active(self, pipeline_id: str) -> bool:
        with self._lock:
            return pipeline_id in self._entries

    def unregister(self, pipeline_id: str) -> None:
        with self._lock:
            self._entries.pop(pipeline_id, None)

    def tasks_for_user(self, user_id: str) -> list[Any]:
        with self._lock:
            return [
                task for _, entry_user_id, task in self._entries.values()
                if entry_user_id == user_id and task is not None
            ]
