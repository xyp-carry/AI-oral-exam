from AIOralExamSystem.Agent.base_Agent import BaseAgent


class TextfixAgent(BaseAgent):
    def __init__(
        self,
        model_settings: dict,
        source: str | None = None,
        thinking: bool = False,
        response_format: bool = False,
        temperature: float = 0,
        show_tool_io: bool = False,
    ):
        super().__init__("TextfixAgent", model_settings, thinking, response_format, temperature, show_tool_io=show_tool_io)
        self.source = source
        self.system_prompt = """
        ## Role
        你是一个文本修正 Agent。你会收到两段输入：
        - content：待修改文本出现的上下文。
        - text：需要修正的 STT 识别文本。

        ## Task
        请根据 content 理解语境，只修正 text 中可能由 STT 识别导致的问题，包括语法不通顺、音译错误、专有名词或英文术语识别错误。
        不要补充新信息，不要扩写，不要替用户完善原本没有表达出来的逻辑，也不要改变原意。
        如果 text 本身已经通顺且没有明显 STT 错误，请原样返回 text。

        ## Response
        只返回修正后的文本，不要返回解释、标题、Markdown 或其他内容。
        """

    async def execute(self, content: str, text: str):
        self.set_heartbeat_interval(1)
        await self.start_heartbeat()

        messages = [{"role": "system", "content": self.system_prompt}]
        messages.append({
            "role": "user",
            "content": (
                "请根据以下上下文修正待修改文本。\n\n"
                f"content:\n{content}\n\n"
                f"text:\n{text}"
            ),
        })

        try:
            return await self.agent.ainvoke({
                "messages": messages
            })
        finally:
            await self.stop_heartbeat()
