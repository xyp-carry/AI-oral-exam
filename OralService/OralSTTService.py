from pipecat.processors.frame_processor import FrameDirection, FrameProcessor
from pipecat.frames.frames import (
    Frame,
    InputAudioRawFrame,
    InputTransportMessageFrame,
    LLMContextFrame
    )
from pipecat.processors.aggregators.llm_context import LLMContext
from loguru import logger
from stt.local_stt import LocalStreamingSTTService

from typing import List, Dict, Optional


class MetricsFrameLogger(FrameProcessor):
    """Get User audio and transform text"""

    def __init__(
        self,
        current_user: Dict,
        history: Optional[List[Dict[str, str]]] = None,
        vad_chunk_ms: int = 200,
    ):
        super().__init__()
        self.initialize()
        self.Framelist = []
        self.current_user = current_user or {}
        self.exam_type = str(self.current_user.get("exam_type") or "").strip().upper()
        self.requires_mic_window = self.exam_type == "C"
        self.history: List[Dict[str, str]] = history if history is not None else []
        self.streaming_stt = LocalStreamingSTTService(
            vad_chunk_ms=vad_chunk_ms,
            transcribe_on_speech_end=True,
            save_audio_segments=self.requires_mic_window,
        )


    async def process_frame(self, frame: Frame, direction: FrameDirection):
        await super().process_frame(frame, direction)

        if isinstance(frame, InputTransportMessageFrame):
            handled = await self.handle_transport_message(frame, direction)
            if handled:
                return

        if isinstance(frame, InputAudioRawFrame):
            if not self.should_process_audio():
                return

            events = await self.streaming_stt.feed_audio(
                frame.audio,
                frame.sample_rate,
                frame.num_channels,
            )
            await self.push_streaming_events(events, direction)
            self.sample_rate = frame.sample_rate
            self.num_channels = frame.num_channels
            return

        await self.push_frame(frame, direction)

    def should_process_audio(self) -> bool:
        if self.start_record:
            return True
        if self.requires_mic_window:
            return False
        return not self.manual_control_seen

    async def handle_transport_message(
        self,
        frame: InputTransportMessageFrame,
        direction: FrameDirection,
    ) -> bool:
        message = frame.message or {}
        signal = message.get("message")

        if signal == "mic_on":
            self.manual_control_seen = True
            self.start_record = True
            self.bufferlist = []
            self.sample_rate = None
            self.num_channels = None
            self.speech_seen = False
            self.answer_text_seen = False
            self.streaming_stt.reset()
            return True

        if signal == "mic_off":
            if not self.start_record:
                return True
            self.manual_control_seen = True

            self.start_record = False
            events = await self.streaming_stt.flush()
            self.reset_manual_recording()
            await self.push_streaming_events(events, direction)
            return True

        if message.get("type") == "user-text":
            data = message.get("data") if isinstance(message.get("data"), dict) else {}
            text = str(data.get("text") or message.get("text") or "")
            logger.info(f"User text: {text}")
            await self.push_user_text(text, direction)
            return True

        return False

    async def push_streaming_events(
        self,
        events: List[Dict],
        direction: FrameDirection,
    ) -> None:
        for event in events:
            event_type = event.get("type")
            if event_type in {"speech_start", "speech_active", "speech_end"}:
                if event_type in {"speech_start", "speech_active"}:
                    self.speech_seen = True
                if self.requires_mic_window:
                    await self.push_frame(
                        InputTransportMessageFrame(
                            message={
                                "type": event_type,
                                "data": {
                                    "speech_seen": self.speech_seen,
                                },
                            }
                        ),
                        direction,
                    )
                continue
            if event_type == "audio_saved":
                path = str(event.get("path") or "")
                if path:
                    print(f"FunASR audio saved: {path}", flush=True)
                continue
            if event_type == "text":
                text = str(event.get("text") or "")
                if text.strip():
                    self.answer_text_seen = True
                    print(f"FunASR STT text: {text}", flush=True)
                await self.push_user_text(text, direction)

    async def push_user_text(self, text: str, direction: FrameDirection) -> None:
        text = (text or "").strip()
        if not text:
            return

        self.history.append({"role": "user", "content": text})
        user_input = LLMContextFrame(
            context=LLMContext(messages=[{"role": "user", "content": text}])
        )
        await self.push_frame(user_input, direction)

    def reset_manual_recording(self) -> None:
        self.start_record = False
        self.bufferlist = []
        self.sample_rate = None
        self.num_channels = None

    def initialize(self):
        self.start_record = False
        self.manual_control_seen = False
        self.speech_seen = False
        self.answer_text_seen = False
        self.bufferlist = []
        self.sample_rate = None
        self.num_channels = None
        