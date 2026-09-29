import asyncio
import json
import re
from copy import deepcopy
from typing import Any, Dict, List, Mapping, Optional
from AIOralExamSystem.Exam.Judger import StandardAnswerJudgerAgent
from AIOralExamSystem.Exam.QAmanagerA import QAmanagerA


class AAnalysisJobRunner:
    """Run exactly one judging Agent call for one submitted answer."""

    def __init__(self, standard_answer_judger: Optional[Any] = None):
        self.standard_answer_judger = standard_answer_judger

    async def run(
        self,
        question: Dict[str, Any],
        feedback: Dict[str, Any],
        judge_order_id: int,
    ) -> Dict[str, Any]:
        raw_answer = student_answer_text(feedback)
        try:
            if self.standard_answer_judger is None:
                result = {
                    "answer_correct": True,
                    "correctness_level": "not_judged",
                    "reason": "answer_judger_not_configured",
                }
            else:
                response = await self.standard_answer_judger.run(
                    payload=self.build_payload(question, raw_answer)
                )
                if isinstance(response, dict) and response.get("agent_error"):
                    raise RuntimeError(
                        json.dumps(
                            response["agent_error"],
                            ensure_ascii=False,
                        )
                    )
                result = parse_agent_json(response)
            status = "done"
            error = ""
        except Exception as exc:
            status = "error"
            error = str(exc)
            result = {}

        return {
            "type": "analysis_result",
            "job_id": f"a_judge_{int(judge_order_id):08d}",
            "judge_order_id": int(judge_order_id),
            "question_id": question_identity(question),
            "question_sequence_id": int(
                question.get("question_sequence_id") or 0
            ),
            "question": dict(question),
            "status": status,
            "error": error,
            "feedback": {
                **dict(feedback),
                "raw_answer": raw_answer,
                "answer": raw_answer,
                "analysis_status": status,
                "evaluation": result,
            },
            "result": result,
        }

    @staticmethod
    def build_payload(
        question: Dict[str, Any],
        student_answer: str,
    ) -> Dict[str, Any]:
        return {
            "question_id": question_identity(question),
            "question_sequence_id": question.get("question_sequence_id"),
            "question": question_value(
                question,
                ("question_content", "content", "question"),
            ),
            "standard_answer": question_value(
                question,
                ("standard_answer", "reference_answer"),
            ),
            "student_answer": student_answer,
            "question_dimension": question_value(
                question,
                ("question_dimension", "dimension"),
            ),
            "difficulty": (
                question.get("difficulty")
                or question.get("difficulty_level")
            ),
        }


def student_answer_text(feedback: Dict[str, Any]) -> str:
    for key in ("answer", "student_answer"):
        if feedback.get(key):
            return str(feedback[key]).strip()
    nested = feedback.get("feedback")
    if isinstance(nested, dict):
        for key in ("answer", "student_answer"):
            if nested.get(key):
                return str(nested[key]).strip()
    return str(nested or "").strip()


def question_identity(question: Dict[str, Any]) -> Optional[str]:
    for key in ("question_id", "preset_question_id", "id"):
        if question.get(key):
            return str(question[key])
    return None


def question_value(
    question: Dict[str, Any],
    keys: tuple[str, ...],
) -> str:
    for key in keys:
        value = question.get(key)
        if value:
            return str(value).strip()
    return ""


def parse_agent_json(response: Any) -> Dict[str, Any]:
    if isinstance(response, dict):
        judgement_keys = {
            "answer_correct",
            "correctness_level",
            "has_problem",
            "problem",
            "needs_followup",
            "mistakes",
            "errors",
            "issues",
        }
        if judgement_keys.intersection(response):
            return dict(response)
        structured = response.get("structured_response")
        if isinstance(structured, dict):
            return dict(structured)

    text = str(response_content(response) or "").strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json|JSON)?\s*\n?", "", text, count=1)
        text = re.sub(r"\n?```\s*$", "", text, count=1)
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", text, flags=re.DOTALL)
        if match is None:
            raise ValueError("No JSON object found in model response")
        parsed = json.loads(match.group(0))
    if not isinstance(parsed, dict):
        raise ValueError("MODEL_RESPONSE_MUST_BE_OBJECT")
    return parsed


