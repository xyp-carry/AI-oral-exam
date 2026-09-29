from __future__ import annotations

from copy import deepcopy
from typing import Any, Dict, Iterable, List, Mapping, Optional


START_DIFFICULTY = "implementation"
FIRST_ADAPTIVE_DIFFICULTIES = ("medium", "easy", "hard")
ADAPTIVE_DIFFICULTIES = ("easy", "medium", "hard")
DIFFICULTY_ORDER = ADAPTIVE_DIFFICULTIES
KNOWN_DIFFICULTIES = (START_DIFFICULTY, *ADAPTIVE_DIFFICULTIES)
MAX_ATTEMPTS_PER_DIFFICULTY = 2

Question = Dict[str, Any]
QuestionList = Dict[str, Dict[str, List[Question]]]


class QAmanagerA:
    """Own prepared A-mode questions and apply its adaptive difficulty rule."""

    def __init__(
        self,
        question_list: Optional[
            Mapping[str, Mapping[str, Iterable[Question]]]
        ] = None,
    ):
        self.question_list = normalize_question_list(question_list or {})
        self.dimension_order = list(self.question_list)
        self._remaining: QuestionList = deepcopy(self.question_list)
        self.current_dimension: Optional[str] = None
        self.current_question: Optional[Question] = None
        self.emitted: List[Question] = []
        self.history: List[Dict[str, Any]] = []
        self._question_sequence = 0
        self._ready_questions: List[Question] = []
        self._question_results: Dict[str, bool] = {}
        self.dimension_states: Dict[str, Dict[str, Any]] = {
            dimension: {
                "active": True,
                "current_difficulty": START_DIFFICULTY,
                "results": {
                    difficulty: []
                    for difficulty in KNOWN_DIFFICULTIES
                },
            }
            for dimension in self.dimension_order
        }
        self._seed_implementation_questions()

    @classmethod
    def from_questions(cls, questions: Iterable[Question]) -> "QAmanagerA":
        return cls(build_question_list(questions))

    @classmethod
    def from_exam_state(cls, exam_state: Any) -> "QAmanagerA":
        return cls.from_questions(collect_questions_from_exam_state(exam_state))

    def start(self) -> Optional[Question]:
        if self.current_question is not None:
            return deepcopy(self.current_question)
        question = self._take_ready_question()
        return self._activate(question) if question is not None else None

    def resolve_question(
        self,
        question_id: Optional[str] = None,
        question_sequence_id: Optional[int] = None,
    ) -> Optional[Question]:
        if self.current_question is None:
            return None
        expected_id = str(self.current_question.get("question_id") or "")
        supplied_id = str(question_id or "")
        if supplied_id and supplied_id != expected_id:
            raise ValueError("QUESTION_ID_NOT_CURRENT")
        expected_sequence = int(
            self.current_question.get("question_sequence_id") or 0
        )
        if (
            question_sequence_id is not None
            and int(question_sequence_id) != expected_sequence
        ):
            raise ValueError("QUESTION_SEQUENCE_ID_NOT_CURRENT")
        return deepcopy(self.current_question)

    def record_judgement(
        self,
        judged_question: Mapping[str, Any],
        result: Optional[Mapping[str, Any]] = None,
        judge_order_id: Optional[int] = None,
    ) -> Optional[Question]:
        question = deepcopy(dict(judged_question or {}))
        normalized_result = dict(result or {})
        is_correct = result_answer_correct(normalized_result)
        has_problem = result_has_problem(normalized_result)
        dimension = str(
            question.get("question_dimension")
            or self.current_dimension
            or ""
        )
        difficulty = normalize_question_difficulty(question)
        self.history.append(
            {
                "judge_order_id": judge_order_id,
                "question": question,
                "question_sequence_id": question.get("question_sequence_id"),
                "result": deepcopy(normalized_result),
                "answer_correct": is_correct,
                "has_problem": has_problem,
            }
        )
        self._record_question_result(question, is_correct)

        if is_correct is not None and dimension in self.dimension_states:
            dimension_state = self.dimension_states[dimension]
            difficulty_results = dimension_state["results"].setdefault(
                difficulty,
                [],
            )
            difficulty_results.append(is_correct)
            dimension_state["current_difficulty"] = difficulty

            adaptive_question = self._choose_next_question(
                dimension,
                difficulty,
                is_correct,
            )
            if adaptive_question is None:
                dimension_state["active"] = False
            else:
                dimension_state["active"] = True
                dimension_state["current_difficulty"] = (
                    normalize_question_difficulty(adaptive_question)
                )
                self._ready_questions.append(adaptive_question)

        self._clear_current_if_judged(question)
        next_question = self._take_ready_question()
        return self._activate(next_question) if next_question is not None else None

    def advance(
        self,
        result: Optional[Mapping[str, Any]] = None,
    ) -> Optional[Question]:
        current = self.current_question
        if current is None:
            return self.start()
        return self.record_judgement(current, result)

    @property
    def has_pending_question(self) -> bool:
        return bool(self._ready_questions) or any(
            questions
            for difficulties in self._remaining.values()
            for questions in difficulties.values()
        )

    @property
    def list_exhausted(self) -> bool:
        return not self._ready_questions and not any(
            questions
            for difficulties in self._remaining.values()
            for questions in difficulties.values()
        )

    @property
    def selection_complete(self) -> bool:
        return self.current_question is None and not self._ready_questions

    def snapshot(self) -> Dict[str, Any]:
        return {
            "current_dimension": self.current_dimension,
            "current_question": deepcopy(self.current_question),
            "dimension_order": list(self.dimension_order),
            "dimension_states": deepcopy(self.dimension_states),
            "remaining": {
                dimension: {
                    difficulty: len(questions)
                    for difficulty, questions in difficulties.items()
                }
                for dimension, difficulties in self._remaining.items()
            },
            "ready": len(self._ready_questions),
            "emitted": len(self.emitted),
            "answered": len(self.history),
            "question_sequence": self._question_sequence,
            "list_exhausted": self.list_exhausted,
            "selection_complete": self.selection_complete,
        }

    def _seed_implementation_questions(self) -> None:
        for dimension in self.dimension_order:
            questions = self._remaining[dimension][START_DIFFICULTY]
            while questions:
                self._ready_questions.append(questions.pop(0))

    def _take_ready_question(self) -> Optional[Question]:
        if not self._ready_questions:
            return None
        return self._ready_questions.pop(0)

    def _choose_next_question(
        self,
        dimension: str,
        difficulty: str,
        is_correct: bool,
    ) -> Optional[Question]:
        if difficulty == START_DIFFICULTY:
            return self._pick_first_adaptive_question(dimension)

        if not is_correct:
            lower = adjacent_difficulty(difficulty, step=-1)
            if lower is None:
                return None
            return self._pick_unanswered_question(dimension, lower)

        correct_count = self._count_correct_answers(
            dimension,
            difficulty,
        )
        if correct_count >= MAX_ATTEMPTS_PER_DIFFICULTY:
            return self._pick_next_higher_question(
                dimension,
                difficulty,
            )

        same_question = self._pick_unanswered_question(
            dimension,
            difficulty,
        )
        if same_question is not None:
            return same_question
        return self._pick_next_higher_question(dimension, difficulty)

    def _pick_first_adaptive_question(
        self,
        dimension: str,
    ) -> Optional[Question]:
        for difficulty in FIRST_ADAPTIVE_DIFFICULTIES:
            question = self._pick_unanswered_question(
                dimension,
                difficulty,
            )
            if question is not None:
                return question
        return None

    def _pick_next_higher_question(
        self,
        dimension: str,
        difficulty: str,
    ) -> Optional[Question]:
        higher = adjacent_difficulty(difficulty, step=1)
        while higher is not None:
            question = self._pick_unanswered_question(
                dimension,
                higher,
            )
            if question is not None:
                return question
            higher = adjacent_difficulty(higher, step=1)
        return None

    def _pick_unanswered_question(
        self,
        dimension: str,
        difficulty: str,
    ) -> Optional[Question]:
        questions = (
            self._remaining.get(dimension, {}).get(difficulty) or []
        )
        if not questions:
            return None
        return questions.pop(0)

    def _count_correct_answers(
        self,
        dimension: str,
        difficulty: str,
    ) -> int:
        return sum(
            1
            for question in self.question_list.get(dimension, {}).get(
                difficulty,
                [],
            )
            if self._question_results.get(
                str(question.get("question_id") or "")
            )
            is True
        )

    def _record_question_result(
        self,
        question: Mapping[str, Any],
        is_correct: Optional[bool],
    ) -> None:
        if is_correct is None:
            return
        question_id = str(question.get("question_id") or "")
        if question_id:
            self._question_results[question_id] = is_correct
        dimension = str(question.get("question_dimension") or "")
        difficulty = normalize_question_difficulty(question)
        for candidate in self.question_list.get(dimension, {}).get(
            difficulty,
            [],
        ):
            if str(candidate.get("question_id") or "") == question_id:
                candidate["answer_correct"] = is_correct
                return

    def _clear_current_if_judged(
        self,
        question: Mapping[str, Any],
    ) -> None:
        if self.current_question is None:
            return
        current_sequence = self.current_question.get("question_sequence_id")
        judged_sequence = question.get("question_sequence_id")
        if current_sequence == judged_sequence:
            self.current_question = None

    def _activate(self, question: Question) -> Question:
        self._question_sequence += 1
        activated = deepcopy(question)
        activated["status"] = "active"
        activated["question_sequence_id"] = self._question_sequence
        self.current_dimension = str(
            activated.get("question_dimension") or ""
        )
        self.current_question = activated
        self.emitted.append(deepcopy(activated))
        return deepcopy(activated)


