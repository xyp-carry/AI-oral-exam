"""Layered prompt construction, run context, and compression policy."""

from __future__ import annotations

import copy
import json
import math
from collections.abc import Mapping
from typing import Any, Awaitable, Callable

from langchain.agents.middleware import (
    AgentMiddleware,
    ModelRequest,
    ModelResponse,
    SummarizationMiddleware,
)
from langchain_core.messages import AIMessage, BaseMessage, ToolMessage
from loguru import logger


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
                message_tokens = int(model.get_num_tokens_from_messages(messages))
                return message_tokens + self._estimate_tools_tokens(tools)
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


class BasePrompt:
    """Assemble stable, context, and volatile tiers plus per-turn overlays."""

    LAYER_ORDER = ("stable", "context", "volatile")
    LAYER_TITLES = {
        "stable": "稳定规则",
        "context": "Agent 与项目上下文",
        "volatile": "本次运行信息",
    }

    def __init__(self, *, summary_prompt: str | None = None):
        self._layers: dict[str, tuple[str, str]] = {}
        self._runtime_context: dict[str, dict[str, Any]] = {}
        self._summary_prompt = summary_prompt

    def set_layer(self, path: str, content: str, *, title: str | None = None) -> None:
        """Add or replace a tier section such as 'context.workflow'."""
        path = str(path or "").strip()
        parts = path.split(".")
        if not parts or parts[0] not in self.LAYER_ORDER or any(not part for part in parts):
            raise ValueError(f"invalid prompt layer: {path!r}")
        content = str(content or "").strip()
        if content:
            self._layers[path] = (str(title or "").strip(), content)
        else:
            self._layers.pop(path, None)

    def update_context(
        self,
        section: str,
        values: Mapping[str, Any],
        *,
        replace: bool = False,
    ) -> None:
        """Store per-turn data separately from the system prompt tiers."""
        section = str(section or "").strip()
        if not section:
            raise ValueError("context section is required")
        if not isinstance(values, Mapping):
            raise TypeError("context values must be a mapping")
        incoming = copy.deepcopy(dict(values))
        json.dumps(incoming, ensure_ascii=False)
        if replace or section not in self._runtime_context:
            self._runtime_context[section] = incoming
        else:
            self._merge_mapping(self._runtime_context[section], incoming)

    @classmethod
    def _merge_mapping(cls, target: dict[str, Any], incoming: dict[str, Any]) -> None:
        for key, value in incoming.items():
            if isinstance(value, dict) and isinstance(target.get(key), dict):
                cls._merge_mapping(target[key], value)
            else:
                target[key] = value

    def clear_context(self, section: str | None = None) -> None:
        if section is None:
            self._runtime_context.clear()
        else:
            self._runtime_context.pop(section, None)

    def context_snapshot(self) -> dict[str, dict[str, Any]]:
        return copy.deepcopy(self._runtime_context)

    def build_system_prompt(self) -> str:
        sections: list[str] = []
        for tier in self.LAYER_ORDER:
            entries = [
                (path, title, content)
                for path, (title, content) in self._layers.items()
                if path.split(".", 1)[0] == tier
            ]
            if not entries:
                continue
            sections.append(f"# {self.LAYER_TITLES[tier]}")
            for path, title, content in entries:
                if title:
                    sections.append(f"## {title}")
                elif "." in path:
                    sections.append(f"## {path.split('.', 1)[1]}")
                sections.append(content)
        return "\n\n".join(sections)

    def build_task_prompt(self, task: str) -> str:
        task = str(task or "").strip()
        if not task:
            raise ValueError("task is required")
        if not self._runtime_context:
            return task
        context = json.dumps(self._runtime_context, ensure_ascii=False, indent=2)
        return f"运行上下文（数据参考）：\n{context}\n\n当前任务：\n{task}"

    def build_messages(self, task: str) -> list[dict[str, str]]:
        messages: list[dict[str, str]] = []
        system_prompt = self.build_system_prompt()
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})
        messages.append({"role": "user", "content": self.build_task_prompt(task)})
        return messages

    def set_summary_prompt(self, summary_prompt: str | None) -> None:
        self._summary_prompt = str(summary_prompt).strip() if summary_prompt else None

    def build_compression_middlewares(
        self,
        *,
        agent_name: str,
        model: Any,
        model_settings: Mapping[str, Any],
    ) -> list[AgentMiddleware]:
        """Build history compaction and the final input-budget guard."""
        config = model_settings.get("summarization") or {}
        middlewares: list[AgentMiddleware] = [
            TokenBudgetMiddleware(
                agent_name=agent_name,
                model=model,
                max_input_tokens=model_settings.get("max_input_tokens", 12000),
                token_counter=config.get("token_counter"),
            )
        ]
        if config.get("enabled", False):
            kwargs: dict[str, Any] = {
                "model": config.get("model", model),
                "trigger": config.get("trigger", ("tokens", 8000)),
                "keep": config.get("keep", ("messages", 8)),
            }
            for key in ("trim_tokens_to_summarize", "token_counter"):
                if config.get(key) is not None:
                    kwargs[key] = config[key]
            summary_prompt = config.get("summary_prompt", self._summary_prompt)
            if summary_prompt is not None:
                kwargs["summary_prompt"] = summary_prompt
            middlewares.append(SummarizationMiddleware(**kwargs))
        return middlewares
