import asyncio
import json
from typing import Dict

from .connection import connect, ensure_database
from .schema import ensure_tables


async def get_exam_item_readiness(course_id: str, exam_item_id: str, owner_user_id: str) -> Dict[str, object]:
    return await asyncio.to_thread(_get_exam_item_readiness_sync, course_id, exam_item_id, owner_user_id)



def _get_exam_item_readiness_sync(
    course_id: str, exam_item_id: str, owner_user_id: str
) -> Dict[str, object]:
    ensure_database()
    connection = connect(use_database=True)
    try:
        ensure_tables(connection)
        with connection.cursor() as cursor:
            state = _load_state(cursor, course_id, exam_item_id)
            if state is None:
                raise ValueError("EXAM_ITEM_NOT_FOUND")
            return _readiness(cursor, state, owner_user_id)
    finally:
        connection.close()



def _load_state(cursor, course_id: str, exam_item_id: str, lock: bool = False):
    cursor.execute(
        f"""
        SELECT course_id, exam_item_id, exam_item_name, dimension_scores_json,
               total_score, exam_available_valid_times, need_code_repository,
               use_preset_questions, enable_report_analysis, report_total_score,
               report_judge_rule, course_document_sources_json,
               status, version, created_by, item_type
        FROM course_exam_items
        WHERE course_id = %s AND exam_item_id = %s
        LIMIT 1{" FOR UPDATE" if lock else ""}
        """,
        (course_id, exam_item_id),
    )
    row = cursor.fetchone()
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


def _readiness(cursor, state: Dict[str, object], owner_user_id: str) -> Dict[str, object]:
    missing = []
    if state["status"] != "active":
        missing.append("EXAM_UNAVAILABLE")
    if not state["exam_item_name"]:
        missing.append("EXAM_ITEM_NAME_REQUIRED")
    scores = state["dimension_scores"]
    is_mode_c = state.get("item_type") == "C"
    if not isinstance(scores, dict) or (not scores and not is_mode_c):
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
    cursor.execute(
        """
        SELECT a.agent_role, a.agent_index, m.model_type, m.status, m.owner_user_id
        FROM exam_judge_configs c
        JOIN exam_judge_config_agents a ON a.config_id = c.config_id
        JOIN user_model_library m ON m.model_id = a.model_id
        WHERE c.exam_item_id = %s AND c.status = 'active' AND a.status = 'active'
        """,
        (state["exam_item_id"],),
    )
    agents = cursor.fetchall()
    roles = [str(row[0]) for row in agents]
    if is_mode_c and "tts" not in roles:
        missing.append("TTS_MODEL_REQUIRED")
    for role, code in (
        ("scorer", "JUDGE_MODEL_REQUIRED"),
        ("setter", "SETTER_MODEL_REQUIRED"),
        ("main_judger", "MAIN_JUDGER_MODEL_REQUIRED"),
    ):
        if role not in roles:
            missing.append(code)
    expected_types = {
        "scorer": "chat", "setter": "chat", "main_judger": "chat",
        "report_judger": "chat", "adjudicator": "chat",
        "mineru": "file", "embedding": "embedding", "tts": "tts",
    }
    for role, _, model_type, model_status, model_owner in agents:
        role = str(role)
        if str(model_status) != "active" or str(model_owner) != str(owner_user_id):
            missing.append("MODEL_NOT_FOUND")
        if role in expected_types and str(model_type or "chat") != expected_types[role]:
            missing.append(f"{role.upper()}_MODEL_TYPE_INVALID")
    if state["enable_report_analysis"]:
        if state["report_total_score"] is None or float(state["report_total_score"] or 0) <= 0:
            missing.append("REPORT_TOTAL_SCORE_REQUIRED")
        if not state["report_judge_rule"]:
            missing.append("REPORT_JUDGE_RULE_REQUIRED")
        if "report_judger" not in roles:
            missing.append("REPORT_MODEL_CONFIG_REQUIRED")
    if state["use_preset_questions"]:
        cursor.execute(
            "SELECT COUNT(*) FROM exam_preset_questions WHERE exam_item_id = %s AND status = 'active'",
            (state["exam_item_id"],),
        )
        if int(cursor.fetchone()[0] or 0) == 0:
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


