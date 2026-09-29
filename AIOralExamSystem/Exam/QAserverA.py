from collections import defaultdict
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple

from AIOralExamSystem.Exam.Examdata.exam_item_repository import get_exam_item_by_id
from AIOralExamSystem.Exam.Examdata.exam_repository import get_exam_session_by_exam_id
from AIOralExamSystem.Exam.Examdata.preset_question_repository import (
    list_user_preset_questions_by_exam_item,
)
from AIOralExamSystem.Agent.Textfix import TextfixAgent
from AIOralExamSystem.Exam.Judger import MainJudgerAgent, StandardAnswerJudgerAgent
from AIOralExamSystem.Exam.QAmanagerA import QAmanagerA
from AIOralExamSystem.Graph.ExamA import ExamAFlow
from AIOralExamSystem.message.session import ExamMessageSession


START_DIFFICULTY = "implementation"
DIFFICULTY_ORDER = (START_DIFFICULTY, "easy", "medium", "hard")


@dataclass
class ExamRuntimeConfig:
    exam_id: str
    course_id: str
    exam_item_id: str
    user_id: str
    exam_mode: str
    exam_session: Dict[str, object]
    exam_item: Dict[str, object]
    question_queue: Optional[List[Dict[str, object]]] = None
    question_bank: Optional[Dict[str, Dict[str, List[Dict[str, object]]]]] = None


