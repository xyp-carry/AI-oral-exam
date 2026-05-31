from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from heapq import heapify, heappop, heappush
from itertools import count
from typing import Deque, Dict, List, Optional

from loguru import logger

@dataclass(frozen=True)
class Question:
    """题目快照，记录题目内容、维度、分值和父题关系。"""

    question_id: str
    content: str
    dimension: str
    question_blocks: List[Dict[str, object]] = field(default_factory=list)
    code_fragments: List[Dict[str, object]] = field(default_factory=list)
    # 当前默认每题 1 分，保留字段是为了后续支持不同题目分值。
    score: float = 1.0
    standard_answer: Optional[str] = None
    # -1 表示基础题；其他值表示该题依赖的历史答题记录下标。
    based_on_record_index: int = -1

    # 生成题目时的补充来源信息，便于日志排查。
    source_detail: Optional[str] = None

    def __post_init__(self):
        question_blocks = self._normalize_question_blocks(self.question_blocks)
        if not question_blocks and self.content:
            question_blocks = [{"type": "text", "content": self.content}]
        object.__setattr__(self, "question_blocks", question_blocks)
        object.__setattr__(
            self,
            "code_fragments",
            self._normalize_code_fragments(self.code_fragments),
        )

    @staticmethod
    def _normalize_question_blocks(blocks) -> List[Dict[str, object]]:
        if not isinstance(blocks, list):
            return []
        normalized: List[Dict[str, object]] = []
        for block in blocks:
            if not isinstance(block, dict):
                continue
            block_type = str(block.get("type", "")).strip()
            if block_type == "text":
                content = str(block.get("content", "")).strip()
                if content:
                    normalized.append({"type": "text", "content": content})
            elif block_type == "code":
                fragment_id = str(block.get("fragment_id", "")).strip()
                if fragment_id:
                    normalized.append({"type": "code", "fragment_id": fragment_id})
        return normalized

    @staticmethod
    def _normalize_code_fragments(fragments) -> List[Dict[str, object]]:
        if not isinstance(fragments, list):
            return []
        normalized: List[Dict[str, object]] = []
        for fragment in fragments:
            if not isinstance(fragment, dict):
                continue
            item = dict(fragment)
            if "id" in item:
                item["id"] = str(item["id"])
            lines = item.get("lines", [])
            if isinstance(lines, str):
                item["lines"] = lines.splitlines()
            elif isinstance(lines, list):
                item["lines"] = [str(line) for line in lines]
            else:
                item["lines"] = []
            normalized.append(item)
        return normalized

@dataclass
class QARecord:
    """一次已完成的问答记录。"""

    question: Question
    correctness_level: str = None
    student_answer: str = None
    evaluation: Optional[str] = None

    @property
    def question_content(self) -> str:
        return self.question.content

    @property
    def standard_answer(self) -> Optional[str]:
        return self.question.standard_answer

    @property
    def score(self) -> float:
        return self.question.score

    @property
    def dimension(self) -> str:
        return self.question.dimension
    

@dataclass(frozen=True)
class QuestionGenerationPlan:
    """判题后生成后续问题的计划。"""

    should_finish: bool = False
    difficulty_level: Optional[int] = None
    target_dimension: Optional[str] = None
    source_question: Optional[Question] = None


@dataclass(order=True)
class PriorityQuestion:
    """优先问题队列中的堆元素。"""

    # heapq 默认小值优先，因此入队时会把外部 priority 取负。
    priority: int
    # 同优先级时保持先进先出。
    sequence: int
    question: Question = field(compare=False)


