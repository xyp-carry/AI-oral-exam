"""Persist B/C root questions and ordered follow-ups in exam_questions."""

import asyncio
from collections.abc import Mapping

from sqlalchemy import delete, func, insert, select, update

from .connection import ensure_database, get_engine
from .models import ExamQuestion, ExamSession
from .schema import ensure_tables
from .serializers import to_json

_QUESTION_COLUMNS = (
    "exam_id",
    "record_index",
    "question_id",
    "question_content",
    "question_dimension",
    "question_score",
    "based_on_record_index",
    "source_detail",
    "student_answer",
    "correctness_level",
    "evaluation",
    "standard_answer",
    "is_preset_question",
    "root_question_id",
    "parent_question_id",
    "root_order",
    "followup_order",
    "chain_depth",
    "relation",
    "question_evaluation_json",
    "question_evaluation_status",
)


def _rows_from_review(exam_id: str, review: Mapping) -> list[tuple]:
    if review.get("mode") not in {"B", "C"}:
        raise ValueError("C_EXAM_REVIEW_REQUIRED")
    is_mode_b = review["mode"] == "B"
    roots = review.get("question_reviews")
    if not isinstance(roots, list):
        raise ValueError("C_EXAM_QUESTION_REVIEWS_INVALID")

    rows = []
    seen_ids = set()
    for root_order, root in enumerate(roots, start=1):
        if not isinstance(root, Mapping):
            raise ValueError("C_EXAM_ROOT_REVIEW_INVALID")
        root_id = str(root.get("root_question_id") or "").strip()
        chain = root.get("question_chain")
        if not root_id or not isinstance(chain, list) or not chain:
            raise ValueError("C_EXAM_QUESTION_CHAIN_INVALID")
        previous_id = None
        chain_ids = set()
        for followup_order, item in enumerate(chain):
            if not isinstance(item, Mapping):
                raise ValueError("C_EXAM_QUESTION_INVALID")
            question_id = str(item.get("question_id") or "").strip()
            if not question_id or question_id in seen_ids:
                raise ValueError("C_EXAM_QUESTION_ID_INVALID")
            if followup_order == 0 and question_id != root_id:
                raise ValueError("C_EXAM_ROOT_ID_MISMATCH")
            parent_id = (
                str(item.get("parent_question_id") or previous_id or "").strip()
                if followup_order else None
            )
            if parent_id and parent_id not in chain_ids:
                raise ValueError("C_EXAM_PARENT_QUESTION_INVALID")
            depth = int(item.get("chain_depth") or followup_order)
            evaluation = item.get("question_evaluation") or {}
            evaluation_status = str(item.get("question_evaluation_status") or "not_started")
            evaluation_data = {
                "result": evaluation,
                "status": evaluation_status,
                "error": str(item.get("question_evaluation_error") or ""),
                "answer_version": int(item.get("answer_version") or 0),
            }
            rows.append((
                exam_id,
                len(rows) + 1,
                question_id,
                str(item.get("question") or root.get("question") or ""),
                str(root.get("dimension") or "") if is_mode_b else f"根题 {root_order}",
                float(root.get("max_score") if is_mode_b else root.get("score") or 0)
                if followup_order == 0 else None,
                parent_id or "-1",
                None,
                str(item.get("student_answer") or ""),
                None,
                to_json(evaluation),
                str(item.get("standard_answer") or ""),
                int(is_mode_b and followup_order == 0),
                root_id,
                parent_id,
                root_order,
                followup_order,
                depth,
                str(item.get("relation") or ("root" if followup_order == 0 else "followup")),
                to_json(evaluation_data),
                evaluation_status,
            ))
            seen_ids.add(question_id)
            chain_ids.add(question_id)
            previous_id = question_id
    return rows


def _save_c_exam_questions_sync(exam_id: str, user_id: str, review: Mapping) -> None:
    rows = _rows_from_review(exam_id, review)
    ensure_database()
    with get_engine().begin() as connection:
        ensure_tables(connection)
        session = connection.execute(
            select(ExamSession.user_id, ExamSession.course_id)
            .where(ExamSession.exam_id == exam_id)
            .with_for_update()
        ).fetchone()
        if session is None:
            raise ValueError("EXAM_SESSION_NOT_FOUND")
        if str(session[0] or "") != str(user_id) or not session[1]:
            raise PermissionError("C_EXAM_SESSION_SCOPE_INVALID")
        connection.execute(delete(ExamQuestion).where(ExamQuestion.exam_id == exam_id))
        if rows:
            connection.execute(
                insert(ExamQuestion),
                [dict(zip(_QUESTION_COLUMNS, row)) for row in rows],
            )
        connection.execute(
            update(ExamSession)
            .where(ExamSession.exam_id == exam_id)
            .values(
                question_count=len(rows),
                ended_at=func.coalesce(ExamSession.ended_at, func.now()),
            )
        )


async def save_c_exam_questions(exam_id: str, user_id: str, review: Mapping) -> None:
    """Finish the database write even if the awaiting pipeline is cancelled."""
    task = asyncio.create_task(
        asyncio.to_thread(_save_c_exam_questions_sync, exam_id, user_id, review)
    )
    try:
        await asyncio.shield(task)
    except asyncio.CancelledError:
        await task
        raise