def build_question_list(questions: Iterable[Question]) -> QuestionList:
    grouped: QuestionList = {}
    for index, question in enumerate(questions or []):
        if not isinstance(question, Mapping):
            raise ValueError("QUESTION_MUST_BE_MAPPING")
        default_dimension = str(
            question.get("question_dimension")
            or question.get("dimension")
            or "default"
        )
        normalized = normalize_question(
            question,
            index=index,
            default_dimension=default_dimension,
        )
        dimension = normalized["question_dimension"]
        difficulty = normalized["difficulty"]
        buckets = grouped.setdefault(
            dimension,
            {known: [] for known in KNOWN_DIFFICULTIES},
        )
        buckets[difficulty].append(normalized)

    for buckets in grouped.values():
        for questions_at_level in buckets.values():
            questions_at_level.sort(key=question_sort_key)
    return grouped


def collect_questions_from_exam_state(exam_state: Any) -> List[Question]:
    questions: List[Question] = []
    seen: set[str] = set()
    for raw_question in iter_exam_state_questions(exam_state):
        question = question_from_exam_state_item(raw_question, len(questions))
        question_id = str(question.get("question_id") or "")
        if question_id and question_id in seen:
            question["question_id"] = f"{question_id}-{len(questions) + 1}"
        seen.add(str(question.get("question_id") or ""))
        questions.append(question)
    return questions


