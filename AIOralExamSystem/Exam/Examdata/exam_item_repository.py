import asyncio
import json
import math
from datetime import datetime, timedelta
from typing import Dict, List, Optional

from sqlalchemy import func, literal, select, update

from .connection import ensure_database, get_engine, mysql_error_code
from .exam_repository import (
    create_pending_exam_sessions_for_exam_item,
    update_exam_sessions_exam_item_name,
    update_exam_sessions_need_code_repository,
    update_pending_exam_sessions_use_preset_questions,
    update_pending_exam_sessions_scores,
)
from .models import CourseExamItem, ExamPresetQuestion, ExamSession
from .schema import ensure_tables
from .serializers import exam_item_row_to_dict

_EXAM_ITEM_COLUMNS = (
    CourseExamItem.exam_item_id,
    CourseExamItem.course_id,
    CourseExamItem.exam_item_name,
    CourseExamItem.description,
    CourseExamItem.item_type,
    CourseExamItem.dimension_names_json,
    CourseExamItem.dimension_scores_json,
    CourseExamItem.total_score,
    CourseExamItem.participant_count,
    CourseExamItem.attempt_count,
    CourseExamItem.need_code_repository,
    CourseExamItem.use_preset_questions,
    CourseExamItem.enable_report_analysis,
    CourseExamItem.report_total_score,
    CourseExamItem.report_judge_rule,
    CourseExamItem.course_document_sources_json,
    CourseExamItem.exam_available_from,
    CourseExamItem.exam_available_until,
    CourseExamItem.status,
    CourseExamItem.created_by,
    CourseExamItem.created_at,
    CourseExamItem.updated_at,
    CourseExamItem.c_max_probe_turns,
    CourseExamItem.analysis_question_count,
)


async def create_exam_item(
    course_id: str,
    exam_item_id: str,
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
    c_max_probe_turns: int = 2,
    analysis_question_count: int = 3,
) -> Dict[str, object]:
    return await asyncio.to_thread(
        _create_exam_item_sync,
        course_id,
        exam_item_id,
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
        c_max_probe_turns,
        analysis_question_count,
    )


async def exam_item_id_exists(exam_item_id: str) -> bool:
    return await asyncio.to_thread(_exam_item_id_exists_sync, exam_item_id)


async def list_exam_items_by_course(course_id: str) -> List[Dict[str, object]]:
    return await asyncio.to_thread(_list_exam_items_by_course_sync, course_id)


async def get_exam_item_by_id(exam_item_id: str) -> Optional[Dict[str, object]]:
    return await asyncio.to_thread(_get_exam_item_by_id_sync, exam_item_id)


async def get_exam_item_for_edit(
    course_id: str, exam_item_id: str
) -> Optional[Dict[str, object]]:
    return await asyncio.to_thread(_get_exam_item_for_edit_sync, course_id, exam_item_id)


async def get_exam_item_course_document_sources(course_id: str, exam_item_id: str) -> List[str]:
    return await asyncio.to_thread(_get_exam_item_course_document_sources_sync, course_id, exam_item_id)


async def course_document_source_is_referenced(course_id: str, document_name: str) -> bool:
    return await asyncio.to_thread(
        _course_document_source_is_referenced_sync, course_id, document_name,
    )


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
    c_max_probe_turns: Optional[int] = None,
    analysis_question_count: Optional[int] = None,
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
        c_max_probe_turns,
        analysis_question_count,
    )


async def delete_exam_item(course_id: str, exam_item_id: str) -> bool:
    return await asyncio.to_thread(_delete_exam_item_sync, course_id, exam_item_id)


