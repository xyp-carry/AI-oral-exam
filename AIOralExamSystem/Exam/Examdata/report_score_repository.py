import asyncio
import json
import uuid
from datetime import datetime
from typing import Dict, List, Optional

from .connection import connect, ensure_database
from .schema import ensure_tables


async def create_report_score(
    course_id: str,
    exam_item_id: str,
    user_id: str,
    report_score: float,
    report_total_score: float,
    report_result: Optional[Dict[str, object]] = None,
    exam_id: Optional[str] = None,
) -> Dict[str, object]:
    return await asyncio.to_thread(
        _create_report_score_sync,
        course_id,
        exam_item_id,
        user_id,
        report_score,
        report_total_score,
        report_result,
        exam_id,
    )


async def get_latest_report_score(
    course_id: str,
    exam_item_id: str,
    user_id: str,
) -> Optional[Dict[str, object]]:
    return await asyncio.to_thread(
        _get_latest_report_score_sync,
        course_id,
        exam_item_id,
        user_id,
    )


async def list_report_scores_by_course_item(
    course_id: str,
    exam_item_id: str,
) -> List[Dict[str, object]]:
    return await asyncio.to_thread(
        _list_report_scores_by_course_item_sync,
        course_id,
        exam_item_id,
    )


def _create_report_score_sync(
    course_id: str,
    exam_item_id: str,
    user_id: str,
    report_score: float,
    report_total_score: float,
    report_result: Optional[Dict[str, object]],
    exam_id: Optional[str],
) -> Dict[str, object]:
    ensure_database()
    course_id = _normalize_required_text(course_id, "COURSE_ID_REQUIRED")
    exam_item_id = _normalize_required_text(exam_item_id, "EXAM_ITEM_ID_REQUIRED")
    user_id = _normalize_required_text(user_id, "USER_ID_REQUIRED")
    exam_id = _normalize_optional_text(exam_id)
    report_total_score = _normalize_positive_score(report_total_score, "REPORT_TOTAL_SCORE_INVALID")
    report_score = _clamp_score(report_score, report_total_score)
    report_result = _sanitize_report_result(report_result)
    report_score_id = str(uuid.uuid4())
    now = _now()
    connection = connect(use_database=True)
    try:
        ensure_tables(connection)
        with connection.cursor() as cursor:
            _raise_if_exam_item_missing(cursor, course_id, exam_item_id)
            cursor.execute(
                """
                UPDATE exam_report_scores
                SET status = 'deleted',
                    updated_at = %s
                WHERE course_id = %s
                  AND exam_item_id = %s
                  AND user_id = %s
                  AND status = 'active'
                """,
                (now, course_id, exam_item_id, user_id),
            )
            cursor.execute(
                """
                INSERT INTO exam_report_scores (
                    report_score_id,
                    course_id,
                    exam_item_id,
                    user_id,
                    exam_id,
                    report_score,
                    report_total_score,
                    report_result_json,
                    status,
                    created_at,
                    updated_at
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, 'active', %s, %s)
                """,
                (
                    report_score_id,
                    course_id,
                    exam_item_id,
                    user_id,
                    exam_id,
                    report_score,
                    report_total_score,
                    _to_json(report_result),
                    now,
                    now,
                ),
            )
            created = _select_report_score_by_id(cursor, report_score_id)
        connection.commit()
        return created
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


