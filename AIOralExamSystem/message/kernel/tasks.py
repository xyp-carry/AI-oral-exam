import asyncio
from typing import Any, Awaitable, Callable, Dict, Set


class TaskManager:
    """Owns fire-and-forget jobs for one session."""

    def __init__(self):
        self._tasks: Set[asyncio.Task] = set()

    def create(self, coroutine: Awaitable[Any], done: Callable[[asyncio.Task], None] | None = None) -> asyncio.Task:
        task = asyncio.create_task(coroutine)
        self._tasks.add(task)

        def remove_when_done(finished: asyncio.Task) -> None:
            self._tasks.discard(finished)
            if done is not None:
                done(finished)

        task.add_done_callback(remove_when_done)
        return task

    @property
    def running_count(self) -> int:
        return sum(1 for task in self._tasks if not task.done())

    @property
    def has_running(self) -> bool:
        return self.running_count > 0

    def snapshots(self) -> Dict[str, Any]:
        return {
            "running": self.running_count,
            "total": len(self._tasks),
        }

    async def wait_all(self) -> None:
        while True:
            tasks = list(self._tasks)
            if not tasks:
                return
            await asyncio.gather(*tasks, return_exceptions=True)

    async def cancel_all(self) -> None:
        tasks = list(self._tasks)
        for task in tasks:
            if not task.done():
                task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
