import asyncio
import json
import uuid
from datetime import datetime
from typing import Dict, List, Optional

from sqlalchemy import delete, exists, func, insert, literal, select, update

from .connection import ensure_database, get_engine
from .models import CourseExamItem, CourseStudent, CourseTeacher, ExamQuestion, ExamSession
from .permissions import build_exam_scope_conditions
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

_HISTORY_COLUMNS = (
    ExamSession.exam_id,
    ExamSession.course_id,
    ExamSession.exam_item_id,
    ExamSession.exam_item_name,
    ExamSession.total_score,
    ExamSession.exam_score,
    ExamSession.dimension_scores_json,
    ExamSession.exam_dimension_scores_json,
    ExamSession.dimension_count,
    ExamSession.question_count,
    ExamSession.ended_at,
    ExamSession.created_at,
)

_RECORD_COLUMNS = (
    ExamQuestion.record_index,
    ExamQuestion.question_id,
    ExamQuestion.question_content,
    ExamQuestion.question_dimension,
    ExamQuestion.question_score,
    ExamQuestion.based_on_record_index,
    ExamQuestion.source_detail,
    ExamQuestion.student_answer,
    ExamQuestion.correctness_level,
    ExamQuestion.evaluation,
    ExamQuestion.standard_answer,
    ExamQuestion.is_preset_question,
    ExamQuestion.created_at,
    ExamQuestion.root_question_id,
    ExamQuestion.parent_question_id,
    ExamQuestion.root_order,
    ExamQuestion.followup_order,
    ExamQuestion.chain_depth,
    ExamQuestion.relation,
    ExamQuestion.question_evaluation_json,
    ExamQuestion.question_evaluation_status,
)

