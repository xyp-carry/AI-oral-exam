import asyncio
import logging
from abc import abstractmethod
from asyncio import iscoroutinefunction
from typing import Any, Optional

from AIOralExamSystem.utils.base_object import BaseObject


DEFAULT_TOOL_TIMEOUT_SECONDS = 60
logger = logging.getLogger(__name__)


class BaseTool(BaseObject):
    """Base class for project tools."""

    def __init__(self, name: str):
        super().__init__()
        self._name = name
        self._approval_result: Optional[bool] = None
        self._error_msg: str = ""
        self.timeout_seconds = DEFAULT_TOOL_TIMEOUT_SECONDS

    async def execute(self, *args, **kwargs) -> Any:
        heartbeat_started = False
        try:
            await self.start_heartbeat()
            heartbeat_started = True
            return await asyncio.wait_for(
                self._execute_run(*args, **kwargs),
                timeout=self.timeout_seconds,
            )
        except asyncio.TimeoutError:
            return self._build_timeout_response()
        except Exception as exc:
            return self._build_execution_error_response(exc)
        finally:
            if heartbeat_started:
                try:
                    await self.stop_heartbeat()
                except Exception as exc:
                    logger.warning("Failed to stop tool heartbeat for %s: %s", self.name, exc)

    async def _execute_run(self, *args, **kwargs) -> Any:
        run_method = self._run
        if iscoroutinefunction(run_method):
            return await run_method(*args, **kwargs)

        return await asyncio.to_thread(run_method, *args, **kwargs)

    def _build_timeout_response(self) -> dict:
        message = f"tool execution timed out after {self.timeout_seconds} seconds"
        return {
            "ok": False,
            "flag": "TOOL_EXECUTION_TIMEOUT",
            "error_type": "timeout",
            "tool": self.name,
            "timeout_seconds": self.timeout_seconds,
            "message": message,
            "error_message": message,
        }

    def _build_execution_error_response(self, exc: Exception) -> dict:
        message = f"{exc.__class__.__name__}: {exc}"
        return {
            "ok": False,
            "flag": "TOOL_EXECUTION_FAILED",
            "error_type": "execution_error",
            "tool": self.name,
            "exception_type": exc.__class__.__name__,
            "message": message,
            "error_message": message,
        }

    @abstractmethod
    def _run(self, *args, **kwargs) -> Any:
        """
        Subclasses implement the real tool logic.

        Use async def _run for async IO tools, or def _run for sync tools that
        should run in a worker thread.
        """
        raise NotImplementedError(f"tool [{self.name}] must implement _run")

