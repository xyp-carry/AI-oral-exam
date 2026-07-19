import asyncio
from abc import abstractmethod
from asyncio import iscoroutinefunction
from typing import Any, Optional

from AIOralExamSystem.utils.base_object import BaseObject
from AIOralExamSystem.utils.monitor import GlobalMonitor


DEFAULT_TOOL_TIMEOUT_SECONDS = 60


class BaseTool(BaseObject):
    """Base class for project tools."""

    def __init__(self, name: str):
        super().__init__()
        self._name = name
        self._approval_result: Optional[bool] = None
        self._error_msg: str = ""
        self._monitor = GlobalMonitor()
        self.queue = asyncio.Queue()
        self.timeout_seconds = DEFAULT_TOOL_TIMEOUT_SECONDS

    async def request_approval(self):
        self.event_signal = asyncio.Event()
        await self._monitor._queue.put(
            "reqObj",
            (
                {"id": self.id, "name": self.name},
                self.rule,
                self.event_signal,
                self.queue,
                "start",
            ),
        )
        await self.event_signal.wait()

    async def execute(self, *args, **kwargs) -> Any:
        await self.start_heartbeat()
        await self.request_approval()

        try:
            result = await asyncio.wait_for(
                self._execute_run(*args, **kwargs),
                timeout=self.timeout_seconds,
            )
            
            return result
        except asyncio.TimeoutError:
            return self._build_timeout_response()
        finally:
            await self.stop_heartbeat()
            await self._monitor._queue.put(
                "reqObj",
                (
                    {"id": self.id, "name": self.name},
                    self.rule,
                    self.event_signal,
                    self.queue,
                    "stop",
                ),
            )

    async def _execute_run(self, *args, **kwargs) -> Any:
        run_method = self._run
        if iscoroutinefunction(run_method):
            return await run_method(*args, **kwargs)

        return await asyncio.to_thread(run_method, *args, **kwargs)

    def _build_timeout_response(self) -> dict:
        return {
            "ok": False,
            "error_type": "timeout",
            "tool": self.name,
            "timeout_seconds": self.timeout_seconds,
            "error_message": f"tool execution timed out after {self.timeout_seconds} seconds",
        }

    @abstractmethod
    def _run(self, *args, **kwargs) -> Any:
        """
        Subclasses implement the real tool logic.

        Use async def _run for async IO tools, or def _run for sync tools that
        should run in a worker thread.
        """
        raise NotImplementedError(f"tool [{self.name}] must implement _run")

    async def rule(self, obj_id: str, active_nodes: dict) -> bool:
        if obj_id in active_nodes:
            return False
        if len(active_nodes) > 3:
            return False
        return True
