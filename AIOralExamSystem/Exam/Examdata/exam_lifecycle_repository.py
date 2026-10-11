import asyncio
import json
from typing import Dict

from sqlalchemy import case, func, select, and_

from LLM.models import UserModelLibrary

from .connection import ensure_database, get_engine
from .models import (
    CourseExamItem,
    ExamJudgeConfig,
    ExamJudgeConfigAgent,
    ExamPresetQuestion,
)
from .schema import ensure_tables


async def get_exam_item_readiness(course_id: str, exam_item_id: str, owner_user_id: str) -> Dict[str, object]:
    return await asyncio.to_thread(_get_exam_item_readiness_sync, course_id, exam_item_id, owner_user_id)



def _get_exam_item_readiness_sync(
    course_id: str, exam_item_id: str, owner_user_id: str
) -> Dict[str, object]:
    ensure_database()
    with get_engine().begin() as connection:
        ensure_tables(connection)
        state = _load_state(connection, course_id, exam_item_id)
        if state is None:
            raise ValueError("EXAM_ITEM_NOT_FOUND")
        return _readiness(connection, state, owner_user_id)



def _load_state(connection, course_id: str, exam_item_id: str, lock: bool = False):
    statement = (
        select(
            CourseExamItem.course_id,
            CourseExamItem.exam_item_id,
            CourseExamItem.exam_item_name,
            CourseExamItem.dimension_scores_json,
            CourseExamItem.total_score,
            CourseExamItem.exam_available_valid_times,
            CourseExamItem.need_code_repository,
            CourseExamItem.use_preset_questions,
            CourseExamItem.enable_report_analysis,
            CourseExamItem.report_total_score,
            CourseExamItem.report_judge_rule,
            CourseExamItem.course_document_sources_json,
            CourseExamItem.status,
            CourseExamItem.version,
            CourseExamItem.created_by,
            CourseExamItem.item_type,
        )
        .where(
            CourseExamItem.course_id == course_id,
            CourseExamItem.exam_item_id == exam_item_id,
        )
        .limit(1)
    )
    if lock:
        statement = statement.with_for_update()
    row = connection.execute(statement).fetchone()
    if row is None:
        return None
    return {
        "course_id": str(row[0]),
        "exam_item_id": str(row[1]),
        "exam_item_name": str(row[2] or "").strip(),
        "dimension_scores": _json_loads(row[3], {}),
        "total_score": float(row[4] or 0),
        "exam_available_valid_times": int(row[5] or 0),
        "need_code_repository": bool(row[6]),
        "use_preset_questions": bool(row[7]),
        "enable_report_analysis": bool(row[8]),
        "report_total_score": row[9],
        "report_judge_rule": str(row[10] or "").strip(),
        "course_document_sources": _json_loads(row[11], []),
        "status": str(row[12]),
        "version": int(row[13] or 1),
        "created_by": str(row[14]),
        "item_type": str(row[15] or "").strip(),
    }


