from AIOralExamSystem.utils.base_object import BaseObject
from langchain_openai import ChatOpenAI

from loguru import logger

from langchain_core.messages import AIMessage
from langchain_core.tools import BaseTool
from langchain_core.agents import AgentAction, AgentFinish
from typing import List
import inspect
from abc import abstractmethod
from langchain.agents import create_agent
from langchain_core.callbacks.base import BaseCallbackHandler
from langchain.agents.middleware import SummarizationMiddleware
from AIOralExamSystem.utils.monitor import GlobalMonitor
import asyncio
import json




class ToolPrintHandler(BaseCallbackHandler):
    """打印工具调用的名称、输入参数和返回结果。"""

    def on_tool_start(self, serialized, input_str, **kwargs):
        serialized = serialized or {}
        print(f"\n>>> 正在调用工具: {serialized.get('name', '')}")
        print(f">>> 输入参数: {input_str}")

    def on_tool_end(self, output, **kwargs):
        print(f">>> 工具返回结果: {output}")
        print(">>> 工具调用结束\n")

class BaseAgent(BaseObject):
    def __init__(self, name: str, model_settings: dict, thinking: bool = False, response_format: bool = False, temperature: float = 0.0, top_p = 1, show_tool_io: bool | None = None):
        super().__init__()
        self._name = name
        self.tools: List[BaseTool] = self.get_tools()
        if self.tools:
            tool_names = [t.name for t in self.tools]
            logger.info(f"[{self.__class__.__name__}] registered {len(self.tools)} tools: {tool_names}")
        else:
            logger.warning(f"[{self.__class__.__name__}] registered no tools")
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
        
        self.model = self.init_model(model_name, url, api_key, temperature, top_p, model_params)
    
        if self.get_response_format():
            self.model.with_structured_output(self.get_response_format())
        logger.info(f"model {model_name} init success")
        self.agent = create_agent(
            self.model,
            tools=self.tools,
            middleware=self._build_middlewares(model_settings),
            # response_format=self.get_response_format(),
        )
        if show_tool_io is True:
            self.agent = self.agent.with_config({
                "callbacks": [ToolPrintHandler()],
            })
        logger.info(f"agent {self._name} init success")
        self.queue = asyncio.Queue()

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

    def init_model(self, model_name: str, url: str, api_key: str, temperature: float, top_p: float, extra_body: dict):
        return ChatOpenAI(
            openai_api_base=url,
            openai_api_key=api_key,
            model=model_name,
            temperature=temperature,
            top_p=top_p,
            extra_body= extra_body
        )
    
    def _build_middlewares(self, model_settings: dict):
        summarization_config = model_settings.get("summarization") or {}
        if not summarization_config.get("enabled", False):
            return []

        middleware_kwargs = {
            "model": summarization_config.get("model", self.model),
            "trigger": summarization_config.get("trigger", ("tokens", 8000)),
            "keep": summarization_config.get("keep", ("messages", 8)),
        }

        if summarization_config.get("trim_tokens_to_summarize") is not None:
            middleware_kwargs["trim_tokens_to_summarize"] = summarization_config["trim_tokens_to_summarize"]
        if summarization_config.get("summary_prompt") is not None:
            middleware_kwargs["summary_prompt"] = summarization_config["summary_prompt"]
        if summarization_config.get("token_counter") is not None:
            middleware_kwargs["token_counter"] = summarization_config["token_counter"]

        return [SummarizationMiddleware(**middleware_kwargs)]
    
    async def rule(self, obj_id: str, active_nodes: dict) -> bool:
        if obj_id in active_nodes:
            logger.info(f"obj_id {obj_id} is in active_nodes")
            return False
            logger.info(f"active_nodes={active_nodes}")
            return False
        return True
        
    def get_tools(self):
        return []
    
    def get_response_format(self):
        return None
