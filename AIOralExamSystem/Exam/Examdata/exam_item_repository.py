import asyncio
import json
import uuid
from datetime import datetime
from typing import Dict, List, Optional

from .connection import connect, ensure_database
from .schema import ensure_tables
from .serializers import exam_item_row_to_dict


async def create_exam_item(
    course_id: str,
    exam_item_name: str,
    created_by: str,
    dimension_scores: Dict[str, float],
    description: Optional[str] = None,
    item_type: Optional[str] = None,
) -> str:
    return await asyncio.to_thread(
        _create_exam_item_sync,
        course_id,
        exam_item_name,
        created_by,
        dimension_scores,
        description,
        item_type,
    )


async def list_exam_items_by_course(course_id: str) -> List[Dict[str, object]]:
    return await asyncio.to_thread(_list_exam_items_by_course_sync, course_id)


async def update_exam_item(
    course_id: str,
    exam_item_id: str,
    exam_item_name: Optional[str] = None,
    dimension_scores: Optional[Dict[str, float]] = None,
    description: Optional[str] = None,
    item_type: Optional[str] = None,
) -> bool:
    return await asyncio.to_thread(
        _update_exam_item_sync,
        course_id,
        exam_item_id,
        exam_item_name,
        dimension_scores,
        description,
        item_type,
    )


async def delete_exam_item(course_id: str, exam_item_id: str) -> bool:
    return await asyncio.to_thread(_delete_exam_item_sync, course_id, exam_item_id)


def _create_exam_item_sync(
    course_id: str,
    exam_item_name: str,
    created_by: str,
    dimension_scores: Dict[str, float],
    description: Optional[str],
    item_type: Optional[str],
) -> str:
    ensure_database()
    connection = connect(use_database=True)
    exam_item_id = str(uuid.uuid4())
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    exam_item_name = _normalize_exam_item_name(exam_item_name)
    dimension_scores = _normalize_dimension_scores(dimension_scores)
    dimension_names = list(dimension_scores.keys())
    total_score = float(sum(dimension_scores.values()))
    try:
        ensure_tables(connection)
        with connection.cursor() as cursor:
            _raise_if_exam_item_name_exists(cursor, course_id, exam_item_name)
            cursor.execute(
                """
                INSERT INTO course_exam_items (
                    exam_item_id,
                    course_id,
                    exam_item_name,
                    description,
                    item_type,
                    dimension_names_json,
                    dimension_scores_json,
                    total_score,
                    participant_count,
                    attempt_count,
                    status,
                    created_by,
                    created_at,
                    updated_at
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, 0, 0, 'active', %s, %s, %s)
                """,
                (
                    exam_item_id,
                    course_id,
                    exam_item_name,
                    description,
                    item_type,
                    _to_json(dimension_names),
                    _to_json(dimension_scores),
                    total_score,
                    created_by,
                    now,
                    now,
                ),
            )
        connection.commit()
        return exam_item_id
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


