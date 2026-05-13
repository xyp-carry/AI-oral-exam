from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from heapq import heappop, heappush
from itertools import count
from typing import Deque, List, Optional, Tuple

@dataclass(frozen=True)
class Question:
    question_id: str
    content: str
    score_band: Tuple[float, float]
    dimension: str
    standard_answer: Optional[str] = None
    # 基于哪一轮问答生成/选择的问题；-1 表示不是基于某一轮问答
    based_on_record_index: int = -1

    # 出题依据，比如 "follow_up"、"score_up"、"score_down"、"same_band"
    question_basis: Optional[str] = None
    source_detail: Optional[str] = None

@dataclass
class QARecord:
    question: Question
    student_answer: str
    evaluation: Optional[str] = None
    score: Optional[float] = None

    @property
    def question_content(self) -> str:
        return self.question.content

    @property
    def standard_answer(self) -> Optional[str]:
        return self.question.standard_answer

    @property
    def score_band(self) -> Tuple[float, float]:
        return self.question.score_band

    @property
    def dimension(self) -> str:
        return self.question.dimension
    

@dataclass(order=True)
class PriorityQuestion:
    priority: int
    sequence: int
    question: Question = field(compare=False)


class CandidateExamState:
    def __init__(
        self,
        candidate_id: str,
        initial_score_band: Tuple[float, float],
    ):
        self.candidate_id = candidate_id
        self.current_score_band = initial_score_band

        self.prepared_question_queue: Deque[Question] = deque()
        self.priority_question_queue: List[PriorityQuestion] = []

        self.exam_records: List[QARecord] = []
        self._sequence = count()

    def add_prepared_question(self, question: Question) -> None:
        self.prepared_question_queue.append(question)

    def add_priority_question(self, question: Question, priority: int = 100) -> None:
        heappush(
            self.priority_question_queue,
            PriorityQuestion(
                priority=-priority,
                sequence=next(self._sequence),
                question=question,
            ),
        )

    def has_next_question(self) -> bool:
        return bool(self.priority_question_queue or self.prepared_question_queue)

    def pop_next_question(self) -> Optional[Question]:
        if self.priority_question_queue:
            return heappop(self.priority_question_queue).question

        if self.prepared_question_queue:
            return self.prepared_question_queue.popleft()

        return None

    def record_answer(
        self,
        question: Question,
        student_answer: str,
        evaluation: Optional[str] = None,
        score: Optional[float] = None,
    ) -> QARecord:
        record = QARecord(
            question=question,
            student_answer=student_answer,
            evaluation=evaluation,
            score=score,
        )
        self.exam_records.append(record)
        return record

    def update_score_band(self, next_score_band: Tuple[float, float]) -> None:
        self.current_score_band = next_score_band

    def latest_record(self) -> Optional[QARecord]:
        if not self.exam_records:
            return None
        return self.exam_records[-1]