def _create_exam_item_sync(
    course_id: str,
    exam_item_id: str,
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
    c_max_probe_turns: int,
    analysis_question_count: int,
) -> Dict[str, object]:
    ensure_database()
    exam_item_id = _normalize_exam_item_id(exam_item_id)
    now = datetime.now()
    exam_item_name = _normalize_exam_item_name(exam_item_name)
    if item_type == "B":
        dimension_scores = {}
        need_code_repository = False
        use_preset_questions = True
        enable_report_analysis = False
        report_total_score = None
        report_judge_rule = None
    else:
        dimension_scores = _normalize_dimension_scores(dimension_scores, allow_empty=item_type == "C")
    dimension_names = list(dimension_scores.keys())
    total_score = float(sum(dimension_scores.values()))
    c_max_probe_turns = _normalize_c_max_probe_turns(c_max_probe_turns)
    analysis_question_count = _normalize_analysis_question_count(analysis_question_count)
    report_config = _normalize_report_analysis_config(
        enable_report_analysis,
        report_total_score,
        report_judge_rule,
    )
    valid_times = _normalize_exam_available_valid_times(exam_available_valid_times)
    exam_available_from = now
    exam_available_until = datetime.now() + timedelta(seconds=valid_times)
    try:
        with get_engine().begin() as connection:
            ensure_tables(connection)
            _raise_if_exam_item_name_exists(connection, course_id, exam_item_name)
            connection.execute(
                CourseExamItem.__table__.insert().values(
                    exam_item_id=exam_item_id,
                    course_id=course_id,
                    exam_item_name=exam_item_name,
                    description=description,
                    item_type=item_type,
                    dimension_names_json=_to_json(dimension_names),
                    dimension_scores_json=_to_json(dimension_scores),
                    total_score=total_score,
                    participant_count=0,
                    attempt_count=0,
                    need_code_repository=1 if need_code_repository else 0,
                    use_preset_questions=1 if use_preset_questions else 0,
                    enable_report_analysis=1 if report_config["enable_report_analysis"] else 0,
                    report_total_score=report_config["report_total_score"],
                    report_judge_rule=report_config["report_judge_rule"],
                    c_max_probe_turns=c_max_probe_turns,
                    analysis_question_count=analysis_question_count,
                    exam_available_valid_times=valid_times,
                    exam_available_from=exam_available_from,
                    exam_available_until=exam_available_until,
                    status="active",
                    version=1,
                    created_by=created_by,
                    created_at=now,
                    updated_at=now,
                )
            )
            pending_session_count = create_pending_exam_sessions_for_exam_item(
                connection, course_id, exam_item_id, exam_item_name,
                need_code_repository, use_preset_questions, dimension_scores, total_score,
            )
            created_row = connection.execute(
                select(*_EXAM_ITEM_COLUMNS).where(
                    CourseExamItem.exam_item_id == exam_item_id
                )
            ).fetchone()
            created_item = exam_item_row_to_dict(created_row)
            created_item["version"] = 1
            created_item["pending_session_count"] = pending_session_count
            created_item["exam_available_valid_times"] = valid_times
        return created_item
    except Exception as error:
        if _is_duplicate_key_error(error):
            # 事务已随异常回滚，用新连接判断是 id 冲突还是同名冲突。
            with get_engine().connect() as connection:
                id_row = connection.execute(
                    select(literal(1))
                    .where(CourseExamItem.exam_item_id == exam_item_id)
                    .limit(1)
                ).fetchone()
                if id_row is not None:
                    raise ValueError("EXAM_ITEM_ID_EXISTS") from error
                name_row = connection.execute(
                    select(literal(1))
                    .where(
                        CourseExamItem.course_id == course_id,
                        CourseExamItem.exam_item_name == exam_item_name,
                        CourseExamItem.status == "active",
                    )
                    .limit(1)
                ).fetchone()
                if name_row is not None:
                    raise ValueError("EXAM_ITEM_NAME_EXISTS") from error
        raise


def _exam_item_id_exists_sync(exam_item_id: str) -> bool:
    ensure_database()
    exam_item_id = _normalize_exam_item_id(exam_item_id)
    with get_engine().begin() as connection:
        ensure_tables(connection)
        row = connection.execute(
            select(literal(1))
            .where(CourseExamItem.exam_item_id == exam_item_id)
            .limit(1)
        ).fetchone()
        return row is not None


