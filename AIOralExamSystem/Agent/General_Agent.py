from typing import Any

from AIOralExamSystem.Agent.base_Agent import BaseAgent


class GeneralAgent(BaseAgent):
    """通用无工具 Agent，调用方必须显式传入系统提示词和用户提示词。"""

    def __init__(
        self,
        model_settings: dict,
        thinking: bool = False,
        response_format: bool = False,
        temperature: float = 0,
        top_p: float = 1,
        show_tool_io: bool | None = None,
        name: str = "GeneralAgent",
    ):
        super().__init__(
            name,
            model_settings,
            thinking=thinking,
            response_format=response_format,
            temperature=temperature,
            top_p=top_p,
            show_tool_io=show_tool_io,
        )

    def get_tools(self):
        return []

    async def execute(self, system_prompt: str, user_prompt: str):
        system_prompt = str(system_prompt or "").strip()
        user_prompt = str(user_prompt or "").strip()
        if not system_prompt:
            raise ValueError("system_prompt is required")
        if not user_prompt:
            raise ValueError("user_prompt is required")

        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ]
        return await self.agent.ainvoke({"messages": messages})

    def message_to_text(self, response: Any) -> str:
        if isinstance(response, dict):
            messages = response.get("messages")
            if messages:
                return self.message_to_text(messages[-1])
            if response.get("content") is not None:
                return self.message_to_text(response["content"])
        content = getattr(response, "content", response)
        if isinstance(content, list):
            return "".join(
                str(item.get("text", item)) if isinstance(item, dict) else str(item)
                for item in content
            )
        return str(content or "")
