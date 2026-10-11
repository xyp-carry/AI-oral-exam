import asyncio
import json
import uuid
from datetime import datetime
from typing import Dict, List, Optional

from sqlalchemy import select, update

from .connection import ensure_database, get_engine
from .models import CourseExamItem, ExamReportScore
from .schema import ensure_tables

_REPORT_SCORE_COLUMNS = (
    ExamReportScore.report_score_id,
    ExamReportScore.course_id,
    ExamReportScore.exam_item_id,
    ExamReportScore.user_id,
    ExamReportScore.exam_id,
    ExamReportScore.report_score,
    ExamReportScore.report_total_score,
    ExamReportScore.report_result_json,
    ExamReportScore.status,
    ExamReportScore.created_at,
    ExamReportScore.updated_at,
)


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
    with get_engine().begin() as connection:
        ensure_tables(connection)
        _raise_if_exam_item_missing(connection, course_id, exam_item_id)
        connection.execute(
            update(ExamReportScore)
            .where(
                ExamReportScore.course_id == course_id,
                ExamReportScore.exam_item_id == exam_item_id,
                ExamReportScore.user_id == user_id,
                ExamReportScore.status == "active",
            )
            .values(status="deleted", updated_at=now)
        )
        connection.execute(
            ExamReportScore.__table__.insert().values(
                report_score_id=report_score_id,
                course_id=course_id,
                exam_item_id=exam_item_id,
                user_id=user_id,
                exam_id=exam_id,
                report_score=report_score,
                report_total_score=report_total_score,
                report_result_json=_to_json(report_result),
                status="active",
                created_at=now,
                updated_at=now,
            )
        )
        created = _select_report_score_by_id(connection, report_score_id)
    return created


def _get_latest_report_score_sync(
    course_id: str,
    exam_item_id: str,
    user_id: str,
) -> Optional[Dict[str, object]]:
    ensure_database()
    with get_engine().begin() as connection:
        ensure_tables(connection)
        row = connection.execute(
            select(*_REPORT_SCORE_COLUMNS)
            .where(
                ExamReportScore.course_id == course_id,
                ExamReportScore.exam_item_id == exam_item_id,
                ExamReportScore.user_id == user_id,
                ExamReportScore.status == "active",
            )
            .order_by(ExamReportScore.updated_at.desc())
            .limit(1)
        ).fetchone()
        return _report_score_row_to_dict(row) if row else None


def _list_report_scores_by_course_item_sync(
    course_id: str,
    exam_item_id: str,
) -> List[Dict[str, object]]:
    ensure_database()
    with get_engine().begin() as connection:
        ensure_tables(connection)
        rows = connection.execute(
            select(*_REPORT_SCORE_COLUMNS)
            .where(
                ExamReportScore.course_id == course_id,
                ExamReportScore.exam_item_id == exam_item_id,
                ExamReportScore.status == "active",
            )
            .order_by(ExamReportScore.updated_at.desc())
        ).fetchall()
        return [_report_score_row_to_dict(row) for row in rows]


def _select_report_score_by_id(connection, report_score_id: str) -> Dict[str, object]:
    row = connection.execute(
        select(*_REPORT_SCORE_COLUMNS)
        .where(ExamReportScore.report_score_id == report_score_id)
        .limit(1)
    ).fetchone()
    if row is None:
        raise ValueError("REPORT_SCORE_NOT_FOUND")
    return _report_score_row_to_dict(row)


def _raise_if_exam_item_missing(connection, course_id: str, exam_item_id: str) -> None:
    row = connection.execute(
        select(CourseExamItem.exam_item_id)
        .where(
            CourseExamItem.course_id == course_id,
            CourseExamItem.exam_item_id == exam_item_id,
            CourseExamItem.status == "active",
        )
        .limit(1)
    ).fetchone()
    if row is None:
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


def _now() -> datetime:
    return datetime.now()
