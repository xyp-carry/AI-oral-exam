import asyncio
import json
import uuid
from datetime import datetime, timedelta
from typing import Dict, List, Optional

from .connection import connect, ensure_database
from .exam_repository import (
    create_pending_exam_sessions_for_exam_item,
    update_exam_sessions_exam_item_name,
    update_exam_sessions_need_code_repository,
    update_pending_exam_sessions_use_preset_questions,
    update_pending_exam_sessions_scores,
)
from .schema import ensure_tables
from .serializers import exam_item_row_to_dict


async def create_exam_item(
    course_id: str,
    exam_item_name: str,
    created_by: str,
    dimension_scores: Dict[str, float],
    exam_available_valid_times: int,
    description: Optional[str] = None,
    item_type: Optional[str] = None,
    need_code_repository: bool = False,
    use_preset_questions: bool = False,
    enable_report_analysis: bool = False,
    report_total_score: Optional[float] = None,
    report_judge_rule: Optional[str] = None,
) -> Dict[str, object]:
    return await asyncio.to_thread(
        _create_exam_item_sync,
        course_id,
        exam_item_name,
        created_by,
        dimension_scores,
        exam_available_valid_times,
        description,
        item_type,
        need_code_repository,
        use_preset_questions,
        enable_report_analysis,
        report_total_score,
        report_judge_rule,
    )


async def list_exam_items_by_course(course_id: str) -> List[Dict[str, object]]:
    return await asyncio.to_thread(_list_exam_items_by_course_sync, course_id)


async def get_exam_item_by_id(exam_item_id: str) -> Optional[Dict[str, object]]:
    return await asyncio.to_thread(_get_exam_item_by_id_sync, exam_item_id)


async def get_exam_item_course_document_sources(course_id: str, exam_item_id: str) -> List[str]:
    return await asyncio.to_thread(_get_exam_item_course_document_sources_sync, course_id, exam_item_id)


async def add_exam_item_course_document_source(
    course_id: str,
    exam_item_id: str,
    document_name: str,
) -> List[str]:
    return await asyncio.to_thread(
        _add_exam_item_course_document_source_sync,
        course_id,
        exam_item_id,
        document_name,
    )


async def remove_exam_item_course_document_source(
    course_id: str,
    exam_item_id: str,
    document_name: str,
) -> List[str]:
    return await asyncio.to_thread(
        _remove_exam_item_course_document_source_sync,
        course_id,
        exam_item_id,
        document_name,
    )


async def get_available_exam_item_by_id(exam_item_id: str) -> Optional[Dict[str, object]]:
    return await asyncio.to_thread(_get_available_exam_item_by_id_sync, exam_item_id)


async def get_available_exam_item_by_exam_id(exam_id: str) -> Optional[Dict[str, object]]:
    return await asyncio.to_thread(_get_available_exam_item_by_exam_id_sync, exam_id)


async def reset_exam_item_availability(
    exam_item_id: str,
    exam_available_valid_times: int,
) -> Optional[Dict[str, object]]:
    return await asyncio.to_thread(
        _reset_exam_item_availability_sync,
        exam_item_id,
        exam_available_valid_times,
    )


async def update_exam_item(
    course_id: str,
    exam_item_id: str,
    exam_item_name: Optional[str] = None,
    dimension_scores: Optional[Dict[str, float]] = None,
    exam_available_valid_times: Optional[int] = None,
    description: Optional[str] = None,
    item_type: Optional[str] = None,
    need_code_repository: Optional[bool] = None,
    use_preset_questions: Optional[bool] = None,
    enable_report_analysis: Optional[bool] = None,
    report_total_score: Optional[float] = None,
    report_judge_rule: Optional[str] = None,
) -> bool:
    return await asyncio.to_thread(
        _update_exam_item_sync,
        course_id,
        exam_item_id,
        exam_item_name,
        dimension_scores,
        exam_available_valid_times,
        description,
        item_type,
        need_code_repository,
        use_preset_questions,
        enable_report_analysis,
        report_total_score,
        report_judge_rule,
    )


async def delete_exam_item(course_id: str, exam_item_id: str) -> bool:
    return await asyncio.to_thread(_delete_exam_item_sync, course_id, exam_item_id)


