import asyncio
from enum import Enum
from typing import Dict, Set
from loguru import logger
from AIOralExamSystem.message.base_queue import QueueManager
import queue
import os

class NodeType(Enum):
    AGENT = "Agent"
    TOOL = "Tool"

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
        # 存储正在运行中的节点: { id: NodeType }
        self._active_nodes: Dict[str, Dict[str, NodeType]] = {"agent": {}, "tool": {}}
        self._is_running = False
        # 存储等待许可的节点事件: { id: asyncio.Event }
        # Event初始为False，当被set为True时，表示允许运行
        self._pending_events: Dict[str, queue.Queue] = {"agent": queue.Queue(), "tool": queue.Queue()}

    async def start(self) -> bool:
        """
        接收启动申请。这是一个异步方法，调用方会被 await 阻塞，
        直到监测者放行（或者未来你可以在这里加入超时拒绝逻辑）。
        """
        if not self._is_running:
            self._is_running = True
            logger.info("监测者已启动。")
            asyncio.create_task(self._monitor_loop())

        return True

    async def _monitor_loop(self):
        while self._is_running:
            obj_id, identifier, event_signal, obj_queue, signal_type = await self._queue.get("reqObj")
            if signal_type == "start":
                self.start_obj(obj_id, identifier, event_signal, obj_queue)
                logger.info(f"对象{identifier} id {obj_id} 已启动。")
            elif signal_type == "stop":
                self.stop_obj(obj_id, identifier, event_signal)
                logger.info(f"对象 {obj_id} 已停止。")
                if not self._pending_events[identifier].empty():
                    obj_id, identifier, event_signal, obj_queue = self._pending_events[identifier].get("reqObj")
                    self.start_obj(obj_id, identifier, event_signal, obj_queue)
    
    async def stop_monitor(self):
        """停止监测者"""
        self._is_running = False
        logger.info("全局监测者已发出停止信号。")

    def _evaluate_rule(self, obj_id: str, identifier: str):
        """
        内部方法：模拟规则判断。
        你可以在这里写：如果当前 active_nodes < MAX_CONN，则放行，否则排队。
        ****(x)当前是随便定义的后续可以根据实际情况调整类型。
        """
        # 规则判断通过，触发 Event，唤醒在 request_start 中等待的协程
        # if node_id in self._pending_events[identifier]:
        #     self._pending_events[identifier][node_id].set()
        if len(self._active_nodes[identifier]) < 2 and obj_id not in self._active_nodes[identifier]:
            return True
        return False

    def start_obj(self, obj_id: str, identifier: str, event_signal: asyncio.Event, obj_queue: asyncio.Queue):
        """
        启动对象。如果对象已存在，直接返回。
        保留了一个对象队列，这个队列可以用于后续的通信，暂时保留这个口子。
        对于是否可以判断优先级，后续如果需要会增加一个高等级队列来进行判断。
        """
        if self._active_nodes[identifier].get(obj_id, None) is None:
            if self._evaluate_rule(obj_id, identifier):
                if identifier == 'tool':
                    print("工具走的是打开")
                self._active_nodes[identifier][obj_id] = event_signal
                event_signal.set()
            else:
                print("工具走的是排队")
                self._pending_events[identifier].put((obj_id, identifier, event_signal, obj_queue))

    def stop_obj(self, obj_id: str, identifier: str,  event_signal: asyncio.Event):
        """
        停止对象。如果对象不存在，直接返回。
        """
        if obj_id in self._active_nodes[identifier]:
            event_signal.set()
            self._active_nodes[identifier].pop(obj_id) # 队列中实际对象的删除由对象自己控制，这里只是删掉索引
            logger.info(f"[{obj_id}] 已从内存移除。当前运行数: {len(self._active_nodes[identifier])}")
        else:
            logger.warning(f"[{obj_id}] 结束通知失败：未在运行内存中找到该ID。")

    def notify_stop(self, node_id: str):
        """
        接收结束通知。这是一个同步方法，因为释放资源不需要异步等待。
        """
        if node_id in self._active_nodes:
            node_type = self._active_nodes.pop(node_id)
            logger.info(f"[{node_id}] ({node_type.value}) 运行结束，已从内存移除。当前运行数: {len(self._active_nodes)}")
            
            # --- 这里是你预留的唤醒排队节点的位置 ---
            # 当有连接释放时，可以在这里检查 _pending_events，放行下一个排队的节点
            # --------------------------------------
        else:
            logger.warning(f"[{node_id}] 结束通知失败：未在运行内存中找到该ID。")

    def get_status(self) -> dict:
        """获取当前监测者状态（可用于调试）"""
        return {
            "active_count": len(self._active_nodes),
            "pending_count": len(self._pending_events),
            "active_nodes": {k: v.value for k, v in self._active_nodes.items()}
        }