from AIOralExamSystem.utils.base_object import BaseObject
from langchain_openai import ChatOpenAI

from loguru import logger

from langchain_core.messages import AIMessage
from langchain_core.tools import BaseTool

from typing import List
import inspect
from abc import abstractmethod
from langchain.agents import create_agent
from AIOralExamSystem.utils.monitor import GlobalMonitor
import asyncio
import json



class BaseAgent(BaseObject):
    def __init__(self, name: str, model_settings: dict, thinking: bool = False, response_format: bool = False, temperature: float = 0.0):
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
        model_params = {}
        if not thinking:
            model_params["thinking"] = {"type": "disabled"}
        if response_format:
            model_params["response_format"] = {"type": "json_object"}
        model_params["temperature"] = temperature
        self.model = self.init_model(model_name, url, api_key, model_params)
    
    #     self.model = self.init_model(model_name, url, api_key, {"response_format": {
    # "type": "json_object"}, "temperature": 0})
        if self.get_response_format():
            self.model.with_structured_output(self.get_response_format())
        logger.info(f"model {model_name} init success")
        self.agent = create_agent(
            self.model,
            tools=self.tools
            # response_format=self.get_response_format(),
        )
        logger.info(f"agent {self._name} init success")
        self.queue = asyncio.Queue()

        # 加载全局监测
        self.global_monitor = GlobalMonitor()

    
    async def run(self, **kwargs):
        await self.start_heartbeat()
        self.event_signal = asyncio.Event()

        await self.global_monitor._queue.put(
            "reqObj",
            (
                {"id": self.id, "name": self._name},
                self.rule,
                self.event_signal,
                self.queue,
                "start",
            ),
        )

        await self.event_signal.wait()

        cleanup_in_wrapper = False
        try:
            ret = self.execute(**kwargs)

            if inspect.isasyncgen(ret):
                cleanup_in_wrapper = True
                return self._wrap_async_generator(ret)

            if inspect.isawaitable(ret):
                res = await ret
            else:
                res = ret
            return res

        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.exception(f"agent {self._name} run failed")
            return self._build_error_response(exc)

        finally:
            if not cleanup_in_wrapper:
                await self.global_monitor._queue.put(
                    "reqObj",
                    (
                        {"id": self.id, "name": self._name},
                        self.rule,
                        self.event_signal,
                        self.queue,
                        "stop",
                    ),
                )
                await self.stop_heartbeat()
        
    async def _wrap_async_generator(self, agen):
        try:
            try:
                async for chunk in agen:
                    yield chunk
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.exception(f"agent {self._name} stream failed")
                yield self._build_error_stream_chunk(exc)

        finally:
            await self.global_monitor._queue.put(
                "reqObj",
                (
                    {"id": self.id, "name": self._name},
                    self.rule,
                    self.event_signal,
                    self.queue,
                    "stop",
                ),
            )
            await self.stop_heartbeat()

    def _build_error_response(self, exc: Exception) -> dict:
        payload = self._build_error_payload(exc)
        return {
            "messages": [AIMessage(content=json.dumps(payload, ensure_ascii=False))],
            "agent_error": payload,
        }

    def _build_error_stream_chunk(self, exc: Exception) -> dict:
        payload = self._build_error_payload(exc)
        return {
            "model": {
                "messages": [AIMessage(content=json.dumps(payload, ensure_ascii=False))],
            },
            "agent_error": payload,
        }

    def _build_error_payload(self, exc: Exception) -> dict:
        return {
            "ok": False,
            "agent": self._name,
            "error_type": self._classify_exception(exc),
            "error_class": exc.__class__.__name__,
            "error_message": str(exc),
        }

    def _classify_exception(self, exc: Exception) -> str:
        if isinstance(exc, (asyncio.TimeoutError, TimeoutError)):
            return "timeout"

        class_name = exc.__class__.__name__.lower()
        module_name = exc.__class__.__module__.lower()
        text = f"{module_name}.{class_name}"

        if "rate" in text and "limit" in text:
            return "rate_limit"
        if any(keyword in text for keyword in ("auth", "permission", "unauthorized", "forbidden")):
            return "auth_error"
        if any(keyword in text for keyword in ("connection", "network", "http", "api", "openai", "request")):
            return "model_request_error"
        if "json" in text or "parse" in text or "validation" in text:
            return "response_parse_error"
        return "unexpected_error"

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
    
    async def rule(self, obj_id: str, active_nodes: dict) -> bool:
        if obj_id in active_nodes:
            return False
        if len(active_nodes) > 3:
            return False
        return True
        
    def get_tools(self):
        return []
    
    def get_response_format(self):
        return None
