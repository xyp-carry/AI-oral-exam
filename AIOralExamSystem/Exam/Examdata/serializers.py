import json
from datetime import datetime
from typing import Dict, Optional


def to_json(value) -> str:
    return json.dumps(value, ensure_ascii=False, default=str)


def get_user_id(current_user: dict) -> Optional[str]:
    user_id = (
        current_user.get("uuid")
        or current_user.get("id")
        or current_user.get("user_id")
    )
    return str(user_id) if user_id is not None else None


def get_course_id(current_user: dict) -> Optional[str]:
    course_id = (
        current_user.get("course_id")
        or current_user.get("courseId")
        or current_user.get("course")
        or current_user.get("class_id")
        or current_user.get("classId")
    )
    if course_id is None:
        course_id = first_course_id(
            current_user.get("course_ids")
            or current_user.get("courses")
            or current_user.get("teacher_course_ids")
            or current_user.get("teacher_courses")
        )
    return str(course_id) if course_id is not None else None


def get_exam_item_id(current_user: dict) -> Optional[str]:
    exam_item_id = (
        current_user.get("exam_item_id")
        or current_user.get("examItemId")
        or current_user.get("exam_item")
        or current_user.get("examItem")
    )
    return str(exam_item_id) if exam_item_id is not None else None


def get_exam_id(current_user: dict) -> Optional[str]:
    exam_id = (
        current_user.get("exam_id")
        or current_user.get("examId")
        or current_user.get("exam")
    )
    return str(exam_id) if exam_id is not None else None


def first_course_id(raw_value) -> Optional[str]:
    if raw_value is None:
        return None
    if isinstance(raw_value, dict):
        for key in ("course_id", "courseId", "id", "uuid", "course", "class_id", "classId"):
            value = raw_value.get(key)
            if value is not None:
                return str(value)
        return None
    if isinstance(raw_value, (list, tuple, set)):
        for item in raw_value:
            course_id = first_course_id(item)
            if course_id:
                return course_id
        return None
    if isinstance(raw_value, str):
        first_value = raw_value.split(",", 1)[0].strip()
        return first_value or None
    return str(raw_value)


def history_row_to_dict(row) -> Dict[str, object]:
    fields = (
        "exam_id",
        "course_id",
        "exam_item_id",
        "total_score",
        "dimension_count",
        "question_count",
        "ended_at",
        "created_at",
    )
    result = dict(zip(fields, row))
    for key in ("ended_at", "created_at"):
        value = result.get(key)
        if value is not None:
            result[key] = value.strftime("%Y-%m-%d %H:%M:%S")
    return result


def record_row_to_dict(row) -> Dict[str, object]:
    fields = (
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
        "created_at",
    )
    result = dict(zip(fields, row))
    value = result.get("created_at")
    if value is not None:
        result["created_at"] = value.strftime("%Y-%m-%d %H:%M:%S")
    return result


def course_row_to_dict(row) -> Dict[str, object]:
    fields = (
        "course_id",
        "course_name",
        "description",
        "owner_teacher_id",
        "invite_code",
        "invite_code_expires_at",
        "invite_code_created_at",
        "status",
        "created_at",
        "updated_at",
    )
    result = dict(zip(fields, row))
    expires_at = result.get("invite_code_expires_at")
    result["invite_code_valid"] = bool(expires_at and expires_at > datetime.now())
    for key in ("invite_code_expires_at", "invite_code_created_at", "created_at", "updated_at"):
        value = result.get(key)
        if value is not None:
            result[key] = value.strftime("%Y-%m-%d %H:%M:%S")
    result.pop("owner_teacher_id", None)
    return result


def exam_item_row_to_dict(row) -> Dict[str, object]:
    fields = (
        "exam_item_id",
        "course_id",
        "exam_item_name",
        "description",
        "item_type",
        "dimension_names_json",
        "dimension_scores_json",
        "total_score",
        "participant_count",
        "attempt_count",
        "need_code_repository",
        "exam_available_from",
        "exam_available_until",
        "status",
        "created_by",
        "created_at",
        "updated_at",
    )
    result = dict(zip(fields, row))
    result["dimension_names"] = _json_loads(result.pop("dimension_names_json"), [])
    result["dimension_scores"] = _json_loads(result.pop("dimension_scores_json"), {})
    result["need_code_repository"] = bool(result.get("need_code_repository"))
    result.pop("created_by", None)
    available_from = result.get("exam_available_from")
    available_until = result.get("exam_available_until")
    result["available"] = bool(
        available_from and available_until and available_from <= datetime.now() < available_until
    )
    for key in ("exam_available_from", "exam_available_until", "created_at", "updated_at"):
        value = result.get(key)
        if value is not None:
            result[key] = value.strftime("%Y-%m-%d %H:%M:%S")
    return result


def _json_loads(value, default):
    if value is None:
        return default
    if isinstance(value, (list, dict)):
        return value
    try:
        return json.loads(value)
    except (TypeError, ValueError):
        return default


def join_request_row_to_dict(row) -> Dict[str, object]:
    fields = (
        "request_id",
        "course_id",
        "course_name",
        "user_id",
        "status",
        "requested_at",
        "reviewed_at",
        "reviewed_by",
    )
    result = dict(zip(fields, row))
    for key in ("requested_at", "reviewed_at"):
        value = result.get(key)
        if value is not None:
            result[key] = value.strftime("%Y-%m-%d %H:%M:%S")
    return result
