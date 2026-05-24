from pipecat.processors.frame_processor import FrameCallback, FrameDirection, FrameProcessor

from pipecat.frames.frames import (
    Frame,
    LLMTextFrame,
    TTSSpeakFrame,
    AggregatedTextFrame,
    LLMContextFrame,
    StartFrame
    )
from typing import List, Dict, Optional
import httpx
import json
from datetime import datetime
import websockets
from AIOralExamSystem.utils.monitor import GlobalMonitor
from AIOralExamSystem.Agent.Interviewer import InterviewerAgent
from AIOralExamSystem.Exam.Judger import JudgerAgent, MainJudgerAgent
from AIOralExamSystem.Exam.examObject import CandidateExamState

import asyncio
from config import get_settings
import json
import re


class LLMService(FrameProcessor):
    def __init__(
        self,
        monitor: GlobalMonitor,
        current_user: dict,
        history: List[Dict[str, str]] = [],
        exam_state: Optional[CandidateExamState] = None,
    ):
        super().__init__()
        self.history: List[Dict[str, str]] = history
        self.exam_state = exam_state
        self.nickname = current_user['nickname']
        self.task_queue = asyncio.Queue()
        settings = get_settings()
        self.interviewer = InterviewerAgent(settings.model_dump(mode="json"), current_user['uuid'])
        self.juderA = JudgerAgent({"model_name": settings.deepseek_model,"model_url":settings.deepseek_url,"model_api_key":settings.deepseek_api_key}, current_user['uuid'])
        self.juderB = JudgerAgent({"model_name": settings.kimi_model,"model_url":settings.kimi_url,"model_api_key":settings.kimi_api_key}, current_user['uuid'],temperature=0.6)
        self.main_judger = MainJudgerAgent({"model_name": settings.model_name,"model_url":settings.model_url,"model_api_key":settings.model_api_key}, current_user['uuid'], thinking=True, response_format=True,temperature=0)
        
    def add_user_msg(self, text: str):
        """记录用户说的话"""
        self.history.append({"role": "user", "content": text})

    def add_ai_msg(self, text: str):
        """记录 AI 回复的话（用于后续拼接上下文）"""
        self.history.append({"role": "assistant", "content": text})

    async def process_frame(self, frame: Frame, direction: FrameDirection):
        await super().process_frame(frame, direction)
        if isinstance(frame, LLMContextFrame):
            await self.task_queue.put((frame, direction))
        if isinstance(frame, StartFrame):
            await self.push_frame(frame, direction) # 需要先发送StartFrame
            await self.push_frame(TTSSpeakFrame(f"你好，{self.nickname}同学，我是本次的考官，请简要介绍一下你的项目"), FrameDirection.DOWNSTREAM)
        else:
            await self.push_frame(frame, direction)

    async def requst_loop(self):
        count = 0
        while True:
            count += 1
            Frame = await self.task_queue.get()
            frame, direction = Frame
            self.history.extend(frame.context.get_messages())

            if count == 3 or ((count - 3) % 2 == 0 and count > 3):
                is_end = await self.judge()
                if is_end:
                    break
            
            await self.push_frame(LLMTextFrame("AI口试开始思考"), direction)
        
            ret = await self.interviewer.run(history=self.history)
            AI_answer = ""
            async for chunk in ret:
                if chunk.get("model"):
                    content = chunk['model']["messages"][-1].content

                    await self.push_frame(LLMTextFrame(content), direction)
                    AI_answer += content

            self.history.append({
            "role": "assistant",
            "content": AI_answer,
        })
            
            await self.push_frame(TTSSpeakFrame(AI_answer), direction)
            await self.push_frame(LLMTextFrame("AI口试结束回答"), direction)
            self.task_queue.task_done()

    async def setup(self, setup):
        await super().setup(setup)
        asyncio.create_task(self.requst_loop())   
    
    def parse_outer_json_block(self, response: str) -> dict:
        text = response.strip()

        if text.startswith("```"):
            text = re.sub(r"^```(?:json|JSON)?\s*\n?", "", text, count=1)
            text = re.sub(r"\n?```\s*$", "", text, count=1)

        if not text.startswith("{"):
            obj_match = re.search(r"\{.*\}", text, flags=re.DOTALL)
            if not obj_match:
                raise ValueError("No JSON object found in model response")
            text = obj_match.group(0)
        return json.loads(text)
    
    async def judge(self):
            
        try:
            await self.push_frame(LLMTextFrame("AI评审开始评估"), FrameDirection.DOWNSTREAM)
            logs = "\n".join(
                            f"{m['role']}: {m['content']}"
                            for m in self.history
                        )
            
            judge_doc_A = await self.juderA.run(history=[{"role": "user", "content": logs}])

            judge_doc_B = await self.juderB.run(history=[{"role": "user", "content": logs}])
            
            main_judge_doc = await self.main_judger.run(history=[{"role": "user", "content": "以下是两个评审的判断结果：" +"\n"+judge_doc_A['messages'][-1].content +"\n"+judge_doc_B['messages'][-1].content}])
            judge_res = self.parse_outer_json_block(main_judge_doc['messages'][-1].content)
            
            if not judge_res['reinterview']:
                await self.push_frame(LLMTextFrame(await self.judge_HTML(judge_res['final_score'], judge_res['final_reason'])), FrameDirection.DOWNSTREAM)
                await self.push_frame(LLMTextFrame("AI评审结束评估"), FrameDirection.DOWNSTREAM)
                await self.push_frame(LLMTextFrame("AI考试全部结束"), FrameDirection.DOWNSTREAM)
                return True
            await self.push_frame(LLMTextFrame("AI评审结束评估"), FrameDirection.DOWNSTREAM)
            return False
        except Exception as e:
            print("x",e)
            pass
    
    async def judge_HTML(self, degree:str, reson:str):
        return f"""
        <div style="background:#fff;border-radius:8px;padding:16px 20px;margin-bottom:12px;max-width:360px;font-family:sans-serif;box-shadow:0 1px 4px rgba(0,0,0,0.08);">
  <p style="margin:0 0 8px;font-size:14px;">评审结果：<strong style="color:#d48806;">{degree}</strong></p>
  <p style="margin:0;font-size:13px;color:#666;line-height:1.6;">评价缘由：{reson}</p>
</div>
        """


    
