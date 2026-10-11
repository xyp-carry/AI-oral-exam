import asyncio
import json
import uuid
from datetime import datetime
from typing import Dict, List, Optional

from sqlalchemy import delete, select

from .connection import ensure_database, get_engine
from .schema import ensure_tables
from .exam_item_repository import ensure_exam_item_editable
from .models import CourseExamItem, ExamReportTemplate


MAX_TEMPLATE_BODY_CHARS = 200_000
MAX_TEMPLATE_PROMPT_CHARS = 20_000
MAX_TEMPLATE_QUEUE_CHARS = 1_000_000

_TEMPLATE_COLUMNS = (
    ExamReportTemplate.template_id,
    ExamReportTemplate.exam_item_id,
    ExamReportTemplate.template_name,
    ExamReportTemplate.module_queue_json,
    ExamReportTemplate.created_at,
    ExamReportTemplate.updated_at,
)


async def replace_exam_report_template(
    exam_item_id: str,
    template_name: str,
    modules: List[Dict[str, object]],
) -> Dict[str, object]:
    return await asyncio.to_thread(
        _replace_exam_report_template_sync,
        exam_item_id,
        template_name,
        modules,
    )


async def get_exam_report_template(
    exam_item_id: str,
) -> Dict[str, object]:
    return await asyncio.to_thread(
        _get_exam_report_template_sync,
        exam_item_id,
    )


def _replace_exam_report_template_sync(
    exam_item_id: str,
    template_name: str,
    modules: List[Dict[str, object]],
) -> Dict[str, object]:
    exam_item_id = _required_text(exam_item_id, "EXAM_ITEM_ID_REQUIRED")
    template_name = _required_text(
        template_name,
        "REPORT_TEMPLATE_NAME_REQUIRED",
    )
    normalized_modules = _normalize_modules(modules)
    module_queue_json = json.dumps(normalized_modules, ensure_ascii=False)
    if len(module_queue_json) > MAX_TEMPLATE_QUEUE_CHARS:
        raise ValueError("REPORT_TEMPLATE_QUEUE_TOO_LARGE")

    ensure_database()
    now = datetime.now()
    template_id = str(uuid.uuid4())
    with get_engine().begin() as connection:
        ensure_tables(connection)
        _raise_if_active_exam_item_missing(connection, exam_item_id)
        ensure_exam_item_editable(connection, exam_item_id)
        previous = connection.execute(
            select(ExamReportTemplate.template_id)
            .where(ExamReportTemplate.exam_item_id == exam_item_id)
            .with_for_update()
        ).fetchone()
        deleted_template_id = str(previous[0]) if previous else None
        connection.execute(
            delete(ExamReportTemplate).where(
                ExamReportTemplate.exam_item_id == exam_item_id
            )
        )
        connection.execute(
            ExamReportTemplate.__table__.insert().values(
                template_id=template_id,
                exam_item_id=exam_item_id,
                template_name=template_name,
                module_queue_json=module_queue_json,
                created_at=now,
                updated_at=now,
            )
        )
        result = _select_exam_report_template(connection, exam_item_id)
        result["replaced"] = previous is not None
        result["deleted_template_id"] = deleted_template_id
    return result


def _get_exam_report_template_sync(
    exam_item_id: str,
) -> Dict[str, object]:
    exam_item_id = _required_text(exam_item_id, "EXAM_ITEM_ID_REQUIRED")
    ensure_database()
    with get_engine().begin() as connection:
        ensure_tables(connection)
        _raise_if_active_exam_item_missing(connection, exam_item_id)
        return _select_exam_report_template(connection, exam_item_id)


def _select_exam_report_template(
    connection,
    exam_item_id: str,
) -> Dict[str, object]:
    row = connection.execute(
        select(*_TEMPLATE_COLUMNS)
        .where(ExamReportTemplate.exam_item_id == exam_item_id)
        .limit(1)
    ).fetchone()
    if row is None:
        return {
            "configured": False,
            "exam_item_id": exam_item_id,
            "modules": [],
        }

    modules = _json_loads(row[3], [])
    if not isinstance(modules, list):
        modules = []
    return {
        "configured": True,
        "template_id": str(row[0]),
        "exam_item_id": str(row[1]),
        "template_name": row[2],
        "modules": modules,
        "created_at": _serialize_datetime(row[4]),
        "updated_at": _serialize_datetime(row[5]),
    }


def _raise_if_active_exam_item_missing(connection, exam_item_id: str) -> None:
    row = connection.execute(
        select(CourseExamItem.exam_item_id)
        .where(
            CourseExamItem.exam_item_id == exam_item_id,
            CourseExamItem.status == "active",
        )
        .limit(1)
    ).fetchone()
    if row is None:
        raise ValueError("EXAM_ITEM_NOT_FOUND")


def _normalize_modules(
    modules: List[Dict[str, object]],
) -> List[Dict[str, object]]:
    if not isinstance(modules, list) or not modules:
        raise ValueError("REPORT_TEMPLATE_MODULES_REQUIRED")
    normalized = []
    seen_keys = set()
    for index, raw_module in enumerate(modules, start=1):
        if not isinstance(raw_module, dict):
            raise ValueError("REPORT_TEMPLATE_MODULE_INVALID")
        module_key = _required_text(
            raw_module.get("module_key"),
            "REPORT_TEMPLATE_MODULE_KEY_REQUIRED",
        )
        if module_key in seen_keys:
            raise ValueError("REPORT_TEMPLATE_MODULE_KEY_DUPLICATED")
        seen_keys.add(module_key)
        template_body = _required_text(
            raw_module.get("template_body"),
            "REPORT_TEMPLATE_BODY_REQUIRED",
        )
        template_prompt = _required_text(
            raw_module.get("template_prompt"),
            "REPORT_TEMPLATE_PROMPT_REQUIRED",
        )
        if len(template_body) > MAX_TEMPLATE_BODY_CHARS:
            raise ValueError("REPORT_TEMPLATE_BODY_TOO_LARGE")
        if len(template_prompt) > MAX_TEMPLATE_PROMPT_CHARS:
            raise ValueError("REPORT_TEMPLATE_PROMPT_TOO_LARGE")
        provides_questions = raw_module.get("provides_questions")
        if not isinstance(provides_questions, bool):
            raise ValueError("REPORT_TEMPLATE_PROVIDES_QUESTIONS_INVALID")
        try:
            sort_order = int(raw_module.get("sort_order", index))
        except (TypeError, ValueError) as exc:
            raise ValueError(
                "REPORT_TEMPLATE_MODULE_SORT_ORDER_INVALID"
            ) from exc
        normalized.append(
            {
                "module_key": module_key,
                "template_body": template_body,
                "template_prompt": template_prompt,
                "provides_questions": provides_questions,
                "sort_order": sort_order,
            }
        )
    return sorted(
        normalized,
        key=lambda module: (
            int(module["sort_order"]),
            str(module["module_key"]),
        ),
    )


def _required_text(value, error_code: str) -> str:
    normalized = str(value or "").strip()
    if not normalized:
        raise ValueError(error_code)
    return normalized


def _json_loads(value, default):
    if value is None:
        return default
    if isinstance(value, (dict, list)):
        return value
    try:
        return json.loads(value)
    except (TypeError, ValueError, json.JSONDecodeError):
        return default


def _serialize_datetime(value) -> Optional[str]:
    if value is None:
        return None
    if hasattr(value, "isoformat"):
        return value.isoformat()
    return str(value)
