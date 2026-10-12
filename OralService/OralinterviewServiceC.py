import asyncio
import math
from html import escape
from typing import Any, Dict, Mapping, Optional

from loguru import logger
from pipecat.frames.frames import (
    EndFrame,
    Frame,
    InputTransportMessageFrame,
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
    get_exam_item_by_id,
    list_ai_preset_questions_by_exam_item_and_user,
    list_preset_questions_by_exam_item,
)
from AIOralExamSystem.Exam.QAmanager import QAmanager
from AIOralExamSystem.Graph.ExamC import ExamCFlow
from AIOralExamSystem.message.session import ExamMessageSession
from AIOralExamSystem.Exam.CFinalReview import CFinalReviewService
from AIOralExamSystem.Exam.Examdata.final_review_repository import get_final_review
from AIOralExamSystem.Exam.Examdata.c_question_repository import save_c_exam_questions
from AIOralExamSystem.Exam.Examdata.c_score_repository import initialize_c_score_limits
from OralService.CAnswerBoundaryProcessor import CAnswerEventFrame

class InterviewServiceC(FrameProcessor):
    """Run the C-mode exam session and publish its output."""

    SENTENCE_END_MARKS = ("。", ".")

    def __init__(
        self,
        exam_id: Optional[str] = None,
        startup_error: Optional[Dict[str, object]] = None,
        mode: str = "C",
        **_: Any,
    ):
        super().__init__()
        if mode not in {"B", "C"}:
            raise ValueError("EXAM_MODE_INVALID")
        self.mode = mode
        self.exam_id = str(exam_id or "").strip()
        self.startup_error = startup_error
        self.exam_graph_init_lock = asyncio.Lock()
        self.exam_graph: Optional[ExamMessageSession] = None
        self.exam_output_task: Optional[asyncio.Task] = None
        self.followup_poll_task: Optional[asyncio.Task] = None
        self.current_question_id = ""
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
            if self.startup_error:
                await self.push_frame(frame, direction)
                self.startup_failed = True
                message = str(
                    self.startup_error.get("message")
                    or "考试启动失败，请联系老师或稍后重试。"
                )
                await self.push_exam_event(
                    {
                        "type": "error",
                        "error": str(self.startup_error.get("code") or "EXAM_STARTUP_ERROR"),
                        "detail": message,
                    }
                )
                await self.push_frame(LLMTextFrame(escape(message)), FrameDirection.DOWNSTREAM)
                await self.push_frame(EndFrame(), FrameDirection.DOWNSTREAM)
                return
            await self.ensure_exam_graph()
            await self.push_frame(frame, direction)
            return

        if isinstance(frame, InputTransportMessageFrame):
            handled = await self.handle_input_message(frame)
            if handled:
                return

        if isinstance(frame, CAnswerEventFrame):
            await self.handle_answer_event(frame)
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
        return False

    async def handle_answer_event(self, frame: CAnswerEventFrame) -> None:
        if self.startup_failed or self.pending_final_review is not None or self.final_review_ready:
            return
        if frame.kind == "error":
            await self.push_exam_event({
                "type": "error", "error": frame.error, "question_id": frame.question_id,
            })
            return
        if self.exam_graph is None or self.exam_graph.closed:
            return
        if not frame.question_id or frame.question_id != self.current_question_id:
            return
        if frame.kind in {"answer_chunk", "answer_end"}:
            await self.exam_graph.handle_message({
                "type": frame.kind,
                "question_id": frame.question_id,
                "text": frame.text,
                "reason": frame.reason,
            })
        elif frame.kind == "confirmation_required":
            await self.push_exam_event({
                "type": "answer_finish_confirmation_required",
                "question_id": frame.question_id,
                "idle_timeout_secs": frame.idle_timeout_secs,
                "message": "你是否已经回答完毕？",
            })

    async def send_answer_control(self, kind: str, question_id: str = "") -> None:
        ack = asyncio.get_running_loop().create_future()
        await self.push_frame(
            CAnswerEventFrame(kind=kind, question_id=question_id, ack=ack),
            FrameDirection.UPSTREAM,
        )
        await asyncio.wait_for(ack, timeout=10.0)

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
                if self.mode == "B":
                    root_questions = await list_preset_questions_by_exam_item(
                        course_id,
                        exam_item_id,
                    )
                    for question in root_questions:
                        try:
                            score = float(question.get("score") or 0)
                        except (TypeError, ValueError) as exc:
                            raise RuntimeError("B_MODE_PRESET_QUESTION_INVALID") from exc
                        if (
                            not str(question.get("question_dimension") or "").strip()
                            or not str(question.get("question_content") or "").strip()
                            or not str(question.get("standard_answer") or "").strip()
                            or not math.isfinite(score) or score <= 0
                        ):
                            raise RuntimeError("B_MODE_PRESET_QUESTION_INVALID")
                else:
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
                    raise RuntimeError(f"{self.mode}_MODE_PRESET_QUESTIONS_REQUIRED")
                exam_item = await get_exam_item_by_id(exam_item_id)
                if not exam_item:
                    raise RuntimeError("EXAM_ITEM_NOT_FOUND")
                max_probe_turns = (
                    2 if self.mode == "B" else await initialize_c_score_limits(
                        self.exam_id, user_id, int(exam_item.get("c_max_probe_turns") or 2),
                        len(root_questions),
                    )
                )
                judge_config = await get_exam_judge_config_by_exam_id(self.exam_id)
                model_settings = self.extract_model_settings(judge_config, self.mode)
                review_settings = (
                    self.extract_role_model_settings(judge_config, "main_judger")
                    if self.mode == "B" else model_settings
                )
                if not model_settings.get("model_name") or not review_settings.get("model_name"):
                    raise RuntimeError(f"{self.mode}_MODE_MODEL_CONFIG_REQUIRED")
                self.final_review_service = CFinalReviewService(review_settings, mode=self.mode)
                if self.mode == "B":
                    from AIOralExamSystem.Graph.ExamB import ExamBFlow

                    flow_class = ExamBFlow
                else:
                    flow_class = ExamCFlow
                flow_kwargs = dict(
                    model_settings=model_settings,
                    exam_id=self.exam_id,
                    user_id=user_id,
                    exam_item_id=exam_item_id,
                    max_probe_turns=max_probe_turns,
                )
                if self.mode == "B":
                    flow_kwargs["mode"] = "B"
                flow = flow_class(QAmanager(root_questions), **flow_kwargs)
                self.exam_graph = ExamMessageSession(flow=flow, exam_id=self.exam_id)
                await self.exam_graph.start()
                self.exam_output_task = asyncio.create_task(self.consume_exam_output())
                await self.exam_graph.handle_message({"type": "start"})
            except Exception as exc:
                self.startup_failed = True
                logger.error(f"{self.mode}-mode interview startup failed")
                await self.push_exam_event(
                    {
                        "type": "error",
                        "error": f"{self.mode}_MODE_STARTUP_FAILED",
                        "detail": str(exc),
                    }
                )
                message = f"{self.mode}模式口试启动失败，请联系老师或稍后重试。"
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
            await self.publish_final_review(event)
            return
        if event_type in {"finished", "closed"}:
            if self.terminal_sent:
                return
            self.terminal_sent = True
            await self.send_answer_control("stop")
        if event_type == "question_evaluation_error":
            await self.send_answer_control(
                "retry_confirmation", str(event.get("question_id") or ""),
            )
        if event_type in {"question", "generated_question_ready"}:
            question = event.get("question") if isinstance(event.get("question"), Mapping) else {}
            self.current_question_id = str(question.get("question_id") or "")
            await self.send_answer_control("question_start", self.current_question_id)
        await self.push_exam_event(event)
        if event_type == "question_evaluation_error":
            return
        if event_type == "question":
            await self.speak_question(question)
            return
        if event_type == "generated_question_ready":
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

    async def publish_final_review(self, event):
        if self.final_review_ready:
            return
        if self.final_review_task is not None and not self.final_review_task.done():
            return
        await self.send_answer_control("stop")
        await save_c_exam_questions(
            self.exam_id, self.exam_user_id, event["review"]
        )
        self.pending_final_review = event
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
                message={"type": "c-exam-event", "mode": self.mode, "data": dict(event or {})}
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
                "question_dimension", "score",
            }}
            if public["question"].get("parent_question_id"):
                public["question"].pop("question_dimension", None)
                public["question"].pop("score", None)
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
        logger.info("开始流式输出")
        try:
            await self.stream_question_display(question)
        finally:
            logger.info("结束流式输出")

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
        for chunk in self.iter_question_display_chunks(text):
            await self.push_frame(LLMTextFrame(escape(chunk)), FrameDirection.DOWNSTREAM)
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
        await self.send_answer_control("stop")
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
    def extract_model_settings(judge_config: Any, mode: str = "C") -> Dict[str, Any]:
        if mode == "B":
            if isinstance(judge_config, Mapping):
                scorers = judge_config.get("scorers")
                if isinstance(scorers, list) and scorers:
                    return InterviewServiceC.extract_role_model_settings(
                        {"scorer": scorers[0]}, "scorer"
                    )
            return {}
        return InterviewServiceC.extract_role_model_settings(judge_config, "report_judger")

    @staticmethod
    def extract_role_model_settings(judge_config: Any, role: str) -> Dict[str, Any]:
        if not isinstance(judge_config, Mapping):
            return {}
        agent_config = judge_config.get(role)
        if isinstance(agent_config, Mapping):
            runtime = agent_config.get("runtime_model_settings")
            if isinstance(runtime, Mapping) and runtime.get("model_name"):
                return dict(runtime)
        return {}

    @classmethod
    def last_sentence_end_index(cls, text: str) -> int:
        return max((str(text or "").rfind(mark) for mark in cls.SENTENCE_END_MARKS), default=-1)

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