def _list_exam_items_by_course_sync(course_id: str) -> List[Dict[str, object]]:
    ensure_database()
    with get_engine().begin() as connection:
        ensure_tables(connection)
        rows = connection.execute(
            select(*_EXAM_ITEM_COLUMNS)
            .where(
                CourseExamItem.course_id == course_id,
                CourseExamItem.status == "active",
            )
            .order_by(CourseExamItem.created_at.desc())
        ).fetchall()
        return [exam_item_row_to_dict(row) for row in rows]


def _get_exam_item_by_id_sync(exam_item_id: str) -> Optional[Dict[str, object]]:
    ensure_database()
    with get_engine().begin() as connection:
        ensure_tables(connection)
        row = connection.execute(
            select(*_EXAM_ITEM_COLUMNS)
            .where(
                CourseExamItem.exam_item_id == exam_item_id,
                CourseExamItem.status == "active",
            )
            .limit(1)
        ).fetchone()
        return exam_item_row_to_dict(row) if row else None


def _get_exam_item_for_edit_sync(
    course_id: str, exam_item_id: str
) -> Optional[Dict[str, object]]:
    ensure_database()
    with get_engine().begin() as connection:
        ensure_tables(connection)
        row = connection.execute(
            select(
                *_EXAM_ITEM_COLUMNS,
                CourseExamItem.version,
                CourseExamItem.exam_available_valid_times,
            )
            .where(
                CourseExamItem.course_id == course_id,
                CourseExamItem.exam_item_id == exam_item_id,
                CourseExamItem.status == "active",
            )
            .limit(1)
        ).fetchone()
        if row is None:
            return None
        item = exam_item_row_to_dict(row[:24])
        item["version"] = int(row[24])
        item["exam_available_valid_times"] = int(row[25])
        return item


def _course_document_source_is_referenced_sync(course_id: str, document_name: str) -> bool:
    ensure_database()
    with get_engine().begin() as connection:
        ensure_tables(connection)
        row = connection.execute(
            select(literal(1))
            .where(
                CourseExamItem.course_id == course_id,
                CourseExamItem.status == "active",
                func.json_contains(
                    func.coalesce(
                        CourseExamItem.course_document_sources_json,
                        func.json_array(),
                    ),
                    func.json_quote(document_name),
                ),
            )
            .limit(1)
        ).fetchone()
        return row is not None


def _get_exam_item_course_document_sources_sync(course_id: str, exam_item_id: str) -> List[str]:
    ensure_database()
    with get_engine().begin() as connection:
        ensure_tables(connection)
        return _select_course_document_sources(connection, course_id, exam_item_id)


def _add_exam_item_course_document_source_sync(
    course_id: str,
    exam_item_id: str,
    document_name: str,
) -> List[str]:
    ensure_database()
    document_name = _normalize_course_document_source(document_name)
    now = datetime.now()
    with get_engine().begin() as connection:
        ensure_tables(connection)
        sources = _select_course_document_sources(connection, course_id, exam_item_id, lock=True)
        ensure_exam_item_editable(connection, exam_item_id)
        if document_name in sources:
            raise ValueError("COURSE_DOCUMENT_SOURCE_EXISTS")
        sources.append(document_name)
        result = connection.execute(
            update(CourseExamItem)
            .where(
                CourseExamItem.course_id == course_id,
                CourseExamItem.exam_item_id == exam_item_id,
                CourseExamItem.status == "active",
            )
            .values(course_document_sources_json=_to_json(sources), updated_at=now)
        )
        if result.rowcount == 0:
            raise ValueError("EXAM_ITEM_NOT_FOUND")
    return sources