def _get_latest_report_score_sync(
    course_id: str,
    exam_item_id: str,
    user_id: str,
) -> Optional[Dict[str, object]]:
    ensure_database()
    connection = connect(use_database=True)
    try:
        ensure_tables(connection)
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT
                    report_score_id,
                    course_id,
                    exam_item_id,
                    user_id,
                    exam_id,
                    report_score,
                    report_total_score,
                    report_result_json,
                    status,
                    created_at,
                    updated_at
                FROM exam_report_scores
                WHERE course_id = %s
                  AND exam_item_id = %s
                  AND user_id = %s
                  AND status = 'active'
                ORDER BY updated_at DESC
                LIMIT 1
                """,
                (course_id, exam_item_id, user_id),
            )
            row = cursor.fetchone()
            return _report_score_row_to_dict(row) if row else None
    finally:
        connection.close()


def _list_report_scores_by_course_item_sync(
    course_id: str,
    exam_item_id: str,
) -> List[Dict[str, object]]:
    ensure_database()
    connection = connect(use_database=True)
    try:
        ensure_tables(connection)
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT
                    report_score_id,
                    course_id,
                    exam_item_id,
                    user_id,
                    exam_id,
                    report_score,
                    report_total_score,
                    report_result_json,
                    status,
                    created_at,
                    updated_at
                FROM exam_report_scores
                WHERE course_id = %s
                  AND exam_item_id = %s
                  AND status = 'active'
                ORDER BY updated_at DESC
                """,
                (course_id, exam_item_id),
            )
            return [_report_score_row_to_dict(row) for row in cursor.fetchall()]
    finally:
        connection.close()


def _select_report_score_by_id(cursor, report_score_id: str) -> Dict[str, object]:
    cursor.execute(
        """
        SELECT
            report_score_id,
            course_id,
            exam_item_id,
            user_id,
            exam_id,
            report_score,
            report_total_score,
            report_result_json,
            status,
            created_at,
            updated_at
        FROM exam_report_scores
        WHERE report_score_id = %s
        LIMIT 1
        """,
        (report_score_id,),
    )
    row = cursor.fetchone()
    if row is None:
        raise ValueError("REPORT_SCORE_NOT_FOUND")
    return _report_score_row_to_dict(row)


def _raise_if_exam_item_missing(cursor, course_id: str, exam_item_id: str) -> None:
    cursor.execute(
        """
        SELECT 1
        FROM course_exam_items
        WHERE course_id = %s
          AND exam_item_id = %s
          AND status = 'active'
        LIMIT 1
        """,
        (course_id, exam_item_id),
    )
    if cursor.fetchone() is None:
        raise ValueError("EXAM_ITEM_NOT_FOUND")


def _report_score_row_to_dict(row) -> Dict[str, object]:
    fields = (
        "report_score_id",
        "course_id",
        "exam_item_id",
        "user_id",
        "exam_id",
        "report_score",
        "report_total_score",
        "report_result_json",
        "status",
        "created_at",
        "updated_at",
    )
    result = dict(zip(fields, row))
    result["report_result"] = _json_loads(result.pop("report_result_json"), {})
    for key in ("created_at", "updated_at"):
        value = result.get(key)
        if value is not None:
            result[key] = value.strftime("%Y-%m-%d %H:%M:%S")
    return result


def _sanitize_report_result(value: Optional[Dict[str, object]]) -> Dict[str, object]:
    if not isinstance(value, dict):
        return {}
    allowed_keys = {
        "completion_level",
        "content_completeness",
        "key_points_coverage",
        "reference_consistency",
        "oral_support",
        "deductions",
        "suggestions",
        "summary",
    }
    sanitized = {}
    for key in allowed_keys:
        if key in value:
            sanitized[key] = value[key]
    return sanitized


def _normalize_required_text(value: str, error_code: str) -> str:
    normalized = str(value or "").strip()
    if not normalized:
        raise ValueError(error_code)
    return normalized


def _normalize_optional_text(value: Optional[str]) -> Optional[str]:
    normalized = str(value or "").strip()
    return normalized or None


def _normalize_positive_score(value, error_code: str) -> float:
    try:
        score = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(error_code) from exc
    if score <= 0:
        raise ValueError(error_code)
    return score


def _clamp_score(value, total_score: float) -> float:
    try:
        score = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError("REPORT_SCORE_INVALID") from exc
    return max(0.0, min(total_score, score))


def _to_json(value) -> str:
    return json.dumps(value, ensure_ascii=False, default=str)


def _json_loads(value, default):
    if value is None:
        return default
    if isinstance(value, (list, dict)):
        return value
    try:
        return json.loads(value)
    except (TypeError, ValueError):
        return default


def _now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")