class CandidateExamState:
    """维护单个候选人的考试状态、题目队列和分支进度。"""

    # 用户状态分数范围；后续出题难度由该分数映射得到。
    MIN_SCORE = 0.0
    MAX_SCORE = 10.0

    # 难度等级边界和单次调整步长。
    MIN_DIFFICULTY_LEVEL = 1
    MAX_DIFFICULTY_LEVEL = 5

    # theta 控制状态分数增长倍率；低于阈值时该维度分支结束。
    INITIAL_THETA = 2.0
    MIN_THETA = 0.3

    def __init__(
        self,
        initial_score: float = 5.0,
        initial_difficulty_level: int = 3,
        dimensions: Optional[List[str]] = None,
    ):
        self.default_score = self._clamp_score(initial_score)
        self.default_difficulty_level = self._clamp_difficulty_level(initial_difficulty_level)

        # 当前维度只是状态视图，实际各维度状态分别存在下面的字典中。
        self.current_dimension: Optional[str] = None
        self.current_score = self.default_score
        self.current_difficulty_level = self.default_difficulty_level

        # 每个维度独立维护用户状态分数、难度、theta 和完成状态。
        self.dimension_scores: Dict[str, float] = {}
        self.dimension_difficulty_levels: Dict[str, int] = {}
        self.dimension_thetas: Dict[str, float] = {}
        self.dimension_finished: Dict[str, bool] = {}

        # 预备队列按 FIFO 出题，优先队列按 priority/sequence 出题。
        self.prepared_question_queues: Dict[str, Deque[Question]] = {}
        self.priority_question_queues: Dict[str, List[PriorityQuestion]] = {}

        # 当前正在等待用户回答的题目，在 AI 真正发出题目时更新。
        self.current_question: Optional[Question] = None
        self.exam_records: List[QARecord] = []
        self._sequence = count()

        self.configured_dimensions: List[str] = []
        self.initialize_dimensions(dimensions or [])

    @property
    def prepared_question_queue(self) -> Deque[Question]:
        """返回所有维度中的预备题目。"""

        questions: Deque[Question] = deque()
        for queue in self.prepared_question_queues.values():
            questions.extend(queue)
        return questions

    @property
    def priority_question_queue(self) -> List[PriorityQuestion]:
        """返回所有维度中的优先题目。"""

        questions: List[PriorityQuestion] = []
        for queue in self.priority_question_queues.values():
            questions.extend(queue)
        return questions

    def _ensure_dimension_state(self, dimension: str) -> str:
        dimension = str(dimension or "default").strip() or "default"
        self.dimension_scores.setdefault(dimension, self.default_score)
        self.dimension_difficulty_levels.setdefault(
            dimension,
            self.default_difficulty_level,
        )
        self.dimension_thetas.setdefault(dimension, self.INITIAL_THETA)
        self.dimension_finished.setdefault(dimension, False)
        self.prepared_question_queues.setdefault(dimension, deque())
        self.priority_question_queues.setdefault(dimension, [])
        return dimension

    def initialize_dimensions(self, dimensions: List[str]) -> None:
        """初始化考试项配置的维度，并为每个维度建立独立状态。"""

        normalized_dimensions = []
        for dimension in dimensions:
            dimension = str(dimension).strip()
            if dimension and dimension not in normalized_dimensions:
                normalized_dimensions.append(dimension)
        self.configured_dimensions = normalized_dimensions
        for dimension in self.configured_dimensions:
            self._ensure_dimension_state(dimension)
        if self.configured_dimensions:
            self.set_current_dimension(self.configured_dimensions[0])

    def get_configured_dimensions(self) -> List[str]:
        """返回本次考试配置的维度列表。"""

        return list(self.configured_dimensions)

    def set_current_dimension(self, dimension: str) -> str:
        """切换当前维度，并同步当前状态视图。"""

        dimension = self._ensure_dimension_state(dimension)
        self.current_dimension = dimension
        self.current_score = self.dimension_scores[dimension]
        self.current_difficulty_level = self.dimension_difficulty_levels[dimension]
        return dimension

    def get_active_question_dimension(
        self,
        question: Optional[Question] = None,
        current_question: Optional[Question] = None,
        last_answered_question: Optional[Question] = None,
        fallback: str = "default",
    ) -> str:
        if question:
            return question.dimension
        if last_answered_question:
            return last_answered_question.dimension
        if current_question:
            return current_question.dimension
        if self.current_dimension:
            return self.current_dimension
        latest_record = self.latest_record()
        if latest_record:
            return latest_record.dimension
        return fallback

    def set_current_question(self, question: Question) -> Question:
        self.current_question = question
        self.set_current_dimension(question.dimension)
        self._log_question_event("current_question_updated", question)
        return question

    def get_current_question(self) -> Optional[Question]:
        return self.current_question

    def clear_current_question(self, question: Optional[Question] = None) -> None:
        if question is None:
            self.current_question = None
            return
        if (
            self.current_question is question
            or (
                self.current_question is not None
                and self.current_question.question_id == question.question_id
            )
        ):
            self.current_question = None

    def plan_prepared_question_generation(
        self,
        dimension: Optional[str] = None,
    ) -> QuestionGenerationPlan:
        if self.all_dimensions_finished():
            return QuestionGenerationPlan(should_finish=True)
        dimension = self._ensure_dimension_state(
            dimension or self.get_next_open_dimension() or self.get_active_question_dimension()
        )
        if self.is_dimension_finished(dimension):
            return QuestionGenerationPlan(should_finish=self.all_dimensions_finished())
        return QuestionGenerationPlan(
            difficulty_level=self.get_difficulty_level(dimension),
            target_dimension=dimension,
        )

    def plan_next_question_after_judge(
        self,
        answered_question: Question,
        judge_res: dict,
    ) -> QuestionGenerationPlan:
        answer_correct = self._is_answer_correct(judge_res)
        dimension = self._ensure_dimension_state(answered_question.dimension)
        current_score = self.get_score(dimension)
        current_theta = self.get_theta(dimension)
        delta_score = float(answered_question.score) * current_theta
        projected_score = current_score + delta_score if answer_correct else current_score - delta_score
        next_score = self._clamp_score(projected_score)
        self.update_score(next_score, dimension)

        target_level = self._difficulty_level_from_score(next_score)
        self.update_difficulty_level(target_level, dimension)
        next_theta = self.update_theta_after_judge(dimension, judge_res)

        if projected_score > self.MAX_SCORE or next_theta < self.MIN_THETA:
            self.mark_dimension_finished(dimension)
            return QuestionGenerationPlan(should_finish=self.all_dimensions_finished())

        return QuestionGenerationPlan(
            difficulty_level=target_level,
            target_dimension=dimension,
            source_question=answered_question,
        )

    def update_theta_after_judge(self, dimension: str, judge_res: dict) -> float:
        dimension = self._ensure_dimension_state(dimension)
        quality = self._judge_quality(judge_res)
        decay_by_quality = {
            "excellent": 1.0,
            "correct": 1.0,
            "average": 0.7,
            "wrong": 0.5,
            "absurd": 0.3,
        }
        self.dimension_thetas[dimension] *= decay_by_quality.get(quality, 1.0)
        return self.dimension_thetas[dimension]

    def _judge_quality(self, judge_res: dict) -> str:
        raw_quality = str(
            judge_res.get("correctness_level")
            or judge_res.get("answer_quality")
            or judge_res.get("quality")
            or judge_res.get("level")
            or judge_res.get("result")
            or ""
        ).strip().lower()
        if any(keyword in raw_quality for keyword in ("excellent", "great", "\u4f18\u79c0")):
            return "excellent"
        if any(keyword in raw_quality for keyword in ("correct", "right", "good", "\u6b63\u786e")):
            return "correct"
        if any(keyword in raw_quality for keyword in ("average", "partial", "\u4e00\u822c", "\u90e8\u5206")):
            return "average"
        if any(keyword in raw_quality for keyword in ("absurd", "irrelevant", "nonsense", "\u79bb\u8c31", "\u65e0\u5173")):
            return "absurd"
        if any(keyword in raw_quality for keyword in ("wrong", "incorrect", "bad", "\u9519\u8bef")):
            return "wrong"

        if self._is_answer_correct(judge_res):
            return "correct"

        try:
            score = float(judge_res.get("score"))
        except (TypeError, ValueError):
            return "wrong"
        if score >= 0.8:
            return "correct"
        if score >= 0.6:
            return "average"
        if score >= 0.3:
            return "wrong"
        return "absurd"

    def _is_answer_correct(self, judge_res: dict) -> bool:
        answer_correct = judge_res.get("answer_correct")
        if isinstance(answer_correct, bool):
            return answer_correct
        if isinstance(answer_correct, str):
            return answer_correct.strip().lower() in {"true", "1", "yes", "correct", "\u6b63\u786e"}
        return bool(answer_correct)

    def mark_dimension_finished(self, dimension: str) -> None:
        dimension = self._ensure_dimension_state(dimension)
        self.dimension_finished[dimension] = True

    def all_dimensions_finished(self) -> bool:
        return bool(self.dimension_finished) and all(self.dimension_finished.values())

    def is_dimension_finished(self, dimension: str) -> bool:
        dimension = self._ensure_dimension_state(dimension)
        return self.dimension_finished[dimension]

    def get_next_open_dimension(self) -> Optional[str]:
        for dimension in self.dimension_finished:
            if not self.dimension_finished[dimension]:
                return dimension
        return None

    def get_theta(self, dimension: Optional[str] = None) -> float:
        dimension = self._ensure_dimension_state(dimension or self.get_active_question_dimension())
        return self.dimension_thetas[dimension]

    def add_prepared_question(self, question: Question) -> None:
        """向预备题队列添加题目。"""

        dimension = self._ensure_dimension_state(question.dimension)
        self.prepared_question_queues[dimension].append(question)
        self._log_question_event("prepared_question_added", question)

    def add_priority_question(self, question: Question, priority: int = 100) -> None:
        """向优先题队列添加题目。"""

        dimension = self._ensure_dimension_state(question.dimension)
        heappush(
            self.priority_question_queues[dimension],
            PriorityQuestion(
                priority=-priority,
                sequence=next(self._sequence),
                question=question,
            ),
        )
        self._log_question_event("priority_question_added", question, priority=priority)

    def has_next_question(self, dimension: Optional[str] = None) -> bool:
        """判断指定维度或全局是否还有可用题目。"""

        if dimension is not None:
            dimension = self._ensure_dimension_state(dimension)
            return bool(
                self.priority_question_queues[dimension]
                or self.prepared_question_queues[dimension]
            )
        return any(self.priority_question_queues.values()) or any(
            self.prepared_question_queues.values()
        )

    def pop_next_question(self, dimension: Optional[str] = None) -> Optional[Question]:
        """按旧规则弹出下一题：先优先队列，再预备队列。"""

        dimension = dimension or self.current_dimension
        if dimension is not None:
            dimension = self._ensure_dimension_state(dimension)
            return self._pop_next_question_from_dimension(dimension)

        question = self._pop_next_priority_question()
        if question:
            return question

        for active_dimension in list(self.prepared_question_queues):
            question = self._pop_next_prepared_question(active_dimension)
            if question:
                return question

        return None

    def pop_ready_question(
        self,
        current_question: Optional[Question] = None,
        last_answered_question: Optional[Question] = None,
    ) -> Optional[Question]:
        if current_question is not None:
            return None

        last_record_index = self._record_index_for_question(last_answered_question)
        if last_answered_question is None and self.exam_records:
            last_record_index = len(self.exam_records) - 1
            last_answered_question = self.exam_records[last_record_index].question

        if last_answered_question is None:
            return self._pop_any_ready_question()

        last_dimension = self._ensure_dimension_state(last_answered_question.dimension)
        if not self.is_dimension_finished(last_dimension):
            direct_follow_up = self._pop_priority_question_matching(
                last_dimension,
                lambda question: (
                    question.based_on_record_index == last_record_index
                    and self._question_has_ready_parent(question)
                ),
            )
            if direct_follow_up:
                return direct_follow_up

        return self._pop_any_ready_question()

    def has_ready_question(
        self,
        current_question: Optional[Question] = None,
        last_answered_question: Optional[Question] = None,
    ) -> bool:
        if current_question is not None:
            return False

        last_record_index = self._record_index_for_question(last_answered_question)
        if last_answered_question is None and self.exam_records:
            last_record_index = len(self.exam_records) - 1
            last_answered_question = self.exam_records[last_record_index].question

        if last_answered_question is None:
            return self._has_any_ready_question()

        last_dimension = self._ensure_dimension_state(last_answered_question.dimension)
        if not self.is_dimension_finished(last_dimension):
            if self._has_priority_question_matching(
                last_dimension,
                lambda question: (
                    question.based_on_record_index == last_record_index
                    and self._question_has_ready_parent(question)
                ),
            ):
                return True

        return self._has_any_ready_question()

    def _record_index_for_question(self, question: Optional[Question]) -> Optional[int]:
        if question is None:
            return None
        for index in range(len(self.exam_records) - 1, -1, -1):
            recorded_question = self.exam_records[index].question
            if recorded_question is question:
                return index
            if recorded_question.question_id == question.question_id:
                return index
        return None

    def _question_has_ready_parent(self, question: Question) -> bool:
        parent_index = question.based_on_record_index
        if parent_index == -1:
            return True
        if not 0 <= parent_index < len(self.exam_records):
            return False
        return bool(self.exam_records[parent_index].question.question_id)

    def _pop_any_ready_question(self, excluded_dimension: Optional[str] = None) -> Optional[Question]:
        question = self._pop_best_ready_priority_question(excluded_dimension)
        if question:
            return question

        for active_dimension in list(self.prepared_question_queues):
            if active_dimension == excluded_dimension or self.is_dimension_finished(active_dimension):
                continue
            question = self._pop_prepared_question_matching(
                active_dimension,
                self._question_has_ready_parent,
            )
            if question:
                return question

        return None

    def _has_any_ready_question(self, excluded_dimension: Optional[str] = None) -> bool:
        for active_dimension, queue in self.priority_question_queues.items():
            if active_dimension == excluded_dimension or self.is_dimension_finished(active_dimension):
                continue
            if any(self._question_has_ready_parent(item.question) for item in queue):
                return True

        for active_dimension, queue in self.prepared_question_queues.items():
            if active_dimension == excluded_dimension or self.is_dimension_finished(active_dimension):
                continue
            if any(self._question_has_ready_parent(question) for question in queue):
                return True

        return False

    def _pop_best_ready_priority_question(
        self,
        excluded_dimension: Optional[str] = None,
    ) -> Optional[Question]:
        best_dimension = None
        best_index = None
        best_item = None
        for active_dimension, queue in self.priority_question_queues.items():
            if active_dimension == excluded_dimension or self.is_dimension_finished(active_dimension):
                continue
            for index, item in enumerate(queue):
                if not self._question_has_ready_parent(item.question):
                    continue
                if best_item is None or item < best_item:
                    best_dimension = active_dimension
                    best_index = index
                    best_item = item

        if best_dimension is None or best_index is None:
            return None

        queue = self.priority_question_queues[best_dimension]
        item = queue.pop(best_index)
        heapify(queue)
        return self._activate_popped_question(item.question)

    def _pop_priority_question_matching(self, dimension: str, predicate) -> Optional[Question]:
        queue = self.priority_question_queues.get(dimension, [])
        best_index = None
        best_item = None
        for index, item in enumerate(queue):
            if not predicate(item.question):
                continue
            if best_item is None or item < best_item:
                best_index = index
                best_item = item

        if best_index is None:
            return None

        item = queue.pop(best_index)
        heapify(queue)
        return self._activate_popped_question(item.question)

    def _has_priority_question_matching(self, dimension: str, predicate) -> bool:
        queue = self.priority_question_queues.get(dimension, [])
        return any(predicate(item.question) for item in queue)

    def _pop_prepared_question_matching(self, dimension: str, predicate) -> Optional[Question]:
        queue = self.prepared_question_queues.get(dimension)
        if not queue:
            return None

        for _ in range(len(queue)):
            question = queue.popleft()
            if predicate(question):
                return self._activate_popped_question(question)
            queue.append(question)

        return None

    def _activate_popped_question(self, question: Question) -> Question:
        self.set_current_dimension(question.dimension)
        return question

    def _pop_next_question_from_dimension(self, dimension: str) -> Optional[Question]:
        question = self._pop_next_priority_question(dimension)
        if question:
            return question
        return self._pop_next_prepared_question(dimension)

    def _pop_next_priority_question(self, dimension: Optional[str] = None) -> Optional[Question]:
        if dimension is not None:
            queue = self.priority_question_queues.get(dimension, [])
            if not queue:
                return None
            question = heappop(queue).question
            return self._activate_popped_question(question)

        best_dimension = None
        best_item = None
        for active_dimension, queue in self.priority_question_queues.items():
            if queue and (best_item is None or queue[0] < best_item):
                best_dimension = active_dimension
                best_item = queue[0]
        if best_dimension is None:
            return None
        question = heappop(self.priority_question_queues[best_dimension]).question
        return self._activate_popped_question(question)

    def _pop_next_prepared_question(self, dimension: str) -> Optional[Question]:
        queue = self.prepared_question_queues.get(dimension)
        if not queue:
            return None
        question = queue.popleft()
        return self._activate_popped_question(question)

    def record_answer(
        self,
        question: Question,
        correctness_level: str = None,
        student_answer: str = None,
        evaluation: Optional[str] = None
    ) -> QARecord:
        """记录一次已完成答题。"""

        self.set_current_dimension(question.dimension)
        record = QARecord(
            question=question,
            correctness_level=correctness_level,
            student_answer=student_answer,
            evaluation=evaluation,
        )
        self.exam_records.append(record)
        self.clear_current_question(question)
        return record

    def get_score(self, dimension: Optional[str] = None) -> float:
        dimension = dimension or self.current_dimension
        if dimension is None:
            return self.default_score
        dimension = self._ensure_dimension_state(dimension)
        return self.dimension_scores[dimension]

    def update_score(
        self,
        next_score: float,
        dimension: Optional[str] = None,
    ) -> None:
        """更新候选人在某个维度上的状态分数。"""

        next_score = self._clamp_score(next_score)
        dimension = dimension or self.current_dimension
        if dimension is None:
            self.default_score = next_score
            self.current_score = next_score
            return
        dimension = self._ensure_dimension_state(dimension)
        self.dimension_scores[dimension] = next_score
        if self.current_dimension == dimension:
            self.current_score = next_score

    def _clamp_difficulty_level(self, level: int) -> int:
        return max(
            self.MIN_DIFFICULTY_LEVEL,
            min(self.MAX_DIFFICULTY_LEVEL, int(level)),
        )

    def _clamp_score(self, score: float) -> float:
        return max(self.MIN_SCORE, min(self.MAX_SCORE, float(score)))

    def _difficulty_level_from_score(self, score: float) -> int:
        score = self._clamp_score(score)
        if score >= self.MAX_SCORE:
            return self.MAX_DIFFICULTY_LEVEL
        return self._clamp_difficulty_level(int(score // 2) + 1)

    def get_difficulty_level(self, dimension: Optional[str] = None) -> int:
        dimension = dimension or self.current_dimension
        if dimension is None:
            return self.default_difficulty_level
        dimension = self._ensure_dimension_state(dimension)
        return self.dimension_difficulty_levels[dimension]

    def update_difficulty_level(
        self,
        next_difficulty_level: int,
        dimension: Optional[str] = None,
    ) -> None:
        """更新指定维度的自适应难度等级。"""

        next_difficulty_level = self._clamp_difficulty_level(next_difficulty_level)
        dimension = dimension or self.current_dimension
        if dimension is None:
            self.default_difficulty_level = next_difficulty_level
            self.current_difficulty_level = next_difficulty_level
            return
        dimension = self._ensure_dimension_state(dimension)
        self.dimension_difficulty_levels[dimension] = next_difficulty_level
        if self.current_dimension == dimension:
            self.current_difficulty_level = next_difficulty_level

    def latest_record(self) -> Optional[QARecord]:
        """返回最近一次已完成答题记录。"""

        if not self.exam_records:
            return None
        return self.exam_records[-1]

    def _format_priority_queue_contents(self) -> str:
        lines: List[str] = []
        for dimension, queue in self.priority_question_queues.items():
            contents = [item.question.content for item in sorted(queue)]
            lines.append(f"{dimension}: {contents}")
        return "\n".join(lines) if lines else "(empty)"

    def _format_prepared_queue_contents(self) -> str:
        lines: List[str] = []
        for dimension, queue in self.prepared_question_queues.items():
            contents = [question.content for question in queue]
            lines.append(f"{dimension}: {contents}")
        return "\n".join(lines) if lines else "(empty)"

    def _log_question_event(self, event: str, question: Question, priority: Optional[int] = None) -> None:
        if event in {"prepared_question_added", "priority_question_added"}:
            logger.info(
                "\n[CandidateExamState] Question queue snapshot\n"
                f"priority_question_queue:\n{self._format_priority_queue_contents()}\n"
                f"prepared_question_queue:\n{self._format_prepared_queue_contents()}"
            )
            return

        extra = f"\npriority: {priority}" if priority is not None else ""
        logger.info(
            "\n[CandidateExamState] Question event\n"
            f"event: {event}\n"
            f"question_id: {question.question_id}\n"
            f"dimension: {question.dimension}\n"
            f"score: {question.score}\n"
            f"source_detail: {question.source_detail}"
            f"{extra}\n"
            f"content: {question.content}"
        )

