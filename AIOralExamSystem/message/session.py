import asyncio
from typing import Any, Dict, List, Optional, Protocol

from AIOralExamSystem.message.kernel import BackgroundMessageSession


class ExamFlow(Protocol):
    async def run(
        self,
        event: Dict[str, Any],
    ) -> List[Dict[str, Any]]:
        ...


class ExamMessageSession:
    """Common ordered message transport for every exam Flow."""

    EVENT_START = "start"
    EVENT_FINISH = "finish"
    EVENT_STOP = "stop"

    def __init__(
        self,
        flow: ExamFlow,
        exam_id: Optional[str] = None,
        input_queue: Optional[asyncio.Queue] = None,
        output_queue: Optional[asyncio.Queue] = None,
    ):
        self.flow = flow
        self.exam_id = str(exam_id or "")
        self.closed = False
        self.processed_events = 0
        self._user_locks: Dict[str, asyncio.Lock] = {}
        self.kernel = BackgroundMessageSession(
            self.exam_id or "exam",
            self._handle_event,
            output_queue=output_queue,
        )
        self.input_queue = input_queue or self.kernel.external_queue
        self.output_queue = self.kernel.output_queue
        if input_queue is not None:
            self.kernel.external_queue = input_queue

    async def start(self) -> None:
        await self.kernel.start()

    async def submit(self, message: Dict[str, Any]) -> Dict[str, Any]:
        return await self.kernel.submit(message)

    async def aclose(self) -> None:
        self.closed = True
        await self.kernel.close()

    async def handle_message(
        self,
        event: Dict[str, Any],
    ) -> List[Dict[str, Any]]:
        if self.kernel.started:
            return [await self.kernel.submit(event)]
        return await self._handle_event(dict(event or {}))

    async def _handle_event(
        self,
        event: Dict[str, Any],
    ) -> List[Dict[str, Any]]:
        event = dict(event or {})
        if self._event_type(event) == "request_next":
            return await self._run_flow_event(event)

        async with self._lock_for_event(event):
            return await self._run_flow_event(event)

    async def _run_flow_event(
        self,
        event: Dict[str, Any],
    ) -> List[Dict[str, Any]]:
        event_type = self._event_type(event)
        if self.closed and event_type not in {
            self.EVENT_FINISH,
            self.EVENT_STOP,
        }:
            return [{"type": "error", "error": "EXAM_ALREADY_CLOSED"}]

        outcomes = await self.flow.run(event)
        messages = [dict(item) for item in outcomes or []]
        self.processed_events += 1
        if any(
            self._event_type(message) in {"finished", "closed"}
            for message in messages
        ):
            self.closed = True
        return messages

    @property
    def state(self) -> Dict[str, Any]:
        return {
            "exam_id": self.exam_id,
            "closed": self.closed,
            "processed_events": self.processed_events,
            "user_lock_count": len(self._user_locks),
            "kernel": self.kernel.snapshot(),
        }

    def _lock_for_event(self, event: Dict[str, Any]) -> asyncio.Lock:
        lock_key = self._lock_key(event)
        lock = self._user_locks.get(lock_key)
        if lock is None:
            lock = asyncio.Lock()
            self._user_locks[lock_key] = lock
        return lock

    def _lock_key(self, event: Dict[str, Any]) -> str:
        user_id = (
            event.get("user_id")
            or getattr(self.flow, "user_id", "")
            or self.exam_id
            or "default"
        )
        return str(user_id)

    @staticmethod
    def _event_type(event: Dict[str, Any]) -> str:
        return str((event or {}).get("type") or "").strip().lower()