def _readiness(connection, state: Dict[str, object], owner_user_id: str) -> Dict[str, object]:
    missing = []
    if state["status"] != "active":
        missing.append("EXAM_UNAVAILABLE")
    if not state["exam_item_name"]:
        missing.append("EXAM_ITEM_NAME_REQUIRED")
    scores = state["dimension_scores"]
    is_mode_b = state.get("item_type") == "B"
    is_mode_c = state.get("item_type") == "C"
    if not isinstance(scores, dict) or (not scores and not (is_mode_b or is_mode_c)):
        missing.append("EXAM_ITEM_DIMENSIONS_REQUIRED")
    elif scores:
        try:
            if any(float(score) <= 0 for score in scores.values()):
                missing.append("EXAM_ITEM_DIMENSION_SCORE_INVALID")
        except (TypeError, ValueError):
            missing.append("EXAM_ITEM_DIMENSION_SCORE_INVALID")
    if state["total_score"] <= 0 and not (is_mode_c and scores == {}):
        missing.append("EXAM_ITEM_TOTAL_SCORE_INVALID")
    if not 1 <= state["exam_available_valid_times"] <= 2592000:
        missing.append("EXAM_AVAILABLE_VALID_TIMES_INVALID")
    agents = connection.execute(
        select(
            ExamJudgeConfigAgent.agent_role,
            ExamJudgeConfigAgent.agent_index,
            UserModelLibrary.model_type,
            UserModelLibrary.status,
            UserModelLibrary.owner_user_id,
        )
        .select_from(ExamJudgeConfig)
        .join(
            ExamJudgeConfigAgent,
            ExamJudgeConfigAgent.config_id == ExamJudgeConfig.config_id,
        )
        .join(
            UserModelLibrary,
            UserModelLibrary.model_id == ExamJudgeConfigAgent.model_id,
        )
        .where(
            ExamJudgeConfig.exam_item_id == state["exam_item_id"],
            ExamJudgeConfig.status == "active",
            ExamJudgeConfigAgent.status == "active",
        )
    ).fetchall()
    roles = [str(row[0]) for row in agents]
    if state.get("item_type") in {"A", "B", "C"} and "tts" not in roles:
        missing.append("TTS_MODEL_REQUIRED")
    required_roles = [
        ("scorer", "JUDGE_MODEL_REQUIRED"),
        ("main_judger", "MAIN_JUDGER_MODEL_REQUIRED"),
    ]
    if is_mode_c:
        required_roles.append(("report_judger", "REPORT_MODEL_CONFIG_REQUIRED"))
    for role, code in required_roles:
        if role not in roles:
            missing.append(code)
    expected_types = {
        "scorer": "chat", "main_judger": "chat",
        "report_judger": "chat", "adjudicator": "chat",
        "mineru": "file", "embedding": "embedding", "tts": "tts",
    }
    for role, _, model_type, model_status, model_owner in agents:
        role = str(role)
        if str(model_status) != "active" or str(model_owner) != str(owner_user_id):
            missing.append("MODEL_NOT_FOUND")
        if role in expected_types and str(model_type or "chat") != expected_types[role]:
            missing.append(f"{role.upper()}_MODEL_TYPE_INVALID")
    if state["enable_report_analysis"] and not is_mode_b:
        if state["report_total_score"] is None or float(state["report_total_score"] or 0) <= 0:
            missing.append("REPORT_TOTAL_SCORE_REQUIRED")
        if not state["report_judge_rule"]:
            missing.append("REPORT_JUDGE_RULE_REQUIRED")
        if "report_judger" not in roles:
            missing.append("REPORT_MODEL_CONFIG_REQUIRED")
    if is_mode_b:
        valid_count_expr = func.coalesce(
            func.sum(
                case(
                    (
                        and_(
                            func.trim(ExamPresetQuestion.question_dimension) != "",
                            func.trim(ExamPresetQuestion.question_content) != "",
                            func.trim(ExamPresetQuestion.standard_answer) != "",
                            ExamPresetQuestion.score > 0,
                        ),
                        1,
                    ),
                    else_=0,
                )
            ),
            0,
        )
        question_count, valid_count = connection.execute(
            select(func.count(), valid_count_expr).where(
                ExamPresetQuestion.exam_item_id == state["exam_item_id"],
                ExamPresetQuestion.user_id.is_(None),
                ExamPresetQuestion.status == "active",
            )
        ).fetchone()
        if not question_count or question_count != valid_count:
            missing.append("PRESET_QUESTION_REQUIRED")
    elif state["use_preset_questions"]:
        count_row = connection.execute(
            select(func.count()).where(
                ExamPresetQuestion.exam_item_id == state["exam_item_id"],
                ExamPresetQuestion.status == "active",
            )
        ).fetchone()
        if int(count_row[0] or 0) == 0:
            missing.append("PRESET_QUESTION_REQUIRED")
    missing = list(dict.fromkeys(missing))
    return {
        "exam_item_id": state["exam_item_id"],
        "course_id": state["course_id"],
        "status": state["status"],
        "version": state["version"],
        "ready": not missing,
        "missing": missing,
        "configured_agent_roles": sorted(set(roles)),
    }


def _json_loads(value, default):
    if value is None:
        return default
    if isinstance(value, (dict, list)):
        return value
    try:
        return json.loads(value)
    except (TypeError, ValueError):
        return default
