#
# Copyright (c) 2024-2026, Daily
#
# SPDX-License-Identifier: BSD 2-Clause License
#

"""Base object class providing event handling and lifecycle management.

This module provides the foundational BaseObject class that offers common
functionality including unique identification, naming, event handling,
and async cleanup for all Pipecat components.
"""

import asyncio
import inspect
import time
import traceback
from abc import ABC
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional

from loguru import logger

from AIOralExamSystem.utils.utils import obj_count, obj_id
logger.remove()

@dataclass
class EventHandler:
    """Data class to store event handlers information.

    This data class stores the event name, a list of handlers to run for this
    event, and whether these handlers will be executed in a task.

    Parameters:
        name (str): The name of the event handler.
        handlers (List[Any]): A list of functions to be called when this event is triggered.
        is_sync (bool): Indicates whether the functions are executed in a task.

    """

    name: str
    handlers: List[Any]
    is_sync: bool


@dataclass
class EventContext:
    """事件上下文，用于在事件传递中携带通用监控信息。

    所有通过 BaseObject 触发的事件都会附带此上下文，便于下游 handler
    获取当前对象的心跳、资源使用等运行时状态。

    特殊信息可由具体事件继承并扩展，但以下字段为通用基础字段：
        timestamp (float): 事件触发时的 Unix 时间戳（秒）。
        heartbeat (float): 对象当前心跳时间戳，可用于判断对象是否存活。
        resource_usage (Dict[str, Any]): 个体资源使用情况，如 CPU、内存等。
        metadata (Dict[str, Any]): 额外的通用元数据，供扩展使用。

    """

    timestamp: float = field(default_factory=time.time)
    heartbeat: float = field(default_factory=time.time)
    resource_usage: Dict[str, Any] = field(default_factory=dict)
    metadata: Dict[str, Any] = field(default_factory=dict)


