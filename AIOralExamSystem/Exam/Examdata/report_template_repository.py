import asyncio
import json
import uuid
from datetime import datetime
from typing import Dict, List, Optional

from .connection import connect, ensure_database
from .schema import ensure_tables
from .exam_item_repository import ensure_exam_item_editable


MAX_TEMPLATE_BODY_CHARS = 200_000
MAX_TEMPLATE_PROMPT_CHARS = 20_000
MAX_TEMPLATE_QUEUE_CHARS = 1_000_000


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
    connection = connect(use_database=True)
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    template_id = str(uuid.uuid4())
    try:
        ensure_tables(connection)
        with connection.cursor() as cursor:
            _raise_if_active_exam_item_missing(cursor, exam_item_id)
            ensure_exam_item_editable(cursor, exam_item_id)
            cursor.execute(
                """
                SELECT template_id
                FROM exam_report_templates
                WHERE exam_item_id = %s
                FOR UPDATE
                """,
                (exam_item_id,),
            )
            previous = cursor.fetchone()
            deleted_template_id = str(previous[0]) if previous else None
            cursor.execute(
                """
                DELETE FROM exam_report_templates
                WHERE exam_item_id = %s
                """,
                (exam_item_id,),
            )
            cursor.execute(
                """
                INSERT INTO exam_report_templates (
                    template_id,
                    exam_item_id,
                    template_name,
                    module_queue_json,
                    created_at,
                    updated_at
                ) VALUES (%s, %s, %s, %s, %s, %s)
                """,
                (
                    template_id,
                    exam_item_id,
                    template_name,
                    module_queue_json,
                    now,
                    now,
                ),
            )
            result = _select_exam_report_template(cursor, exam_item_id)
            result["replaced"] = previous is not None
            result["deleted_template_id"] = deleted_template_id
        connection.commit()
        return result
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


def _get_exam_report_template_sync(
    exam_item_id: str,
) -> Dict[str, object]:
    exam_item_id = _required_text(exam_item_id, "EXAM_ITEM_ID_REQUIRED")
    ensure_database()
    connection = connect(use_database=True)
    try:
        ensure_tables(connection)
        with connection.cursor() as cursor:
            _raise_if_active_exam_item_missing(cursor, exam_item_id)
            return _select_exam_report_template(cursor, exam_item_id)
    finally:
        connection.close()


def _select_exam_report_template(
    cursor,
    exam_item_id: str,
) -> Dict[str, object]:
    cursor.execute(
        """
        SELECT
            template_id,
            exam_item_id,
            template_name,
            module_queue_json,
            created_at,
            updated_at
        FROM exam_report_templates
        WHERE exam_item_id = %s
        LIMIT 1
        """,
        (exam_item_id,),
    )
    row = cursor.fetchone()
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


def _raise_if_active_exam_item_missing(cursor, exam_item_id: str) -> None:
    cursor.execute(
        """
        SELECT 1
        FROM course_exam_items
        WHERE exam_item_id = %s
          AND status = 'active'
        LIMIT 1
        """,
        (exam_item_id,),
    )
    if cursor.fetchone() is None:
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