def iter_exam_state_questions(exam_state: Any):
    for attr in ("prepared_question_queue", "preset_question_queue"):
        queue = getattr(exam_state, attr, None)
        for question in list(queue or []):
            yield question
    for item in list(getattr(exam_state, "priority_question_queue", []) or []):
        yield getattr(item, "question", item)


def question_from_exam_state_item(question: Any, index: int) -> Question:
    question_id = str(getattr(question, "question_id", "") or f"A-{index + 1}")
    dimension = str(getattr(question, "dimension", "") or "default")
    content = str(getattr(question, "content", "") or "")
    return {
        "question_id": question_id,
        "question_dimension": dimension,
        "question_content": content,
        "content": content,
        "difficulty": exam_state_question_difficulty(question),
        "question_blocks": list(getattr(question, "question_blocks", []) or []),
        "code_fragments": list(getattr(question, "code_fragments", []) or []),
        "standard_answer": getattr(question, "standard_answer", None),
        "score": getattr(question, "score", 1.0),
        "sort_order": index,
    }


def exam_state_question_difficulty(question: Any) -> str:
    for block in getattr(question, "question_blocks", []) or []:
        if not isinstance(block, Mapping):
            continue
        block_value = block.get("difficulty") or block.get("difficulty_level")
        if block_value is not None:
            return normalize_difficulty(block_value)
    value = (
        getattr(question, "difficulty", None)
        or getattr(question, "difficulty_level", None)
        or START_DIFFICULTY
    )
    return normalize_difficulty(value)