_QUESTION_INSERT_COLUMNS = (
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


async def get_exam_history_by_course(
    course_id: str,
    user_id: Optional[str] = None,
    include_all_users: bool = False,
    exam_item_id: Optional[str] = None,
) -> List[Dict[str, object]]:
    return await asyncio.to_thread(
        _get_exam_history_by_course_sync,
        course_id,
        user_id,
        include_all_users,
        exam_item_id,
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


async def get_exam_questions_by_exam_item(
    course_id: str,
    exam_item_id: str,
    user_id: Optional[str] = None,
    include_all_users: bool = False,
) -> List[Dict[str, object]]:
    return await asyncio.to_thread(
        _get_exam_questions_by_exam_item_sync,
        course_id,
        exam_item_id,
        user_id,
        include_all_users,
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


async def update_exam_session_use_preset_questions(
    course_id: str,
    exam_id: str,
    use_preset_questions: bool,
) -> bool:
    return await asyncio.to_thread(
        _update_exam_session_use_preset_questions_sync,
        course_id,
        exam_id,
        use_preset_questions,
    )


def _update_exam_session_use_preset_questions_sync(
    course_id: str,
    exam_id: str,
    use_preset_questions: bool,
) -> bool:
    ensure_database()
    with get_engine().begin() as connection:
        ensure_tables(connection)
        result = connection.execute(
            update(ExamSession)
            .where(
                ExamSession.course_id == course_id,
                ExamSession.exam_id == exam_id,
                ExamSession.exam_completed == 0,
            )
            .values(use_preset_questions=1 if use_preset_questions else 0)
        )
        updated = result.rowcount > 0
    return updated


def _update_exam_session_repository_url_sync(
    user_id: str,
    course_id: str,
    exam_id: str,
    repository_url: str,
) -> bool:
    ensure_database()
    with get_engine().begin() as connection:
        ensure_tables(connection)
        result = connection.execute(
            update(ExamSession)
            .where(
                ExamSession.user_id == user_id,
                ExamSession.course_id == course_id,
                ExamSession.exam_id == exam_id,
            )
            .values(
                repository_url=repository_url,
                repository_updated_at=func.now(6),
            )
        )
        updated = result.rowcount > 0
    return updated


def create_pending_exam_sessions_for_exam_item(
    connection,
    course_id: Optional[str],
    exam_item_id: Optional[str],
    exam_item_name: Optional[str] = None,
    need_code_repository: bool = False,
    use_preset_questions: bool = False,
    dimension_scores: Optional[Dict[str, float]] = None,
    total_score: float = 0,
) -> int:
    if not course_id or not exam_item_id:
        return 0
    student_rows = connection.execute(
        select(CourseStudent.student_id).where(
            CourseStudent.course_id == course_id,
            CourseStudent.status == "active",
        )
    ).fetchall()
    user_ids = {str(row[0]) for row in student_rows if row and row[0]}
    teacher_rows = connection.execute(
        select(CourseTeacher.teacher_id).where(
            CourseTeacher.course_id == course_id,
            CourseTeacher.status == "active",
        )
    ).fetchall()
    user_ids.update(str(row[0]) for row in teacher_rows if row and row[0])

    created_count = 0
    for user_id in user_ids:
        created_count += _insert_pending_exam_session_if_missing(
            connection,
            user_id=user_id,
            course_id=course_id,
            exam_item_id=exam_item_id,
            exam_item_name=exam_item_name,
            need_code_repository=need_code_repository,
            use_preset_questions=use_preset_questions,
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
    item_rows = connection.execute(
        select(
            CourseExamItem.exam_item_id,
            CourseExamItem.exam_item_name,
            CourseExamItem.need_code_repository,
            CourseExamItem.use_preset_questions,
            CourseExamItem.dimension_scores_json,
            CourseExamItem.total_score,
        ).where(
            CourseExamItem.course_id == course_id,
            CourseExamItem.status == "active",
        )
    ).fetchall()
    exam_items = [
        (
            str(row[0]),
            str(row[1]) if row[1] is not None else None,
            bool(row[2]),
            bool(row[3]),
            _normalize_dimension_scores_json(row[4]),
            float(row[5] or 0),
        )
        for row in item_rows
        if row and row[0]
    ]

    created_count = 0
    for exam_item_id, exam_item_name, need_code_repository, use_preset_questions, dimension_scores, total_score in exam_items:
        created_count += _insert_pending_exam_session_if_missing(
            connection,
            user_id=user_id,
            course_id=course_id,
            exam_item_id=exam_item_id,
            exam_item_name=exam_item_name,
            need_code_repository=need_code_repository,
            use_preset_questions=use_preset_questions,
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
    connection.execute(
        update(ExamSession)
        .where(
            ExamSession.course_id == course_id,
            ExamSession.exam_item_id == exam_item_id,
            ExamSession.exam_completed == 0,
        )
        .values(need_code_repository=1 if need_code_repository else 0)
    )


def update_pending_exam_sessions_use_preset_questions(
    connection,
    course_id: Optional[str],
    exam_item_id: Optional[str],
    use_preset_questions: bool,
) -> None:
    if not course_id or not exam_item_id:
        return
    connection.execute(
        update(ExamSession)
        .where(
            ExamSession.course_id == course_id,
            ExamSession.exam_item_id == exam_item_id,
            ExamSession.exam_completed == 0,
        )
        .values(use_preset_questions=1 if use_preset_questions else 0)
    )


def update_exam_sessions_exam_item_name(
    connection,
    course_id: Optional[str],
    exam_item_id: Optional[str],
    exam_item_name: Optional[str],
) -> None:
    if not course_id or not exam_item_id:
        return
    connection.execute(
        update(ExamSession)
        .where(
            ExamSession.course_id == course_id,
            ExamSession.exam_item_id == exam_item_id,
            ExamSession.exam_completed == 0,
        )
        .values(exam_item_name=exam_item_name)
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
    connection.execute(
        update(ExamSession)
        .where(
            ExamSession.course_id == course_id,
            ExamSession.exam_item_id == exam_item_id,
            ExamSession.exam_completed == 0,
        )
        .values(
            total_score=float(total_score or 0),
            dimension_count=len(dimension_scores),
            dimension_scores_json=to_json(dimension_scores),
        )
    )


def _insert_pending_exam_session_if_missing(
    connection,
    user_id: str,
    course_id: str,
    exam_item_id: str,
    exam_item_name: Optional[str] = None,
    need_code_repository: bool = False,
    use_preset_questions: bool = False,
    dimension_scores: Optional[Dict[str, float]] = None,
    total_score: float = 0,
) -> int:
    dimension_scores = dimension_scores or {}
    statement = ExamSession.__table__.insert().from_select(
        (
            "exam_id",
            "user_id",
            "course_id",
            "exam_item_id",
            "exam_item_name",
            "total_score",
            "dimension_count",
            "question_count",
            "dimension_scores_json",
            "need_code_repository",
            "use_preset_questions",
            "exam_completed",
            "ended_at",
        ),
        select(
            literal(str(uuid.uuid4())),
            literal(user_id),
            literal(course_id),
            literal(exam_item_id),
            literal(exam_item_name),
            literal(float(total_score or 0)),
            literal(len(dimension_scores)),
            literal(0),
            literal(to_json(dimension_scores)),
            literal(1 if need_code_repository else 0),
            literal(1 if use_preset_questions else 0),
            literal(0),
            literal(None),
        ).where(
            ~exists(
                select(literal(1)).where(
                    ExamSession.user_id == user_id,
                    ExamSession.course_id == course_id,
                    ExamSession.exam_item_id == exam_item_id,
                )
            )
        ),
    )
    result = connection.execute(statement)
    return result.rowcount


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
        "exam_item_name",
        "total_score",
        "exam_score",
        "dimension_count",
        "question_count",
        "dimension_scores_json",
        "exam_dimension_scores_json",
        "repository_url",
        "repository_updated_at",
        "need_code_repository",
        "use_preset_questions",
        "exam_type",
        "enable_report_analysis",
        "report_total_score",
        "report_judge_rule",
        "exam_completed",
        "ended_at",
        "created_at",
    )
    result = dict(zip(fields, row))
    result["dimension_scores"] = _normalize_dimension_scores_json(result.pop("dimension_scores_json", None))
    exam_dimension_scores_json = result.pop("exam_dimension_scores_json", None)
    result["exam_dimension_scores"] = (
        _normalize_dimension_scores_json(exam_dimension_scores_json)
        if exam_dimension_scores_json is not None
        else None
    )
    result["need_code_repository"] = bool(result.get("need_code_repository"))
    result["use_preset_questions"] = bool(result.get("use_preset_questions"))
    result["enable_report_analysis"] = bool(result.get("enable_report_analysis"))
    result["exam_completed"] = bool(result.get("exam_completed"))
    for key in ("repository_updated_at", "ended_at", "created_at"):
        value = result.get(key)
        if value is not None and hasattr(value, "strftime"):
            result[key] = value.strftime("%Y-%m-%d %H:%M:%S")
    return result


def _list_exam_sessions_by_course_and_user_sync(
    course_id: str,
    user_id: str,
) -> List[Dict[str, object]]:
    ensure_database()
    with get_engine().begin() as connection:
        ensure_tables(connection)
        rows = connection.execute(
            _session_with_item_columns()
            .where(
                ExamSession.course_id == course_id,
                ExamSession.user_id == user_id,
            )
            .order_by(ExamSession.created_at.desc())
        ).fetchall()
        return [_exam_session_row_to_dict(row) for row in rows]


def _get_exam_session_by_exam_id_sync(exam_id: str) -> Optional[Dict[str, object]]:
    ensure_database()
    with get_engine().begin() as connection:
        ensure_tables(connection)
        row = connection.execute(
            _session_with_item_columns()
            .where(ExamSession.exam_id == exam_id)
            .limit(1)
        ).fetchone()
        return _exam_session_row_to_dict(row) if row else None


def _session_with_item_columns():
    """exam_sessions LEFT JOIN course_exam_items 的会话查询列（顺序与 _exam_session_row_to_dict 对齐）。"""
    return select(
        ExamSession.exam_id,
        ExamSession.user_id,
        ExamSession.course_id,
        ExamSession.exam_item_id,
        ExamSession.exam_item_name,
        ExamSession.total_score,
        ExamSession.exam_score,
        ExamSession.dimension_count,
        ExamSession.question_count,
        ExamSession.dimension_scores_json,
        ExamSession.exam_dimension_scores_json,
        ExamSession.repository_url,
        ExamSession.repository_updated_at,
        ExamSession.need_code_repository,
        ExamSession.use_preset_questions,
        CourseExamItem.item_type.label("exam_type"),
        CourseExamItem.enable_report_analysis,
        CourseExamItem.report_total_score,
        CourseExamItem.report_judge_rule,
        ExamSession.exam_completed,
        ExamSession.ended_at,
        ExamSession.created_at,
    ).outerjoin(
        CourseExamItem,
        ExamSession.exam_item_id == CourseExamItem.exam_item_id,
    )


def _save_exam_data_sync(
    current_user: dict,
    exam_state,
    final_review: Dict[str, object],
) -> str:
    ensure_database()
    with get_engine().begin() as connection:
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
    return exam_id


def _get_exam_session_refs_by_exam_id(connection, exam_id: Optional[str]) -> Optional[Dict[str, Optional[str]]]:
    if not exam_id:
        return None
    row = connection.execute(
        select(
            ExamSession.user_id,
            ExamSession.course_id,
            ExamSession.exam_item_id,
        )
        .where(ExamSession.exam_id == exam_id)
        .limit(1)
    ).fetchone()
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
    row = connection.execute(
        select(ExamSession.exam_id)
        .where(
            ExamSession.user_id == user_id,
            ExamSession.course_id == course_id,
            ExamSession.exam_item_id == exam_item_id,
        )
        .order_by(ExamSession.ended_at.desc(), ExamSession.created_at.desc())
        .limit(1)
    ).fetchone()
    return str(row[0]) if row else None


def _get_exam_session_total_score(connection, exam_id: Optional[str]) -> Optional[float]:
    if not exam_id:
        return None
    row = connection.execute(
        select(ExamSession.total_score)
        .where(ExamSession.exam_id == exam_id)
        .limit(1)
    ).fetchone()
    if not row or row[0] is None:
        return None
    return float(row[0])


def _get_exam_item_total_score(connection, exam_item_id: Optional[str]) -> Optional[float]:
    if not exam_item_id:
        return None
    row = connection.execute(
        select(CourseExamItem.total_score)
        .where(CourseExamItem.exam_item_id == exam_item_id)
        .limit(1)
    ).fetchone()
    if not row or row[0] is None:
        return None
    return float(row[0])


def _get_exam_session_dimension_scores(
    connection,
    exam_id: Optional[str],
) -> Optional[Dict[str, float]]:
    if not exam_id:
        return None
    row = connection.execute(
        select(ExamSession.dimension_scores_json)
        .where(ExamSession.exam_id == exam_id)
        .limit(1)
    ).fetchone()
    if not row or row[0] is None:
        return None
    return _normalize_dimension_scores_json(row[0])


def _get_exam_item_dimension_scores(
    connection,
    exam_item_id: Optional[str],
) -> Optional[Dict[str, float]]:
    if not exam_item_id:
        return None
    row = connection.execute(
        select(CourseExamItem.dimension_scores_json)
        .where(CourseExamItem.exam_item_id == exam_item_id)
        .limit(1)
    ).fetchone()
    if not row or row[0] is None:
        return None
    return _normalize_dimension_scores_json(row[0])


def _resolve_configured_dimension_scores(
    connection,
    exam_id: Optional[str],
    exam_item_id: Optional[str],
    fallback: Dict[str, float],
) -> Dict[str, float]:
    session_dimension_scores = _get_exam_session_dimension_scores(connection, exam_id)
    if session_dimension_scores is not None:
        return session_dimension_scores
    item_dimension_scores = _get_exam_item_dimension_scores(connection, exam_item_id)
    if item_dimension_scores is not None:
        return item_dimension_scores
    return dict(fallback or {})


def _get_exam_session_exam_item_name(connection, exam_id: Optional[str]) -> Optional[str]:
    if not exam_id:
        return None
    row = connection.execute(
        select(ExamSession.exam_item_name)
        .where(ExamSession.exam_id == exam_id)
        .limit(1)
    ).fetchone()
    if not row or row[0] is None:
        return None
    return str(row[0])


def _get_exam_item_name(connection, exam_item_id: Optional[str]) -> Optional[str]:
    if not exam_item_id:
        return None
    row = connection.execute(
        select(CourseExamItem.exam_item_name)
        .where(CourseExamItem.exam_item_id == exam_item_id)
        .limit(1)
    ).fetchone()
    if not row or row[0] is None:
        return None
    return str(row[0])


def _resolve_exam_item_name(
    connection,
    exam_id: Optional[str],
    exam_item_id: Optional[str],
) -> Optional[str]:
    return (
        _get_exam_session_exam_item_name(connection, exam_id)
        or _get_exam_item_name(connection, exam_item_id)
    )


def _resolve_configured_total_score(
    connection,
    exam_id: Optional[str],
    exam_item_id: Optional[str],
    fallback: float,
) -> float:
    session_total_score = _get_exam_session_total_score(connection, exam_id)
    if session_total_score is not None:
        return session_total_score
    item_total_score = _get_exam_item_total_score(connection, exam_item_id)
    if item_total_score is not None:
        return item_total_score
    return float(fallback or 0)


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
    exam_dimension_scores = dict(getattr(exam_state, "dimension_scores", {}) or {})
    record_dimensions = {
        record.question.dimension
        for record in records
        if getattr(record, "question", None) is not None
    }
    dimension_scores = _resolve_configured_dimension_scores(
        connection,
        exam_id=exam_id,
        exam_item_id=exam_item_id,
        fallback=exam_dimension_scores,
    )
    dimension_names = set(dimension_scores.keys()) | set(exam_dimension_scores.keys()) | record_dimensions
    exam_score = float(sum(exam_dimension_scores.values()))
    total_score = _resolve_configured_total_score(
        connection,
        exam_id=exam_id,
        exam_item_id=exam_item_id,
        fallback=exam_score,
    )
    exam_item_name = _resolve_exam_item_name(
        connection,
        exam_id=exam_id,
        exam_item_id=exam_item_id,
    )
    ended_at = datetime.now()

    exists_row = connection.execute(
        select(func.count()).where(ExamSession.exam_id == exam_id)
    ).fetchone()
    exists = exists_row[0] > 0

    if exists:
        connection.execute(
            update(ExamSession)
            .where(ExamSession.exam_id == exam_id)
            .values(
                user_id=user_id,
                course_id=course_id,
                exam_item_id=exam_item_id,
                exam_item_name=exam_item_name,
                candidate_info_json=to_json(current_user),
                total_score=total_score,
                exam_score=exam_score,
                dimension_count=len(dimension_names),
                question_count=len(records),
                dimension_scores_json=to_json(dimension_scores),
                exam_dimension_scores_json=to_json(exam_dimension_scores),
                final_review_json=to_json(final_review),
                exam_completed=1,
                ended_at=ended_at,
            )
        )
        return

    connection.execute(
        ExamSession.__table__.insert().values(
            exam_id=exam_id,
            user_id=user_id,
            course_id=course_id,
            exam_item_id=exam_item_id,
            exam_item_name=exam_item_name,
            candidate_info_json=to_json(current_user),
            total_score=total_score,
            exam_score=exam_score,
            dimension_count=len(dimension_names),
            question_count=len(records),
            dimension_scores_json=to_json(dimension_scores),
            exam_dimension_scores_json=to_json(exam_dimension_scores),
            final_review_json=to_json(final_review),
            exam_completed=1,
            ended_at=ended_at,
        )
    )


def _refresh_exam_item_stats(connection, exam_item_id: Optional[str]) -> None:
    if not exam_item_id:
        return
    connection.execute(
        update(CourseExamItem)
        .where(CourseExamItem.exam_item_id == exam_item_id)
        .values(
            attempt_count=select(func.count())
            .where(
                ExamSession.exam_item_id == exam_item_id,
                ExamSession.exam_completed == 1,
            )
            .scalar_subquery(),
            participant_count=select(func.count(func.distinct(ExamSession.user_id)))
            .where(
                ExamSession.exam_item_id == exam_item_id,
                ExamSession.user_id.isnot(None),
                ExamSession.exam_completed == 1,
            )
            .scalar_subquery(),
        )
    )


def _insert_exam_questions(connection, exam_id: str, exam_state) -> None:
    records = list(getattr(exam_state, "exam_records", []) or [])
    questions = {
        str(record.question.question_id): record.question
        for record in records
        if getattr(record, "question", None) is not None
        and getattr(record.question, "question_id", None)
    }

    def root_and_depth(question):
        current = question
        seen = {str(question.question_id)}
        depth = 0
        while True:
            parent_id = str(getattr(current, "based_on_record_index", "") or "")
            if not parent_id or parent_id == "-1" or parent_id not in questions:
                return str(current.question_id), depth
            if parent_id in seen:
                return str(question.question_id), 0
            seen.add(parent_id)
            current = questions[parent_id]
            depth += 1

    rows = []
    root_orders = {}
    followup_counts = {}
    for index, record in enumerate(records, start=1):
        question = getattr(record, "question", None)
        if question is None:
            continue
        root_id, depth = root_and_depth(question)
        if root_id not in root_orders:
            root_orders[root_id] = len(root_orders) + 1
        if depth:
            followup_counts[root_id] = followup_counts.get(root_id, 0) + 1
        parent_id = str(question.based_on_record_index) if depth else None
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
            1 if getattr(question, "is_preset_question", False) else 0,
            root_id,
            parent_id,
            root_orders[root_id],
            followup_counts.get(root_id, 0) if depth else 0,
            depth,
            "followup" if depth else "root",
            None,
            None,
        ))

    if not rows:
        return

    connection.execute(
        insert(ExamQuestion),
        [dict(zip(_QUESTION_INSERT_COLUMNS, row)) for row in rows],
    )


def _delete_exam_questions(connection, exam_id: str) -> None:
    connection.execute(delete(ExamQuestion).where(ExamQuestion.exam_id == exam_id))


def _get_exam_history_by_user_sync(
    user_id: Optional[str],
    course_ids: Optional[List[str]],
    allow_course_scope: bool,
    allow_all: bool,
) -> List[Dict[str, object]]:
    ensure_database()
    with get_engine().begin() as connection:
        ensure_tables(connection)
        conditions = build_exam_scope_conditions(
            user_id=user_id,
            course_ids=course_ids,
            allow_course_scope=allow_course_scope,
            allow_all=allow_all,
            user_id_column=ExamSession.user_id,
            course_id_column=ExamSession.course_id,
        )
        conditions.append(ExamSession.exam_completed == 1)
        rows = connection.execute(
            select(*_HISTORY_COLUMNS)
            .where(*conditions)
            .order_by(ExamSession.ended_at.desc(), ExamSession.created_at.desc())
        ).fetchall()
        return [history_row_to_dict(row) for row in rows]


def _get_exam_history_by_course_sync(
    course_id: str,
    user_id: Optional[str],
    include_all_users: bool,
    exam_item_id: Optional[str] = None,
) -> List[Dict[str, object]]:
    ensure_database()
    with get_engine().begin() as connection:
        ensure_tables(connection)
        conditions = [
            ExamSession.course_id == course_id,
            ExamSession.exam_completed == 1,
        ]
        if not include_all_users:
            if not user_id:
                raise ValueError("user_id is required for user scoped query")
            conditions.append(ExamSession.user_id == user_id)
        if exam_item_id:
            conditions.append(ExamSession.exam_item_id == exam_item_id)
        rows = connection.execute(
            select(*_HISTORY_COLUMNS)
            .where(*conditions)
            .order_by(ExamSession.ended_at.desc(), ExamSession.created_at.desc())
        ).fetchall()
        return [history_row_to_dict(row) for row in rows]


def _get_exam_record_by_exam_id_sync(
    exam_id: str,
    user_id: Optional[str],
    course_ids: Optional[List[str]],
    allow_course_scope: bool,
    allow_all: bool,
) -> List[Dict[str, object]]:
    ensure_database()
    with get_engine().begin() as connection:
        ensure_tables(connection)
        conditions = build_exam_scope_conditions(
            user_id=user_id,
            course_ids=course_ids,
            allow_course_scope=allow_course_scope,
            allow_all=allow_all,
            user_id_column=ExamSession.user_id,
            course_id_column=ExamSession.course_id,
        )
        conditions.insert(0, ExamQuestion.exam_id == exam_id)
        rows = connection.execute(
            select(*_RECORD_COLUMNS)
            .join(ExamSession, ExamQuestion.exam_id == ExamSession.exam_id)
            .where(*conditions)
            .order_by(ExamQuestion.record_index.asc(), ExamQuestion.id.asc())
        ).fetchall()
        return [record_row_to_dict(row) for row in rows]


def _get_exam_questions_by_exam_item_sync(
    course_id: str,
    exam_item_id: str,
    user_id: Optional[str],
    include_all_users: bool,
) -> List[Dict[str, object]]:
    ensure_database()
    with get_engine().begin() as connection:
        ensure_tables(connection)
        conditions = [
            ExamSession.course_id == course_id,
            ExamSession.exam_item_id == exam_item_id,
        ]
        if not include_all_users:
            if not user_id:
                raise ValueError("user_id is required for user scoped query")
            conditions.append(ExamSession.user_id == user_id)
        rows = connection.execute(
            select(
                ExamSession.exam_id,
                ExamSession.user_id,
                ExamSession.course_id,
                ExamSession.exam_item_id,
                ExamSession.exam_item_name,
                ExamSession.total_score,
                ExamSession.exam_score,
                ExamSession.exam_completed,
                ExamSession.ended_at,
                ExamSession.created_at,
                *_RECORD_COLUMNS,
            )
            .join(ExamQuestion, ExamQuestion.exam_id == ExamSession.exam_id)
            .where(*conditions)
            .order_by(
                ExamSession.created_at.desc(),
                ExamQuestion.record_index.asc(),
                ExamQuestion.id.asc(),
            )
        ).fetchall()

        sessions: Dict[str, Dict[str, object]] = {}
        for row in rows:
            exam_id = str(row[0])
            session = sessions.setdefault(
                exam_id,
                {
                    "exam_id": exam_id,
                    "user_id": row[1],
                    "course_id": row[2],
                    "exam_item_id": row[3],
                    "exam_item_name": row[4],
                    "total_score": row[5],
                    "exam_score": row[6],
                    "exam_completed": bool(row[7]),
                    "ended_at": row[8].strftime("%Y-%m-%d %H:%M:%S") if row[8] is not None else None,
                    "created_at": row[9].strftime("%Y-%m-%d %H:%M:%S") if row[9] is not None else None,
                    "questions": [],
                },
            )
            session["questions"].append(record_row_to_dict(row[10:]))
        return list(sessions.values())
