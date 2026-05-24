from AIOralExamSystem.Agent.base_Agent import BaseAgent
from langchain_core.tools import tool
from pydantic import BaseModel


class JudgerAgent(BaseAgent):
    def __init__(self, model_settings: dict, source: str, thinking: bool = False, response_format: bool = False, temperature: float = 0):
        super().__init__("JudgerAgent", model_settings, thinking, response_format, temperature)
        self.source = source
        self.system_prompt_step1 = """
        ## Role（角色设定）
         你是一名客观的评委，负责根据学生与面试官的对话内容，判断学生的回答质量。
        
        ## Task（核心任务）
        1. 根据学生与面试官的对话内容，判断学生的回答质量。将学生的回答质量按好(7-10分)、中(4-6分)、差(0-3分)进行评分。
        2. 如果有多轮对话，每轮对话后，评委需要根据学生的回答质量，判断学生的回答是否符合要求。
        3. 不仅要给出分数还需要给出打分的依据。

        ## Output（输出格式）
        【第n轮】
        第n轮分数：{score}
        第n轮打分依据：{reason}
        【第n+1轮】
        第n+1轮分数：{score}
        第n+1轮打分依据：{reason}
        ...
        """
        
    
    
    async def execute(self, history: str):
        self.set_heartbeat_interval(1)
        await self.start_heartbeat()

        historys = [{"role": "system", "content": self.system_prompt_step1}]
        historys.extend(history)

        response = await self.agent.ainvoke({
            "messages": historys
        })
        
        await self.stop_heartbeat()
        return response


class MainJudgerAgent(BaseAgent):
    def __init__(self, model_settings: dict, source: str, thinking: bool = True, response_format: bool = True, temperature: float = 0):
        super().__init__("MainJudgerAgent", model_settings, thinking, response_format, temperature)
        self.source = source
        self.system_prompt="""
        ## Role（角色设定）
        你是一名主评委，负责根据所有评委的打分和打分依据，来最终裁决学生的回答质量，若各个评委的评价差异很大，则需要让面试再多进行一到两轮。
        
        ## Task（核心任务）
        1. 务必根据各个评委的打分和打分依据，来最终裁决学生的回答质量。
        2. 若各个评委的评价差异很大，则需要让面试再多进行两轮。
        3. 若可以最终裁决，则需要按照输出格式给出最终的判断结果和对学生的建议。

        ## Output（输出格式，以json格式输出）
        {
            "reinterview": bool,
            "final_score": "优秀/良好/中等/合格/不合格",
            "final_reason": "{reason}"
        }
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
    
    def get_response_format(self):
        class ResponseFormat(BaseModel):
            reinterview: bool
            final_score: str
            final_reason: str
        return ResponseFormat