def normalize_question_list(
    question_list: Mapping[str, Mapping[str, Iterable[Question]]],
) -> QuestionList:
    normalized_list: QuestionList = {}
    seen_ids: set[str] = set()
    for dimension_value, difficulty_map in question_list.items():
        dimension = required_text(
            dimension_value,
            "QUESTION_DIMENSION_REQUIRED",
        )
        if not isinstance(difficulty_map, Mapping):
            raise ValueError(
                f"QUESTION_DIFFICULTIES_MUST_BE_MAPPING: {dimension}"
            )
        buckets = {known: [] for known in KNOWN_DIFFICULTIES}
        for difficulty_value, questions in difficulty_map.items():
            difficulty = normalize_difficulty(difficulty_value)
            if not isinstance(questions, Iterable) or isinstance(
                questions,
                (str, bytes, Mapping),
            ):
                raise ValueError(
                    f"QUESTION_BUCKET_MUST_BE_LIST: {dimension}.{difficulty}"
                )
            for index, question in enumerate(questions):
                normalized = normalize_question(
                    question,
                    index=index,
                    default_dimension=dimension,
                    default_difficulty=difficulty,
                )
                question_id = normalized["question_id"]
                if question_id in seen_ids:
                    raise ValueError(f"QUESTION_ID_DUPLICATED: {question_id}")
                seen_ids.add(question_id)
                buckets[difficulty].append(normalized)
        for questions_at_level in buckets.values():
            questions_at_level.sort(key=question_sort_key)
        normalized_list[dimension] = buckets
    return normalized_list


def normalize_question(
    question: Mapping[str, Any],
    index: int,
    default_dimension: Optional[str] = None,
    default_difficulty: Optional[str] = None,
) -> Question:
    if not isinstance(question, Mapping):
        raise ValueError("QUESTION_MUST_BE_MAPPING")
    normalized = dict(question)
    dimension = required_text(
        normalized.get("question_dimension")
        or normalized.get("dimension")
        or default_dimension,
        "QUESTION_DIMENSION_REQUIRED",
    )
    difficulty = normalize_difficulty(
        extract_question_difficulty(normalized)
        or default_difficulty
        or START_DIFFICULTY
    )
    content = required_text(
        normalized.get("question_content")
        or normalized.get("content")
        or normalized.get("question"),
        "QUESTION_CONTENT_REQUIRED",
    )
    question_id = str(
        normalized.get("question_id")
        or normalized.get("preset_question_id")
        or normalized.get("id")
        or f"{dimension}:{difficulty}:{index + 1}"
    ).strip()
    normalized.update(
        {
            "question_id": question_id,
            "question_dimension": dimension,
            "difficulty": difficulty,
            "question_content": content,
        }
    )
    if (
        not normalized.get("standard_answer")
        and normalized.get("reference_answer")
    ):
        normalized["standard_answer"] = normalized["reference_answer"]
    return normalized


def extract_question_difficulty(
    question: Mapping[str, Any],
) -> Optional[str]:
    blocks = question.get("question_blocks")
    if isinstance(blocks, list):
        for block in blocks:
            if isinstance(block, Mapping) and block.get("difficulty"):
                return str(block["difficulty"])
    value = question.get("difficulty") or question.get("difficulty_level")
    return str(value) if value is not None else None