def _list_exam_items_by_course_sync(course_id: str) -> List[Dict[str, object]]:
    ensure_database()
    connection = connect(use_database=True)
    try:
        ensure_tables(connection)
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT
                    exam_item_id,
                    course_id,
                    exam_item_name,
                    description,
                    item_type,
                    dimension_names_json,
                    dimension_scores_json,
                    total_score,
                    participant_count,
                    attempt_count,
                    status,
                    created_by,
                    created_at,
                    updated_at
                FROM course_exam_items
                WHERE course_id = %s
                  AND status = 'active'
                ORDER BY created_at DESC
                """,
                (course_id,),
            )
            return [exam_item_row_to_dict(row) for row in cursor.fetchall()]
    finally:
        connection.close()


def _update_exam_item_sync(
    course_id: str,
    exam_item_id: str,
    exam_item_name: Optional[str],
    dimension_scores: Optional[Dict[str, float]],
    description: Optional[str],
    item_type: Optional[str],
) -> bool:
    ensure_database()
    connection = connect(use_database=True)
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    try:
        ensure_tables(connection)
        set_clauses = []
        values = []
        with connection.cursor() as cursor:
            if exam_item_name is not None:
                exam_item_name = _normalize_exam_item_name(exam_item_name)
                _raise_if_exam_item_name_exists(cursor, course_id, exam_item_name, exclude_exam_item_id=exam_item_id)
                set_clauses.append("exam_item_name = %s")
                values.append(exam_item_name)
            if description is not None:
                set_clauses.append("description = %s")
                values.append(description)
            if item_type is not None:
                set_clauses.append("item_type = %s")
                values.append(item_type)
            if dimension_scores is not None:
                dimension_scores = _normalize_dimension_scores(dimension_scores)
                dimension_names = list(dimension_scores.keys())
                set_clauses.extend([
                    "dimension_names_json = %s",
                    "dimension_scores_json = %s",
                    "total_score = %s",
                ])
                values.extend([
                    _to_json(dimension_names),
                    _to_json(dimension_scores),
                    float(sum(dimension_scores.values())),
                ])
            if not set_clauses:
                return True
            set_clauses.append("updated_at = %s")
            values.append(now)
            values.extend([course_id, exam_item_id])
            cursor.execute(
                f"""
                UPDATE course_exam_items
                SET {', '.join(set_clauses)}
                WHERE course_id = %s
                  AND exam_item_id = %s
                  AND status = 'active'
                """,
                values,
            )
            updated = cursor.rowcount > 0
        connection.commit()
        return updated
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


def _delete_exam_item_sync(course_id: str, exam_item_id: str) -> bool:
    ensure_database()
    connection = connect(use_database=True)
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    try:
        ensure_tables(connection)
        with connection.cursor() as cursor:
            cursor.execute(
                """
                UPDATE course_exam_items
                SET status = 'deleted',
                    updated_at = %s
                WHERE course_id = %s
                  AND exam_item_id = %s
                  AND status = 'active'
                """,
                (now, course_id, exam_item_id),
            )
            deleted = cursor.rowcount > 0
        connection.commit()
        return deleted
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


def _normalize_exam_item_name(exam_item_name: str) -> str:
    exam_item_name = (exam_item_name or "").strip()
    if not exam_item_name:
        raise ValueError("EXAM_ITEM_NAME_REQUIRED")
    return exam_item_name


def _normalize_dimension_scores(dimension_scores: Dict[str, float]) -> Dict[str, float]:
    if not dimension_scores:
        raise ValueError("EXAM_ITEM_DIMENSIONS_REQUIRED")
    normalized: Dict[str, float] = {}
    for name, score in dimension_scores.items():
        dimension_name = str(name).strip()
        if not dimension_name:
            raise ValueError("EXAM_ITEM_DIMENSION_NAME_REQUIRED")
        normalized[dimension_name] = float(score)
    return normalized


def _raise_if_exam_item_name_exists(
    cursor,
    course_id: str,
    exam_item_name: str,
    exclude_exam_item_id: Optional[str] = None,
) -> None:
    if exclude_exam_item_id:
        cursor.execute(
            """
            SELECT 1
            FROM course_exam_items
            WHERE course_id = %s
              AND exam_item_name = %s
              AND exam_item_id <> %s
            LIMIT 1
            """,
            (course_id, exam_item_name, exclude_exam_item_id),
        )
    else:
        cursor.execute(
            """
            SELECT 1
            FROM course_exam_items
            WHERE course_id = %s
              AND exam_item_name = %s
            LIMIT 1
            """,
            (course_id, exam_item_name),
        )
    if cursor.fetchone() is not None:
        raise ValueError("EXAM_ITEM_NAME_EXISTS")


def _to_json(value) -> str:
    return json.dumps(value, ensure_ascii=False, default=str)
