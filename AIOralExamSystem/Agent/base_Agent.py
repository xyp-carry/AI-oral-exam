from AIOralExamSystem.utils.base_object import BaseObject
from langchain_openai import ChatOpenAI

from loguru import logger

from langchain_core.messages import AIMessage, BaseMessage
from langchain_core.tools import BaseTool
from langchain_core.agents import AgentAction, AgentFinish
from typing import Awaitable, Callable, List
import inspect
from abc import abstractmethod
from langchain.agents import create_agent
from langchain_core.callbacks.base import BaseCallbackHandler
from langchain.agents.middleware import (
    AgentMiddleware,
    ModelCallLimitMiddleware,
    ModelRequest,
    ModelResponse,
)
import asyncio
import json
import math
from AIOralExamSystem.Agent.base_prompt import BasePrompt



class FinalizationBudgetMiddleware(AgentMiddleware):
    """Tell an agent to finish required work as its model-call budget runs low."""

    def __init__(
        self,
        agent_name: str,
        max_iterations: int,
        warning_ratio: float = 0.8,
        rewrite_tool_name: str | None = None,
    ):
        super().__init__()
        self.agent_name = agent_name
        self.max_iterations = max(1, int(max_iterations))
        self.warning_ratio = float(warning_ratio)
        if not 0 < self.warning_ratio <= 1:
            raise ValueError("finish_warning_ratio must be in the range (0, 1]")
        self.warning_at = max(1, math.ceil(self.max_iterations * self.warning_ratio))
        self.rewrite_tool_name = rewrite_tool_name

    def wrap_model_call(
        self,
        request: ModelRequest,
        handler: Callable[[ModelRequest], ModelResponse],
    ) -> ModelResponse:
        return handler(self._prepare_request(request))

    async def awrap_model_call(
        self,
        request: ModelRequest,
        handler: Callable[[ModelRequest], Awaitable[ModelResponse]],
    ) -> ModelResponse:
        return await handler(self._prepare_request(request))

    def _prepare_request(self, request: ModelRequest) -> ModelRequest:
        completed = int(request.state.get("run_model_call_count", 0) or 0)
        current = completed + 1
        if current < self.warning_at:
            return request

        instruction = self._build_instruction(current)
        return request.override(
            system_message=self._append_instruction(request.system_message, instruction)
        )

    def _build_instruction(self, current: int) -> str:
        remaining = max(0, self.max_iterations - current)
        if self.rewrite_tool_name:
            required_action = (
                f"停止非必要的搜索和扩展分析，立即整理已有证据并尽快调用 "
                f"{self.rewrite_tool_name} 实际修改当前模板。"
                '调用时只传 replacements，标准格式为 '
                '{"replacements":[{"old_text":"原文","new_text":"新内容"}]}；'
                "字段名必须使用下划线且所有条目结构一致。"
                "出现 tool call failed 或 ValidationError 时必须修正参数并立即重试。"
                "不得只给出修改建议或下一步计划；找不到的内容按模板规则填写“未找到”。"
                "只有写入工具明确返回成功后，才能读取并检查最终文档，然后结束任务。"
            )
        else:
            required_action = (
                "停止非必要的搜索和扩展分析，使用已有信息完成尚未完成的必要行为，"
                "并尽快给出完整的最终结果。不得只描述下一步计划。"
            )

        if current >= self.max_iterations:
            return (
                f"这是本次运行允许的最后一次模型调用（{current}/{self.max_iterations}）。"
                f"{required_action}"
            )

        return (
            f"本次运行的模型调用预算已达到 {self.warning_ratio:.0%}："
            f"当前第 {current}/{self.max_iterations} 次，之后最多还剩 {remaining} 次。"
            f"{required_action}不要把模板修改或其他必要收尾行为留到迭代上限之后。"
        )

    def _append_instruction(
        self,
        system_message: BaseMessage | None,
        instruction: str,
    ) -> BaseMessage:
        addition = f"\n\n## 当前迭代预算要求\n{instruction}"
        if system_message is None:
            from langchain_core.messages import SystemMessage

            return SystemMessage(content=addition.lstrip())

        content = getattr(system_message, "content", "")
        if isinstance(content, str):
            updated_content = content.rstrip() + addition
        elif isinstance(content, list):
            updated_content = [*content, {"type": "text", "text": addition.lstrip()}]
        else:
            updated_content = f"{content}{addition}"

        if hasattr(system_message, "model_copy"):
            return system_message.model_copy(update={"content": updated_content})
        return system_message.copy(update={"content": updated_content})




