import asyncio
import json
import uuid
from datetime import datetime
from typing import Dict, List, Optional

from .connection import connect, ensure_database
from .permissions import build_exam_scope_filter
from .schema import ensure_tables
from .serializers import (
    get_course_id,
    get_exam_id,
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


async def get_exam_session_by_exam_id(exam_id: str) -> Optional[Dict[str, object]]:
    return await asyncio.to_thread(_get_exam_session_by_exam_id_sync, exam_id)


async def list_exam_sessions_by_course_and_user(
    course_id: str,
    user_id: str,
) -> List[Dict[str, object]]:
    return await asyncio.to_thread(
        _list_exam_sessions_by_course_and_user_sync,
        course_id,
        user_id,
    )


async def has_repository_url_by_exam_session(
    user_id: str,
    course_id: str,
    exam_id: str,
) -> bool:
    return await asyncio.to_thread(
        _has_repository_url_by_exam_session_sync,
        user_id,
        course_id,
        exam_id,
    )


async def update_exam_session_repository_url(
    user_id: str,
    course_id: str,
    exam_id: str,
    repository_url: str,
) -> bool:
    return await asyncio.to_thread(
        _update_exam_session_repository_url_sync,
        user_id,
        course_id,
        exam_id,
        repository_url,
    )


def _update_exam_session_repository_url_sync(
    user_id: str,
    course_id: str,
    exam_id: str,
    repository_url: str,
) -> bool:
    ensure_database()
    connection = connect(use_database=True)
    try:
        ensure_tables(connection)
        with connection.cursor() as cursor:
            cursor.execute(
                """
                UPDATE exam_sessions
                SET repository_url = %s
                WHERE user_id = %s
                  AND course_id = %s
                  AND exam_id = %s
                """,
                (repository_url, user_id, course_id, exam_id),
            )
            updated = cursor.rowcount > 0
        connection.commit()
        return updated
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


def _has_repository_url_by_exam_session_sync(
    user_id: str,
    course_id: str,
    exam_id: str,
) -> bool:
    ensure_database()
    connection = connect(use_database=True)
    try:
        ensure_tables(connection)
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT 1
                FROM exam_sessions
                WHERE user_id = %s
                  AND course_id = %s
                  AND exam_id = %s
                  AND repository_url IS NOT NULL
                  AND TRIM(repository_url) <> ''
                LIMIT 1
                """,
                (user_id, course_id, exam_id),
            )
            return cursor.fetchone() is not None
    finally:
        connection.close()


def create_pending_exam_sessions_for_exam_item(
    connection,
    course_id: Optional[str],
    exam_item_id: Optional[str],
    need_code_repository: bool = False,
    dimension_scores: Optional[Dict[str, float]] = None,
    total_score: float = 0,
) -> int:
    if not course_id or not exam_item_id:
        return 0
    with connection.cursor() as cursor:
        cursor.execute(
            """
            SELECT student_id
            FROM course_students
            WHERE course_id = %s
              AND status = 'active'
            """,
            (course_id,),
        )
        user_ids = {str(row[0]) for row in cursor.fetchall() if row and row[0]}
        cursor.execute(
            """
            SELECT teacher_id
            FROM course_teachers
            WHERE course_id = %s
              AND status = 'active'
            """,
            (course_id,),
        )
        user_ids.update(str(row[0]) for row in cursor.fetchall() if row and row[0])

    created_count = 0
    with connection.cursor() as cursor:
        for user_id in user_ids:
            created_count += _insert_pending_exam_session_if_missing(
                cursor,
                user_id=user_id,
                course_id=course_id,
                exam_item_id=exam_item_id,
                need_code_repository=need_code_repository,
                dimension_scores=dimension_scores,
                total_score=total_score,
            )
    return created_count


def create_pending_exam_sessions_for_user(
    connection,
    course_id: Optional[str],
    user_id: Optional[str],
) -> int:
    if not course_id or not user_id:
        return 0
    with connection.cursor() as cursor:
        cursor.execute(
            """
            SELECT exam_item_id, need_code_repository, dimension_scores_json, total_score
            FROM course_exam_items
            WHERE course_id = %s
              AND status = 'active'
            """,
            (course_id,),
        )
        exam_items = [
            (str(row[0]), bool(row[1]), _normalize_dimension_scores_json(row[2]), float(row[3] or 0))
            for row in cursor.fetchall()
            if row and row[0]
        ]

    created_count = 0
    with connection.cursor() as cursor:
        for exam_item_id, need_code_repository, dimension_scores, total_score in exam_items:
            created_count += _insert_pending_exam_session_if_missing(
                cursor,
                user_id=user_id,
                course_id=course_id,
                exam_item_id=exam_item_id,
                need_code_repository=need_code_repository,
                dimension_scores=dimension_scores,
                total_score=total_score,
            )
    return created_count


def create_pending_exam_sessions_for_student(
    connection,
    course_id: Optional[str],
    user_id: Optional[str],
) -> int:
    return create_pending_exam_sessions_for_user(connection, course_id, user_id)


def update_exam_sessions_need_code_repository(
    connection,
    course_id: Optional[str],
    exam_item_id: Optional[str],
    need_code_repository: bool,
) -> None:
    if not course_id or not exam_item_id:
        return
    with connection.cursor() as cursor:
        cursor.execute(
            """
            UPDATE exam_sessions
            SET need_code_repository = %s
            WHERE course_id = %s
              AND exam_item_id = %s
            """,
            (1 if need_code_repository else 0, course_id, exam_item_id),
        )


def update_pending_exam_sessions_scores(
    connection,
    course_id: Optional[str],
    exam_item_id: Optional[str],
    dimension_scores: Optional[Dict[str, float]],
    total_score: float,
) -> None:
    if not course_id or not exam_item_id:
        return
    dimension_scores = dimension_scores or {}
    with connection.cursor() as cursor:
        cursor.execute(
            """
            UPDATE exam_sessions
            SET total_score = %s,
                dimension_count = %s,
                dimension_scores_json = %s
            WHERE course_id = %s
              AND exam_item_id = %s
              AND exam_completed = 0
            """,
            (
                float(total_score or 0),
                len(dimension_scores),
                to_json(dimension_scores),
                course_id,
                exam_item_id,
            ),
        )


def _insert_pending_exam_session_if_missing(
    cursor,
    user_id: str,
    course_id: str,
    exam_item_id: str,
    need_code_repository: bool = False,
    dimension_scores: Optional[Dict[str, float]] = None,
    total_score: float = 0,
) -> int:
    dimension_scores = dimension_scores or {}
    cursor.execute(
        """
        INSERT INTO exam_sessions (
            exam_id,
            user_id,
            course_id,
            exam_item_id,
            total_score,
            dimension_count,
            question_count,
            dimension_scores_json,
            need_code_repository,
            exam_completed,
            ended_at
        )
        SELECT %s, %s, %s, %s, %s, %s, 0, %s, %s, 0, NULL
        WHERE NOT EXISTS (
            SELECT 1
            FROM exam_sessions
            WHERE user_id = %s
              AND course_id = %s
              AND exam_item_id = %s
        )
        """,
        (
            str(uuid.uuid4()),
            user_id,
            course_id,
            exam_item_id,
            float(total_score or 0),
            len(dimension_scores),
            to_json(dimension_scores),
            1 if need_code_repository else 0,
            user_id,
            course_id,
            exam_item_id,
        ),
    )
    return cursor.rowcount


def _normalize_dimension_scores_json(value) -> Dict[str, float]:
    if not value:
        return {}
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except json.JSONDecodeError:
            return {}
    if not isinstance(value, dict):
        return {}
    normalized = {}
    for name, score in value.items():
        dimension_name = str(name).strip()
        if not dimension_name:
            continue
        try:
            normalized[dimension_name] = float(score)
        except (TypeError, ValueError):
            normalized[dimension_name] = 0.0
    return normalized


def _exam_session_row_to_dict(row) -> Dict[str, object]:
    fields = (
        "exam_id",
        "user_id",
        "course_id",
        "exam_item_id",
        "total_score",
        "dimension_count",
        "question_count",
        "dimension_scores_json",
        "repository_url",
        "need_code_repository",
        "exam_completed",
        "ended_at",
        "created_at",
    )
    result = dict(zip(fields, row))
    result["dimension_scores"] = _normalize_dimension_scores_json(result.pop("dimension_scores_json", None))
    result["need_code_repository"] = bool(result.get("need_code_repository"))
    result["exam_completed"] = bool(result.get("exam_completed"))
    for key in ("ended_at", "created_at"):
        value = result.get(key)
        if value is not None:
            result[key] = value.strftime("%Y-%m-%d %H:%M:%S")
    return result


def _list_exam_sessions_by_course_and_user_sync(
    course_id: str,
    user_id: str,
) -> List[Dict[str, object]]:
    ensure_database()
    connection = connect(use_database=True)
    try:
        ensure_tables(connection)
        print(course_id, user_id)
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT
                    exam_id,
                    user_id,
                    course_id,
                    exam_item_id,
                    total_score,
                    dimension_count,
                    question_count,
                    dimension_scores_json,
                    repository_url,
                    need_code_repository,
                    exam_completed,
                    ended_at,
                    created_at
                FROM exam_sessions
                WHERE course_id = %s
                  AND user_id = %s
                ORDER BY created_at DESC
                """,
                (course_id, user_id),
            )
            rows = cursor.fetchall()
        return [_exam_session_row_to_dict(row) for row in rows]
    finally:
        connection.close()


