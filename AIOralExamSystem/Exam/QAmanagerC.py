from copy import deepcopy
from typing import Any, Dict, Iterable, List, Mapping, Optional


Question = Dict[str, Any]


class QAmanagerC:
    """Own C-mode root questions and generated question chains."""

    VALID_RELATIONS = {"deepen", "simplify"}

    def __init__(
        self,
        root_questions: Optional[Iterable[Mapping[str, Any]]] = None,
    ):
        self.roots: List[Question] = []
        for index, raw_question in enumerate(root_questions or [], start=1):
            question = self._normalize(
                dict(raw_question),
                fallback_id=f"root_{index:03d}",
            )
            question.update(
                {
                    "root_question_id": question["question_id"],
                    "parent_question_id": None,
                    "chain_depth": 0,
                    "relation": "root",
                    "root_index": index,
                    "status": "pending",
                }
            )
            self.roots.append(question)

        self.question_chains: Dict[str, List[Question]] = {
            str(root["question_id"]): [] for root in self.roots
        }
        self.current_root_id: Optional[str] = None
        self.current_question: Optional[Question] = None
        self.judgement_history: List[Dict[str, Any]] = []

    def start(self) -> Optional[Question]:
        if self.current_question is not None:
            return deepcopy(self.current_question)
        return self.next_root()

    def resolve_question(
        self,
        question_id: Optional[str] = None,
    ) -> Optional[Question]:
        if self.current_question is None:
            return None
        supplied_id = str(question_id or "")
        current_id = str(self.current_question.get("question_id") or "")
        if supplied_id and supplied_id != current_id:
            raise ValueError("QUESTION_ID_NOT_CURRENT")
        return deepcopy(self.current_question)

    def add_generated_question(
        self,
        parent_question_id: str,
        raw_question: Mapping[str, Any],
        relation: str,
    ) -> Question:
        relation = str(relation or "").strip().lower()
        if relation not in self.VALID_RELATIONS:
            raise ValueError("INVALID_QUESTION_RELATION")
        if self.current_root_id is None or self.current_question is None:
            raise ValueError("NO_ACTIVE_ROOT")

        parent_id = str(parent_question_id or "")
        current_id = str(self.current_question.get("question_id") or "")
        if parent_id != current_id:
            raise ValueError("PARENT_QUESTION_NOT_CURRENT")

        chain = self.question_chains.setdefault(self.current_root_id, [])
        question = self._normalize(
            dict(raw_question),
            fallback_id=(
                f"{self.current_root_id}_followup_{len(chain) + 1:03d}"
            ),
        )
        question.update(
            {
                "root_question_id": self.current_root_id,
                "parent_question_id": parent_id,
                "chain_depth": int(
                    self.current_question.get("chain_depth") or 0
                )
                + 1,
                "relation": relation,
                "root_index": int(
                    self.current_question.get("root_index") or 1
                ),
                "status": "active",
            }
        )
        self.current_question["status"] = "completed"
        chain.append(question)
        self.current_question = question
        return deepcopy(question)

    def record_judgement(
        self,
        question: Mapping[str, Any],
        answer: Mapping[str, Any],
        judgement: Mapping[str, Any],
    ) -> None:
        self.judgement_history.append(
            {
                "root_question_id": self.current_root_id,
                "question": deepcopy(dict(question or {})),
                "answer": deepcopy(dict(answer or {})),
                "judgement": deepcopy(dict(judgement or {})),
            }
        )

    def complete_current_root(self, reason: str) -> Optional[str]:
        if self.current_root_id is None:
            return None
        root_id = self.current_root_id
        for root in self.roots:
            if str(root.get("question_id")) == root_id:
                root["status"] = "completed"
                root["completion_reason"] = str(reason or "")
        if self.current_question is not None:
            self.current_question["status"] = "completed"
        self.current_question = None
        self.current_root_id = None
        return root_id

    def next_root(self) -> Optional[Question]:
        if self.current_question is not None:
            return deepcopy(self.current_question)
        for root in self.roots:
            if root.get("status") == "pending":
                root["status"] = "active"
                self.current_root_id = str(root["question_id"])
                self.current_question = deepcopy(root)
                return deepcopy(self.current_question)
        return None

    @property
    def has_pending_root(self) -> bool:
        return any(root.get("status") == "pending" for root in self.roots)

    @property
    def selection_complete(self) -> bool:
        return self.current_root_id is None and not self.has_pending_root

    def root_by_id(self, root_id: str) -> Optional[Question]:
        for root in self.roots:
            if str(root.get("question_id")) == str(root_id):
                return deepcopy(root)
        return None

    def chain_snapshot(self) -> List[Question]:
        if self.current_root_id is None:
            return []
        root = self.root_by_id(self.current_root_id)
        if root is None:
            return []
        return [root] + deepcopy(
            self.question_chains.get(self.current_root_id, [])
        )

    def relation_count(self, relation: str) -> int:
        return sum(
            1
            for question in self.question_chains.get(
                str(self.current_root_id or ""),
                [],
            )
            if question.get("relation") == relation
        )

    def snapshot(self) -> Dict[str, Any]:
        return {
            "roots": deepcopy(self.roots),
            "question_chains": deepcopy(self.question_chains),
            "current_question": deepcopy(self.current_question),
            "current_root_id": self.current_root_id,
            "has_pending_root": self.has_pending_root,
            "selection_complete": self.selection_complete,
            "judgement_count": len(self.judgement_history),
        }

    @staticmethod
    def _normalize(
        question: Question,
        fallback_id: str,
    ) -> Question:
        normalized = dict(question or {})
        normalized["question_id"] = str(
            normalized.get("question_id")
            or normalized.get("id")
            or normalized.get("preset_question_id")
            or fallback_id
        )
        normalized["content"] = question_content(normalized)
        return normalized


def question_content(question: Mapping[str, Any]) -> str:
    for key in ("content", "question_content", "question", "text"):
        value = question.get(key)
        if isinstance(value, list):
            text = "\n".join(str(item) for item in value).strip()
            if text:
                return text
        if value:
            return str(value).strip()
    return ""
