import asyncio
import uuid
from datetime import datetime
from typing import Dict, List, Optional

from .connection import connect, ensure_database
from .permissions import build_exam_scope_filter
from .schema import ensure_tables
from .serializers import (
    get_course_id,
    get_exam_item_id,
    get_user_id,
    history_row_to_dict,
    record_row_to_dict,
    to_json,
)


async def save_exam_data(
    current_user: Optional[dict],
    exam_state,
    final_review: Dict[str, object],
) -> str:
    """异步保存一次考试的汇总信息和题目明细，返回本次考试 UUID。"""
    return await asyncio.to_thread(
        _save_exam_data_sync,
        current_user or {},
        exam_state,
        final_review,
    )


async def get_exam_history_by_user(
    user_id: Optional[str],
    course_ids: Optional[List[str]] = None,
    allow_course_scope: bool = False,
    allow_all: bool = False,
) -> List[Dict[str, object]]:
    """根据用户权限范围查询考试历史摘要。"""
    return await asyncio.to_thread(
        _get_exam_history_by_user_sync,
        user_id,
        course_ids,
        allow_course_scope,
        allow_all,
    )


async def get_exam_record_by_exam_id(
    exam_id: str,
    user_id: Optional[str],
    course_ids: Optional[List[str]] = None,
    allow_course_scope: bool = False,
    allow_all: bool = False,
) -> List[Dict[str, object]]:
    """根据考试 ID 和用户权限范围查询考试问答明细。"""
    return await asyncio.to_thread(
        _get_exam_record_by_exam_id_sync,
        exam_id,
        user_id,
        course_ids,
        allow_course_scope,
        allow_all,
    )


def _save_exam_data_sync(
    current_user: dict,
    exam_state,
    final_review: Dict[str, object],
) -> str:
    ensure_database()
    connection = connect(use_database=True)
    exam_id = str(uuid.uuid4())
    try:
        ensure_tables(connection)
        _insert_exam_session(connection, exam_id, current_user, exam_state, final_review)
        _insert_exam_questions(connection, exam_id, exam_state)
        _refresh_exam_item_stats(connection, get_exam_item_id(current_user))
        connection.commit()
        return exam_id
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


def _insert_exam_session(
    connection,
    exam_id: str,
    current_user: dict,
    exam_state,
    final_review: Dict[str, object],
) -> None:
    records = list(getattr(exam_state, "exam_records", []) or [])
    dimension_scores = dict(getattr(exam_state, "dimension_scores", {}) or {})
    record_dimensions = {
        record.question.dimension
        for record in records
        if getattr(record, "question", None) is not None
    }
    dimension_names = set(dimension_scores.keys()) | record_dimensions
    total_score = float(sum(dimension_scores.values()))

    with connection.cursor() as cursor:
        cursor.execute(
            """
            INSERT INTO exam_sessions (
                exam_id,
                user_id,
                course_id,
                exam_item_id,
                candidate_info_json,
                total_score,
                dimension_count,
                question_count,
                dimension_scores_json,
                final_review_json,
                ended_at
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            """,
            (
                exam_id,
                get_user_id(current_user),
                get_course_id(current_user),
                get_exam_item_id(current_user),
                to_json(current_user),
                total_score,
                len(dimension_names),
                len(records),
                to_json(dimension_scores),
                to_json(final_review),
                datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            ),
        )


def _refresh_exam_item_stats(connection, exam_item_id: Optional[str]) -> None:
    if not exam_item_id:
        return
    with connection.cursor() as cursor:
        cursor.execute(
            """
            UPDATE course_exam_items
            SET attempt_count = (
                    SELECT COUNT(*)
                    FROM exam_sessions
                    WHERE exam_item_id = %s
                ),
                participant_count = (
                    SELECT COUNT(DISTINCT user_id)
                    FROM exam_sessions
                    WHERE exam_item_id = %s
                      AND user_id IS NOT NULL
                )
            WHERE exam_item_id = %s
            """,
            (exam_item_id, exam_item_id, exam_item_id),
        )


def _insert_exam_questions(connection, exam_id: str, exam_state) -> None:
    records = list(getattr(exam_state, "exam_records", []) or [])
    rows = []
    for index, record in enumerate(records, start=1):
        question = getattr(record, "question", None)
        if question is None:
            continue
        rows.append((
            exam_id,
            index,
            question.question_id,
            question.content,
            question.dimension,
            float(question.score),
            question.based_on_record_index,
            question.source_detail,
            record.student_answer,
            record.correctness_level,
            record.evaluation,
            question.standard_answer,
        ))

    if not rows:
        return

    with connection.cursor() as cursor:
        cursor.executemany(
            """
            INSERT INTO exam_questions (
                exam_id,
                record_index,
                question_id,
                question_content,
                question_dimension,
                question_score,
                based_on_record_index,
                source_detail,
                student_answer,
                correctness_level,
                evaluation,
                standard_answer
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            """,
            rows,
        )


def _get_exam_history_by_user_sync(
    user_id: Optional[str],
    course_ids: Optional[List[str]],
    allow_course_scope: bool,
    allow_all: bool,
) -> List[Dict[str, object]]:
    ensure_database()
    connection = connect(use_database=True)
    try:
        ensure_tables(connection)
        where_sql, params = build_exam_scope_filter(
            user_id=user_id,
            course_ids=course_ids,
            allow_course_scope=allow_course_scope,
            allow_all=allow_all,
            table_alias="",
        )
        with connection.cursor() as cursor:
            cursor.execute(
                f"""
                SELECT
                    exam_id,
                    course_id,
                    exam_item_id,
                    total_score,
                    dimension_count,
                    question_count,
                    ended_at,
                    created_at
                FROM exam_sessions
                {where_sql}
                ORDER BY ended_at DESC, created_at DESC
                """,
                params,
            )
            rows = cursor.fetchall()
        return [history_row_to_dict(row) for row in rows]
    finally:
        connection.close()


def _get_exam_record_by_exam_id_sync(
    exam_id: str,
    user_id: Optional[str],
    course_ids: Optional[List[str]],
    allow_course_scope: bool,
    allow_all: bool,
) -> List[Dict[str, object]]:
    ensure_database()
    connection = connect(use_database=True)
    try:
        ensure_tables(connection)
        scope_sql, scope_params = build_exam_scope_filter(
            user_id=user_id,
            course_ids=course_ids,
            allow_course_scope=allow_course_scope,
            allow_all=allow_all,
            table_alias="s",
        )
        if scope_sql:
            scope_sql = "AND " + scope_sql.removeprefix("WHERE ").strip()
        with connection.cursor() as cursor:
            cursor.execute(
                f"""
                SELECT
                    q.record_index,
                    q.question_id,
                    q.question_content,
                    q.question_dimension,
                    q.question_score,
                    q.based_on_record_index,
                    q.source_detail,
                    q.student_answer,
                    q.correctness_level,
                    q.evaluation,
                    q.standard_answer,
                    q.created_at
                FROM exam_questions q
                JOIN exam_sessions s ON q.exam_id = s.exam_id
                WHERE q.exam_id = %s
                {scope_sql}
                ORDER BY q.record_index ASC, q.id ASC
                """,
                (exam_id, *scope_params),
            )
            rows = cursor.fetchall()
        return [record_row_to_dict(row) for row in rows]
    finally:
        connection.close()
