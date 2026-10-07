import asyncio
from html import escape
from typing import Any, Dict, List, Optional

from pipecat.frames.frames import EndFrame, Frame, LLMContextFrame, LLMTextFrame, StartFrame, TTSSpeakFrame
from pipecat.processors.frame_processor import FrameDirection, FrameProcessor

from AIOralExamSystem.Exam.OutputSetting import build_final_review_output
from AIOralExamSystem.Exam.QAserverA import QAserverA


class InterviewServiceA(FrameProcessor):
    def __init__(
        self,
        current_user: dict,
        history: List[Dict[str, str]] = [],
        exam_state: Optional[Any] = None,
        startup_error: Optional[Dict[str, object]] = None,
    ):
        super().__init__()
        self.current_user = current_user
        self.history = history
        self.exam_state = exam_state
        self.startup_error = startup_error
        self.nickname = current_user.get("nickname") or current_user.get("username") or "同学"
        self.qa_server: Optional[QAserverA] = None
        self.exam_output_task: Optional[asyncio.Task] = None

    async def process_frame(self, frame: Frame, direction: FrameDirection):
        await super().process_frame(frame, direction)

        if isinstance(frame, LLMContextFrame):
            if self.startup_error:
                await self.push_startup_error(direction)
                return

            qa_server = await self.ensure_qa_server_or_report(direction)
            if qa_server is None:
                return

            answer = self.extract_latest_user_answer(frame.context.get_messages())
            if not answer:
                await self.push_frame(
                    TTSSpeakFrame("我没有听清你的回答，请再说一遍。"),
                    FrameDirection.DOWNSTREAM,
                )
                return

            await self.push_frame(LLMTextFrame("AI口试开始思考"), direction)
            output_events = await qa_server.submit_answer(answer=answer)
            await self.push_output_events(output_events, direction)
            return

        if isinstance(frame, StartFrame):
            await self.push_frame(frame, direction)
            if self.startup_error:
                await self.push_startup_error(direction)
                await self.push_frame(EndFrame(), FrameDirection.DOWNSTREAM)
                return

            qa_server = await self.ensure_qa_server_or_report(direction)
            if qa_server is None:
                await self.push_frame(EndFrame(), FrameDirection.DOWNSTREAM)
                return

            await self.push_frame(
                TTSSpeakFrame(
                    f"你好，{self.nickname}同学，我是本次的考官，现在开始口试。"
                ),
                FrameDirection.DOWNSTREAM,
            )
            output_events = await qa_server.request_next_question()
            await self.push_output_events(output_events, direction)
            return

        if isinstance(frame, EndFrame):
            await self.stop_exam_output_listener()
            if self.qa_server is not None:
                await self.qa_server.stop_exam()
            print("XYPTEST: EndFrame")

        await self.push_frame(frame, direction)

    async def ensure_qa_server_or_report(self, direction: FrameDirection) -> Optional[QAserverA]:
        try:
            return await self.ensure_qa_server()
        except Exception as exc:
            self.startup_error = {
                "code": exc.__class__.__name__,
                "message": str(exc) or "考试启动失败，请联系管理员",
            }
            await self.push_startup_error(direction)
            return None

    async def ensure_qa_server(self) -> QAserverA:
        if self.qa_server is not None:
            self.start_exam_output_listener()
            return self.qa_server

        exam_id = str(self.current_user.get("exam_id") or "").strip()
        if not exam_id:
            raise ValueError("EXAM_ID_REQUIRED")

        self.qa_server = await QAserverA.create(
            self.current_user,
            exam_id,
        )
        self.start_exam_output_listener()
        return self.qa_server

    def start_exam_output_listener(self) -> None:
        if self.exam_output_task is not None and not self.exam_output_task.done():
            return
        if self.qa_server is None or self.qa_server.exam_graph is None:
            return
        if self.qa_server.exam_graph.state.get("finished"):
            return

        self.exam_output_task = asyncio.create_task(
            self.consume_exam_output()
        )

    async def consume_exam_output(self) -> None:
        if self.qa_server is None or self.qa_server.exam_graph is None:
            return

        output_queue = self.qa_server.exam_graph.output_queue
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

                if event.get("type") == "finished":
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
        await self.push_frame(LLMTextFrame(content), FrameDirection.DOWNSTREAM)
        await self.push_frame(TTSSpeakFrame(speech), FrameDirection.DOWNSTREAM)

    async def setup(self, setup):
        await super().setup(setup)

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

    async def push_output_events(self, output_events: List[Dict[str, object]], direction: FrameDirection):
        for event in output_events:
            event_type = event.get("type")
            if event_type == "question":
                question = event.get("question")
                display_content = self.render_question_html(question)
                speech_content = self.render_question_speech(question)
                if display_content:
                    await self.push_frame(LLMTextFrame(display_content), direction)
                if speech_content:
                    await self.push_frame(TTSSpeakFrame(speech_content), direction)
                continue

            if event_type == "waiting_feedback":
                content = "请先回答当前问题。"
                await self.push_frame(TTSSpeakFrame(content), FrameDirection.DOWNSTREAM)
                continue

            if event_type == "waiting_decision":
                content = "正在判断你的回答，请稍候。"
                await self.push_frame(LLMTextFrame(content), direction)
                await self.push_frame(TTSSpeakFrame(content), FrameDirection.DOWNSTREAM)
                continue

            if event_type == "final_review":
                review = event.get("review") or {}
                display_content = build_final_review_output(
                    review,
                    output_type="html",
                )
                if display_content:
                    await self.push_frame(LLMTextFrame(display_content), direction)

                speech_content = str(review.get("overall_summary") or "").strip()
                if speech_content:
                    await self.push_frame(
                        TTSSpeakFrame(speech_content),
                        FrameDirection.DOWNSTREAM,
                    )
                continue

            if event_type == "final_review_error":
                error = str(event.get("error") or "FINAL_REVIEW_FAILED")
                content = f"\u6700\u7ec8\u603b\u8bc4\u751f\u6210\u5931\u8d25\uff1a{error}"
                await self.push_frame(LLMTextFrame(escape(content)), direction)
                await self.push_frame(TTSSpeakFrame(content), FrameDirection.DOWNSTREAM)
                continue

            if event_type == "finished":
                content = "本次口试已结束。"
                await self.push_frame(LLMTextFrame(content), direction)
                await self.push_frame(TTSSpeakFrame(content), FrameDirection.DOWNSTREAM)
                await self.push_frame(EndFrame(), FrameDirection.DOWNSTREAM)
                continue

            if event_type == "error":
                content = str(event.get("error") or "EXAM_ERROR")
                await self.push_frame(LLMTextFrame(escape(content)), direction)
                await self.push_frame(TTSSpeakFrame(content), FrameDirection.DOWNSTREAM)
                continue

            content = str(event.get("content", ""))
            if event_type == "speak":
                await self.push_frame(TTSSpeakFrame(content), direction)
            elif content:
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


InterviewServiceB = InterviewServiceA