class QAserverA:
    """Exam runtime entry. Mode B is backed by the ExamA graph."""

    MODE_A = "A"
    MODE_B = "B"

    def __init__(
        self,
        current_user: dict,
        exam_id: str,
    ):
        self.current_user = current_user
        self.exam_id = self._normalize_required_text(exam_id, "EXAM_ID_REQUIRED")
        self.text_fixer: Optional[TextfixAgent] = None
        self.standard_answer_judger: Optional[StandardAnswerJudgerAgent] = None
        self.main_judger: Optional[MainJudgerAgent] = None
        self.exam_session: Optional[Dict[str, object]] = None
        self.exam_item: Optional[Dict[str, object]] = None
        self.exam_mode: Optional[str] = None
        self.preset_questions: List[Dict[str, object]] = []
        self.question_queue: List[Dict[str, object]] = []
        self.question_bank: Dict[str, Dict[str, List[Dict[str, object]]]] = {}
        self.exam_graph: Optional[ExamMessageSession] = None
        self.runtime_config: Optional[ExamRuntimeConfig] = None

    @classmethod
    async def create(
        cls,
        current_user: dict,
        exam_id: str,
    ) -> "QAserverA":
        server = cls(
            current_user,
            exam_id,
        )
        await server.initialize()
        return server

    async def initialize(self) -> None:
        self.exam_session = await get_exam_session_by_exam_id(self.exam_id)
        if not self.exam_session:
            raise ValueError("EXAM_SESSION_NOT_FOUND")

        exam_item_id = self._normalize_required_text(
            self.exam_session.get("exam_item_id"),
            "EXAM_ITEM_ID_REQUIRED",
        )
        self.exam_item = await get_exam_item_by_id(exam_item_id)
        if not self.exam_item:
            raise ValueError("EXAM_ITEM_NOT_FOUND")

        self.exam_mode = self._normalize_required_text(
            self.exam_item.get("item_type"),
            "EXAM_MODE_REQUIRED",
        )

        if self.exam_mode == self.MODE_A:
            await self._initialize_mode_a()
        elif self.exam_mode == self.MODE_B:
            await self._initialize_mode_b()
        else:
            raise ValueError("UNSUPPORTED_EXAM_MODE")

        self.runtime_config = self._build_runtime_config()

    async def _initialize_mode_a(self) -> None:
        return None

    async def _initialize_mode_b(self) -> None:
        if not self.exam_session:
            raise ValueError("EXAM_SESSION_NOT_LOADED")

        user_id = self._normalize_required_text(
            self.exam_session.get("user_id"),
            "USER_ID_REQUIRED",
        )
        exam_item_id = self._normalize_required_text(
            self.exam_session.get("exam_item_id"),
            "EXAM_ITEM_ID_REQUIRED",
        )
        course_id = self._normalize_required_text(
            self.exam_session.get("course_id"),
            "COURSE_ID_REQUIRED",
        )

        self.preset_questions = await list_user_preset_questions_by_exam_item(
            course_id=course_id,
            exam_item_id=exam_item_id,
            user_id=user_id,
        )
        self.question_queue, self.question_bank = build_mode_b_question_sets(
            self.preset_questions,
        )
        self.text_fixer = self._build_text_fixer()
        self.standard_answer_judger = self._build_standard_answer_judger()
        self.main_judger = self._build_main_judger()
        qa_manager = QAmanagerA(self.question_bank)
        flow = ExamAFlow(
            qa_manager=qa_manager,
            standard_answer_judger=self.standard_answer_judger,
            main_judger=self.main_judger,
            exam_id=self.exam_id,
            user_id=user_id,
            exam_item_id=exam_item_id,
        )
        self.exam_graph = ExamMessageSession(
            flow=flow,
            exam_id=self.exam_id,
        )
        await self.exam_graph.start()

    async def request_next_question(self) -> List[Dict[str, Any]]:
        return await self._require_mode_b_exam_graph().handle_message({
            "type": ExamAFlow.EVENT_REQUEST_NEXT,
        })

    async def submit_answer(
        self,
        answer: str,
        question_id: Optional[str] = None,
        feedback: Optional[Dict[str, Any]] = None,
    ) -> List[Dict[str, Any]]:
        event = {
            "type": ExamAFlow.EVENT_SUBMIT_FEEDBACK,
            "question_id": question_id,
            "answer": answer,
        }
        if feedback is not None:
            event["feedback"] = feedback
        return await self._require_mode_b_exam_graph().handle_message(event)

    async def stop_exam(self) -> List[Dict[str, Any]]:
        return await self._require_mode_b_exam_graph().handle_message({
            "type": ExamAFlow.EVENT_STOP,
        })

    def _require_mode_b_exam_graph(self) -> ExamMessageSession:
        if self.exam_mode != self.MODE_B:
            raise ValueError("EXAM_GRAPH_ONLY_AVAILABLE_IN_MODE_B")
        if self.exam_graph is None:
            raise ValueError("EXAM_GRAPH_NOT_INITIALIZED")
        return self.exam_graph

    def _build_text_fixer(self) -> TextfixAgent:
        judge_config = self._require_judge_config(self.current_user.get("judge_config"))
        model_settings = self._require_agent_settings(judge_config, "report_judger")
        source = self._normalize_required_text(
            self.current_user.get("uuid")
            or self.current_user.get("id")
            or self.current_user.get("user_id")
            or (self.exam_session or {}).get("user_id"),
            "USER_ID_REQUIRED",
        )
        return TextfixAgent(
            model_settings,
            source,
            thinking=False,
            response_format=False,
            temperature=float(model_settings.get("temperature", 0)),
        )

    def _build_standard_answer_judger(self) -> StandardAnswerJudgerAgent:
        judge_config = self._require_judge_config(self.current_user.get("judge_config"))
        model_settings = self._optional_agent_settings(judge_config, "report_judger")
        if model_settings is None:
            model_settings = self._require_agent_settings(judge_config, "report_judger")
        source = self._normalize_required_text(
            self.current_user.get("uuid")
            or self.current_user.get("id")
            or self.current_user.get("user_id")
            or (self.exam_session or {}).get("user_id"),
            "USER_ID_REQUIRED",
        )
        return StandardAnswerJudgerAgent(
            model_settings,
            source,
            thinking=True,
            response_format=True,
            temperature=float(model_settings.get("temperature", 0)),
        )

    def _build_main_judger(self) -> MainJudgerAgent:
        judge_config = self._require_judge_config(self.current_user.get("judge_config"))
        model_settings = self._require_agent_settings(judge_config, "report_judger")
        source = self._normalize_required_text(
            self.current_user.get("uuid")
            or self.current_user.get("id")
            or self.current_user.get("user_id")
            or (self.exam_session or {}).get("user_id"),
            "USER_ID_REQUIRED",
        )
        return MainJudgerAgent(
            model_settings,
            source,
            thinking=True,
            response_format=True,
            temperature=float(model_settings.get("temperature", 0)),
        )

    @staticmethod
    def _require_judge_config(judge_config) -> Dict[str, object]:
        if not isinstance(judge_config, dict) or not judge_config:
            raise ValueError("MODEL_CONFIG_REQUIRED")
        return judge_config

    @staticmethod
    def _require_agent_settings(judge_config: Dict[str, object], role: str) -> Dict[str, object]:
        model_settings = QAserverA._optional_agent_settings(judge_config, role)
        if model_settings is None:
            raise ValueError("MODEL_CONFIG_REQUIRED")
        return model_settings

    @staticmethod
    def _optional_agent_settings(judge_config: Dict[str, object], role: str) -> Optional[Dict[str, object]]:
        agent_config = judge_config.get(role)
        if not isinstance(agent_config, dict):
            return None
        model_settings = agent_config.get("runtime_model_settings")
        if not isinstance(model_settings, dict) or not model_settings.get("model_name"):
            return None
        return model_settings

    def _build_runtime_config(self) -> ExamRuntimeConfig:
        if not self.exam_session or not self.exam_item or not self.exam_mode:
            raise ValueError("EXAM_RUNTIME_NOT_INITIALIZED")

        return ExamRuntimeConfig(
            exam_id=self._normalize_required_text(
                self.exam_session.get("exam_id"),
                "EXAM_ID_REQUIRED",
            ),
            course_id=self._normalize_required_text(
                self.exam_session.get("course_id"),
                "COURSE_ID_REQUIRED",
            ),
            exam_item_id=self._normalize_required_text(
                self.exam_session.get("exam_item_id"),
                "EXAM_ITEM_ID_REQUIRED",
            ),
            user_id=self._normalize_required_text(
                self.exam_session.get("user_id"),
                "USER_ID_REQUIRED",
            ),
            exam_mode=self.exam_mode,
            exam_session=self.exam_session,
            exam_item=self.exam_item,
            question_queue=self.question_queue if self.exam_mode == self.MODE_B else None,
            question_bank=self.question_bank if self.exam_mode == self.MODE_B else None,
        )

    @staticmethod
    def _normalize_required_text(value, error_code: str) -> str:
        normalized = str(value or "").strip()
        if not normalized:
            raise ValueError(error_code)
        return normalized