def _remove_exam_item_course_document_source_sync(
    course_id: str,
    exam_item_id: str,
    document_name: str,
) -> List[str]:
    ensure_database()
    document_name = _normalize_course_document_source(document_name)
    now = datetime.now()
    with get_engine().begin() as connection:
        ensure_tables(connection)
        sources = _select_course_document_sources(connection, course_id, exam_item_id, lock=True)
        ensure_exam_item_editable(connection, exam_item_id)
        if document_name not in sources:
            raise ValueError("COURSE_DOCUMENT_SOURCE_NOT_FOUND")
        sources = [source for source in sources if source != document_name]
        result = connection.execute(
            update(CourseExamItem)
            .where(
                CourseExamItem.course_id == course_id,
                CourseExamItem.exam_item_id == exam_item_id,
                CourseExamItem.status == "active",
            )
            .values(course_document_sources_json=_to_json(sources), updated_at=now)
        )
        if result.rowcount == 0:
            raise ValueError("EXAM_ITEM_NOT_FOUND")
    return sources


def _get_available_exam_item_by_id_sync(exam_item_id: str) -> Optional[Dict[str, object]]:
    ensure_database()
    now = datetime.now()
    with get_engine().begin() as connection:
        ensure_tables(connection)
        row = connection.execute(
            select(*_EXAM_ITEM_COLUMNS)
            .where(
                CourseExamItem.exam_item_id == exam_item_id,
                CourseExamItem.status == "active",
                CourseExamItem.exam_available_from <= now,
                CourseExamItem.exam_available_until > now,
            )
            .limit(1)
        ).fetchone()
        return exam_item_row_to_dict(row) if row else None


def _get_available_exam_item_by_exam_id_sync(exam_id: str) -> Optional[Dict[str, object]]:
    ensure_database()
    now = datetime.now()
    with get_engine().begin() as connection:
        ensure_tables(connection)
        row = connection.execute(
            select(*_EXAM_ITEM_COLUMNS)
            .join(
                ExamSession,
                ExamSession.exam_item_id == CourseExamItem.exam_item_id,
            )
            .where(
                ExamSession.exam_id == exam_id,
                CourseExamItem.status == "active",
                CourseExamItem.exam_available_from <= now,
                CourseExamItem.exam_available_until > now,
            )
            .limit(1)
        ).fetchone()
        return exam_item_row_to_dict(row) if row else None


