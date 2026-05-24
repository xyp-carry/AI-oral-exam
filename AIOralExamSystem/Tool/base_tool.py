import asyncio
from typing import Any, Optional
from AIOralExamSystem.utils.base_object import BaseObject
from asyncio import iscoroutinefunction
from abc import abstractmethod
from AIOralExamSystem.utils.monitor import GlobalMonitor
from pydantic import Field

class BaseTool(BaseObject):
    """基础工具类"""
    def __init__(self, name: str):
        super().__init__()
        self._name = name
        self._approval_result: Optional[bool] = None
        self._error_msg: str = ""
        self._monitor = GlobalMonitor()
        self.queue = asyncio.Queue()

    async def request_approval(self):
        self.event_signal = asyncio.Event()
        await self._monitor._queue.put("reqObj",({"id": self.id, "name": self.name}, self.rule, self.event_signal, self.queue, "start"))
        await self.event_signal.wait()

    async def execute(self, *args, **kwargs) -> Any:
        await self.start_heartbeat()
        # 1. 审批等待逻辑
        await self.request_approval()

        print(f"[{self.name}] 审批通过，开始派发任务...")

        # 获取子类实际重写的方法
        run_method = self._run

        # 2. 核心路由：判断子类的 _run 是异步还是同步
        if iscoroutinefunction(run_method):
            # 【异步监测模式】：判定为非密集型 (如调接口、查库)，直接协程挂起等待
            print(f"[{self.name}] -> 启用 异步IO监测模式")
            
            res = await run_method(*args, **kwargs)
            await self.stop_heartbeat()
            await self._monitor._queue.put("reqObj",({"id": self.id, "name": self.name}, self.rule, self.event_signal, self.queue, "stop"))
            return res
        else:
            # 【多线程监测模式】：判定为密集型 (如复杂计算)，扔进线程池保护事件循环
            print(f"[{self.name}] -> 启用 多线程CPU监测模式")
            res = await asyncio.to_thread(run_method, *args, **kwargs)
            await self.stop_heartbeat()
            await self._monitor._queue.put("reqObj",({"id": self.id, "name": self.name}, self.rule, self.event_signal, self.queue, "stop"))
            return res
        


    @abstractmethod
    def _run(self, *args, **kwargs) -> Any:
        """
        基类的默认实现。
        子类既可以重写为 async def _run (走异步模式)
        也可以重写为 def _run (走多线程模式)
        """
        raise NotImplementedError(f"工具 [{self.name}] 必须实现 _run 方法")

    async def rule(self, obj_id: str, active_nodes: dict) -> bool:
        if obj_id in active_nodes:
            return False
        if len(active_nodes) > 3:
            return False
        return True
