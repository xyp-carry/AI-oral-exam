from pipecat.processors.frame_processor import FrameCallback, FrameDirection, FrameProcessor

from pipecat.frames.frames import (
    Frame,
    LLMTextFrame,
    TTSSpeakFrame
    )
from typing import List, Dict
import httpx
import json

import websockets

class LLMService(FrameProcessor):
    def __init__(self):
        super().__init__()
        self.history: List[Dict[str, str]] = []
        self.history.append({"role": "system", "content": "你是一名操作系统课程的老师，名叫张三，接下来我将进行对话，你的回复务必避免冗余繁杂，回答不要超过100个字尽量简洁。"})
        self.params = {
            "model": "glm-4.7",
            "temperature": 0,
            "stream": True,
            "thinking":{"type": "disabled"}
        }
        self.headers = {
            "Authorization": "b4a804d2412b4d8995742b5d55453ae5.zp2h4b5jHcNkSi6Z",
            "Content-Type": "application/json"
        }
        self.base_url = "https://open.bigmodel.cn/api/paas/v4/chat/completions"

        self.ws = None

    def add_user_msg(self, text: str):
        """记录用户说的话"""
        self.history.append({"role": "user", "content": text})

    def add_ai_msg(self, text: str):
        """记录 AI 回复的话（用于后续拼接上下文）"""
        self.history.append({"role": "assistant", "content": text})
    
    async def process_frame(self, frame: Frame, direction: FrameDirection):
        await super().process_frame(frame, direction)
        # print("FRAME",type(frame))
        if isinstance(frame, LLMTextFrame):

            params = self.params.copy()

            self.add_user_msg(frame.text)
            params["messages"] = self.history
            
            Output = []
            async with httpx.AsyncClient(timeout=httpx.Timeout(5.0, read=120.0)) as client:
                async with client.stream("POST", self.base_url, headers=self.headers, json=params) as response:
                        async for chunk in response.aiter_text():
                            try:
                                chunks = chunk.split('\n\n')[:-1]
                                data = ''.join([json.loads(chunk[6:])['choices'][0]['delta']['content'] for chunk in chunks])
                                await self.push_text(data)
                                Output.append(data)
                            except Exception as e:
                                print(e)
            await self.ws.send(json.dumps({"type": "ai_done"}))
            print(''.join(Output))
            await self.push_frame(TTSSpeakFrame(''.join(Output)))
        
        await self.push_frame(frame, direction)

    async def push_text(self, chunk):
        await self.ws.send(json.dumps({"type": "ai_message", "chunk": chunk}))


    async def connect_websoket(self):
        import subprocess
        result = subprocess.run(["ip", "route"], capture_output=True, text=True)
        for line in result.stdout.splitlines():
            if "default via" in line:
                url = line.split()[2]  # 拿到网关 IP
        self.ws = await websockets.connect(f"ws://{url}:8001/ws/ai")
    
    async def setup(self, setup):
        await super().setup(setup)

        await self.connect_websoket()