def _reset_exam_item_availability_sync(
    exam_item_id: str,
    exam_available_valid_times: int,
) -> Optional[Dict[str, object]]:
    ensure_database()
    now = datetime.now()
    valid_times = _normalize_exam_available_valid_times(exam_available_valid_times)
    expires_at = datetime.now() + timedelta(seconds=valid_times)
    with get_engine().begin() as connection:
        ensure_tables(connection)
        lock_row = connection.execute(
            select(literal(1))
            .where(
                CourseExamItem.exam_item_id == exam_item_id,
                CourseExamItem.status == "active",
            )
            .with_for_update()
        ).fetchone()
        if lock_row is None:
            connection.rollback()
            return None
        ensure_exam_item_editable(connection, exam_item_id)
        result = connection.execute(
            update(CourseExamItem)
            .where(
                CourseExamItem.exam_item_id == exam_item_id,
                CourseExamItem.status == "active",
            )
            .values(
                exam_available_from=now,
                exam_available_until=expires_at,
                updated_at=now,
            )
        )
        if result.rowcount == 0:
            connection.rollback()
            return None
        updated_row = connection.execute(
            select(*_EXAM_ITEM_COLUMNS).where(
                CourseExamItem.exam_item_id == exam_item_id
            )
        ).fetchone()
        updated_item = exam_item_row_to_dict(updated_row)
    return updated_item


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
    c_max_probe_turns: Optional[int],
    analysis_question_count: Optional[int],
) -> bool:
    ensure_database()
    now = datetime.now()
    with get_engine().begin() as connection:
        ensure_tables(connection)
        values: Dict[str, object] = {}
        updated_exam_item_name = None
        state_row = connection.execute(
            select(CourseExamItem.status, CourseExamItem.item_type)
            .where(
                CourseExamItem.course_id == course_id,
                CourseExamItem.exam_item_id == exam_item_id,
                CourseExamItem.status == "active",
            )
            .limit(1)
            .with_for_update()
        ).fetchone()
        if state_row is None:
            return False
        ensure_exam_item_editable(connection, exam_item_id)
        effective_item_type = item_type if item_type is not None else state_row[1]
        if exam_item_name is not None:
            exam_item_name = _normalize_exam_item_name(exam_item_name)
            _raise_if_exam_item_name_exists(
                connection, course_id, exam_item_name, exclude_exam_item_id=exam_item_id
            )
            updated_exam_item_name = exam_item_name
            values["exam_item_name"] = exam_item_name
        if description is not None:
            values["description"] = description
        if item_type is not None:
            values["item_type"] = item_type
        if effective_item_type == "B":
            need_code_repository = False
            use_preset_questions = True
        if need_code_repository is not None:
            values["need_code_repository"] = 1 if need_code_repository else 0
        if use_preset_questions is not None:
            values["use_preset_questions"] = 1 if use_preset_questions else 0
        if c_max_probe_turns is not None:
            if effective_item_type != "C":
                raise ValueError("C_MAX_PROBE_TURNS_ONLY_FOR_C")
            values["c_max_probe_turns"] = _normalize_c_max_probe_turns(c_max_probe_turns)
        if analysis_question_count is not None:
            values["analysis_question_count"] = _normalize_analysis_question_count(
                analysis_question_count
            )
        if effective_item_type == "B":
            values["enable_report_analysis"] = 0
            values["report_total_score"] = None
            values["report_judge_rule"] = None
        elif (
            enable_report_analysis is not None
            or report_total_score is not None
            or report_judge_rule is not None
        ):
            current_report_config = _select_exam_item_report_config(
                connection, course_id, exam_item_id
            )
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
            values["enable_report_analysis"] = (
                1 if report_config["enable_report_analysis"] else 0
            )
            values["report_total_score"] = report_config["report_total_score"]
            values["report_judge_rule"] = report_config["report_judge_rule"]
        if exam_available_valid_times is not None:
            valid_times = _normalize_exam_available_valid_times(exam_available_valid_times)
            values["exam_available_valid_times"] = valid_times
            values["exam_available_from"] = now
            values["exam_available_until"] = datetime.now() + timedelta(seconds=valid_times)
        updated_dimension_scores = None
        updated_total_score = 0.0
        if dimension_scores is not None and effective_item_type != "B":
            dimension_scores = _normalize_dimension_scores(
                dimension_scores, allow_empty=effective_item_type == "C",
            )
            dimension_names = list(dimension_scores.keys())
            updated_dimension_scores = dimension_scores
            updated_total_score = float(sum(dimension_scores.values()))
            values["dimension_names_json"] = _to_json(dimension_names)
            values["dimension_scores_json"] = _to_json(dimension_scores)
            values["total_score"] = updated_total_score
        if not values:
            return True
        values["version"] = CourseExamItem.version + 1
        values["updated_at"] = now
        result = connection.execute(
            update(CourseExamItem)
            .where(
                CourseExamItem.course_id == course_id,
                CourseExamItem.exam_item_id == exam_item_id,
                CourseExamItem.status == "active",
            )
            .values(**values)
        )
        updated = result.rowcount > 0
        if not updated and (need_code_repository is not None or use_preset_questions is not None):
            # MySQL 在值未变化时 rowcount 为 0，这里确认行确实存在。
            exists_row = connection.execute(
                select(literal(1))
                .where(
                    CourseExamItem.course_id == course_id,
                    CourseExamItem.exam_item_id == exam_item_id,
                    CourseExamItem.status == "active",
                )
                .limit(1)
            ).fetchone()
            updated = exists_row is not None
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
        if updated and effective_item_type == "B" and state_row[1] != "B":
            sync_mode_b_question_scores(connection, course_id, exam_item_id)
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
    return updated


