import asyncio
from typing import Dict, Any
from collections import defaultdict
from loguru import logger
from AIOralExamSystem.message.base_queue import QueueManager
from pipecat.pipeline.task import  PipelineTask

class GlobalMonitor:
    _instance = None

    def __new__(cls, *args, **kwargs):
        # 实现单例模式，确保全局只有一个监测者
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._initialized = False
        return cls._instance

    def __init__(self):
        if self._initialized:
            return

        self._initialized = True
        self._queue = QueueManager()
        self.task: Dict[str, PipelineTask] = {}

        # 动态类别: { name: { obj_id: event_signal } }
        self._active_nodes: Dict[str, Dict[str, asyncio.Event]] = defaultdict(dict)

        self._is_running = False

        # 动态类别等待队列: { name: asyncio.Queue }
        self._pending_events: Dict[str, asyncio.Queue] = defaultdict(asyncio.Queue)

    async def start(self) -> bool:
        """启动监测者。"""
        if not self._is_running:
            self._is_running = True
            logger.info("监测者已启动。")
            asyncio.create_task(self._monitor_loop())

        return True

    async def _monitor_loop(self):
        while self._is_running:
            obj_info, rule, event_signal, obj_queue, signal_type = await self._queue.get("reqObj")

            obj_id = obj_info["id"]
            obj_name = obj_info["name"]

            if signal_type == "start":
                await self.start_obj(obj_id, obj_name, rule, event_signal, obj_queue)
                logger.info(f"[{obj_name}] 对象 id {obj_id} 已提交启动申请。")

            elif signal_type == "stop":
                await self.stop_obj(obj_id, obj_name, event_signal)
                logger.info(f"[{obj_name}] 对象 {obj_id} 已停止。")

                if not self._pending_events[obj_name].empty():
                    obj_info, rule, event_signal, obj_queue = await self._pending_events[obj_name].get()
                    obj_id = obj_info["id"]
                    obj_name = obj_info["name"]
                    await self.start_obj(obj_id, obj_name, rule, event_signal, obj_queue)

    async def stop_monitor(self):
        """停止监测者"""
        self._is_running = False
        logger.info("全局监测者已发出停止信号。")

    async def _evaluate_rule(self, obj_id: str, obj_name: str, rule: callable) -> bool:
        """
        判断对象是否允许启动。
        rule 接收当前类别下的 active_nodes。
        """
        if obj_id in self._active_nodes[obj_name]:
            return False

        return await rule(obj_id, self._active_nodes[obj_name])

    async def start_obj(
        self,
        obj_id: str,
        obj_name: str,
        rule: callable,
        event_signal: asyncio.Event,
        obj_queue: asyncio.Queue,
    ):
        """
        启动对象。如果对象已存在，直接返回。
        obj_queue 保留给后续通信使用。
        """
        if obj_id in self._active_nodes[obj_name]:
            return

        if await self._evaluate_rule(obj_id, obj_name, rule):
            self._active_nodes[obj_name][obj_id] = event_signal
            event_signal.set()

            logger.info(
                f"[{obj_name}][{obj_id}] 已放行。"
                f"当前类别运行数: {len(self._active_nodes[obj_name])}"
            )
        else:
            await self._pending_events[obj_name].put(
                (
                    {
                        "id": obj_id,
                        "name": obj_name,
                    },
                    rule,
                    event_signal,
                    obj_queue,
                )
            )

            logger.info(
                f"[{obj_name}][{obj_id}] 已进入等待队列。"
                f"当前类别等待数: {self._pending_events[obj_name].qsize()}"
            )

    async def stop_obj(
        self,
        obj_id: str,
        obj_name: str,
        event_signal: asyncio.Event,
    ):
        """
        停止对象。如果对象不存在，直接返回。
        """
        if obj_id in self._active_nodes[obj_name]:
            event_signal.set()
            self._active_nodes[obj_name].pop(obj_id)

            logger.info(
                f"[{obj_name}][{obj_id}] 已从内存移除。"
                f"当前类别运行数: {len(self._active_nodes[obj_name])}"
            )
        else:
            logger.warning(f"[{obj_name}][{obj_id}] 结束通知失败：未在运行内存中找到该ID。")

    async def get_status(self) -> dict:
        """获取当前监测者状态（可用于调试）"""
        return {
            "active_count": {
                obj_name: len(nodes)
                for obj_name, nodes in self._active_nodes.items()
            },
            "pending_count": {
                obj_name: pending_queue.qsize()
                for obj_name, pending_queue in self._pending_events.items()
            },
            "active_nodes": {
                obj_name: list(nodes.keys())
                for obj_name, nodes in self._active_nodes.items()
            },
        }