class BaseObject(ABC):
    """Abstract base class providing common functionality for Pipecat objects.

    Provides unique identification, naming, event handling capabilities,
    and async lifecycle management for all Pipecat components. All major
    classes in the framework should inherit from this base class.
    """

    def __init__(self, *, name: Optional[str] = None, **kwargs):
        """Initialize the base object.

        Args:
            name: Optional custom name for the object. If not provided,
                generates a name using the class name and instance count.
            **kwargs: Additional arguments passed to parent class.
        """
        self._id: int = obj_id()
        self._name = name or f"{self.__class__.__name__}#{obj_count(self)}"

        # Registered event handlers.
        self._event_handlers: Dict[str, EventHandler] = {}

        # Set of tasks being executed. When a task finishes running it gets
        # automatically removed from the set. When we cleanup we wait for all
        # event tasks still being executed.
        self._event_tasks = set()

        # Event context for carrying heartbeat and resource usage information.
        self._event_context = EventContext()
        self._heartbeat_interval: float = 5.0
        self.show_heartbeat: bool = False
        self._heartbeat_task: Optional[asyncio.Task] = None
        self._resource_collectors: List[Callable[[], Dict[str, Any]]] = []

    @property
    def id(self) -> int:
        """Get the unique identifier for this object.

        Returns:
            The unique integer ID assigned to this object instance.
        """
        return self._id

    @property
    def name(self) -> str:
        """Get the name of this object.

        Returns:
            The object's name, either custom-provided or auto-generated.
        """
        return self._name

    async def cleanup(self):
        """Clean up resources and wait for running event handlers to complete.

        This method should be called when the object is no longer needed.
        It waits for all currently executing event handler tasks to finish
        before returning. Also stops the heartbeat loop if running.
        """
        await self.stop_heartbeat()

        if self._event_tasks:
            event_names, tasks = zip(*self._event_tasks)
            # logger.debug(f"{self}: waiting on event handlers to finish {list(event_names)}...")
            await asyncio.wait(tasks)

    def event_handler(self, event_name: str):
        """Decorator for registering event handlers.

        Args:
            event_name: The name of the event to handle.

        Returns:
            The decorator function that registers the handler.
        """

        def decorator(handler):
            self.add_event_handler(event_name, handler)
            return handler

        return decorator

    def add_event_handler(self, event_name: str, handler):
        """Add an event handler for the specified event.

        Args:
            event_name: The name of the event to handle.
            handler: The function to call when the event occurs.
                Can be sync or async.
        """
        if event_name in self._event_handlers:
            self._event_handlers[event_name].handlers.append(handler)
        else:
            logger.warning(f"{self}: event handler {event_name} not registered")

    def _register_event_handler(self, event_name: str, sync: bool = False):
        """Register an event handler type.

        Args:
            event_name: The name of the event type to register.
            sync: Whether this event handler will be executed in a task.
        """
        if event_name not in self._event_handlers:
            self._event_handlers[event_name] = EventHandler(
                name=event_name, handlers=[], is_sync=sync
            )
        else:
            logger.warning(f"{self}: event handler {event_name} already registered")

    async def _call_event_handler(self, event_name: str, *args, **kwargs):
        """Call all registered handlers for the specified event.

        自动将当前 event_context 注入到 kwargs 中，供 handler 获取心跳和资源信息。

        Args:
            event_name: The name of the event to trigger.
            *args: Positional arguments to pass to event handlers.
            **kwargs: Keyword arguments to pass to event handlers.
        """
        if event_name not in self._event_handlers:
            return

        # Inject event_context so handlers can access heartbeat/resource info.
        kwargs.setdefault("event_context", self._event_context)

        event_handler = self._event_handlers[event_name]

        for handler in event_handler.handlers:
            if event_handler.is_sync:
                # Just run the handler.
                await self._run_handler(event_handler.name, handler, *args, **kwargs)
            else:
                # Create the task. Note that this is a task per each function
                # handler. Users can register to an event handler multiple
                # times.
                task = asyncio.create_task(
                    self._run_handler(event_handler.name, handler, *args, **kwargs)
                )

                # Add it to our list of event tasks.
                self._event_tasks.add((event_name, task))

                # Remove the task from the event tasks list when the task completes.
                task.add_done_callback(self._event_task_finished)

    async def _run_handler(self, event_name: str, handler, *args, **kwargs):
        """Execute all handlers for an event.

        Args:
            event_name: The event name for this handler.
            handler: The handler function to run.
            *args: Positional arguments to pass to handlers.
            **kwargs: Keyword arguments to pass to handlers.
        """
        try:
            if inspect.iscoroutinefunction(handler):
                await handler(self, *args, **kwargs)
            else:
                handler(self, *args, **kwargs)
        except Exception as e:
            tb = traceback.extract_tb(e.__traceback__)
            last = tb[-1]
            logger.error(
                f"{self}: uncaught exception in event handler '{event_name}' ({last.filename}:{last.lineno}): {e}"
            )

    def _event_task_finished(self, task: asyncio.Task):
        """Clean up completed event handler tasks.

        Args:
            task: The completed asyncio Task to remove from tracking.
        """
        tuple_to_remove = next((t for t in self._event_tasks if t[1] == task), None)
        if tuple_to_remove:
            self._event_tasks.discard(tuple_to_remove)

    def _remove_handler(self, event_name: str, handler) -> bool:
        """🆕 从指定事件中移除某个具体的 handler 函数。

        Args:
            event_name: 事件名称。
            handler: 要移除的函数引用（需与 add_event_handler 时传入的是同一对象）。

        Returns:
            bool: 是否成功移除。
        """
        if event_name not in self._event_handlers:
            logger.debug(f"{self}: event handler '{event_name}' not found")
            return False

        handlers = self._event_handlers[event_name].handlers

        try:
            handlers.remove(handler)
            handler_name = getattr(handler, "__name__", repr(handler))
            logger.info(
                f"{self}: removed handler '{handler_name}' from event '{event_name}' "
                f"({len(handlers)} remaining)"
            )
            return True
        except ValueError:
            logger.debug(
                f"{self}: handler {getattr(handler, '__name__', repr(handler))} "
                f"not found in event '{event_name}'"
            )
            return False
        
    def get_all_handlers(self) -> List[Any]:
        """🆕 获取所有事件中所有已注册 handler 的信息。

        Returns:
            List[HandlerInfo]: 按「事件名 → 注册顺序」排列的 handler 列表。
        """
        result: List[Any] = []
        for event_name in sorted(self._event_handlers.keys()):
            result.extend(self.get_handlers(event_name))
        return result

    # ------------------------------------------------------------------
    # Event context utilities (heartbeat & resource usage)
    # ------------------------------------------------------------------

    @property
    def event_context(self) -> EventContext:
        """获取当前对象的事件上下文。

        Returns:
            EventContext: 包含心跳、资源使用等运行时信息。
        """
        return self._event_context

    def update_heartbeat(self):
        """手动更新心跳时间戳。

        通常在对象执行关键操作时调用，也可由自动心跳任务定期调用。
        """
        # self._event_context.heartbeat = time.time()
        logger.debug(f"{self}: heartbeat")

    def set_heartbeat_interval(self, interval: float):
        """设置自动心跳间隔。

        Args:
            interval: 心跳间隔秒数，必须大于 0。
        """
        if interval <= 0:
            raise ValueError("heartbeat interval must be greater than 0")
        self._heartbeat_interval = interval

    async def start_heartbeat(self):
        """启动自动心跳任务。

        该任务会在后台定期更新 heartbeat 字段，直到调用 stop_heartbeat 或 cleanup。
        """
        if self._heartbeat_task is not None and not self._heartbeat_task.done():
            logger.warning(f"{self}: heartbeat task already running")
            return
        
        self.task_start_time = time.time()
        self._heartbeat_task = asyncio.create_task(self._heartbeat_loop())
        logger.info(f"{self}: heartbeat started (interval={self._heartbeat_interval}s)")

    async def _heartbeat_loop(self):
        """后台心跳循环，定期更新 heartbeat 时间戳。"""
        while True:
            try:
                await asyncio.sleep(self._heartbeat_interval)
                if self.show_heartbeat:
                    self.update_heartbeat()
            except asyncio.CancelledError:
                logger.debug(f"{self}: heartbeat loop cancelled")
                break

    async def stop_heartbeat(self):
        """停止自动心跳任务。"""
        if self._heartbeat_task is not None and not self._heartbeat_task.done():
            self._heartbeat_task.cancel()
            try:
                await self._heartbeat_task
            except asyncio.CancelledError:
                pass
            self._heartbeat_task = None
            logger.info(f"{self}: heartbeat stopped and the task run {time.time() - self.task_start_time}s")

    def register_resource_collector(self, collector: Callable[[], Dict[str, Any]]):
        """注册资源采集函数。

        采集函数应返回一个字典，描述当前对象的资源使用情况
        （如 CPU、内存、句柄数等）。

        Args:
            collector: 无参数函数，返回 Dict[str, Any]。
        """
        self._resource_collectors.append(collector)
        logger.debug(f"{self}: resource collector registered")

    def unregister_resource_collector(self, collector: Callable[[], Dict[str, Any]]) -> bool:
        """注销资源采集函数。

        Args:
            collector: 之前注册过的采集函数。

        Returns:
            bool: 是否成功移除。
        """
        try:
            self._resource_collectors.remove(collector)
            logger.debug(f"{self}: resource collector unregistered")
            return True
        except ValueError:
            logger.debug(f"{self}: resource collector not found")
            return False

    def collect_resources(self) -> Dict[str, Any]:
        """执行所有已注册的资源采集函数并汇总结果。

        Returns:
            Dict[str, Any]: 合并后的资源使用信息。
        """
        merged: Dict[str, Any] = {}
        for collector in self._resource_collectors:
            try:
                data = collector()
                if isinstance(data, dict):
                    merged.update(data)
            except Exception as e:
                logger.warning(f"{self}: resource collector failed: {e}")
        self._event_context.resource_usage = merged
        return merged

    def update_event_metadata(self, key: str, value: Any):
        """更新事件上下文中的通用元数据。

        Args:
            key: 元数据键。
            value: 元数据值。
        """
        self._event_context.metadata[key] = value

    def get_event_metadata(self, key: str, default: Any = None) -> Any:
        """获取事件上下文中的通用元数据。

        Args:
            key: 元数据键。
            default: 键不存在时的默认值。

        Returns:
            元数据值或 default。
        """
        return self._event_context.metadata.get(key, default)

    def __str__(self):
        """Return the string representation of this object.

        Returns:
            The object's name as its string representation.
        """
        return self.name
