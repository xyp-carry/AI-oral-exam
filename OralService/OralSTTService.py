from pipecat.processors.frame_processor import FrameCallback, FrameDirection, FrameProcessor
from pipecat.frames.frames import (
    Frame,
    InputAudioRawFrame,
    UserStartedSpeakingFrame,
    UserStoppedSpeakingFrame,
    LLMTextFrame,
    InputTransportMessageFrame,
    TranscriptionFrame,
    LLMContextFrame
    )
from pipecat.processors.aggregators.llm_context import LLMContext
from loguru import logger
import datetime
import os
from OralService.model import SenseVoiceSmall
from funasr.utils.postprocess_utils import rich_transcription_postprocess
from OralService.BaseService import save_audio_file

import requests
import json
import subprocess
from typing import List, Dict
from AIOralExamSystem.Agent.Textfix import TextfixAgent
from config import get_settings

model_dir = "iic/SenseVoiceSmall"
m, STTkwargs = SenseVoiceSmall.from_pretrained(model=model_dir, device="cuda:0")
m.eval()




class MetricsFrameLogger(FrameProcessor):
    """Get User audio and transform text"""

    def __init__(self, current_user: Dict, history: List[Dict[str, str]] = []):
        super().__init__()
        self.initialize()
        self.Framelist = []
        self.history: List[Dict[str, str]] = history
        settings = get_settings()
        # self.Textfixer = TextfixAgent(model_settings={"model_name": settings.deepseek_model,"model_url":settings.deepseek_url,"model_api_key":settings.deepseek_api_key}, source=current_user['uuid'])

    async def process_frame(self, frame: Frame, direction: FrameDirection):
        await super().process_frame(frame, direction)

        if isinstance(frame, InputTransportMessageFrame):
            if frame.message.get('message', False):
                if frame.message['message'] == 'mic_on':
                    self.start_record = True
                    logger.info(f"User start Speak")
                
                elif frame.message['message'] == 'mic_off' and self.start_record:
                    self.start_record = False
                    timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
                    filename = f"recordings1/{timestamp}.wav"
                    os.makedirs("recordings1", exist_ok=True)
                    audio = bytearray().join(self.bufferlist)
                    await save_audio_file(audio, filename, self.sample_rate, self.num_channels)
                    res = m.inference(
                    data_in=filename,
                    language="auto", # "zh", "en", "yue", "ja", "ko", "nospeech"
                    use_itn=False,
                    ban_emo_unk=False,
                    output_timestamp=True,
                    **STTkwargs,
                )
                    text = rich_transcription_postprocess(res[0][0]["text"])
                    
                    self.initialize()
                    logger.info(f"User stop Speak")
                    # logs = "\n".join(
                    #             f"{m['role']}: {m['content']}"
                    #             for m in self.history
                    #         )
                    # main_judge_doc = await self.Textfixer.run(history=[{"role": "user", "content": f"以下是历史上文:{self.history[-1]['content']} \n 当前STT生成的文本:{text}"}])
                    # framenew = LLMContextFrame(context=LLMContext(messages = [{'role':'user','content':main_judge_doc['messages'][-1].content}]))
                    framenew = LLMContextFrame(context=LLMContext(messages = [{'role':'user','content':text}]))

                    # await self.push_frame(frame, direction)
                    await self.push_frame(framenew)
            elif frame.message['type'] == 'user-text':
                userInput = LLMContextFrame(context=LLMContext(messages = [{'role':'user','content':frame.message['data']['text']}]))
                await self.push_frame(userInput, direction)

        if self.start_record and isinstance(frame, InputAudioRawFrame):
            self.bufferlist.append(frame.audio)
            self.sample_rate = frame.sample_rate
            self.num_channels = frame.num_channels

        else:
            await self.push_frame(frame, direction)
    
    def initialize(self):
        self.start_record = False
        self.bufferlist = []
        self.sample_rate = None
        self.num_channels = None
        