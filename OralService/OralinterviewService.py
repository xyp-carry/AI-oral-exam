from html import escape
from typing import Dict, List, Optional

from pipecat.frames.frames import Frame, LLMContextFrame, LLMTextFrame, StartFrame, TTSSpeakFrame, EndFrame
from pipecat.processors.frame_processor import FrameDirection, FrameProcessor

from AIOralExamSystem.Exam.QAserver import QAserver
from AIOralExamSystem.Exam.examObject import CandidateExamState, Question
from AIOralExamSystem.utils.monitor import GlobalMonitor


class InterviewService(FrameProcessor):
    def __init__(
        self,
        monitor: GlobalMonitor,
        current_user: dict,
        history: List[Dict[str, str]] = [],
        exam_state: Optional[CandidateExamState] = None,
        startup_error: Optional[Dict[str, object]] = None,
    ):
        super().__init__()
        self.monitor = monitor
        self.current_user = current_user
        self.history = history
        self.exam_state = exam_state
        self.startup_error = startup_error
        self.nickname = current_user.get("nickname") or current_user.get("username") or "同学"
        self.qa_server = QAserver(current_user, exam_state, history)

    async def process_frame(self, frame: Frame, direction: FrameDirection):
        await super().process_frame(frame, direction)

        if isinstance(frame, LLMContextFrame):
            await self.push_frame(LLMTextFrame("AI口试开始思考"), direction)
            output_events = await self.qa_server.put_messages(frame.context.get_messages())
            await self.push_output_events(output_events, direction)
            return

        if isinstance(frame, StartFrame):
            await self.push_frame(frame, direction)
            if self.startup_error:
                content = self.render_startup_error(self.startup_error)
                speech = str(self.startup_error.get("message") or "考试启动失败，请联系老师或稍后重试。")
                await self.push_frame(LLMTextFrame(content), FrameDirection.DOWNSTREAM)
                await self.push_frame(TTSSpeakFrame(speech), FrameDirection.DOWNSTREAM)
                await self.push_frame(EndFrame(), FrameDirection.DOWNSTREAM)
                return
            await self.push_frame(
                TTSSpeakFrame(f"你好，{self.nickname}同学，我是本次的考官，现在开始口试。"),
                FrameDirection.DOWNSTREAM,
            )
            output_events = await self.qa_server.put_messages([])
            await self.push_output_events(output_events, direction)
            return

        if isinstance(frame, EndFrame):
            await self.qa_server.stop_request_loop()
            print("XYPTEST: EndFrame")
            

        await self.push_frame(frame, direction)

    async def setup(self, setup):
        await super().setup(setup)

    @staticmethod
    def render_startup_error(error: Dict[str, object]) -> str:
        code = escape(str(error.get("code") or "EXAM_STARTUP_ERROR"))
        message = escape(str(error.get("message") or "考试启动失败，请联系老师或稍后重试。"))
        return (
            '<div class="exam-error" data-type="exam_error" '
            f'data-code="{code}">'
            "<h3>考试启动失败</h3>"
            f"<p>{message}</p>"
            "</div>"
        )

    async def push_output_events(self, output_events: List[Dict[str, object]], direction: FrameDirection):
        for event in output_events:
            if event.get("type") == "question":
                question = event.get("question")
                display_content = self.render_question_html(question)
                speech_content = self.render_question_speech(question)
                if display_content:
                    await self.push_frame(LLMTextFrame(display_content), direction)
                if speech_content:
                    await self.push_frame(TTSSpeakFrame(speech_content), direction)
                continue

            content = str(event.get("content", ""))
            if event.get("type") == "speak":
                await self.push_frame(TTSSpeakFrame(content), direction)
            else:
                await self.push_frame(LLMTextFrame(content), direction)

    def render_question_html(self, question) -> str:
        if not isinstance(question, Question):
            return str(question or "")

        fragments = {
            str(fragment.get("id", "")): fragment
            for fragment in question.code_fragments
            if isinstance(fragment, dict) and fragment.get("id") is not None
        }
        blocks = question.question_blocks or [{"type": "text", "content": question.content}]
        parts: List[str] = []
        for block in blocks:
            if not isinstance(block, dict):
                continue
            block_type = str(block.get("type", "")).strip()
            if block_type == "text":
                content = str(block.get("content", "")).strip()
                if content:
                    parts.append(f"<p>{escape(content)}</p>")
            elif block_type == "code":
                fragment = fragments.get(str(block.get("fragment_id", "")))
                if fragment:
                    parts.append(self.render_code_fragment_html(fragment))

        if not parts and question.content:
            parts.append(f"<p>{escape(question.content)}</p>")
        return "\n".join(parts)

    def render_question_speech(self, question) -> str:
        if not isinstance(question, Question):
            return str(question or "")

        blocks = question.question_blocks or [{"type": "text", "content": question.content}]
        texts: List[str] = []
        for block in blocks:
            if not isinstance(block, dict):
                continue
            if str(block.get("type", "")).strip() != "text":
                continue
            content = str(block.get("content", "")).strip()
            if content:
                texts.append(content)
        if not texts and question.content:
            texts.append(question.content)
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