def _create_exam_item_sync(
    course_id: str,
    exam_item_name: str,
    created_by: str,
    dimension_scores: Dict[str, float],
    exam_available_valid_times: int,
    description: Optional[str],
    item_type: Optional[str],
    need_code_repository: bool,
    use_preset_questions: bool,
    enable_report_analysis: bool,
    report_total_score: Optional[float],
    report_judge_rule: Optional[str],
) -> Dict[str, object]:
    ensure_database()
    exam_item_id = str(uuid.uuid4())
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    exam_item_name = _normalize_exam_item_name(exam_item_name)
    dimension_scores = _normalize_dimension_scores(dimension_scores)
    dimension_names = list(dimension_scores.keys())
    total_score = float(sum(dimension_scores.values()))
    report_config = _normalize_report_analysis_config(
        enable_report_analysis,
        report_total_score,
        report_judge_rule,
    )
    valid_times = _normalize_exam_available_valid_times(exam_available_valid_times)
    exam_available_from = now
    exam_available_until = (datetime.now() + timedelta(seconds=valid_times)).strftime("%Y-%m-%d %H:%M:%S")
    connection = connect(use_database=True)
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
                    need_code_repository,
                    use_preset_questions,
                    enable_report_analysis,
                    report_total_score,
                    report_judge_rule,
                    exam_available_from,
                    exam_available_until,
                    status,
                    created_by,
                    created_at,
                    updated_at
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, 0, 0, %s, %s, %s, %s, %s, %s, %s, 'active', %s, %s, %s)
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
                    1 if need_code_repository else 0,
                    1 if use_preset_questions else 0,
                    1 if report_config["enable_report_analysis"] else 0,
                    report_config["report_total_score"],
                    report_config["report_judge_rule"],
                    exam_available_from,
                    exam_available_until,
                    created_by,
                    now,
                    now,
                ),
            )
        create_pending_exam_sessions_for_exam_item(
            connection,
            course_id,
            exam_item_id,
            exam_item_name=exam_item_name,
            need_code_repository=need_code_repository,
            use_preset_questions=use_preset_questions,
            dimension_scores=dimension_scores,
            total_score=total_score,
        )
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT
                    exam_item_id, course_id, exam_item_name, description, item_type,
                    dimension_names_json, dimension_scores_json, total_score,
                    participant_count, attempt_count, need_code_repository, use_preset_questions,
                    enable_report_analysis, report_total_score, report_judge_rule,
                    course_document_sources_json, exam_available_from,
                    exam_available_until, status, created_by, created_at, updated_at
                FROM course_exam_items
                WHERE exam_item_id = %s
                """,
                (exam_item_id,),
            )
            created_item = exam_item_row_to_dict(cursor.fetchone())
        connection.commit()
        return created_item
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
                    need_code_repository,
                    use_preset_questions,
                    enable_report_analysis,
                    report_total_score,
                    report_judge_rule,
                    course_document_sources_json,
                    exam_available_from,
                    exam_available_until,
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


def _get_exam_item_by_id_sync(exam_item_id: str) -> Optional[Dict[str, object]]:
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
                    need_code_repository,
                    use_preset_questions,
                    enable_report_analysis,
                    report_total_score,
                    report_judge_rule,
                    course_document_sources_json,
                    exam_available_from,
                    exam_available_until,
                    status,
                    created_by,
                    created_at,
                    updated_at
                FROM course_exam_items
                WHERE exam_item_id = %s
                  AND status = 'active'
                LIMIT 1
                """,
                (exam_item_id,),
            )
            row = cursor.fetchone()
            return exam_item_row_to_dict(row) if row else None
    finally:
        connection.close()


def _get_exam_item_course_document_sources_sync(course_id: str, exam_item_id: str) -> List[str]:
    ensure_database()
    connection = connect(use_database=True)
    try:
        ensure_tables(connection)
        with connection.cursor() as cursor:
            return _select_course_document_sources(cursor, course_id, exam_item_id)
    finally:
        connection.close()


def _add_exam_item_course_document_source_sync(
    course_id: str,
    exam_item_id: str,
    document_name: str,
) -> List[str]:
    ensure_database()
    document_name = _normalize_course_document_source(document_name)
    connection = connect(use_database=True)
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    try:
        ensure_tables(connection)
        with connection.cursor() as cursor:
            sources = _select_course_document_sources(cursor, course_id, exam_item_id, lock=True)
            if document_name in sources:
                raise ValueError("COURSE_DOCUMENT_SOURCE_EXISTS")
            sources.append(document_name)
            cursor.execute(
                """
                UPDATE course_exam_items
                SET course_document_sources_json = %s,
                    updated_at = %s
                WHERE course_id = %s
                  AND exam_item_id = %s
                  AND status = 'active'
                """,
                (_to_json(sources), now, course_id, exam_item_id),
            )
            if cursor.rowcount == 0:
                raise ValueError("EXAM_ITEM_NOT_FOUND")
        connection.commit()
        return sources
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