def _delete_exam_item_sync(course_id: str, exam_item_id: str) -> bool:
    ensure_database()
    now = datetime.now()
    with get_engine().begin() as connection:
        ensure_tables(connection)
        lock_row = connection.execute(
            select(literal(1))
            .where(
                CourseExamItem.course_id == course_id,
                CourseExamItem.exam_item_id == exam_item_id,
                CourseExamItem.status == "active",
            )
            .with_for_update()
        ).fetchone()
        if lock_row is None:
            connection.rollback()
            return False
        ensure_exam_item_deletable(connection, exam_item_id)
        result = connection.execute(
            update(CourseExamItem)
            .where(
                CourseExamItem.course_id == course_id,
                CourseExamItem.exam_item_id == exam_item_id,
                CourseExamItem.status == "active",
            )
            .values(status="deleted", updated_at=now)
        )
        deleted = result.rowcount > 0
    return deleted


def sync_mode_b_question_scores(connection, course_id: str, exam_item_id: str) -> None:
    """Keep B-mode exam and pending-session scores aligned with teacher questions."""
    row = connection.execute(
        select(CourseExamItem.item_type)
        .where(
            CourseExamItem.course_id == course_id,
            CourseExamItem.exam_item_id == exam_item_id,
        )
        .with_for_update()
    ).fetchone()
    if row is None or row[0] != "B":
        return
    dimension_rows = connection.execute(
        select(ExamPresetQuestion.question_dimension, func.sum(ExamPresetQuestion.score))
        .where(
            ExamPresetQuestion.exam_item_id == exam_item_id,
            ExamPresetQuestion.user_id.is_(None),
            ExamPresetQuestion.status == "active",
        )
        .group_by(ExamPresetQuestion.question_dimension)
        .order_by(
            func.min(ExamPresetQuestion.sort_order),
            ExamPresetQuestion.question_dimension,
        )
    ).fetchall()
    dimension_scores = {str(name): float(score) for name, score in dimension_rows}
    if any(not math.isfinite(score) or score <= 0 for score in dimension_scores.values()):
        raise ValueError("PRESET_QUESTION_SCORE_INVALID")
    total_score = sum(dimension_scores.values())
    connection.execute(
        update(CourseExamItem)
        .where(
            CourseExamItem.course_id == course_id,
            CourseExamItem.exam_item_id == exam_item_id,
        )
        .values(
            dimension_names_json=_to_json(list(dimension_scores)),
            dimension_scores_json=_to_json(dimension_scores),
            total_score=total_score,
            version=CourseExamItem.version + 1,
            updated_at=datetime.now(),
        )
    )
    update_pending_exam_sessions_scores(
        connection, course_id, exam_item_id, dimension_scores, total_score,
    )


def _normalize_exam_item_id(exam_item_id: str) -> str:
    if not isinstance(exam_item_id, str) or len(exam_item_id) != 32:
        raise ValueError("EXAM_ITEM_ID_INVALID")
    if any(char not in "0123456789abcdef" for char in exam_item_id):
        raise ValueError("EXAM_ITEM_ID_INVALID")
    return exam_item_id


def _is_duplicate_key_error(error: Exception) -> bool:
    return mysql_error_code(error) == 1062


def _normalize_exam_item_name(exam_item_name: str) -> str:
    exam_item_name = (exam_item_name or "").strip()
    if not exam_item_name:
        raise ValueError("EXAM_ITEM_NAME_REQUIRED")
    return exam_item_name


