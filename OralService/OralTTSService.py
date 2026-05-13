from pipecat.processors.frame_processor import FrameCallback, FrameDirection, FrameProcessor
from pipecat.frames.frames import (
    Frame,
    TTSSpeakFrame,
    OutputAudioRawFrame,
    TTSAudioRawFrame,
    TextFrame,
    LLMTextFrame
    )
import httpx
import re
import json
import base64
import asyncio
import itertools

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
                "format": "wav",
                "channel": 1
            },
            "pronunciation_dict": { "tone": ["处理/(chu3)(li3)", "危险/dangerous"] },
            "subtitle_enable": False
        }
        self.FRAME_SIZE = 417
        self.task_queue = asyncio.Queue()

    async def process_frame(self, frame: Frame, direction: FrameDirection):
        await super().process_frame(frame, direction)
        # print(frame)
        if isinstance(frame, TTSSpeakFrame):
            print("开始输入",direction)
            await self.task_queue.put((frame,direction))
            # self.payload['text'] = frame.text
            # mp3_bytes = bytearray()
            # async with httpx.AsyncClient(timeout=None) as client:
            #     async with client.stream("POST", url=self.url, headers=self.headers, json=self.payload) as response:
            #         response.raise_for_status()
            #         mp3_frame_cache = b""
            #         async for raw_line in response.aiter_lines():
            #             if not raw_line or not raw_line.startswith("data:"):
            #                 continue
            #             pattern = re.compile(r'data:\s*(\{.*?\}(?=\s*data:|$))')
            #             matches = pattern.findall(raw_line)
                          
            #             for json_str in matches:
            #                 json_str = json_str.strip()
            #                 data = json.loads(json_str)
            #                 if data.get("data", False):
            #                     if data['data'].get('audio', False):
                                    
            #                         audio_str = data['data']['audio']
            #                         chunk_bytes = bytes.fromhex(audio_str)
            #                         mp3_bytes.extend(chunk_bytes)
            #                         # merged_bytes = mp3_frame_cache + chunk_bytes
            #                         # full_frames_length = (len(merged_bytes) // self.FRAME_SIZE) * self.FRAME_SIZE
            #                         # print(len(merged_bytes[:full_frames_length]))
            #                         # audio_str = audio_str + "=" * (4 - len(audio_str) % 4) if len(audio_str) % 4 else audio_str
            #                         if len(chunk_bytes) % 2 != 0:
            #                             chunk_bytes = chunk_bytes + b'\x00'
            #                         OutAudioFrame = TTSAudioRawFrame(chunk_bytes, sample_rate=32000, num_channels=1)
            #                         await self.push_frame(OutAudioFrame, direction)
            # with open("output.mp3", "wb") as f:
            #     f.write(mp3_bytes)

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

        await self.push_frame(frame, direction)

    async def requst_loop(self):
        while True:
            Frame = await self.task_queue.get()
            self.payload['text'] = Frame[0].text
            async with httpx.AsyncClient(timeout=None) as client:
                response = await client.post(url=self.url,headers=self.headers, json=self.payload)
                try:
                    chunk_bytes = bytes.fromhex(response.json()['data']['audio'])
                    frame_iterator = iter(lambda i=iter(chunk_bytes): bytes(itertools.islice(i, 1920)), b'')
                    bt = b''
                    for frame_index, frame_data in enumerate(frame_iterator):
                        bt += frame_data
                        OutAudioFrame = OutputAudioRawFrame(frame_data, sample_rate=32000, num_channels=1)
                        await self.push_frame(OutAudioFrame, Frame[1])
                    with open("output12.wav", "wb") as f:
                        f.write(chunk_bytes)
                except Exception as e:
                    print(response.json())
                    continue
    async def setup(self, setup):
        await super().setup(setup)
        asyncio.create_task(self.requst_loop())