def _remove_exam_item_course_document_source_sync(
    course_id: str,
    exam_item_id: str,
    document_name: str,
) -> List[str]:
    ensure_database()
    document_name = _normalize_course_document_source(document_name)
    connection = connect(use_database=True)
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    try:
        ensure_tables(connection)
        with connection.cursor() as cursor:
            sources = _select_course_document_sources(cursor, course_id, exam_item_id, lock=True)
            if document_name not in sources:
                raise ValueError("COURSE_DOCUMENT_SOURCE_NOT_FOUND")
            sources = [source for source in sources if source != document_name]
            cursor.execute(
                """
                UPDATE course_exam_items
                SET course_document_sources_json = %s,
                    updated_at = %s
                WHERE course_id = %s
                  AND exam_item_id = %s
                  AND status = 'active'
                """,
                (_to_json(sources), now, course_id, exam_item_id),
            )
            if cursor.rowcount == 0:
                raise ValueError("EXAM_ITEM_NOT_FOUND")
        connection.commit()
        return sources
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


def _get_available_exam_item_by_id_sync(exam_item_id: str) -> Optional[Dict[str, object]]:
    ensure_database()
    connection = connect(use_database=True)
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
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
                    need_code_repository,
                    use_preset_questions,
                    enable_report_analysis,
                    report_total_score,
                    report_judge_rule,
                    course_document_sources_json,
                    exam_available_from,
                    exam_available_until,
                    status,
                    created_by,
                    created_at,
                    updated_at
                FROM course_exam_items
                WHERE exam_item_id = %s
                  AND status = 'active'
                  AND exam_available_from <= %s
                  AND exam_available_until > %s
                LIMIT 1
                """,
                (exam_item_id, now, now),
            )
            row = cursor.fetchone()
            return exam_item_row_to_dict(row) if row else None
    finally:
        connection.close()


def _get_available_exam_item_by_exam_id_sync(exam_id: str) -> Optional[Dict[str, object]]:
    ensure_database()
    connection = connect(use_database=True)
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    try:
        ensure_tables(connection)
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT
                    i.exam_item_id,
                    i.course_id,
                    i.exam_item_name,
                    i.description,
                    i.item_type,
                    i.dimension_names_json,
                    i.dimension_scores_json,
                    i.total_score,
                    i.participant_count,
                    i.attempt_count,
                    i.need_code_repository,
                    i.use_preset_questions,
                    i.enable_report_analysis,
                    i.report_total_score,
                    i.report_judge_rule,
                    i.course_document_sources_json,
                    i.exam_available_from,
                    i.exam_available_until,
                    i.status,
                    i.created_by,
                    i.created_at,
                    i.updated_at
                FROM exam_sessions s
                JOIN course_exam_items i
                  ON s.exam_item_id = i.exam_item_id
                WHERE s.exam_id = %s
                  AND i.status = 'active'
                  AND i.exam_available_from <= %s
                  AND i.exam_available_until > %s
                LIMIT 1
                """,
                (exam_id, now, now),
            )
            row = cursor.fetchone()
            return exam_item_row_to_dict(row) if row else None
    finally:
        connection.close()


def _reset_exam_item_availability_sync(
    exam_item_id: str,
    exam_available_valid_times: int,
) -> Optional[Dict[str, object]]:
    ensure_database()
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    valid_times = _normalize_exam_available_valid_times(exam_available_valid_times)
    expires_at = (datetime.now() + timedelta(seconds=valid_times)).strftime("%Y-%m-%d %H:%M:%S")
    connection = connect(use_database=True)
    try:
        ensure_tables(connection)
        with connection.cursor() as cursor:
            cursor.execute(
                """
                UPDATE course_exam_items
                SET exam_available_from = %s,
                    exam_available_until = %s,
                    updated_at = %s
                WHERE exam_item_id = %s
                  AND status = 'active'
                """,
                (now, expires_at, now, exam_item_id),
            )
            if cursor.rowcount == 0:
                connection.rollback()
                return None
            cursor.execute(
                """
                SELECT
                    exam_item_id, course_id, exam_item_name, description, item_type,
                    dimension_names_json, dimension_scores_json, total_score,
                    participant_count, attempt_count, need_code_repository, use_preset_questions,
                    enable_report_analysis, report_total_score, report_judge_rule,
                    course_document_sources_json, exam_available_from,
                    exam_available_until, status, created_by, created_at, updated_at
                FROM course_exam_items
                WHERE exam_item_id = %s
                """,
                (exam_item_id,),
            )
            updated_item = exam_item_row_to_dict(cursor.fetchone())
        connection.commit()
        return updated_item
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


