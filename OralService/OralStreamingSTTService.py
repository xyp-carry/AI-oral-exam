from typing import Dict, List, Optional

from loguru import logger
from pipecat.frames.frames import (
    Frame,
    InputAudioRawFrame,
    InputTransportMessageFrame,
    LLMContextFrame,
)
from pipecat.processors.aggregators.llm_context import LLMContext
from pipecat.processors.frame_processor import FrameDirection, FrameProcessor

from stt.local_stt import LocalStreamingSTTService


class StreamingMetricsFrameLogger(FrameProcessor):
    """Continuously listen to input audio and emit text when FunASR VAD ends speech."""

    def __init__(
        self,
        history: Optional[List[Dict[str, str]]] = None,
        current_user: Optional[Dict] = None,
        vad_chunk_ms: int = 200,
    ):
        super().__init__()
        self.history: List[Dict[str, str]] = history if history is not None else []
        self.current_user = current_user or {}
        self.stt = LocalStreamingSTTService(
            vad_chunk_ms=vad_chunk_ms,
            transcribe_on_speech_end=True,
        )

    async def process_frame(self, frame: Frame, direction: FrameDirection):
        await super().process_frame(frame, direction)

        if isinstance(frame, InputTransportMessageFrame):
            await self._handle_transport_message(frame, direction)
            return

        if isinstance(frame, InputAudioRawFrame):
            events = await self.stt.feed_audio(
                frame.audio,
                frame.sample_rate,
                frame.num_channels,
            )
            await self._push_stt_events(events, direction)
            return

        await self.push_frame(frame, direction)

    async def flush(self, direction: FrameDirection = FrameDirection.DOWNSTREAM) -> None:
        events = await self.stt.flush()
        await self._push_stt_events(events, direction)

    async def close(self, direction: FrameDirection = FrameDirection.DOWNSTREAM) -> None:
        events = await self.stt.close()
        await self._push_stt_events(events, direction)

    async def _handle_transport_message(
        self,
        frame: InputTransportMessageFrame,
        direction: FrameDirection,
    ) -> None:
        message = frame.message or {}
        if message.get("type") == "user-text":
            text = (message.get("data") or {}).get("text", "")
            await self._push_user_text(text, direction)
            return

        signal = message.get("message")
        if signal in {"mic_on", "mic_off"}:
            logger.info(f"Ignore mic signal in streaming STT mode: {signal}")
            return

        await self.push_frame(frame, direction)

    async def _push_stt_events(
        self,
        events: List[Dict],
        direction: FrameDirection,
    ) -> None:
        for event in events:
            event_type = event.get("type")
            if event_type == "speech_start":
                logger.info("FunASR VAD speech start")
                continue
            if event_type == "speech_end":
                reason = event.get("reason", "vad")
                logger.info(f"FunASR VAD speech end: {reason}")
                continue
            if event_type == "text":
                await self._push_user_text(event.get("text", ""), direction)

    async def _push_user_text(self, text: str, direction: FrameDirection) -> None:
        text = (text or "").strip()
        if not text:
            return

        self.history.append({"role": "user", "content": text})
        logger.info(f"Streaming STT text: {text}")

        await self.push_frame(
            LLMContextFrame(context=LLMContext(messages=[{"role": "user", "content": text}])),
            direction,
        )


MetricsFrameLogger = StreamingMetricsFrameLogger
