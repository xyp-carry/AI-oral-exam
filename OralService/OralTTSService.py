from pipecat.processors.frame_processor import FrameCallback, FrameDirection, FrameProcessor
from pipecat.frames.frames import (
    Frame,
    TTSSpeakFrame
    )
import requests
import datetime
import pygame
import websockets
import asyncio
import json

class TTSAudio(FrameProcessor):
    def __init__(self):
        super().__init__()
        self.record = False
        self.url = "https://api.minimaxi.com/v1/t2a_v2"
        self.headers = {
            "Content-Type": "application/json",
            "Authorization": "Bearer sk-api-3e6njVvrue36MUF3jk754eyEoAxG8JCUPrGzSaBHar80MR92qulPAaJsHi-wXZgH6O1J46AkXnUGsRdU5326p4Przps9HbXg8KYpz-xZb8roigtOhKa0n1M"
        }
        self.payload = {
            "model": "speech-2.8-turbo",
            "text": "",
            "stream": False,
            "voice_setting": {
                "voice_id": "Chinese (Mandarin)_Male_Announcer",
                "speed": 1,
                "vol": 1,
                "pitch": 0,
                "emotion": "happy"
            },
            "audio_setting": {
                "sample_rate": 32000,
                "bitrate": 128000,
                "format": "mp3",
                "channel": 1
            },
            "pronunciation_dict": { "tone": ["处理/(chu3)(li3)", "危险/dangerous"] },
            "subtitle_enable": False
        }

        self.ws = None


    async def process_frame(self, frame: Frame, direction: FrameDirection):
        await super().process_frame(frame, direction)
        # print(frame)
        if isinstance(frame, TTSSpeakFrame):
            self.payload['text'] = frame.text
            response = requests.post(self.url, json=self.payload, headers=self.headers)
            audio_bytes = bytes.fromhex(response.json()['data']['audio'])
            await self.ws.send(json.dumps({
                "type": "audio_start", "sample_rate": 24000, "channels": 1
            }))
            asyncio.create_task(self.push_audio(audio_bytes))
            # timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
            # with open(f"AIrecording/AI-{timestamp}.mp3", "wb") as f:
            #     f.write(audio_bytes)
            
            # pygame.mixer.init()
            # pygame.mixer.music.load(f"AIrecording/AI-{timestamp}.mp3")
            # pygame.mixer.music.play()

            # print("正在播放...")
            # while pygame.mixer.music.get_busy():
            #     pygame.time.Clock().tick(10)
            # print("播放完毕")

        # if isinstance(frame, TTSStoppedFrame):
        #     self.record = False
            
        
        # if isinstance(frame, (TTSStartedFrame, TTSStoppedFrame, TTSAudioRawFrame, TTSTextFrame)):
        #     print("xypp",frame)

        await self.push_frame(frame, direction)

    async def setup(self,setup1):
        await super().setup(setup1)

        await self.connect_websoket()
    
    async def connect_websoket(self):
        import subprocess
        result = subprocess.run(["ip", "route"], capture_output=True, text=True)
        for line in result.stdout.splitlines():
            if "default via" in line:
                url = line.split()[2]  # 拿到网关 IP
        self.ws = await websockets.connect(f"ws://{url}:8001/ws/audio")

    async def push_audio(self, audio_bytes):
        import base64
        await self.ws.send(json.dumps({"type": "audio_start"}))
        audio_b64 = base64.b64encode(audio_bytes).decode("utf-8")
        await self.ws.send(json.dumps({"bytes":audio_b64}))
        await self.ws.send(json.dumps({"type": "audio_end"}))