import asyncio
from collections.abc import AsyncIterator
from typing import Any, Awaitable, Callable, Dict, Optional, Set

from .contracts import Envelope, MessageDirection, normalize_envelope


EventHandler = Callable[[Dict[str, Any]], Awaitable[list[Dict[str, Any]]]]


class BackgroundMessageSession:
    """Serialize messages and own the receive/pause/terminal lifecycle."""

    PAUSE_TYPES = {"pause"}
    RESUME_TYPES = {"resume"}
    START_TYPE = "start"
    TERMINAL_TYPES = {"end", "finish", "stop"}

    def __init__(
        self,
        exam_id: str,
        handler: EventHandler,
        output_queue: Optional[asyncio.Queue] = None,
    ):
        self.exam_id = str(exam_id or "")
        if not self.exam_id:
            raise ValueError("EXAM_ID_REQUIRED")

        self.handler = handler
        self.external_queue: asyncio.Queue[Envelope] = asyncio.Queue()
        self.internal_queue: asyncio.Queue[Envelope] = asyncio.Queue()
        self.output_queue = output_queue or asyncio.Queue()
        self.started = False
        self.paused = False
        self.closed = False
        self.dropped_count = 0
        self._worker: Optional[asyncio.Task] = None
        self._dispatch_tasks: Set[asyncio.Task] = set()

    async def start(self) -> None:
        if self.closed:
            raise RuntimeError("MESSAGE_SESSION_CLOSED")
        if self.started:
            return
        self.started = True
        self._worker = asyncio.create_task(self._run())

    async def submit(self, message: Dict[str, Any]) -> Dict[str, Any]:
        envelope = self._put(message, MessageDirection.EXTERNAL)
        return {
            "type": "accepted",
            "message_id": envelope.message_id,
            "exam_id": self.exam_id,
        }

    async def emit_internal(self, message: Dict[str, Any]) -> None:
        self._put(message, MessageDirection.INTERNAL)

    def _put(
        self,
        message: Dict[str, Any],
        direction: MessageDirection,
    ) -> Envelope:
        if self.closed:
            raise RuntimeError("MESSAGE_SESSION_CLOSED")
        if not self.started and direction is MessageDirection.EXTERNAL:
            raise RuntimeError("MESSAGE_SESSION_NOT_STARTED")

        envelope = normalize_envelope(message, direction)
        queue = (
            self.internal_queue
            if direction is MessageDirection.INTERNAL
            else self.external_queue
        )
        queue.put_nowait(envelope)
        return envelope

    async def next_output(self) -> Dict[str, Any]:
        return await self.output_queue.get()

    async def outputs(self) -> AsyncIterator[Dict[str, Any]]:
        while not self.closed or not self.output_queue.empty():
            yield await self.next_output()

    async def close(self) -> None:
        if self.closed and self._worker is None:
            return
        self.closed = True
        self.paused = False
        self.started = False
        worker = self._worker
        self._worker = None
        if (
            worker is not None
            and not worker.done()
            and worker is not asyncio.current_task()
        ):
            worker.cancel()
            await asyncio.gather(worker, return_exceptions=True)
        for task in list(self._dispatch_tasks):
            if not task.done():
                task.cancel()
        if self._dispatch_tasks:
            await asyncio.gather(*self._dispatch_tasks, return_exceptions=True)
            self._dispatch_tasks.clear()
        self._drain_queue(self.external_queue)
        self._drain_queue(self.internal_queue)

    def snapshot(self) -> Dict[str, Any]:
        return {
            "exam_id": self.exam_id,
            "started": self.started,
            "paused": self.paused,
            "closed": self.closed,
            "dropped": self.dropped_count,
            "external_pending": self.external_queue.qsize(),
            "internal_pending": self.internal_queue.qsize(),
            "dispatch_pending": len(self._dispatch_tasks),
        }

    async def _run(self) -> None:
        try:
            while not self.closed:
                envelope = await self._next_envelope()
                event = envelope.event()

                if envelope.direction is MessageDirection.EXTERNAL:
                    control = self._control_type(event)
                    if control == "pause":
                        self.paused = True
                        await self._put_output(
                            {
                                "type": "paused",
                                "exam_id": self.exam_id,
                            }
                        )
                        continue

                    if control == "resume":
                        was_paused = self.paused
                        self.paused = False
                        await self._put_output(
                            {
                                "type": "resumed",
                                "exam_id": self.exam_id,
                                "was_paused": was_paused,
                            }
                        )
                        continue

                    if control == "terminal":
                        if str(event.get("type") or "").strip().lower() == "end":
                            event["type"] = "stop"
                        await self._dispatch(event)
                        self.closed = True
                        self.paused = False
                        self._drain_queue(self.external_queue)
                        self._drain_queue(self.internal_queue)
                        return

                    if self.paused:
                        self.dropped_count += 1
                        continue

                self._schedule_dispatch(event)
        finally:
            self.started = False
            if self._worker is asyncio.current_task():
                self._worker = None

    def _schedule_dispatch(self, event: Dict[str, Any]) -> None:
        task = asyncio.create_task(self._dispatch(event))
        self._dispatch_tasks.add(task)
        task.add_done_callback(self._dispatch_tasks.discard)

    async def _dispatch(self, event: Dict[str, Any]) -> None:
        try:
            messages = await self.handler(event)
        except Exception as exc:
            messages = [
                {
                    "type": "error",
                    "error": "MESSAGE_HANDLER_FAILED",
                    "detail": str(exc),
                }
            ]
        for message in messages:
            await self._put_output(dict(message))

    async def _put_output(self, message: Dict[str, Any]) -> None:
        await self.output_queue.put(message)

    def _control_type(self, event: Dict[str, Any]) -> Optional[str]:
        event_type = str(event.get("type") or "").strip().lower()
        if event_type in self.TERMINAL_TYPES:
            return "terminal"
        if event_type in self.PAUSE_TYPES:
            return "pause"
        if event_type in self.RESUME_TYPES:
            return "resume"
        if event_type == self.START_TYPE and self.paused:
            return "resume"
        return None

    async def _next_envelope(self) -> Envelope:
        if not self.internal_queue.empty():
            return self.internal_queue.get_nowait()
        if not self.external_queue.empty():
            return self.external_queue.get_nowait()

        internal_wait = asyncio.create_task(self.internal_queue.get())
        external_wait = asyncio.create_task(self.external_queue.get())
        tasks = {
            internal_wait: self.internal_queue,
            external_wait: self.external_queue,
        }
        try:
            done, pending = await asyncio.wait(
                set(tasks),
                return_when=asyncio.FIRST_COMPLETED,
            )
            for task in pending:
                task.cancel()
            if pending:
                await asyncio.gather(*pending, return_exceptions=True)

            if internal_wait in done:
                if external_wait in done:
                    self.external_queue.put_nowait(external_wait.result())
                return internal_wait.result()
            return external_wait.result()
        finally:
            for task in tasks:
                if not task.done():
                    task.cancel()
            unfinished = [task for task in tasks if not task.done()]
            if unfinished:
                await asyncio.gather(*unfinished, return_exceptions=True)

    @staticmethod
    def _drain_queue(queue: asyncio.Queue) -> None:
        while not queue.empty():
            queue.get_nowait()