def build_mode_b_question_sets(
    questions: List[Dict[str, object]],
) -> Tuple[List[Dict[str, object]], Dict[str, Dict[str, List[Dict[str, object]]]]]:
    question_bank = group_mode_b_questions(questions)
    question_queue = build_question_queue(question_bank)
    if not question_queue:
        raise ValueError("MODE_B_QUESTION_QUEUE_REQUIRED")
    return question_queue, question_bank


def build_question_queue(
    question_bank: Dict[str, Dict[str, List[Dict[str, object]]]],
) -> List[Dict[str, object]]:
    question_queue: List[Dict[str, object]] = []
    for questions_by_difficulty in question_bank.values():
        question_queue.extend(questions_by_difficulty.get(START_DIFFICULTY) or [])
    return question_queue


def group_mode_b_questions(
    questions: List[Dict[str, object]],
) -> Dict[str, Dict[str, List[Dict[str, object]]]]:
    grouped_questions = defaultdict(
        lambda: {difficulty: [] for difficulty in DIFFICULTY_ORDER}
    )
    for index, question in enumerate(questions or []):
        normalized_question = normalize_mode_b_question(question, index)
        dimension = str(normalized_question["question_dimension"])
        difficulty = str(normalized_question["difficulty"])
        grouped_questions[dimension][difficulty].append(normalized_question)

    for questions_by_difficulty in grouped_questions.values():
        for difficulty_questions in questions_by_difficulty.values():
            difficulty_questions.sort(key=question_sort_key)
    return dict(grouped_questions)


def normalize_mode_b_question(
    question: Dict[str, object],
    index: int,
) -> Dict[str, object]:
    normalized = dict(question)
    path = f"preset_questions[{index}]"
    dimension = normalize_required_text_field(
        normalized.get("question_dimension") or normalized.get("dimension"),
        "QUESTION_DIMENSION_REQUIRED",
        path,
    )
    difficulty = extract_required_question_difficulty(normalized, path)
    content = normalize_required_text_field(
        normalized.get("question_content")
        or normalized.get("content")
        or normalized.get("question"),
        "QUESTION_CONTENT_REQUIRED",
        path,
    )
    standard_answer = normalize_required_text_field(
        normalized.get("standard_answer") or normalized.get("reference_answer"),
        "QUESTION_STANDARD_ANSWER_REQUIRED",
        path,
    )

    normalized["question_dimension"] = dimension
    normalized["difficulty"] = difficulty
    normalized["question_content"] = content
    normalized["standard_answer"] = standard_answer
    return normalized


def extract_required_question_difficulty(
    question: Dict[str, object],
    path: str,
) -> str:
    difficulty = None
    question_blocks = question.get("question_blocks")
    if isinstance(question_blocks, list):
        for block in question_blocks:
            if isinstance(block, dict) and block.get("difficulty"):
                difficulty = block["difficulty"]
                break

    if not difficulty:
        difficulty = question.get("difficulty") or question.get("difficulty_level")

    normalized = str(difficulty or "").strip().lower()
    if normalized == "middle":
        normalized = "medium"
    if not normalized:
        raise ValueError(f"QUESTION_DIFFICULTY_REQUIRED: {path}")
    if normalized not in DIFFICULTY_ORDER:
        raise ValueError(f"QUESTION_DIFFICULTY_INVALID: {path}")
    return normalized


def normalize_required_text_field(value, error_code: str, path: str) -> str:
    normalized = str(value or "").strip()
    if not normalized:
        raise ValueError(f"{error_code}: {path}")
    return normalized


def question_sort_key(question: Dict[str, object]) -> Tuple[int, str]:
    try:
        sort_order = int(question.get("sort_order") or 0)
    except (TypeError, ValueError):
        sort_order = 0
    return sort_order, str(question.get("created_at") or "")