def _normalize_dimension_scores(
    dimension_scores: Optional[Dict[str, float]], allow_empty: bool = False,
) -> Dict[str, float]:
    if not dimension_scores:
        if allow_empty:
            return {}
        raise ValueError("EXAM_ITEM_DIMENSIONS_REQUIRED")
    normalized: Dict[str, float] = {}
    for name, score in dimension_scores.items():
        dimension_name = str(name).strip()
        if not dimension_name:
            raise ValueError("EXAM_ITEM_DIMENSION_NAME_REQUIRED")
        normalized[dimension_name] = float(score)
    return normalized


def _normalize_c_max_probe_turns(value: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= 20:
        raise ValueError("C_MAX_PROBE_TURNS_INVALID")
    return value


def _normalize_analysis_question_count(value: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= 5:
        raise ValueError("ANALYSIS_QUESTION_COUNT_INVALID")
    return value


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


def _select_exam_item_report_config(connection, course_id: str, exam_item_id: str) -> Dict[str, object]:
    row = connection.execute(
        select(
            CourseExamItem.enable_report_analysis,
            CourseExamItem.report_total_score,
            CourseExamItem.report_judge_rule,
        )
        .where(
            CourseExamItem.course_id == course_id,
            CourseExamItem.exam_item_id == exam_item_id,
            CourseExamItem.status == "active",
        )
        .limit(1)
    ).fetchone()
    if row is None:
        raise ValueError("EXAM_ITEM_NOT_FOUND")
    return {
        "enable_report_analysis": bool(row[0]),
        "report_total_score": row[1],
        "report_judge_rule": row[2],
    }


def _select_course_document_sources(
    connection,
    course_id: str,
    exam_item_id: str,
    lock: bool = False,
) -> List[str]:
    statement = (
        select(CourseExamItem.course_document_sources_json)
        .where(
            CourseExamItem.course_id == course_id,
            CourseExamItem.exam_item_id == exam_item_id,
            CourseExamItem.status == "active",
        )
        .limit(1)
    )
    if lock:
        statement = statement.with_for_update()
    row = connection.execute(statement).fetchone()
    if row is None:
        raise ValueError("EXAM_ITEM_NOT_FOUND")
    return _normalize_course_document_sources(row[0])


def _raise_if_exam_item_name_exists(
    connection,
    course_id: str,
    exam_item_name: str,
    exclude_exam_item_id: Optional[str] = None,
) -> None:
    conditions = [
        CourseExamItem.course_id == course_id,
        CourseExamItem.exam_item_name == exam_item_name,
        CourseExamItem.status == "active",
    ]
    if exclude_exam_item_id:
        conditions.append(CourseExamItem.exam_item_id != exclude_exam_item_id)
    row = connection.execute(
        select(literal(1)).where(*conditions).limit(1)
    ).fetchone()
    if row is not None:
        raise ValueError("EXAM_ITEM_NAME_EXISTS")


def ensure_exam_item_deletable(connection, exam_item_id: str) -> None:
    row = connection.execute(
        select(literal(1))
        .where(
            ExamSession.exam_item_id == exam_item_id,
            ExamSession.exam_completed == 0,
            ExamSession.exam_active_token.isnot(None),
            ExamSession.exam_active_until > func.now(),
        )
        .limit(1)
    ).fetchone()
    if row is not None:
        raise ValueError("EXAM_IN_PROGRESS")


def ensure_exam_item_editable(connection, exam_item_id: str) -> None:
    row = connection.execute(
        select(literal(1))
        .where(
            ExamSession.exam_item_id == exam_item_id,
            (
                (ExamSession.exam_completed == 0)
                & ExamSession.exam_active_token.isnot(None)
                & (ExamSession.exam_active_until > func.now())
            )
            | (
                ExamSession.repository_url.isnot(None)
                & (func.trim(ExamSession.repository_url) != "")
            ),
        )
        .limit(1)
    ).fetchone()
    if row is not None:
        raise ValueError("EXAM_IN_PROGRESS")


def _to_json(value) -> str:
    return json.dumps(value, ensure_ascii=False, default=str)
