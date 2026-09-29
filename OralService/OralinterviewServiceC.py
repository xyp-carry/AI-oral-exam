import asyncio
from html import escape
from time import monotonic
from typing import Any, Dict, Mapping, Optional

from loguru import logger
from pipecat.frames.frames import (
    EndFrame,
    Frame,
    InputTransportMessageFrame,
    LLMContextFrame,
    LLMTextFrame,
    OutputTransportMessageFrame,
    StartFrame,
    TTSSpeakFrame,
)
from pipecat.processors.frame_processor import FrameDirection, FrameProcessor
from pathlib import Path

from AIOralExamSystem.Exam.Examdata import (
    get_exam_judge_config_by_exam_id,
    get_exam_session_by_exam_id,
    list_ai_preset_questions_by_exam_item_and_user,
    list_preset_questions_by_exam_item,
)
from AIOralExamSystem.Exam.QAmanagerC import QAmanagerC
from AIOralExamSystem.Graph.ExamC import ExamCFlow
from AIOralExamSystem.message.session import ExamMessageSession
from AIOralExamSystem.Exam.CFinalReview import CFinalReviewService
from AIOralExamSystem.Exam.Examdata.final_review_repository import get_final_review

import time

class InterviewServiceC(FrameProcessor):
    """Pure Exam C processor for incremental text answers."""

    SENTENCE_END_MARKS = ("。", ".")

    def __init__(
        self,
        answer_idle_timeout_secs: float = 5.0,
        exam_id: Optional[str] = None,
        min_chunk_interval_secs: float = 10.0,
        min_chunk_chars: int = 50,
        **_: Any,
    ):
        super().__init__()
        self.answer_idle_timeout_secs = float(answer_idle_timeout_secs)
        self.min_chunk_interval_secs = float(min_chunk_interval_secs)
        self.min_chunk_chars = int(min_chunk_chars)
        self.exam_id = str(exam_id or "").strip()
        self.lock = asyncio.Lock()
        self.exam_graph_init_lock = asyncio.Lock()
        self.exam_graph: Optional[ExamMessageSession] = None
        self.exam_output_task: Optional[asyncio.Task] = None
        self.followup_poll_task: Optional[asyncio.Task] = None
        self.answer_idle_task: Optional[asyncio.Task] = None
        self.current_question_id = ""
        self.answer_buffer = ""
        self.question_started_at = 0.0
        self.answer_started_at = 0.0
        self.last_chunk_sent_at = 0.0
        self.answer_finished = False
        self.has_answer_text = False
        self.answer_finish_confirming = False
        self.startup_failed = False
        self.final_review_service = None
        self.pending_final_review = None
        self.final_review_ready = False
        self.final_review_task: Optional[asyncio.Task] = None
        self.terminal_sent = False
        self.exam_user_id = ""

    async def process_frame(self, frame: Frame, direction: FrameDirection):
        await super().process_frame(frame, direction)

        if isinstance(frame, StartFrame):
            await self.ensure_exam_graph()
            await self.push_frame(frame, direction)
            return

        if isinstance(frame, InputTransportMessageFrame):
            handled = await self.handle_input_message(frame)
            if handled:
                return

        if isinstance(frame, LLMContextFrame):
            handled = await self.handle_context_frame(frame)
            if handled:
                return

        if isinstance(frame, EndFrame):
            await self.close_exam_graph()

        await self.push_frame(frame, direction)

    async def handle_input_message(self, frame: InputTransportMessageFrame) -> bool:
        message = frame.message or {}
        event_type = str(message.get("type") or message.get("message") or "").strip()

        if event_type == "finish":
            await self.ensure_exam_graph()
            if self.exam_graph is not None:
                await self.exam_graph.handle_message({"type": "finish"})
            return True

        if self.pending_final_review is not None:
            return True

        if event_type in {"speech_start", "speech_active"}:
            await self.handle_speech_activity()
            return True

        if event_type == "speech_end":
            return True

        if event_type in {"user-stt", "answer_chunk"}:
            data = message.get("data") if isinstance(message.get("data"), Mapping) else {}
            text = str(data.get("text") or message.get("text") or "")
            source = "stt" if event_type == "user-stt" else "message"
            await self.receive_answer_delta(text, finished=False, source=source)
            return True

        if event_type == "answer_end":
            data = message.get("data") if isinstance(message.get("data"), Mapping) else {}
            text = str(data.get("text") or message.get("text") or "")
            await self.handle_answer_finish_confirmation(True, text=text)
            return True

        if event_type == "answer_finish_confirmation":
            data = message.get("data") if isinstance(message.get("data"), Mapping) else {}
            finished = self.parse_bool(
                data.get("finished", message.get("finished")),
                default=False,
            )
            await self.handle_answer_finish_confirmation(finished)
            return True

        if event_type in {"answer_text", "user-text"}:
            data = message.get("data") if isinstance(message.get("data"), Mapping) else {}
            text = str(data.get("text") or message.get("text") or "")
            await self.receive_answer_delta(text, finished=False, source="message")
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
        if not text.strip():
            return True
        if self.exam_graph is None or self.startup_failed or not self.current_question_id:
            logger.info("Ignoring C-mode context answer before active question")
            return True
        await self.receive_answer_delta(text, finished=False, source="stt")
        return True

    async def handle_speech_activity(self) -> None:
        if self.pending_final_review is not None or self.final_review_ready:
            return
        async with self.lock:
            if self.exam_graph is None or self.exam_graph.closed:
                return
            if self.answer_finished or not self.current_question_id:
                return
            if not self.answer_started_at:
                self.answer_started_at = monotonic()
            self.answer_finish_confirming = False
            if self.has_answer_text:
                self.reset_answer_idle_timer()

    async def receive_answer_delta(self, text: str, finished: bool, source: str = "message") -> None:
        if self.pending_final_review is not None or self.final_review_ready:
            return
        await self.ensure_exam_graph()
        if self.exam_graph is None or self.startup_failed:
            return
        async with self.lock:
            if not self.current_question_id:
                await self.push_exam_event(
                    {
                        "type": "error",
                        "error": "NO_ACTIVE_QUESTION",
                    }
                )
                return
            if self.answer_finished:
                logger.info(
                    "Ignoring C-mode answer event after answer finished, question_id={}",
                    self.current_question_id,
                )
                return

            if text:
                self.answer_buffer += text
                if text.strip():
                    if not self.answer_started_at:
                        self.answer_started_at = monotonic()
                    self.has_answer_text = True
                    self.answer_finish_confirming = False
                    if source == "stt":
                        print(f"C-mode answer buffer: {self.answer_buffer}", flush=True)

            if finished:
                await self.finish_buffered_answer_locked(reason="answer_end")
                return

            if not text.strip():
                return
            self.reset_answer_idle_timer()
            await self.send_ready_answer_chunk_locked()

    async def send_ready_answer_chunk_locked(
        self,
        force: bool = False,
        reason: str = "answer_chunk",
    ) -> None:
        if self.exam_graph is None or self.exam_graph.closed:
            return
        if self.answer_finished:
            return
        buffer_text = self.answer_buffer.strip()
        if not buffer_text:
            return

        now = monotonic()
        if force:
            send_text = buffer_text
            self.answer_buffer = ""
        else:
            last_sent_or_started = self.last_chunk_sent_at or self.answer_started_at or self.question_started_at or now
            if now - last_sent_or_started < self.min_chunk_interval_secs:
                return
            send_text = buffer_text
            self.answer_buffer = ""

        self.last_chunk_sent_at = now
        await self.exam_graph.handle_message(
            {
                "type": "answer_chunk",
                "question_id": self.current_question_id,
                "text": send_text,
                "reason": reason,
            }
        )

    async def handle_answer_finish_confirmation(
        self,
        finished: bool,
        text: str = "",
    ) -> None:
        await self.ensure_exam_graph()
        if self.exam_graph is None or self.startup_failed:
            return
        async with self.lock:
            if not self.current_question_id:
                await self.push_exam_event(
                    {
                        "type": "error",
                        "error": "NO_ACTIVE_QUESTION",
                    }
                )
                return
            if self.answer_finished:
                logger.info(
                    "Ignoring C-mode answer confirmation after answer finished, question_id={}",
                    self.current_question_id,
                )
                return
            if not self.answer_finish_confirming:
                logger.info(
                    "Ignoring C-mode answer confirmation before idle prompt, question_id={}",
                    self.current_question_id,
                )
                return
            if text:
                self.answer_buffer += text
                if text.strip():
                    self.has_answer_text = True
            if finished:
                await self.finish_buffered_answer_locked(reason="finish_confirmation_done")
                return

            self.answer_finish_confirming = False
            await self.send_ready_answer_chunk_locked(
                force=True,
                reason="finish_confirmation_continue",
            )
            self.reset_answer_idle_timer()

    async def finish_buffered_answer_locked(self, reason: str) -> None:
        if self.exam_graph is None or self.exam_graph.closed:
            return
        self.cancel_answer_idle_timer()
        text = self.answer_buffer.strip()
        if not text and not self.has_answer_text:
            await self.push_exam_event(
                {
                    "type": "error",
                    "error": "ANSWER_TEXT_REQUIRED",
                    "question_id": self.current_question_id,
                }
            )
            return
        self.answer_buffer = ""
        self.answer_finished = True
        self.answer_finish_confirming = False
        await self.exam_graph.handle_message(
            {
                "type": "answer_end",
                "question_id": self.current_question_id,
                "text": text,
                "reason": reason,
            }
        )

    def reset_answer_idle_timer(self) -> None:
        self.cancel_answer_idle_timer()
        if self.answer_finished or not self.has_answer_text:
            return
        question_id = self.current_question_id
        if not question_id:
            return
        self.answer_idle_task = asyncio.create_task(
            self.finish_answer_after_idle(question_id)
        )

    def cancel_answer_idle_timer(self) -> None:
        task = self.answer_idle_task
        self.answer_idle_task = None
        if task and not task.done() and task is not asyncio.current_task():
            task.cancel()

    def reset_answer_state(self) -> None:
        self.cancel_answer_idle_timer()
        self.answer_buffer = ""
        self.question_started_at = monotonic()
        self.answer_started_at = 0.0
        self.last_chunk_sent_at = 0.0
        self.answer_finished = False
        self.has_answer_text = False
        self.answer_finish_confirming = False

    async def finish_answer_after_idle(self, question_id: str) -> None:
        try:
            await asyncio.sleep(self.answer_idle_timeout_secs)
            async with self.lock:
                print("test,89898", self.answer_finished, self.answer_finish_confirming)
                if self.exam_graph is None or self.exam_graph.closed:
                    return
                if self.answer_finished:
                    return
                if question_id != self.current_question_id:
                    return
                if self.answer_finish_confirming:
                    return
                self.answer_finish_confirming = True
                await self.push_exam_event(
                    {
                        "type": "answer_finish_confirmation_required",
                        "question_id": question_id,
                        "idle_timeout_secs": self.answer_idle_timeout_secs,
                        "message": "你是否已经回答完毕？",
                    }
                )
                logger.info(
                    "C-mode answer finish confirmation requested after {}s idle, question_id={}",
                    self.answer_idle_timeout_secs,
                    question_id,
                )
        except asyncio.CancelledError:
            pass
        except Exception:
            logger.error(
                "C-mode answer idle timeout failed, question_id={}",
                question_id,
            )
        finally:
            if self.answer_idle_task is asyncio.current_task():
                self.answer_idle_task = None

    async def ensure_exam_graph(self) -> None:
        async with self.exam_graph_init_lock:
            if self.exam_graph is not None or self.startup_failed or self.final_review_ready:
                return
            try:
                if not self.exam_id:
                    raise RuntimeError("EXAM_ID_REQUIRED")
                exam_session = await get_exam_session_by_exam_id(self.exam_id)
                if not exam_session:
                    raise RuntimeError("EXAM_SESSION_NOT_FOUND")
                course_id = str(exam_session.get("course_id") or "").strip()
                exam_item_id = str(exam_session.get("exam_item_id") or "").strip()
                user_id = str(exam_session.get("user_id") or "").strip()
                if not course_id or not exam_item_id or not user_id:
                    raise RuntimeError("EXAM_SESSION_CONTEXT_INCOMPLETE")
                self.exam_user_id = user_id
                saved_review = await get_final_review(self.exam_id)
                if saved_review is not None:
                    self.final_review_ready = True
                    await self.push_exam_event({"type": "final_review_ready", "exam_id": self.exam_id})
                    terminal = "finished" if saved_review["review"].get("status") == "finished" else "closed"
                    await self.handle_exam_event({"type": terminal, "exam_id": self.exam_id})
                    return
                root_questions = await list_ai_preset_questions_by_exam_item_and_user(
                    course_id,
                    exam_item_id,
                    user_id,
                )
                if not root_questions:
                    root_questions = await list_preset_questions_by_exam_item(
                        course_id,
                        exam_item_id,
                        user_id=user_id,
                    )
                if not root_questions:
                    raise RuntimeError("C_MODE_PRESET_QUESTIONS_REQUIRED")
                judge_config = await get_exam_judge_config_by_exam_id(self.exam_id)
                model_settings = self.extract_model_settings(judge_config)
                self.final_review_service = CFinalReviewService(model_settings)
                flow = ExamCFlow(
                    QAmanagerC(root_questions),
                    model_settings=model_settings,
                    exam_id=self.exam_id,
                    user_id=user_id,
                    exam_item_id=exam_item_id,
                )
                self.exam_graph = ExamMessageSession(flow=flow, exam_id=self.exam_id)
                await self.exam_graph.start()
                self.exam_output_task = asyncio.create_task(self.consume_exam_output())
                await self.exam_graph.handle_message({"type": "start"})
            except Exception as exc:
                self.startup_failed = True
                logger.error("C-mode InterviewServiceC startup failed")
                await self.push_exam_event(
                    {
                        "type": "error",
                        "error": "C_MODE_STARTUP_FAILED",
                        "detail": str(exc),
                    }
                )
                message = "C模式口试启动失败，请联系老师或稍后重试。"
                await self.speak_exam_text(message)
                await self.push_frame(EndFrame(), FrameDirection.DOWNSTREAM)

    async def consume_exam_output(self) -> None:
        try:
            while self.exam_graph is not None:
                event = await self.exam_graph.output_queue.get()
                await self.handle_exam_event(dict(event or {}))
                if self._event_type(event) in {"closed", "finished"}:
                    return
        except asyncio.CancelledError:
            pass
        except Exception:
            logger.error("C-mode output consumer failed")

    async def handle_exam_event(self, event: Dict[str, Any]) -> None:
        event_type = self._event_type(event)
        if event_type == "final_review":
            self.publish_final_review(event)
            return
        if event_type in {"finished", "closed"}:
            if self.terminal_sent:
                return
            self.terminal_sent = True
        await self.push_exam_event(event)
        if event_type == "question":
            question = event.get("question") if isinstance(event.get("question"), Mapping) else {}
            self.current_question_id = str(question.get("question_id") or "")
            self.reset_answer_state()
            await self.speak_question(question)
            return
        if event_type == "generated_question_ready":
            question = event.get("question") if isinstance(event.get("question"), Mapping) else {}
            self.current_question_id = str(question.get("question_id") or "")
            self.reset_answer_state()
            self._cancel(self.followup_poll_task)
            self.followup_poll_task = None
            await self.speak_question(question)
            return
        if event_type in {"followup_generation_started", "followup_generation_pending"}:
            self.start_followup_polling()
            return
        if event_type in {"followup_generation_error", "question_probe_completed", "closed", "finished"}:
            self._cancel(self.followup_poll_task)
            self.followup_poll_task = None
        if event_type in {"closed", "finished"}:
            await self.push_frame(EndFrame(), FrameDirection.DOWNSTREAM)
            if self.exam_output_task is not asyncio.current_task():
                self._cancel(self.exam_output_task)

    def publish_final_review(self, event):
        if self.final_review_ready:
            return
        if self.final_review_task is not None and not self.final_review_task.done():
            return
        self.pending_final_review = event
        self.cancel_answer_idle_timer()
        self._cancel(self.followup_poll_task)
        self.followup_poll_task = None
        self.final_review_task = asyncio.create_task(
            self.generate_final_review_background(dict(event or {}))
        )
        self.final_review_task.add_done_callback(self.final_review_task_done)

    async def generate_final_review_background(self, event):
        try:
            review = event["review"]
            if self.final_review_service is None:
                raise RuntimeError("FINAL_REVIEW_SERVICE_NOT_CONFIGURED")
            await self.final_review_service.finalize(
                self.exam_id,
                self.exam_user_id,
                review,
            )
            self.final_review_ready = True
        except Exception:
            logger.error("C-mode final review background generation or persistence failed")

    def final_review_task_done(self, task):
        if self.final_review_task is task:
            self.final_review_task = None

    def start_followup_polling(self) -> None:
        if self.followup_poll_task and not self.followup_poll_task.done():
            return
        self.followup_poll_task = asyncio.create_task(self.poll_generated_question())

    async def poll_generated_question(self) -> None:
        try:
            while self.exam_graph is not None and not self.exam_graph.closed:
                await asyncio.sleep(0.5)
                await self.exam_graph.handle_message({"type": "poll_generated_question"})
        except asyncio.CancelledError:
            pass
        except Exception:
            logger.error("C-mode generated-question polling failed")

    async def push_exam_event(self, event: Mapping[str, Any]) -> None:
        event = self.public_exam_event(event)
        if event is None:
            return
        await self.push_frame(
            OutputTransportMessageFrame(
                message={"type": "c-exam-event", "data": dict(event or {})}
            ),
            FrameDirection.DOWNSTREAM,
        )

    @staticmethod
    def public_exam_event(event):
        event_type = str(event.get("type") or "")
        if event_type in {"final_review", "judgement_update", "judgement_error", "probe_score_update",
                          "followup_prefetch_started", "followup_generation_started"}:
            return None
        fields = {
            "question": ("type", "question"),
            "generated_question_ready": ("type", "question", "source_question_id"),
            "root_question_completed": ("type", "root_question_id", "question_id"),
            "question_probe_completed": ("type", "question_id"),
            "final_review_ready": ("type", "exam_id"),
            "final_review_error": ("type", "exam_id", "error", "retryable"),
            "finished": ("type", "exam_completed", "exam_id"),
            "closed": ("type", "exam_completed", "exam_id"),
        }
        private_fields = {"result", "judgement", "probe_score", "probe_score_history", "score_delta",
                          "selection_reason", "followup_kind", "candidate_label", "generation_focus",
                          "answer_signals", "exam_record_path", "html", "review"}
        public = {key: event[key] for key in fields.get(event_type, event.keys())
                  if key in event and key not in private_fields}
        if isinstance(public.get("question"), Mapping):
            public["question"] = {key: value for key, value in public["question"].items() if key in {
                "question_id", "root_question_id", "parent_question_id", "content", "question_content",
                "question_blocks", "code_fragments", "chain_depth", "root_index",
            }}
        return public

    async def speak_exam_text(self, text: str) -> None:
        text = str(text or "").strip()
        if not text:
            return
        await self.push_frame(LLMTextFrame(escape(text)), FrameDirection.DOWNSTREAM)
        await self.push_frame(TTSSpeakFrame(text), FrameDirection.DOWNSTREAM)

    async def speak_question(self, question: Mapping[str, Any]) -> None:
        speech_content = self.render_question_speech(question)
        if speech_content:
            await self.push_frame(TTSSpeakFrame(speech_content), FrameDirection.DOWNSTREAM)
        await self.stream_question_display(question)

    async def stream_question_display(self, question: Mapping[str, Any]) -> None:
        content = self.get_question_content(question)
        code_fragments = self.get_question_code_fragments(question)
        question_blocks = self.get_question_blocks(question) or [
            {"type": "text", "content": content}
        ]
        fragments = {
            str(fragment.get("id", "")): fragment
            for fragment in code_fragments
            if isinstance(fragment, dict) and fragment.get("id") is not None
        }
        sent_any = False
        for block in question_blocks:
            if not isinstance(block, dict):
                continue
            block_type = str(block.get("type", "")).strip()
            if block_type == "text":
                block_content = str(block.get("content", "")).strip()
                if not block_content:
                    continue
                await self.push_question_display_separator(sent_any)
                await self.stream_question_text_block(block_content)
                sent_any = True
                continue
            if block_type == "code":
                fragment = fragments.get(str(block.get("fragment_id", "")))
                if not fragment:
                    continue
                await self.push_question_display_separator(sent_any)
                await self.push_frame(
                    LLMTextFrame(self.render_code_fragment_html(fragment)),
                    FrameDirection.DOWNSTREAM,
                )
                sent_any = True

        if not sent_any and content:
            await self.stream_question_text_block(content)

    async def stream_question_text_block(self, text: str) -> None:
        await self.push_frame(LLMTextFrame("<p>"), FrameDirection.DOWNSTREAM)
        start_time = time.time()
        for chunk in self.iter_question_display_chunks(text):
            await self.push_frame(LLMTextFrame(escape(chunk)), FrameDirection.DOWNSTREAM)
            print(f"sent chunk: {chunk} in time: {time.time() - start_time}")
            await self.sleep_question_display(chunk)
        await self.push_frame(LLMTextFrame("</p>"), FrameDirection.DOWNSTREAM)

    async def push_question_display_separator(self, sent_any: bool) -> None:
        if sent_any:
            await self.push_frame(LLMTextFrame("\n"), FrameDirection.DOWNSTREAM)

    async def sleep_question_display(self, text: str) -> None:
        visible_chars = len(str(text or "").strip())
        if visible_chars <= 0:
            return
        await asyncio.sleep(visible_chars / 20.0)

    @staticmethod
    def iter_question_display_chunks(text: str):
        chunk_chars = 4
        text = str(text or "")
        for index in range(0, len(text), chunk_chars):
            yield text[index : index + chunk_chars]

    def render_question_html(self, question) -> str:
        content = self.get_question_content(question)
        code_fragments = self.get_question_code_fragments(question)
        question_blocks = self.get_question_blocks(question) or [
            {"type": "text", "content": content}
        ]
        fragments = {
            str(fragment.get("id", "")): fragment
            for fragment in code_fragments
            if isinstance(fragment, dict) and fragment.get("id") is not None
        }
        parts = []
        for block in question_blocks:
            if not isinstance(block, dict):
                continue
            block_type = str(block.get("type", "")).strip()
            if block_type == "text":
                block_content = str(block.get("content", "")).strip()
                if block_content:
                    parts.append(f"<p>{escape(block_content)}</p>")
            elif block_type == "code":
                fragment = fragments.get(str(block.get("fragment_id", "")))
                if fragment:
                    parts.append(self.render_code_fragment_html(fragment))

        if not parts and content:
            parts.append(f"<p>{escape(content)}</p>")
        return "\n".join(parts)

    def render_question_speech(self, question) -> str:
        content = self.get_question_content(question)
        question_blocks = self.get_question_blocks(question) or [
            {"type": "text", "content": content}
        ]
        texts = []
        for block in question_blocks:
            if not isinstance(block, dict):
                continue
            if str(block.get("type", "")).strip() != "text":
                continue
            block_content = str(block.get("content", "")).strip()
            if block_content:
                texts.append(block_content)
        if not texts and content:
            texts.append(content)
        return "\n".join(texts)

    def render_code_fragment_html(self, fragment: Dict[str, object]) -> str:
        relative_path = str(fragment.get("relative_path", "")).strip()
        start_line = fragment.get("start_line", "")
        end_line = fragment.get("end_line", "")
        language = str(fragment.get("language") or self.guess_language(relative_path) or "text")
        source = self.format_fragment_source(relative_path, start_line, end_line)
        title = str(fragment.get("title") or source or relative_path or "code")
        code = self.get_fragment_code(fragment)
        language_attr = escape(language, quote=True)
        source_attr = escape(source, quote=True)
        return (
            f'<figure class="code-fragment" data-language="{language_attr}" '
            f'data-source="{source_attr}">'
            f"<figcaption>{escape(title)}</figcaption>"
            f'<pre><code class="language-{language_attr}">{escape(code)}</code></pre>'
            f"</figure>"
        )

    @staticmethod
    def get_question_content(question) -> str:
        if isinstance(question, dict):
            return str(
                question.get("question_content")
                or question.get("content")
                or question.get("question")
                or ""
            )
        return str(
            getattr(question, "content", None)
            or getattr(question, "question_content", None)
            or question
            or ""
        )

    @staticmethod
    def get_question_blocks(question) -> list[Dict[str, object]]:
        if isinstance(question, dict):
            blocks = question.get("question_blocks") or []
        else:
            blocks = getattr(question, "question_blocks", []) or []
        return blocks if isinstance(blocks, list) else []

    @staticmethod
    def get_question_code_fragments(question) -> list[Dict[str, object]]:
        if isinstance(question, dict):
            fragments = question.get("code_fragments") or []
        else:
            fragments = getattr(question, "code_fragments", []) or []
        return fragments if isinstance(fragments, list) else []

    @staticmethod
    def get_fragment_code(fragment: Dict[str, object]) -> str:
        return str(
            fragment.get("code")
            or fragment.get("content")
            or fragment.get("text")
            or ""
        )

    @staticmethod
    def format_fragment_source(relative_path: str, start_line, end_line) -> str:
        source = str(relative_path or "").strip()
        start = str(start_line or "").strip()
        end = str(end_line or "").strip()
        if start and end:
            return f"{source}:{start}-{end}" if source else f"{start}-{end}"
        if start:
            return f"{source}:{start}" if source else start
        return source

    @staticmethod
    def guess_language(relative_path: str) -> str:
        suffix = Path(str(relative_path or "")).suffix.lower().lstrip(".")
        return {
            "py": "python",
            "js": "javascript",
            "ts": "typescript",
            "tsx": "tsx",
            "jsx": "jsx",
            "java": "java",
            "cpp": "cpp",
            "cc": "cpp",
            "cxx": "cpp",
            "c": "c",
            "h": "c",
            "hpp": "cpp",
            "rs": "rust",
            "go": "go",
            "php": "php",
            "rb": "ruby",
            "cs": "csharp",
            "sql": "sql",
            "json": "json",
            "md": "markdown",
            "yaml": "yaml",
            "yml": "yaml",
            "html": "html",
            "css": "css",
        }.get(suffix, suffix or "text")

    async def close_exam_graph(self) -> None:
        self.cancel_answer_idle_timer()
        self._cancel(self.followup_poll_task)
        self.followup_poll_task = None
        session = self.exam_graph
        if session is not None:
            try:
                if not session.closed:
                    await session.handle_message({"type": "stop"})
                    await asyncio.sleep(0)
                await session.aclose()
            except Exception:
                logger.error("C-mode session close failed")
        self._cancel(self.exam_output_task)
        self.exam_output_task = None
        self.exam_graph = None

    @staticmethod
    def extract_model_settings(judge_config: Any) -> Dict[str, Any]:
        if not isinstance(judge_config, Mapping):
            return {}
        for role in ("report_judger", "main_judger", "setter"):
            agent_config = judge_config.get(role)
            if not isinstance(agent_config, Mapping):
                continue
            runtime = agent_config.get("runtime_model_settings")
            if isinstance(runtime, Mapping) and runtime.get("model_name"):
                return dict(runtime)
        return {}

    @classmethod
    def last_sentence_end_index(cls, text: str) -> int:
        return max((str(text or "").rfind(mark) for mark in cls.SENTENCE_END_MARKS), default=-1)

    @staticmethod
    def parse_bool(value: Any, default: bool = False) -> bool:
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

    @staticmethod
    def _question_text(question: Mapping[str, Any]) -> str:
        for key in ("content", "question_content", "question", "text"):
            value = question.get(key) if isinstance(question, Mapping) else None
            if isinstance(value, list):
                text = "\n".join(str(item) for item in value).strip()
                if text:
                    return text
            if value:
                return str(value).strip()
        return ""

    @staticmethod
    def _event_type(event: Mapping[str, Any]) -> str:
        return str((event or {}).get("type") or "").strip().lower()

    @staticmethod
    def _cancel(task):
        if task and not task.done():
            task.cancel()


VoiceLogger = InterviewServiceC
