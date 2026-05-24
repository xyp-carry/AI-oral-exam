from typing import Dict, List, Optional

from pipecat.frames.frames import Frame, LLMContextFrame, LLMTextFrame, StartFrame, TTSSpeakFrame
from pipecat.processors.frame_processor import FrameDirection, FrameProcessor

from AIOralExamSystem.Exam.QAserver import QAserver
from AIOralExamSystem.Exam.examObject import CandidateExamState
from AIOralExamSystem.utils.monitor import GlobalMonitor


class InterviewService(FrameProcessor):
    def __init__(
        self,
        monitor: GlobalMonitor,
        current_user: dict,
        history: List[Dict[str, str]] = [],
        exam_state: Optional[CandidateExamState] = None,
    ):
        super().__init__()
        self.monitor = monitor
        self.current_user = current_user
        self.history = history
        self.exam_state = exam_state
        self.nickname = current_user["nickname"]
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
            await self.push_frame(
                TTSSpeakFrame(f"你好，{self.nickname}同学，我是本次的考官，现在开始口试。"),
                FrameDirection.DOWNSTREAM,
            )
            output_events = await self.qa_server.put_messages([])
            await self.push_output_events(output_events, direction)
            return

        await self.push_frame(frame, direction)

    async def setup(self, setup):
        await super().setup(setup)

    async def push_output_events(self, output_events: List[Dict[str, str]], direction: FrameDirection):
        for event in output_events:
            content = str(event.get("content", ""))
            if event.get("type") == "speak":
                await self.push_frame(TTSSpeakFrame(content), direction)
            else:
                await self.push_frame(LLMTextFrame(content), direction)
