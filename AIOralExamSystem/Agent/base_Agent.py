from AIOralExamSystem.utils.base_object import BaseObject
from langchain_openai import ChatOpenAI

from loguru import logger

from langchain_core.messages import AIMessage, BaseMessage, ToolMessage
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
    SummarizationMiddleware,
)
from AIOralExamSystem.utils.monitor import GlobalMonitor
import asyncio
import json
import math



class TokenBudgetMiddleware(AgentMiddleware):
    """Prune old tool results before model input exceeds the configured budget."""

    def __init__(
        self,
        agent_name: str,
        model,
        max_input_tokens: int = 12000,
        token_counter: Callable | None = None,
    ):
        super().__init__()
        self.agent_name = agent_name
        self.model = model
        self.max_input_tokens = max(1, int(max_input_tokens or 12000))
        self.token_counter = token_counter

    def wrap_model_call(
        self,
        request: ModelRequest,
        handler: Callable[[ModelRequest], ModelResponse],
    ) -> ModelResponse:
        request = self._prune_request_if_needed(request)
        return handler(request)

    async def awrap_model_call(
        self,
        request: ModelRequest,
        handler: Callable[[ModelRequest], Awaitable[ModelResponse]],
    ) -> ModelResponse:
        request = self._prune_request_if_needed(request)
        return await handler(request)

    def _prune_request_if_needed(self, request: ModelRequest) -> ModelRequest:
        input_tokens = self._count_request_tokens(request)
        if input_tokens <= self.max_input_tokens:
            logger.info(
                f"[{self.agent_name}] next_model_input_tokens={input_tokens}, "
                f"limit={self.max_input_tokens}"
            )
            return request

        pruned_messages = self._keep_latest_tool_result(request.messages)
        pruned_request = request.override(messages=pruned_messages)
        pruned_tokens = self._count_request_tokens(pruned_request)
        logger.warning(
            f"[{self.agent_name}] model input tokens exceeded limit; "
            f"before={input_tokens}, after_prune={pruned_tokens}, "
            f"limit={self.max_input_tokens}"
        )

        if pruned_tokens > self.max_input_tokens:
            raise ValueError(
                f"Agent input tokens still exceed limit after pruning old tool results: "
                f"{pruned_tokens} > {self.max_input_tokens}"
            )

        return pruned_request

    def _count_request_tokens(self, request: ModelRequest) -> int:
        messages = self._request_messages(request)
        tools = getattr(request, "tools", None)

        if self.token_counter is not None:
            counted = self._call_token_counter(messages)
            if counted is not None:
                return counted + self._estimate_tools_tokens(tools)

        model = getattr(request, "model", None) or self.model
        if hasattr(model, "get_num_tokens_from_messages"):
            try:
                return int(model.get_num_tokens_from_messages(messages, tools=tools))
            except TypeError:
                try:
                    return int(model.get_num_tokens_from_messages(messages)) + self._estimate_tools_tokens(tools)
                except Exception:
                    pass
            except Exception:
                pass

        return self._estimate_messages_tokens(messages) + self._estimate_tools_tokens(tools)

    def _request_messages(self, request: ModelRequest) -> list[BaseMessage]:
        messages = list(getattr(request, "messages", None) or [])
        system_message = getattr(request, "system_message", None)
        if system_message is not None and system_message not in messages:
            return [system_message, *messages]
        return messages

    def _call_token_counter(self, messages: list[BaseMessage]) -> int | None:
        try:
            return int(self.token_counter(messages))
        except TypeError:
            return None
        except Exception:
            return None

    def _keep_latest_tool_result(self, messages: list[BaseMessage]) -> list[BaseMessage]:
        latest_tool_index = None
        latest_tool_call_id = None
        for index, message in enumerate(messages):
            if isinstance(message, ToolMessage):
                latest_tool_index = index
                latest_tool_call_id = getattr(message, "tool_call_id", None)

        if latest_tool_index is None or not latest_tool_call_id:
            return messages

        pruned_messages: list[BaseMessage] = []
        for index, message in enumerate(messages):
            if isinstance(message, ToolMessage):
                if index == latest_tool_index:
                    pruned_messages.append(message)
                continue

            if isinstance(message, AIMessage) and getattr(message, "tool_calls", None):
                cleaned_message = self._clean_ai_tool_calls(message, latest_tool_call_id)
                if cleaned_message is not None:
                    pruned_messages.append(cleaned_message)
                continue

            pruned_messages.append(message)

        return pruned_messages

    def _clean_ai_tool_calls(self, message: AIMessage, keep_tool_call_id: str | None) -> AIMessage | None:
        keep_ids = {keep_tool_call_id} if keep_tool_call_id else set()
        tool_calls = [
            tool_call
            for tool_call in getattr(message, "tool_calls", None) or []
            if tool_call.get("id") in keep_ids
        ]

        if tool_calls:
            return self._copy_message(
                message,
                {
                    "tool_calls": tool_calls,
                    "invalid_tool_calls": self._filter_tool_calls(
                        getattr(message, "invalid_tool_calls", None) or [],
                        keep_ids,
                    ),
                    "additional_kwargs": self._filter_additional_kwargs(message, keep_ids),
                },
            )

        if self._has_content(message.content):
            return self._copy_message(
                message,
                {
                    "tool_calls": [],
                    "invalid_tool_calls": [],
                    "additional_kwargs": self._filter_additional_kwargs(message, set()),
                },
            )

        return None

    def _copy_message(self, message: BaseMessage, updates: dict) -> BaseMessage:
        if hasattr(message, "model_copy"):
            return message.model_copy(update=updates)
        return message.copy(update=updates)

    def _filter_tool_calls(self, tool_calls: list, keep_ids: set[str]) -> list:
        return [tool_call for tool_call in tool_calls if tool_call.get("id") in keep_ids]

    def _filter_additional_kwargs(self, message: AIMessage, keep_ids: set[str]) -> dict:
        additional_kwargs = dict(getattr(message, "additional_kwargs", None) or {})
        raw_tool_calls = additional_kwargs.get("tool_calls")
        if isinstance(raw_tool_calls, list):
            filtered = [tool_call for tool_call in raw_tool_calls if tool_call.get("id") in keep_ids]
            if filtered:
                additional_kwargs["tool_calls"] = filtered
            else:
                additional_kwargs.pop("tool_calls", None)
        return additional_kwargs

    def _has_content(self, content) -> bool:
        if isinstance(content, str):
            return bool(content.strip())
        if isinstance(content, list):
            return bool(content)
        return content is not None

    def _estimate_messages_tokens(self, messages: list[BaseMessage]) -> int:
        total = 0
        for message in messages:
            total += self._estimate_text_tokens(getattr(message, "type", "message"))
            total += self._estimate_text_tokens(str(getattr(message, "content", "")))
            if isinstance(message, AIMessage):
                total += self._estimate_text_tokens(json.dumps(getattr(message, "tool_calls", []) or [], ensure_ascii=False))
            if isinstance(message, ToolMessage):
                total += self._estimate_text_tokens(str(getattr(message, "tool_call_id", "")))
            total += 4
        return total

    def _estimate_tools_tokens(self, tools) -> int:
        if not tools:
            return 0
        payloads = []
        for tool in tools:
            payloads.append(
                {
                    "name": getattr(tool, "name", ""),
                    "description": getattr(tool, "description", ""),
                    "args": getattr(tool, "args", None),
                }
            )
        return self._estimate_text_tokens(json.dumps(payloads, ensure_ascii=False, default=str))

    def _estimate_text_tokens(self, text: str) -> int:
        if not text:
            return 0
        cjk_chars = sum(1 for char in text if "\u4e00" <= char <= "\u9fff")
        visible_chars = sum(1 for char in text if not char.isspace())
        non_cjk_chars = max(0, visible_chars - cjk_chars)
        return cjk_chars + math.ceil(non_cjk_chars / 2)


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


class BaseAgent(BaseObject):
    def __init__(self, name: str, model_settings: dict, thinking: bool = False, response_format: bool = False, temperature: float = 0.0, top_p = 1, show_tool_io: bool | None = None, tool_event_callback: Callable[[str], None] | None = None):
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
            logger.error(f"agent {self._name} run failed")
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
                logger.error(f"agent {self._name} stream failed")
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
        middlewares = [
            TokenBudgetMiddleware(
                agent_name=self._name,
                model=self.model,
                max_input_tokens=model_settings.get("max_input_tokens", 12000),
                token_counter=summarization_config.get("token_counter"),
            ),
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
        ]

        if not summarization_config.get("enabled", False):
            return middlewares

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

        middlewares.append(SummarizationMiddleware(**middleware_kwargs))
        return middlewares
    
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