def _update_exam_item_sync(
    course_id: str,
    exam_item_id: str,
    exam_item_name: Optional[str],
    dimension_scores: Optional[Dict[str, float]],
    exam_available_valid_times: Optional[int],
    description: Optional[str],
    item_type: Optional[str],
    need_code_repository: Optional[bool],
    use_preset_questions: Optional[bool],
    enable_report_analysis: Optional[bool],
    report_total_score: Optional[float],
    report_judge_rule: Optional[str],
) -> bool:
    ensure_database()
    connection = connect(use_database=True)
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    try:
        ensure_tables(connection)
        set_clauses = []
        values = []
        updated_exam_item_name = None
        with connection.cursor() as cursor:
            if exam_item_name is not None:
                exam_item_name = _normalize_exam_item_name(exam_item_name)
                _raise_if_exam_item_name_exists(cursor, course_id, exam_item_name, exclude_exam_item_id=exam_item_id)
                updated_exam_item_name = exam_item_name
                set_clauses.append("exam_item_name = %s")
                values.append(exam_item_name)
            if description is not None:
                set_clauses.append("description = %s")
                values.append(description)
            if item_type is not None:
                set_clauses.append("item_type = %s")
                values.append(item_type)
            if need_code_repository is not None:
                set_clauses.append("need_code_repository = %s")
                values.append(1 if need_code_repository else 0)
            if use_preset_questions is not None:
                set_clauses.append("use_preset_questions = %s")
                values.append(1 if use_preset_questions else 0)
            if (
                enable_report_analysis is not None
                or report_total_score is not None
                or report_judge_rule is not None
            ):
                current_report_config = _select_exam_item_report_config(cursor, course_id, exam_item_id)
                report_config = _normalize_report_analysis_config(
                    (
                        enable_report_analysis
                        if enable_report_analysis is not None
                        else current_report_config["enable_report_analysis"]
                    ),
                    (
                        report_total_score
                        if report_total_score is not None
                        else current_report_config["report_total_score"]
                    ),
                    (
                        report_judge_rule
                        if report_judge_rule is not None
                        else current_report_config["report_judge_rule"]
                    ),
                )
                set_clauses.extend([
                    "enable_report_analysis = %s",
                    "report_total_score = %s",
                    "report_judge_rule = %s",
                ])
                values.extend([
                    1 if report_config["enable_report_analysis"] else 0,
                    report_config["report_total_score"],
                    report_config["report_judge_rule"],
                ])
            if exam_available_valid_times is not None:
                try:
                    valid_times = int(exam_available_valid_times)
                except (TypeError, ValueError) as exc:
                    raise ValueError("EXAM_AVAILABLE_VALID_TIMES_INVALID") from exc
                if valid_times != 0:
                    valid_times = _normalize_exam_available_valid_times(valid_times)
                    set_clauses.extend([
                        "exam_available_from = %s",
                        "exam_available_until = %s",
                    ])
                    values.extend([
                        now,
                        (datetime.now() + timedelta(seconds=valid_times)).strftime("%Y-%m-%d %H:%M:%S"),
                    ])
            updated_dimension_scores = None
            updated_total_score = 0.0
            if dimension_scores is not None:
                dimension_scores = _normalize_dimension_scores(dimension_scores)
                dimension_names = list(dimension_scores.keys())
                updated_dimension_scores = dimension_scores
                updated_total_score = float(sum(dimension_scores.values()))
                set_clauses.extend([
                    "dimension_names_json = %s",
                    "dimension_scores_json = %s",
                    "total_score = %s",
                ])
                values.extend([
                    _to_json(dimension_names),
                    _to_json(dimension_scores),
                    updated_total_score,
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
            if not updated and (need_code_repository is not None or use_preset_questions is not None):
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
                updated = cursor.fetchone() is not None
            if updated and need_code_repository is not None:
                update_exam_sessions_need_code_repository(
                    connection,
                    course_id,
                    exam_item_id,
                    need_code_repository,
                )
            if updated and use_preset_questions is not None:
                update_pending_exam_sessions_use_preset_questions(
                    connection,
                    course_id,
                    exam_item_id,
                    use_preset_questions,
                )
            if updated and updated_exam_item_name is not None:
                update_exam_sessions_exam_item_name(
                    connection,
                    course_id,
                    exam_item_id,
                    updated_exam_item_name,
                )
            if updated and updated_dimension_scores is not None:
                update_pending_exam_sessions_scores(
                    connection,
                    course_id,
                    exam_item_id,
                    updated_dimension_scores,
                    updated_total_score,
                )
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


def _normalize_report_analysis_config(
    enable_report_analysis: bool,
    report_total_score: Optional[float],
    report_judge_rule: Optional[str],
) -> Dict[str, object]:
    enabled = bool(enable_report_analysis)
    normalized_score = None
    if report_total_score is not None:
        try:
            normalized_score = float(report_total_score)
        except (TypeError, ValueError) as exc:
            raise ValueError("REPORT_TOTAL_SCORE_INVALID") from exc
        if normalized_score <= 0:
            raise ValueError("REPORT_TOTAL_SCORE_INVALID")
    normalized_rule = str(report_judge_rule or "").strip() or None
    if enabled:
        if normalized_score is None:
            raise ValueError("REPORT_TOTAL_SCORE_REQUIRED")
        if not normalized_rule:
            raise ValueError("REPORT_JUDGE_RULE_REQUIRED")
    return {
        "enable_report_analysis": enabled,
        "report_total_score": normalized_score,
        "report_judge_rule": normalized_rule,
    }


def _normalize_exam_available_valid_times(exam_available_valid_times: int) -> int:
    try:
        valid_times = int(exam_available_valid_times)
    except (TypeError, ValueError) as exc:
        raise ValueError("EXAM_AVAILABLE_VALID_TIMES_INVALID") from exc
    if valid_times <= 0 or valid_times > 2592000:
        raise ValueError("EXAM_AVAILABLE_VALID_TIMES_INVALID")
    return valid_times


def _normalize_course_document_source(document_name: str) -> str:
    document_name = str(document_name or "").strip()
    if not document_name:
        raise ValueError("COURSE_DOCUMENT_SOURCE_REQUIRED")
    return document_name


def _normalize_course_document_sources(value) -> List[str]:
    if value is None:
        return []
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except (TypeError, ValueError):
            return []
    if not isinstance(value, list):
        return []
    sources = []
    seen = set()
    for item in value:
        source = str(item or "").strip()
        if not source or source in seen:
            continue
        sources.append(source)
        seen.add(source)
    return sources


def _select_exam_item_report_config(cursor, course_id: str, exam_item_id: str) -> Dict[str, object]:
    cursor.execute(
        """
        SELECT enable_report_analysis, report_total_score, report_judge_rule
        FROM course_exam_items
        WHERE course_id = %s
          AND exam_item_id = %s
          AND status = 'active'
        LIMIT 1
        """,
        (course_id, exam_item_id),
    )
    row = cursor.fetchone()
    if row is None:
        raise ValueError("EXAM_ITEM_NOT_FOUND")
    return {
        "enable_report_analysis": bool(row[0]),
        "report_total_score": row[1],
        "report_judge_rule": row[2],
    }


def _select_course_document_sources(
    cursor,
    course_id: str,
    exam_item_id: str,
    lock: bool = False,
) -> List[str]:
    cursor.execute(
        f"""
        SELECT course_document_sources_json
        FROM course_exam_items
        WHERE course_id = %s
          AND exam_item_id = %s
          AND status = 'active'
        LIMIT 1
        {'FOR UPDATE' if lock else ''}
        """,
        (course_id, exam_item_id),
    )
    row = cursor.fetchone()
    if row is None:
        raise ValueError("EXAM_ITEM_NOT_FOUND")
    return _normalize_course_document_sources(row[0])


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
              AND status = 'active'
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
              AND status = 'active'
            LIMIT 1
            """,
            (course_id, exam_item_name),
        )
    if cursor.fetchone() is not None:
        raise ValueError("EXAM_ITEM_NAME_EXISTS")


def _to_json(value) -> str:
    return json.dumps(value, ensure_ascii=False, default=str)
