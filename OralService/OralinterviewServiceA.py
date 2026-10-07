import asyncio
import json
from html import escape
from typing import Any, Dict, List, Optional

from pipecat.frames.frames import (
    EndFrame,
    Frame,
    LLMContextFrame,
    LLMTextFrame,
    OutputTransportMessageFrame,
    StartFrame,
    TTSSpeakFrame,
)
from pipecat.processors.frame_processor import FrameDirection, FrameProcessor

from AIOralExamSystem.Exam.Examdata import list_preset_questions_by_exam_item
from AIOralExamSystem.Exam.Judger import MainJudgerAgent
from AIOralExamSystem.Exam.OutputSetting import build_final_review_output
from AIOralExamSystem.Exam.QAmanagerA import QAmanagerA
from AIOralExamSystem.Graph.ExamA import ExamAFlow
from AIOralExamSystem.message.session import ExamMessageSession


def get_current_user_id(current_user: dict) -> Optional[str]:
    user_id = (
        current_user.get("uuid")
        or current_user.get("id")
        or current_user.get("user_id")
    )
    return str(user_id).strip() if user_id is not None and str(user_id).strip() else None


def build_question_from_preset_item(
    item: Dict[str, object],
    question_id: str,
    is_preset_question: bool = True,
) -> Optional[Dict[str, object]]:
    content = str(item.get("question_content", "")).strip()
    dimension = str(item.get("question_dimension", "")).strip()
    if not content or not dimension:
        return None

    question_blocks = parse_json_list_field(
        item.get("question_blocks"),
        item.get("question_blocks_json"),
    )
    difficulty = extract_question_difficulty(item, question_blocks)
    return {
        "question_id": question_id,
        "question_dimension": dimension,
        "question_content": content,
        "content": content,
        "difficulty": difficulty,
        "question_blocks": question_blocks,
        "code_fragments": parse_json_list_field(
            item.get("code_fragments"),
            item.get("code_fragments_json"),
        ),
        "standard_answer": item.get("standard_answer"),
        "score": float(item.get("score", 1.0)),
        "sort_order": item.get("sort_order") or 0,
        "is_preset_question": is_preset_question,
    }


def parse_json_list_field(*values: object) -> List[object]:
    for value in values:
        if isinstance(value, list):
            if value:
                return value
            continue
        if isinstance(value, bytes):
            value = value.decode("utf-8")
        if isinstance(value, str) and value.strip():
            try:
                parsed = json.loads(value)
            except json.JSONDecodeError:
                continue
            if isinstance(parsed, list):
                return parsed
    return []


def extract_question_difficulty(
    item: Dict[str, object],
    question_blocks: List[object],
) -> object:
    for block in question_blocks:
        if isinstance(block, dict) and block.get("difficulty"):
            return block["difficulty"]
    return item.get("difficulty") or item.get("difficulty_level") or "implementation"


async def load_prepared_question_bank(
    current_user: dict,
) -> Dict[str, Dict[str, List[Dict[str, object]]]]:
    course_id = current_user.get("course_id")
    exam_item_id = current_user.get("exam_item_id")
    if not course_id or not exam_item_id:
        raise ValueError("考试上下文缺少课程或考试项")

    preset_questions = await list_preset_questions_by_exam_item(
        str(course_id),
        str(exam_item_id),
        user_id=get_current_user_id(current_user),
    )
    question_bank: Dict[str, Dict[str, List[Dict[str, object]]]] = {}
    seen_question_ids: set[str] = set()
    for index, item in enumerate(preset_questions, start=1):
        raw_id = item.get("preset_question_id")
        question_id = str(raw_id or f"Prepared{index}")
        if question_id in seen_question_ids:
            continue
        question = build_question_from_preset_item(
            item,
            question_id=question_id,
            is_preset_question=True,
        )
        if question is None:
            continue
        dimension = str(question["question_dimension"])
        difficulty = str(question["difficulty"])
        question_bank.setdefault(dimension, {}).setdefault(difficulty, []).append(question)
        seen_question_ids.add(question_id)

    return question_bank