def response_content(response: Any) -> str:
    if isinstance(response, dict):
        messages = response.get("messages")
        if isinstance(messages, list):
            for message in reversed(messages):
                content = response_content(message)
                if content:
                    return content
        content = response.get("content")
        if isinstance(content, list):
            return "".join(
                item.get("text", "")
                if isinstance(item, dict)
                else str(item)
                for item in content
            )
        if content is not None:
            return str(content)
        if isinstance(response.get("response"), dict):
            return response_content(response["response"])
    content = getattr(response, "content", None)
    if content is not None:
        if isinstance(content, list):
            return "".join(
                item.get("text", "")
                if isinstance(item, dict)
                else str(item)
                for item in content
            )
        return str(content)
    return str(response or "")


SUMMARY_KEYS = {
    "overall_summary",
    "dimension_summaries",
    "strengths",
    "weaknesses",
    "suggestions",
}
DIFFICULTY_RANK = {
    "implementation": 0,
    "easy": 1,
    "medium": 2,
    "hard": 3,
}


class AFinalReviewJobRunner:
    """Run the single final-summary Agent call after A-mode completion."""

    def __init__(self, main_judger: Optional[Any] = None):
        self.main_judger = main_judger

    async def run(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        try:
            if self.main_judger is None:
                raise RuntimeError("MAIN_JUDGER_NOT_CONFIGURED")
            response = await self.main_judger.run(
                history=[
                    {
                        "role": "user",
                        "content": json.dumps(
                            payload,
                            ensure_ascii=False,
                        ),
                    }
                ]
            )
            if isinstance(response, dict) and response.get("agent_error"):
                raise RuntimeError(
                    json.dumps(
                        response["agent_error"],
                        ensure_ascii=False,
                    )
                )
            review = parse_final_review(response)
            return {
                "type": "final_review_result",
                "status": "done",
                "review": review,
                "error": "",
            }
        except Exception as exc:
            return {
                "type": "final_review_result",
                "status": "error",
                "review": None,
                "error": str(exc),
            }


def build_final_review_payload(
    exam_id: str,
    user_id: str,
    exam_item_id: str,
    committed_events: List[Mapping[str, Any]],
) -> Dict[str, Any]:
    records = [
        build_review_record(index, event)
        for index, event in enumerate(committed_events, start=1)
    ]
    return {
        "exam_id": exam_id,
        "user_id": user_id,
        "exam_item_id": exam_item_id,
        "records": records,
        "dimension_statistics": build_dimension_statistics(records),
    }


def build_review_record(
    index: int,
    event: Mapping[str, Any],
) -> Dict[str, Any]:
    question = dict(event.get("question") or {})
    feedback = dict(event.get("feedback") or {})
    result = dict(event.get("result") or {})
    return {
        "index": index,
        "judge_order_id": event.get("judge_order_id"),
        "question_sequence_id": event.get("question_sequence_id"),
        "question": {
            "question_id": question.get("question_id"),
            "content": (
                question.get("question_content")
                or question.get("content")
                or question.get("question")
                or ""
            ),
            "question_blocks": list(
                question.get("question_blocks") or []
            ),
            "code_fragments": list(
                question.get("code_fragments") or []
            ),
            "dimension": (
                question.get("question_dimension")
                or question.get("dimension")
                or "default"
            ),
            "difficulty": (
                question.get("difficulty")
                or question.get("difficulty_level")
                or "implementation"
            ),
            "standard_answer": (
                question.get("standard_answer")
                or question.get("reference_answer")
                or ""
            ),
        },
        "student_answer": student_answer_text(feedback),
        "answer_correct": result.get("answer_correct"),
        "analysis_status": event.get("status"),
        "evaluation": result,
    }


def build_dimension_statistics(
    records: List[Mapping[str, Any]],
) -> Dict[str, Dict[str, Any]]:
    statistics: Dict[str, Dict[str, Any]] = {}
    for record in records:
        question = dict(record.get("question") or {})
        dimension = str(question.get("dimension") or "default")
        difficulty = str(
            question.get("difficulty") or "implementation"
        ).strip().lower()
        dimension_stats = statistics.setdefault(
            dimension,
            {
                "answered": 0,
                "correct": 0,
                "incorrect": 0,
                "unresolved": 0,
                "difficulty_counts": {},
                "highest_difficulty_reached": None,
            },
        )
        difficulty_stats = dimension_stats["difficulty_counts"].setdefault(
            difficulty,
            {
                "answered": 0,
                "correct": 0,
                "incorrect": 0,
                "unresolved": 0,
            },
        )
        dimension_stats["answered"] += 1
        difficulty_stats["answered"] += 1
        answer_correct = record.get("answer_correct")
        if answer_correct is True:
            dimension_stats["correct"] += 1
            difficulty_stats["correct"] += 1
        elif answer_correct is False:
            dimension_stats["incorrect"] += 1
            difficulty_stats["incorrect"] += 1
        else:
            dimension_stats["unresolved"] += 1
            difficulty_stats["unresolved"] += 1

        current_highest = dimension_stats["highest_difficulty_reached"]
        if (
            current_highest is None
            or DIFFICULTY_RANK.get(difficulty, -1)
            > DIFFICULTY_RANK.get(str(current_highest), -1)
        ):
            dimension_stats["highest_difficulty_reached"] = difficulty
    return statistics


def parse_final_review(response: Any) -> Dict[str, Any]:
    if isinstance(response, dict):
        if SUMMARY_KEYS.intersection(response):
            return dict(response)
        structured = response.get("structured_response")
        if isinstance(structured, dict):
            return dict(structured)
    parsed = parse_agent_json(response)
    if not isinstance(parsed, dict):
        raise ValueError("FINAL_REVIEW_MUST_BE_OBJECT")
    return parsed






class ExamAFlow:
    """Execute A-mode judging, question progression, and completion policy."""

    EVENT_SUBMIT_FEEDBACK = "submit_feedback"
    EVENT_REQUEST_NEXT = "request_next"
    EVENT_FINISH = "finish"
    EVENT_STOP = "stop"

    def __init__(
        self,
        qa_manager: QAmanagerA,
        judge_config: Optional[Dict[str, Any]] = None,
        main_judger: Optional[Any] = None,
        exam_id: str = "",
        user_id: str = "",
        exam_item_id: str = "",
        standard_answer_judger: Optional[Any] = None,
    ):
        self.qa_manager = qa_manager
        if standard_answer_judger is None:
            standard_answer_judger = self.build_standard_answer_judger(
                judge_config or {},
                user_id,
            )
        self.analysis_runner = AAnalysisJobRunner(standard_answer_judger)
        self.final_review_runner = AFinalReviewJobRunner(main_judger)
        self.exam_id = str(exam_id or "")
        self.user_id = str(user_id or "")
        self.exam_item_id = str(exam_item_id or "")
        self._committed_events: List[Dict[str, Any]] = []
        self._background_tasks: set[asyncio.Task] = set()
        self._state_lock = asyncio.Lock()
        self._delivered_question_keys: set[tuple[str, str]] = set()
        self._completion_messages: Optional[List[Dict[str, Any]]] = None

    @staticmethod
    def build_standard_answer_judger(
        judge_config: Dict[str, Any],
        source: str,
    ) -> StandardAnswerJudgerAgent:
        model_settings = judge_config["report_judger"]["runtime_model_settings"]
        return StandardAnswerJudgerAgent(
            model_settings,
            source,
            thinking=True,
            response_format=True,
            temperature=float(model_settings.get("temperature", 0)),
        )

    async def run(self, event: Dict[str, Any]) -> List[Dict[str, Any]]:
        event = dict(event or {})
        event_type = self._event_type(event)
        if event_type == self.EVENT_REQUEST_NEXT:
            return await self._request_next()
        if event_type == self.EVENT_SUBMIT_FEEDBACK:
            self._schedule_feedback_background(event)
            return await self._request_next()
        if event_type in {self.EVENT_FINISH, self.EVENT_STOP}:
            return [
                {
                    "type": "closed",
                    "exam_completed": self.qa_manager.selection_complete,
                }
            ]
        return [{"type": "waiting", "reason": "unknown_event"}]

    async def _request_next(self) -> List[Dict[str, Any]]:
        while True:
            async with self._state_lock:
                current_question = self.qa_manager.current_question
                if (
                    current_question is not None
                    and not self._question_delivered(current_question)
                ):
                    return [self._question_message(current_question)]

                if current_question is None:
                    question = self.qa_manager.start()
                    if question is not None:
                        return [self._question_message(question)]

                if self._completion_messages is not None:
                    return [dict(item) for item in self._completion_messages]

            await asyncio.sleep(0.2)

    def _schedule_feedback_background(
        self,
        event: Dict[str, Any],
    ) -> asyncio.Task:
        task = asyncio.create_task(
            self._process_feedback_background(dict(event))
        )
        self._background_tasks.add(task)
        task.add_done_callback(self._finish_feedback_background)
        return task

    async def _process_feedback_background(
        self,
        event: Dict[str, Any],
    ) -> List[Dict[str, Any]]:
        async with self._state_lock:
            messages = await self._process_answer(event)
            self._remember_terminal_messages(messages)
            return messages

    def _finish_feedback_background(self, task: asyncio.Task) -> None:
        self._background_tasks.discard(task)
        try:
            task.result()
        except Exception as exc:
            self._completion_messages = [{"type": "error", "error": str(exc)}]

    def _remember_terminal_messages(
        self,
        messages: List[Dict[str, Any]],
    ) -> None:
        normalized = [dict(item) for item in messages or []]
        if any(
            self._event_type(message)
            in {
                "analysis_error",
                "error",
                "final_review",
                "final_review_error",
                "finished",
                "closed",
            }
            for message in normalized
        ):
            self._completion_messages = normalized

    def _question_message(
        self,
        question: Mapping[str, Any],
    ) -> Dict[str, Any]:
        self._delivered_question_keys.add(self._question_key(question))
        return {"type": "question", "question": dict(question)}

    def _question_delivered(self, question: Mapping[str, Any]) -> bool:
        return self._question_key(question) in self._delivered_question_keys

    @staticmethod
    def _question_key(question: Optional[Mapping[str, Any]]) -> tuple[str, str]:
        if not question:
            return ("", "")
        return (
            str(question.get("question_sequence_id") or ""),
            str(question.get("question_id") or ""),
        )

    async def _process_answer(
        self,
        event: Dict[str, Any],
    ) -> List[Dict[str, Any]]:
        try:
            question = self.qa_manager.resolve_question(
                event.get("question_id"),
                event.get("question_sequence_id"),
            )
        except (TypeError, ValueError) as exc:
            return [{"type": "error", "error": str(exc)}]
        if question is None:
            return [{"type": "error", "error": "NO_ACTIVE_QUESTION"}]

        feedback_value = event.get("feedback")
        feedback = (
            dict(feedback_value)
            if isinstance(feedback_value, Mapping)
            else dict(event)
        )
        feedback["question"] = dict(question)
        judgement = await self.analysis_runner.run(
            question=dict(question),
            feedback=feedback,
            judge_order_id=int(event.get("judge_order_id") or 0),
        )
        return await self._commit_judgement(judgement)

    async def _commit_judgement(
        self,
        judgement: Dict[str, Any],
    ) -> List[Dict[str, Any]]:
        if str(judgement.get("status") or "error") != "done":
            error = str(judgement.get("error") or "JUDGING_FAILED")
            return [
                {
                    "type": "analysis_error",
                    "job_id": judgement.get("job_id"),
                    "judge_order_id": judgement.get("judge_order_id"),
                    "question_id": judgement.get("question_id"),
                    "question_sequence_id": judgement.get(
                        "question_sequence_id"
                    ),
                    "error": error,
                },
                {"type": "waiting", "reason": "judging_failed"},
            ]

        question = dict(judgement.get("question") or {})
        result = dict(judgement.get("result") or {})
        next_question = self.qa_manager.record_judgement(
            question,
            result,
            judge_order_id=judgement.get("judge_order_id"),
        )
        self._committed_events.append(deepcopy(judgement))
        outcomes: List[Dict[str, Any]] = [
            {
                "type": "judged",
                "job_id": judgement.get("job_id"),
                "judge_order_id": judgement.get("judge_order_id"),
                "question_id": judgement.get("question_id"),
                "question_sequence_id": judgement.get("question_sequence_id"),
                "has_problem": bool(
                    self.qa_manager.history[-1].get("has_problem")
                ),
                "result": result,
            }
        ]
        if next_question is not None:
            outcomes.append(
                {"type": "question", "question": dict(next_question)}
            )
            return outcomes
        if not self.qa_manager.selection_complete:
            outcomes.append(
                {"type": "waiting", "reason": "no_next_question"}
            )
            return outcomes
        return await self._finalize(outcomes)

    async def _finalize(
        self,
        outcomes: List[Dict[str, Any]],
    ) -> List[Dict[str, Any]]:
        completed = [dict(item) for item in outcomes]
        payload = build_final_review_payload(
            exam_id=self.exam_id,
            user_id=self.user_id,
            exam_item_id=self.exam_item_id,
            committed_events=self._committed_events,
        )
        result = await self.final_review_runner.run(payload)
        if str(result.get("status") or "error") == "done":
            completed.append(
                {
                    "type": "final_review",
                    "review": dict(result.get("review") or {}),
                }
            )
        else:
            completed.append(
                {
                    "type": "final_review_error",
                    "error": result.get("error") or "FINAL_REVIEW_FAILED",
                }
            )
        completed.append({"type": "finished", "exam_completed": True})
        return completed

    @property
    def committed_events(self) -> List[Dict[str, Any]]:
        return deepcopy(self._committed_events)

    @staticmethod
    def _event_type(event: Dict[str, Any]) -> str:
        return str(event.get("type") or "").strip().lower()
