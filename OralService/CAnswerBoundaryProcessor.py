"""Determine when the answer to one C-mode question is complete."""

import asyncio
from dataclasses import dataclass, field
from time import monotonic
from typing import Mapping, Optional

from loguru import logger
from pipecat.frames.frames import (
    ControlFrame,
    EndFrame,
    Frame,
    InputTransportMessageFrame,
    LLMContextFrame,
    UninterruptibleFrame,
)
from pipecat.processors.frame_processor import FrameDirection, FrameProcessor


@dataclass
class CAnswerEventFrame(ControlFrame, UninterruptibleFrame):
    """Private control message between the answer boundary and C interviewer."""

    kind: str
    question_id: str = ""
    text: str = ""
    reason: str = ""
    error: str = ""
    idle_timeout_secs: float = 0.0
    ack: Optional[asyncio.Future] = field(default=None, repr=False, compare=False)


class CAnswerBoundaryProcessor(FrameProcessor):
    """Own the answer buffer, idle clock, and finish confirmation for C mode."""

    def __init__(self, answer_idle_timeout_secs: float = 5.0, min_chunk_interval_secs: float = 10.0):
        super().__init__()
        self.answer_idle_timeout_secs = float(answer_idle_timeout_secs)
        self.min_chunk_interval_secs = float(min_chunk_interval_secs)
        self.lock = asyncio.Lock()
        self.answer_idle_task: Optional[asyncio.Task] = None
        self.generation = 0
        self.current_question_id = ""
        self.active = False
        self.answer_buffer = ""
        self.answer_full_text = ""
        self.answer_sources: set[str] = set()
        self.answer_logged = False
        self.question_started_at = 0.0
        self.answer_started_at = 0.0
        self.last_chunk_sent_at = 0.0
        self.answer_finished = False
        self.has_answer_text = False
        self.answer_finish_confirming = False

    async def process_frame(self, frame: Frame, direction: FrameDirection):
        await super().process_frame(frame, direction)

        if isinstance(frame, CAnswerEventFrame) and direction == FrameDirection.UPSTREAM:
            await self.handle_control(frame)
            return

        if isinstance(frame, EndFrame):
            await self.stop()
            await self.push_frame(frame, direction)
            return

        if direction == FrameDirection.DOWNSTREAM:
            if isinstance(frame, InputTransportMessageFrame):
                if await self.handle_input_message(frame):
                    return
            if isinstance(frame, LLMContextFrame):
                if await self.handle_context_frame(frame):
                    return

        await self.push_frame(frame, direction)

    async def handle_control(self, frame: CAnswerEventFrame) -> None:
        async with self.lock:
            if frame.kind == "question_start":
                self.cancel_answer_idle_timer()
                self.generation += 1
                self.current_question_id = frame.question_id
                self.active = bool(frame.question_id)
                self.answer_buffer = ""
                self.answer_full_text = ""
                self.answer_sources.clear()
                self.answer_logged = False
                self.question_started_at = monotonic()
                self.answer_started_at = 0.0
                self.last_chunk_sent_at = 0.0
                self.answer_finished = False
                self.has_answer_text = False
                self.answer_finish_confirming = False
            elif (frame.kind == "retry_confirmation" and self.active
                  and frame.question_id == self.current_question_id):
                self.cancel_answer_idle_timer()
                self.answer_finished = False
                self.answer_finish_confirming = True
            elif frame.kind == "stop":
                self._stop_locked()
        if frame.ack is not None and not frame.ack.done():
            frame.ack.set_result(None)

    async def stop(self) -> None:
        async with self.lock:
            self._stop_locked()

    def _stop_locked(self) -> None:
        self.cancel_answer_idle_timer()
        self.generation += 1
        self.current_question_id = ""
        self.active = False
        self.answer_buffer = ""
        self.answer_full_text = ""
        self.answer_sources.clear()
        self.answer_logged = False
        self.answer_finish_confirming = False

    async def handle_input_message(self, frame: InputTransportMessageFrame) -> bool:
        message = frame.message or {}
        event_type = str(message.get("type") or message.get("message") or "").strip()
        data = message.get("data") if isinstance(message.get("data"), Mapping) else {}

        if event_type == "finish":
            await self.stop()
            return False
        if event_type in {"speech_start", "speech_active"}:
            await self.handle_speech_activity()
            return True
        if event_type == "speech_end":
            return True
        if event_type == "stt_segment_complete":
            await self.handle_stt_segment_complete()
            return True
        if event_type in {"user-stt", "answer_chunk", "answer_text", "user-text"}:
            text = str(data.get("text") or message.get("text") or "")
            source = "voice_message" if event_type == "user-stt" else "message"
            await self.receive_answer_delta(text, source=source)
            if text.strip():
                await self.handle_stt_segment_complete()
            return True
        if event_type == "answer_end":
            text = str(data.get("text") or message.get("text") or "")
            await self.handle_answer_finish_confirmation(True, text=text)
            return True
        if event_type == "answer_finish_confirmation":
            finished = self.parse_bool(data.get("finished", message.get("finished")), default=False)
            await self.handle_answer_finish_confirmation(finished)
            return True
        return False

    async def handle_context_frame(self, frame: LLMContextFrame) -> bool:
        messages = getattr(getattr(frame, "context", None), "messages", None)
        if not messages:
            return False
        latest = messages[-1]
        if not isinstance(latest, Mapping) or latest.get("role") != "user":
            return False
        text = str(latest.get("content") or "")
        if text.strip():
            await self.receive_answer_delta(text, source="local_stt")
        return True

    async def handle_speech_activity(self) -> None:
        async with self.lock:
            if not self.active or self.answer_finished:
                return
            if not self.answer_started_at:
                self.answer_started_at = monotonic()
            self.answer_finish_confirming = False
            self.cancel_answer_idle_timer()

    async def handle_stt_segment_complete(self) -> None:
        async with self.lock:
            if self.active and not self.answer_finished:
                self.reset_answer_idle_timer()

    async def receive_answer_delta(self, text: str, source: str) -> None:
        async with self.lock:
            if not self.active:
                if source != "local_stt":
                    await self.emit(CAnswerEventFrame(kind="error", error="NO_ACTIVE_QUESTION"))
                return
            if self.answer_finished or not text.strip():
                return
            self.answer_buffer += text
            self.answer_full_text += text
            self.answer_sources.add("voice" if source in {"local_stt", "voice_message"} else "text")
            if not self.answer_started_at:
                self.answer_started_at = monotonic()
            self.has_answer_text = True
            self.answer_finish_confirming = False
            # Both audio and text start the clock through segment completion.
            await self.send_ready_answer_chunk_locked()

    async def send_ready_answer_chunk_locked(self, force: bool = False, reason: str = "answer_chunk") -> None:
        text = self.answer_buffer.strip()
        if not self.active or self.answer_finished or not text:
            return
        now = monotonic()
        last = self.last_chunk_sent_at or self.answer_started_at or self.question_started_at or now
        if not force and now - last < self.min_chunk_interval_secs:
            return
        self.answer_buffer = ""
        self.last_chunk_sent_at = now
        await self.emit(CAnswerEventFrame(
            kind="answer_chunk", question_id=self.current_question_id, text=text, reason=reason
        ))

    async def handle_answer_finish_confirmation(self, finished: bool, text: str = "") -> None:
        async with self.lock:
            if not self.active:
                await self.emit(CAnswerEventFrame(kind="error", error="NO_ACTIVE_QUESTION"))
                return
            if self.answer_finished or not self.answer_finish_confirming:
                return
            if text:
                self.answer_buffer += text
                self.answer_full_text += text
                if text.strip():
                    self.has_answer_text = True
                    self.answer_sources.add("text")
            if finished:
                await self.finish_buffered_answer_locked("finish_confirmation_done")
                return
            self.answer_finish_confirming = False
            await self.send_ready_answer_chunk_locked(force=True, reason="finish_confirmation_continue")
            self.reset_answer_idle_timer()

    async def finish_buffered_answer_locked(self, reason: str) -> None:
        self.cancel_answer_idle_timer()
        text = self.answer_buffer.strip()
        if not text and not self.has_answer_text:
            await self.emit(CAnswerEventFrame(
                kind="error", question_id=self.current_question_id, error="ANSWER_TEXT_REQUIRED"
            ))
            return
        self.answer_buffer = ""
        self.answer_finished = True
        self.answer_finish_confirming = False
        answer_log = None
        if not self.answer_logged:
            source_label = (
                "语音及文字输入" if len(self.answer_sources) > 1
                else "语音输入" if "voice" in self.answer_sources
                else "文字输入"
            )
            answer_log = (self.current_question_id, source_label, self.answer_full_text.strip())
            self.answer_logged = True
        await self.emit(CAnswerEventFrame(
            kind="answer_end", question_id=self.current_question_id, text=text, reason=reason
        ))
        if answer_log is not None:
            logger.info("本轮问题回答完整文字 ({}，{}): {}", *answer_log)

    def reset_answer_idle_timer(self) -> None:
        self.cancel_answer_idle_timer()
        if not self.active or self.answer_finished or not self.has_answer_text or self.answer_finish_confirming:
            return
        self.answer_idle_task = asyncio.create_task(
            self.finish_answer_after_idle(self.current_question_id, self.generation)
        )

    def cancel_answer_idle_timer(self) -> None:
        task = self.answer_idle_task
        self.answer_idle_task = None
        if task and not task.done() and task is not asyncio.current_task():
            task.cancel()

    async def finish_answer_after_idle(self, question_id: str, generation: int) -> None:
        try:
            await asyncio.sleep(self.answer_idle_timeout_secs)
            async with self.lock:
                if not self.active or self.answer_finished or self.answer_finish_confirming:
                    return
                if question_id != self.current_question_id or generation != self.generation:
                    return
                self.answer_finish_confirming = True
                await self.emit(CAnswerEventFrame(
                    kind="confirmation_required",
                    question_id=question_id,
                    idle_timeout_secs=self.answer_idle_timeout_secs,
                ))
        except asyncio.CancelledError:
            pass
        except Exception:
            logger.exception("C-mode answer idle timer failed, question_id={}", question_id)
        finally:
            if self.answer_idle_task is asyncio.current_task():
                self.answer_idle_task = None

    async def emit(self, frame: CAnswerEventFrame) -> None:
        await self.push_frame(frame, FrameDirection.DOWNSTREAM)

    @staticmethod
    def parse_bool(value, default: bool = False) -> bool:
        if isinstance(value, bool):
            return value
        if value is None:
            return default
        if isinstance(value, (int, float)):
            return bool(value)
        text = str(value).strip().lower()
        if text in {"true", "1", "yes", "y", "done", "finished"}:
            return True
        if text in {"false", "0", "no", "n", "continue", "unfinished"}:
            return False
        return default
