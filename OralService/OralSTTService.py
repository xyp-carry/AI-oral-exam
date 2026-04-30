from pipecat.processors.frame_processor import FrameCallback, FrameDirection, FrameProcessor
from pipecat.frames.frames import (
    Frame,
    InputAudioRawFrame,
    UserStartedSpeakingFrame,
    UserStoppedSpeakingFrame,
    LLMTextFrame,
    InputTransportMessageFrame
    )
from loguru import logger
import datetime
import os
from OralService.model import SenseVoiceSmall
from funasr.utils.postprocess_utils import rich_transcription_postprocess
from OralService.BaseService import save_audio_file

import requests
import json
import subprocess


model_dir = "iic/SenseVoiceSmall"
m, STTkwargs = SenseVoiceSmall.from_pretrained(model=model_dir, device="cuda:0")
m.eval()




class MetricsFrameLogger(FrameProcessor):
    """Get User audio and transform text"""

    def __init__(self):
        super().__init__()
        self.initialize()
        self.Framelist = []
        self.Ip = self.get_host_ip()



    async def process_frame(self, frame: Frame, direction: FrameDirection):
        await super().process_frame(frame, direction)
        
        if isinstance(frame, InputTransportMessageFrame):
            
            if frame.message['message'] == 'mic_on':
                self.start_record = True
                logger.info(f"User start Speak")
            
            elif frame.message['message'] == 'mic_off' and self.start_record:
                self.start_record = False
                timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
                filename = f"recordings1/{timestamp}.wav"
                os.makedirs("recordings1", exist_ok=True)
                audio = bytearray().join(self.bufferlist)
                print(type(audio), len(b''.join(self.bufferlist)))
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
                requests.post(f"http://{self.Ip}:8001/api/user-message",data=json.dumps({"text":text,"client_id":"123"}))
                print(text)
                
                self.initialize()
                logger.info(f"User stop Speak")
                framenew = LLMTextFrame(text)
                await self.push_frame(frame, direction)
                await self.push_frame(framenew)

        if self.start_record and isinstance(frame, InputAudioRawFrame):
            self.bufferlist.append(frame.audio)
            self.sample_rate = frame.sample_rate
            self.num_channels = frame.num_channels

        # if isinstance(frame, UserStartedSpeakingFrame):
        #     self.start_record = True  
        #     logger.info(f"User start Speak")
        #     await self.push_frame(frame, direction)
        # elif isinstance(frame, UserStoppedSpeakingFrame):
        #     timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        #     filename = f"recordings1/{timestamp}.wav"
        #     os.makedirs("recordings1", exist_ok=True)
        #     audio = bytearray().join(self.bufferlist)
        #     print(type(audio), len(b''.join(self.bufferlist)))
        #     await save_audio_file(audio, filename, self.sample_rate, self.num_channels)
        #     res = m.inference(
        #     data_in=filename,
        #     language="auto", # "zh", "en", "yue", "ja", "ko", "nospeech"
        #     use_itn=False,
        #     ban_emo_unk=False,
        #     output_timestamp=True,
        #     **STTkwargs,
        # )
        #     text = rich_transcription_postprocess(res[0][0]["text"])
        #     requests.post(f"http://{self.Ip}:8001/api/user-message",data=json.dumps({"text":text,"client_id":"123"}))
        #     print(text)
            
        #     self.initialize()
        #     logger.info(f"User stop Speak")
        #     framenew = LLMTextFrame(text)
        #     await self.push_frame(frame, direction)
        #     await self.push_frame(framenew)

        else:
            await self.push_frame(frame, direction)
    
    def initialize(self):
        self.start_record = False
        self.bufferlist = []
        self.sample_rate = None
        self.num_channels = None

    def get_host_ip(self):
        """动态获取宿主机 IP"""
        try:
            result = subprocess.run(["ip", "route"], capture_output=True, text=True)
            for line in result.stdout.splitlines():
                if "default via" in line:
                    return line.split()[2]  # 拿到网关 IP
        except Exception as e:
            return "10.255.255.254"