class ToolPrintHandler(BaseCallbackHandler):
    """打印工具调用的名称、输入参数和返回结果。"""

    def on_tool_start(self, serialized, input_str, **kwargs):
        serialized = serialized or {}
        print(f"\n>>> 正在调用工具: {serialized.get('name', '')}")
        print(f">>> 输入参数: {input_str}")

    def on_tool_end(self, output, **kwargs):
        print(f">>> 工具返回结果: {output}")
        print(">>> 工具调用结束\n")

class ToolNameHandler(BaseCallbackHandler):
    """Report only the name of each tool when its execution starts."""

    def __init__(self, callback: Callable[[str], None]):
        self.callback = callback

    def on_tool_start(self, serialized, input_str, **kwargs):
        name = str((serialized or {}).get("name") or kwargs.get("name") or "").strip()
        if name:
            self.callback(name)


class ModelRoundProgressMiddleware(AgentMiddleware):
    """Report main Agent model calls without counting tool-internal models."""

    def __init__(self, callback: Callable[[str], None]):
        super().__init__()
        self.callback = callback

    def wrap_model_call(
        self,
        request: ModelRequest,
        handler: Callable[[ModelRequest], ModelResponse],
    ) -> ModelResponse:
        self.callback("started")
        response = handler(request)
        self.callback("completed")
        return response

    async def awrap_model_call(
        self,
        request: ModelRequest,
        handler: Callable[[ModelRequest], Awaitable[ModelResponse]],
    ) -> ModelResponse:
        self.callback("started")
        response = await handler(request)
        self.callback("completed")
        return response


class BaseAgent(BaseObject):
    def __init__(self, name: str, model_settings: dict, thinking: bool = False, response_format: bool = False, temperature: float = 0.0, top_p = 1, show_tool_io: bool | None = None, tool_event_callback: Callable[[str], None] | None = None, model_round_callback: Callable[[str], None] | None = None):
        super().__init__()
        self._name = name
        self._model_round_callback = model_round_callback
        self.prompt_builder = getattr(self, "prompt_builder", None) or BasePrompt()
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
        callbacks = []
        if show_tool_io is True:
            callbacks.append(ToolPrintHandler())
        if tool_event_callback is not None:
            callbacks.append(ToolNameHandler(tool_event_callback))
        if callbacks:
            self.agent = self.agent.with_config({
                "callbacks": callbacks,
            })
        logger.info(f"agent {self._name} init success")

    

    async def run(self, **kwargs):
        await self.start_heartbeat()
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
            logger.error(f"agent {self._name} run failed")
            return self._build_error_response(exc)

        finally:
            if not cleanup_in_wrapper:
                await self.stop_heartbeat()
        
    async def _wrap_async_generator(self, agen):
        try:
            try:
                async for chunk in agen:
                    yield chunk
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.error(f"agent {self._name} stream failed")
                yield self._build_error_stream_chunk(exc)

        finally:
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
        max_agent_model_calls = max(
            1,
            int(model_settings.get("max_agent_model_calls", 40) or 40),
        )
        finish_warning_ratio = float(
            model_settings.get("finish_warning_ratio", 0.8) or 0.8
        )
        configured_rewrite_tool = str(
            model_settings.get("finalization_tool_name", "rewriteDocument") or ""
        ).strip()
        registered_tool_names = {
            str(getattr(tool, "name", "") or "") for tool in self.tools
        }
        rewrite_tool_name = (
            configured_rewrite_tool
            if configured_rewrite_tool in registered_tool_names
            else None
        )
        middlewares = self.prompt_builder.build_compression_middlewares(
            agent_name=self._name,
            model=self.model,
            model_settings=model_settings,
        )
        middlewares.extend((
            ModelCallLimitMiddleware(
                run_limit=max_agent_model_calls,
                exit_behavior="end",
            ),
            FinalizationBudgetMiddleware(
                agent_name=self._name,
                max_iterations=max_agent_model_calls,
                warning_ratio=finish_warning_ratio,
                rewrite_tool_name=rewrite_tool_name,
            ),
        ))
        if self._model_round_callback is not None:
            middlewares.append(ModelRoundProgressMiddleware(self._model_round_callback))
        return middlewares

    def build_prompt_messages(self, task: str) -> list[dict[str, str]]:
        return self.prompt_builder.build_messages(task)

    def update_prompt_context(
        self,
        section: str,
        values: dict,
        *,
        replace: bool = False,
    ) -> None:
        self.prompt_builder.update_context(section, values, replace=replace)

    def get_tools(self):
        return []
    
    def get_response_format(self):
        return None
