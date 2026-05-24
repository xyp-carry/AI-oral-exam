from AIOralExamSystem.Agent.base_Agent import BaseAgent
from langchain_core.tools import tool
from pydantic import BaseModel


class TextfixAgent(BaseAgent):
    def __init__(self, model_settings: dict, source: str, thinking: bool = False, response_format: bool = False, temperature: float = 0):
        super().__init__("TextfixAgent", model_settings, thinking, response_format, temperature)
        self.source = source
        self.system_prompt = """
        ## Role（角色设定）
        你是一名文本修正机器人，接下来将会给你一个STT生成的文本和该文本出现的上下文语境，你需要将STT生成的一些不太通顺或者名词表达不正确的地方修正过来。
        
        ## Task（任务设定）
        你需要根据上下文语境，修正STT生成的文本，确保其语法正确，语义通顺，并且规范专有名词或一些英文单词。
        不要自动修正其中本身的语义逻辑问题，只修正你判断可能因为STT生成导致的文本错误。
        STT可能会出现一些音译的错误，不要擅自增加其中逻辑，只返回修正后的文本。

        ## Response（响应设定）
        你需要返回修正后的文本，不要返回其他内容。
        """

    async def execute(self, history: str):
        self.set_heartbeat_interval(1)
        await self.start_heartbeat()

        historys = [{"role": "system", "content": self.system_prompt}]
        historys.extend(history)

        response = await self.agent.ainvoke({
            "messages": historys
        })
        
        await self.stop_heartbeat()
        return response