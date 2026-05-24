import asyncio
from typing import Any, Dict
from loguru import logger
from enum import Enum

class NodeType(Enum):
    AGENT = "Agent"
    TOOL = "Tool"

class QueueManager:
    _instance = None
    
    def __new__(cls, *args, **kwargs):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._initialized = False
        return cls._instance

    def __init__(self):
        if self._initialized:
            return
        self._initialized = False # 防止重入，下面马上设为 True
        # 管理各种类型的异步队列
        self._queues: Dict[str, asyncio.Queue] = {}
        self._initialized = True

    def get_queue(self, queue_name: str = "agent") -> asyncio.Queue:
        """获取指定名称的队列，如果不存在则自动创建"""
        if queue_name not in self._queues:
            self._queues[queue_name] = asyncio.Queue()
            logger.debug(f"队列 [{queue_name}] 已创建。")
        return self._queues[queue_name]

    async def put(self, queue_name: str, item: Any):
        """向指定队列放入消息（异步安全）"""
        queue = self.get_queue(queue_name)
        await queue.put(item)

    async def get(self, queue_name: str):
        """从指定队列获取消息（异步安全）"""
        queue = self.get_queue(queue_name)
        return await queue.get()