class InterviewServiceA(FrameProcessor):
    """A-mode oral exam service backed directly by ExamAFlow."""

    def __init__(
        self,
        current_user: dict,
        history: List[Dict[str, str]] = [],
        qa_manager: Optional[QAmanagerA] = None,
        startup_error: Optional[Dict[str, object]] = None,
    ):
        super().__init__()
        self.current_user = current_user
        self.history = history
        self.qa_manager = qa_manager
        self.startup_error = startup_error
        self.exam_graph: Optional[ExamMessageSession] = None
        self.exam_output_task: Optional[asyncio.Task] = None
        self.current_question_id = ""
        self.current_question_sequence_id: Optional[int] = None
        self.judge_order_id = 0

    async def process_frame(self, frame: Frame, direction: FrameDirection):
        await super().process_frame(frame, direction)

        if isinstance(frame, LLMContextFrame):
            if self.startup_error:
                await self.push_startup_error(direction)
                return
            exam_graph = await self.ensure_exam_graph_or_report(direction)
            if exam_graph is None:
                return
            answer = self.extract_latest_user_answer(frame.context.get_messages())

            self.judge_order_id += 1
            await self.push_frame(LLMTextFrame("AI口试开始思考"), direction)
            output_events = await exam_graph.handle_message(
                {
                    "type": ExamAFlow.EVENT_SUBMIT_FEEDBACK,
                    "question_id": self.current_question_id,
                    "question_sequence_id": self.current_question_sequence_id,
                    "judge_order_id": self.judge_order_id,
                    "answer": answer,
                    "feedback": {"answer": answer},
                }
            )
            await self.push_output_events(output_events, direction)
            return

        if isinstance(frame, StartFrame):
            await self.push_frame(frame, direction)
            if self.startup_error:
                await self.push_startup_error(direction)
                await self.push_frame(EndFrame(), FrameDirection.DOWNSTREAM)
                return
            exam_graph = await self.ensure_exam_graph_or_report(direction)
            if exam_graph is None:
                await self.push_frame(EndFrame(), FrameDirection.DOWNSTREAM)
                return
            output_events = await exam_graph.handle_message(
                {"type": ExamAFlow.EVENT_REQUEST_NEXT}
            )
            await self.push_output_events(output_events, direction)
            return

        if isinstance(frame, EndFrame):
            await self.stop_exam_output_listener()
            if self.exam_graph is not None:
                try:
                    await self.exam_graph.handle_message({"type": ExamAFlow.EVENT_STOP})
                    await self.exam_graph.aclose()
                except Exception:
                    pass

        await self.push_frame(frame, direction)

    async def ensure_exam_graph_or_report(
        self,
        direction: FrameDirection,
    ) -> Optional[ExamMessageSession]:
        try:
            return await self.ensure_exam_graph()
        except Exception as exc:
            self.startup_error = {
                "code": exc.__class__.__name__,
                "message": str(exc) or "考试启动失败，请联系管理员",
            }
            await self.push_startup_error(direction)
            return None

    async def ensure_exam_graph(self) -> ExamMessageSession:
        if self.exam_graph is not None:
            self.start_exam_output_listener()
            return self.exam_graph

        qa_manager = await self.ensure_qa_manager()
        source = self.user_source()
        main_judger = self.build_main_judger(source)
        exam_id = str(self.current_user.get("exam_id") or "").strip()
        exam_item_id = str(self.current_user.get("exam_item_id") or "").strip()
        flow = ExamAFlow(
            qa_manager=qa_manager,
            judge_config=self.current_user["judge_config"],
            main_judger=main_judger,
            exam_id=exam_id,
            user_id=source,
            exam_item_id=exam_item_id,
        )
        self.exam_graph = ExamMessageSession(
            flow=flow,
            exam_id=exam_id or "exam-a",
        )
        await self.exam_graph.start()
        self.start_exam_output_listener()
        return self.exam_graph

    async def ensure_qa_manager(self) -> QAmanagerA:
        if self.qa_manager is not None:
            return self.qa_manager

        question_bank = await load_prepared_question_bank(self.current_user)
        qa_manager = QAmanagerA(question_bank)
        if not qa_manager.has_pending_question:
            raise ValueError("当前考试没有已准备好的题目，请先完成题目准备")
        self.qa_manager = qa_manager
        return self.qa_manager

    def build_main_judger(self, source: str) -> MainJudgerAgent:
        model_settings = self.require_agent_settings("report_judger")
        return MainJudgerAgent(
            model_settings,
            source,
            thinking=True,
            response_format=True,
            temperature=float(model_settings.get("temperature", 0)),
        )

    def require_agent_settings(self, role: str) -> Dict[str, object]:
        return self.current_user["judge_config"][role]["runtime_model_settings"]

    def user_source(self) -> str:
        return str(
            self.current_user.get("uuid")
            or self.current_user.get("id")
            or self.current_user.get("user_id")
        )

    def start_exam_output_listener(self) -> None:
        if self.exam_output_task is not None and not self.exam_output_task.done():
            return
        if self.exam_graph is None:
            return
        if self.exam_graph.closed:
            return
        self.exam_output_task = asyncio.create_task(self.consume_exam_output())

    async def consume_exam_output(self) -> None:
        if self.exam_graph is None:
            return
        output_queue = self.exam_graph.output_queue
        try:
            while True:
                event = await output_queue.get()
                try:
                    await self.push_output_events(
                        [event],
                        FrameDirection.DOWNSTREAM,
                    )
                finally:
                    output_queue.task_done()
                if event.get("type") in {"finished", "closed"}:
                    return
        finally:
            if self.exam_output_task is asyncio.current_task():
                self.exam_output_task = None

    async def stop_exam_output_listener(self) -> None:
        task = self.exam_output_task
        self.exam_output_task = None
        if task is None or task.done() or task is asyncio.current_task():
            return
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

    async def push_startup_error(self, direction: FrameDirection) -> None:
        content = self.render_startup_error(self.startup_error or {})
        speech = str(
            (self.startup_error or {}).get("message")
            or "考试启动失败，请联系老师或稍后重试。"
        )
        await self.push_frame(LLMTextFrame(content), direction)
        await self.push_frame(TTSSpeakFrame(speech), FrameDirection.DOWNSTREAM)

    async def setup(self, setup):
        await super().setup(setup)

    async def push_exam_event(self, event: Dict[str, object]) -> None:
        await self.push_frame(
            OutputTransportMessageFrame(
                message={"type": "a-exam-event", "data": dict(event or {})}
            ),
            FrameDirection.DOWNSTREAM,
        )

    @staticmethod
    def render_startup_error(error: Dict[str, object]) -> str:
        code = escape(str(error.get("code") or "EXAM_STARTUP_ERROR"))
        message = escape(
            str(
                error.get("message")
                or "考试启动失败，请联系老师或稍后重试。"
            )
        )
        return (
            '<div class="exam-error" data-type="exam_error" '
            f'data-code="{code}">'
            "<h3>考试启动失败</h3>"
            f"<p>{message}</p>"
            "</div>"
        )

    async def push_output_events(
        self,
        output_events: List[Dict[str, object]],
        direction: FrameDirection,
    ):
        for event in output_events:
            event_type = event.get("type")
            if event_type == "accepted":
                continue

            await self.push_exam_event(event)

            if event_type == "question":
                question = event.get("question")
                self.current_question_id = str(
                    self.get_question_value(question, "question_id") or ""
                )
                sequence = self.get_question_value(question, "question_sequence_id")
                try:
                    self.current_question_sequence_id = int(sequence) if sequence is not None else None
                except (TypeError, ValueError):
                    self.current_question_sequence_id = None
                display_content = self.render_question_html(question)
                speech_content = self.render_question_speech(question)
                if display_content:
                    await self.push_frame(LLMTextFrame(display_content), direction)
                if speech_content:
                    await self.push_frame(TTSSpeakFrame(speech_content), FrameDirection.DOWNSTREAM)
                continue

            if event_type == "judged":
                await self.push_frame(LLMTextFrame("AI口试结束思考"), direction)
                continue

            if event_type == "waiting":
                reason = str(event.get("reason") or "")
                if reason == "waiting_feedback":
                    content = "请先回答当前问题。"
                    await self.push_frame(LLMTextFrame(content), direction)
                elif reason == "judging_failed":
                    content = "回答判断失败，请稍后再试。"
                    await self.push_frame(LLMTextFrame(content), direction)
                continue

            if event_type == "analysis_error":
                error = str(event.get("error") or "JUDGING_FAILED")
                content = f"回答判断失败：{error}"
                await self.push_frame(LLMTextFrame(escape(content)), direction)
                continue

            if event_type == "final_review":
                review = event.get("review") or {}
                display_content = build_final_review_output(
                    review,
                    output_type="html",
                )
                if display_content:
                    await self.push_frame(LLMTextFrame(display_content), direction)
                continue

            if event_type == "final_review_error":
                error = str(event.get("error") or "FINAL_REVIEW_FAILED")
                content = f"最终总评生成失败：{error}"
                await self.push_frame(LLMTextFrame(escape(content)), direction)
                continue

            if event_type in {"finished", "closed"}:
                content = "本次口试已结束。"
                await self.push_frame(LLMTextFrame(content), direction)
                await self.push_frame(EndFrame(), FrameDirection.DOWNSTREAM)
                continue

            if event_type == "error":
                content = str(event.get("error") or "EXAM_ERROR")
                await self.push_frame(LLMTextFrame(escape(content)), direction)
                continue

            content = str(event.get("content", ""))
            if content:
                await self.push_frame(LLMTextFrame(content), direction)

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
        parts: List[str] = []
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
        texts: List[str] = []
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
    def extract_latest_user_answer(messages) -> str:
        for message in reversed(messages or []):
            role = InterviewServiceA.get_message_value(message, "role") or InterviewServiceA.get_message_value(message, "type")
            if str(role or "").lower() not in ("user", "human"):
                continue
            content = InterviewServiceA.get_message_value(message, "content")
            if isinstance(content, list):
                return "".join(
                    str(item.get("text", item)) if isinstance(item, dict) else str(item)
                    for item in content
                ).strip()
            return str(content or "").strip()
        return ""

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
    def get_question_value(question, key: str):
        if isinstance(question, dict):
            return question.get(key)
        return getattr(question, key, None)

    @staticmethod
    def get_question_blocks(question) -> List[Dict[str, object]]:
        if isinstance(question, dict):
            blocks = question.get("question_blocks") or []
        else:
            blocks = getattr(question, "question_blocks", []) or []
        return blocks if isinstance(blocks, list) else []

    @staticmethod
    def get_question_code_fragments(question) -> List[Dict[str, object]]:
        if isinstance(question, dict):
            fragments = question.get("code_fragments") or []
        else:
            fragments = getattr(question, "code_fragments", []) or []
        return fragments if isinstance(fragments, list) else []

    @staticmethod
    def get_message_value(message, key: str):
        if isinstance(message, dict):
            return message.get(key)
        return getattr(message, key, None)

    @staticmethod
    def format_fragment_source(relative_path: str, start_line, end_line) -> str:
        if relative_path and start_line != "" and end_line != "":
            return f"{relative_path}:{start_line}-{end_line}"
        return relative_path

    @staticmethod
    def get_fragment_code(fragment: Dict[str, object]) -> str:
        lines = fragment.get("lines", [])
        if isinstance(lines, list):
            return "\n".join(str(line) for line in lines)
        if isinstance(lines, str):
            return lines
        content = fragment.get("content", "")
        return str(content) if content is not None else ""

    @staticmethod
    def guess_language(relative_path: str) -> str:
        suffix = relative_path.rsplit(".", 1)[-1].lower() if "." in relative_path else ""
        return {
            "py": "python",
            "js": "javascript",
            "jsx": "javascript",
            "ts": "typescript",
            "tsx": "typescript",
            "java": "java",
            "cpp": "cpp",
            "cc": "cpp",
            "cxx": "cpp",
            "c": "c",
            "h": "c",
            "hpp": "cpp",
            "rs": "rust",
            "go": "go",
            "html": "html",
            "css": "css",
            "json": "json",
            "md": "markdown",
        }.get(suffix, "text")
