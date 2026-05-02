from AIOralExamSystem.utils.base_object import BaseObject
from langchain_openai import ChatOpenAI

from loguru import logger

from langchain_core.tools import BaseTool

from typing import List
import inspect
from abc import abstractmethod
from langchain.agents import create_agent
from AIOralExamSystem.utils.monitor import GlobalMonitor
import asyncio



class BaseAgent(BaseObject):
    def __init__(self, name: str, model_settings: dict):
        super().__init__()
        self._name = name
        self.tools: List[BaseTool] = self.get_tools()
        if self.tools:
            tool_names = [t.name for t in self.tools]
            print(f"[{self.__class__.__name__}] 自动注册了 {len(self.tools)} 个工具: {tool_names}")
        if model_settings.get("model_name"):
            model_name = model_settings["model_name"]
        else:
            raise ValueError("model_name is required")
        
        if model_settings.get("model_url"):
            url = model_settings["model_url"]
        else:
            raise ValueError("model_url is required")
        
        if model_settings.get("model_api_key"):
            api_key = model_settings["model_api_key"]
        else:
            raise ValueError("model_api_key is required")
        
        self.model = self.init_model(model_name, url, api_key, {"thinking":{"type": "disabled"}})
        logger.info(f"model {model_name} init success")
        self.agent = create_agent(
            self.model,
            tools=self.tools
        )
        logger.info(f"agent {self._name} init success")
        self.queue = asyncio.Queue()

        # 加载全局监测
        self.global_monitor = GlobalMonitor()

    
    async def run(self, **kwargs):
        await self.start_heartbeat()
        self.event_signal = asyncio.Event()
        # 请求Monitor许可
        await self.global_monitor._queue.put("reqObj",(self.id, "agent", self.event_signal, self.queue, "start"))
        await self.event_signal.wait()
        # 执行任务
        await self.execute(**kwargs)
        # 告知Monitor已完成
        await self.global_monitor._queue.put("reqObj",(self.id, "agent", self.event_signal, self.queue, "stop"))
        # await self.event_signal.wait()
        await self.stop_heartbeat()

    @abstractmethod
    async def execute(self, **kwargs) -> str:
        pass

    def init_model(self, model_name: str, url: str, api_key: str, extra_body: dict):
        return ChatOpenAI(
            openai_api_base=url,
            openai_api_key=api_key,
            model=model_name,
            extra_body= extra_body
        )
        