def _get_exam_session_by_exam_id_sync(exam_id: str) -> Optional[Dict[str, object]]:
    ensure_database()
    connection = connect(use_database=True)
    try:
        ensure_tables(connection)
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT
                    exam_id,
                    user_id,
                    course_id,
                    exam_item_id,
                    total_score,
                    dimension_count,
                    question_count,
                    dimension_scores_json,
                    repository_url,
                    need_code_repository,
                    exam_completed,
                    ended_at,
                    created_at
                FROM exam_sessions
                WHERE exam_id = %s
                LIMIT 1
                """,
                (exam_id,),
            )
            row = cursor.fetchone()
        return _exam_session_row_to_dict(row) if row else None
    finally:
        connection.close()


def _save_exam_data_sync(
    current_user: dict,
    exam_state,
    final_review: Dict[str, object],
) -> str:
    ensure_database()
    connection = connect(use_database=True)
    try:
        ensure_tables(connection)
        user_id = get_user_id(current_user)
        course_id = get_course_id(current_user)
        exam_item_id = get_exam_item_id(current_user)
        exam_id = (
            get_exam_id(current_user)
            or _find_existing_exam_id(connection, user_id, course_id, exam_item_id)
            or str(uuid.uuid4())
        )
        session_refs = _get_exam_session_refs_by_exam_id(connection, exam_id)
        if session_refs:
            user_id = user_id or session_refs["user_id"]
            course_id = course_id or session_refs["course_id"]
            exam_item_id = exam_item_id or session_refs["exam_item_id"]
        _upsert_exam_session(
            connection,
            exam_id,
            current_user,
            exam_state,
            final_review,
            user_id,
            course_id,
            exam_item_id,
        )
        _delete_exam_questions(connection, exam_id)
        _insert_exam_questions(connection, exam_id, exam_state)
        _refresh_exam_item_stats(connection, exam_item_id)
        connection.commit()
        return exam_id
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


def _get_exam_session_refs_by_exam_id(connection, exam_id: Optional[str]) -> Optional[Dict[str, Optional[str]]]:
    if not exam_id:
        return None
    with connection.cursor() as cursor:
        cursor.execute(
            """
            SELECT user_id, course_id, exam_item_id
            FROM exam_sessions
            WHERE exam_id = %s
            LIMIT 1
            """,
            (exam_id,),
        )
        row = cursor.fetchone()
    if not row:
        return None
    return {
        "user_id": str(row[0]) if row[0] is not None else None,
        "course_id": str(row[1]) if row[1] is not None else None,
        "exam_item_id": str(row[2]) if row[2] is not None else None,
    }


def _find_existing_exam_id(
    connection,
    user_id: Optional[str],
    course_id: Optional[str],
    exam_item_id: Optional[str],
) -> Optional[str]:
    if not user_id or not course_id or not exam_item_id:
        return None
    with connection.cursor() as cursor:
        cursor.execute(
            """
            SELECT exam_id
            FROM exam_sessions
            WHERE user_id = %s
              AND course_id = %s
              AND exam_item_id = %s
            ORDER BY ended_at DESC, created_at DESC
            LIMIT 1
            """,
            (user_id, course_id, exam_item_id),
        )
        row = cursor.fetchone()
    return str(row[0]) if row else None


def _upsert_exam_session(
    connection,
    exam_id: str,
    current_user: dict,
    exam_state,
    final_review: Dict[str, object],
    user_id: Optional[str],
    course_id: Optional[str],
    exam_item_id: Optional[str],
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
    ended_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    with connection.cursor() as cursor:
        cursor.execute(
            """
            SELECT COUNT(*)
            FROM exam_sessions
            WHERE exam_id = %s
            """,
            (exam_id,),
        )
        exists = cursor.fetchone()[0] > 0

        if exists:
            cursor.execute(
                """
                UPDATE exam_sessions
                SET user_id = %s,
                    course_id = %s,
                    exam_item_id = %s,
                    candidate_info_json = %s,
                    total_score = %s,
                    dimension_count = %s,
                    question_count = %s,
                    dimension_scores_json = %s,
                    final_review_json = %s,
                    exam_completed = %s,
                    ended_at = %s
                WHERE exam_id = %s
                """,
                (
                    user_id,
                    course_id,
                    exam_item_id,
                    to_json(current_user),
                    total_score,
                    len(dimension_names),
                    len(records),
                    to_json(dimension_scores),
                    to_json(final_review),
                    1,
                    ended_at,
                    exam_id,
                ),
            )
            return

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
                exam_completed,
                ended_at
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            """,
            (
                exam_id,
                user_id,
                course_id,
                exam_item_id,
                to_json(current_user),
                total_score,
                len(dimension_names),
                len(records),
                to_json(dimension_scores),
                to_json(final_review),
                1,
                ended_at,
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
                      AND exam_completed = 1
                ),
                participant_count = (
                    SELECT COUNT(DISTINCT user_id)
                    FROM exam_sessions
                    WHERE exam_item_id = %s
                      AND user_id IS NOT NULL
                      AND exam_completed = 1
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


def _delete_exam_questions(connection, exam_id: str) -> None:
    with connection.cursor() as cursor:
        cursor.execute(
            "DELETE FROM exam_questions WHERE exam_id = %s",
            (exam_id,),
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
        completed_filter = f"{where_sql} AND exam_completed = 1" if where_sql else "WHERE exam_completed = 1"
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
                {completed_filter}
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