def normalize_question_difficulty(question: Mapping[str, Any]) -> str:
    return normalize_difficulty(
        extract_question_difficulty(question) or START_DIFFICULTY
    )


def normalize_difficulty(value: Any) -> str:
    difficulty = str(value or "").strip().lower()
    if difficulty == "middle":
        difficulty = "medium"
    if difficulty in {"1", "2"}:
        difficulty = "easy"
    elif difficulty == "3":
        difficulty = "medium"
    elif difficulty in {"4", "5"}:
        difficulty = "hard"
    if difficulty not in KNOWN_DIFFICULTIES:
        raise ValueError(f"QUESTION_DIFFICULTY_INVALID: {difficulty}")
    return difficulty


def adjacent_difficulty(
    difficulty: str,
    step: int,
) -> Optional[str]:
    if difficulty not in ADAPTIVE_DIFFICULTIES:
        return None
    next_index = ADAPTIVE_DIFFICULTIES.index(difficulty) + int(step)
    if 0 <= next_index < len(ADAPTIVE_DIFFICULTIES):
        return ADAPTIVE_DIFFICULTIES[next_index]
    return None


def result_answer_correct(result: Mapping[str, Any]) -> Optional[bool]:
    payload = dict(result or {})
    value = payload.get("answer_correct")
    if isinstance(value, bool):
        return value
    if value is not None:
        normalized = str(value).strip().lower()
        if normalized in {"true", "correct", "yes", "1"}:
            return True
        if normalized in {"false", "wrong", "no", "0"}:
            return False

    correctness_level = str(
        payload.get("correctness_level") or ""
    ).strip().lower()
    if correctness_level in {
        "excellent",
        "correct",
        "fully_correct",
        "mostly_correct",
    }:
        return True
    if correctness_level in {
        "average",
        "wrong",
        "incorrect",
        "slightly_correct",
    }:
        return False

    for nested_key in ("result", "evaluation", "feedback"):
        nested = payload.get(nested_key)
        if isinstance(nested, Mapping):
            nested_result = result_answer_correct(nested)
            if nested_result is not None:
                return nested_result
    return None


def result_has_problem(result: Mapping[str, Any]) -> bool:
    payload = dict(result or {})
    for key in ("has_problem", "problem", "needs_followup"):
        value = payload.get(key)
        if isinstance(value, bool):
            return value
        if value is not None:
            return str(value).strip().lower() in {"true", "yes", "1"}

    for nested_key in ("result", "evaluation", "feedback"):
        nested = payload.get(nested_key)
        if isinstance(nested, Mapping) and result_has_problem(nested):
            return True
    if any(
        payload.get(key)
        for key in ("mistakes", "errors", "dig_points", "issues")
    ):
        return True
    answer_correct = result_answer_correct(payload)
    return answer_correct is False


def question_sort_key(question: Mapping[str, Any]) -> tuple[int, str]:
    try:
        sort_order = int(question.get("sort_order") or 0)
    except (TypeError, ValueError):
        sort_order = 0
    return sort_order, str(question.get("created_at") or "")


def required_text(value: Any, error_code: str) -> str:
    normalized = str(value or "").strip()
    if not normalized:
        raise ValueError(error_code)
    return normalized


__all__ = [
    "ADAPTIVE_DIFFICULTIES",
    "DIFFICULTY_ORDER",
    "FIRST_ADAPTIVE_DIFFICULTIES",
    "KNOWN_DIFFICULTIES",
    "MAX_ATTEMPTS_PER_DIFFICULTY",
    "QAmanagerA",
    "QuestionList",
    "START_DIFFICULTY",
    "adjacent_difficulty",
    "build_question_list",
    "normalize_question",
    "normalize_question_list",
    "result_answer_correct",
    "result_has_problem",
]
