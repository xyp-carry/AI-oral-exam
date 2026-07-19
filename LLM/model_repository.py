import asyncio
import json
import uuid
from datetime import datetime
from typing import Dict, List, Optional

from .config import MODEL_STATUS_ACTIVE, MODEL_TABLE_NAME
from .schema import create_llm_tables


async def create_user_model(
    owner_user_id: str,
    model_name: str,
    model_api_key: str,
    provider: Optional[str] = None,
    base_url: Optional[str] = None,
    display_name: Optional[str] = None,
    provider_model_key: Optional[str] = None,
    params: Optional[Dict[str, object]] = None,
    last_test_result: Optional[Dict[str, object]] = None,
) -> Dict[str, object]:
    return await asyncio.to_thread(
        _create_user_model_sync,
        owner_user_id,
        model_name,
        model_api_key,
        provider,
        base_url,
        display_name,
        provider_model_key,
        params,
        last_test_result,
    )


async def list_user_models(owner_user_id: str, include_api_key: bool = False) -> List[Dict[str, object]]:
    return await asyncio.to_thread(_list_user_models_sync, owner_user_id, include_api_key)


async def get_user_model(
    model_id: str,
    owner_user_id: Optional[str] = None,
    include_api_key: bool = True,
) -> Optional[Dict[str, object]]:
    return await asyncio.to_thread(
        _get_user_model_sync,
        model_id,
        owner_user_id,
        include_api_key,
    )


async def delete_user_model(model_id: str, owner_user_id: str) -> bool:
    return await asyncio.to_thread(_delete_user_model_sync, model_id, owner_user_id)


def _create_user_model_sync(
    owner_user_id: str,
    model_name: str,
    model_api_key: str,
    provider: Optional[str],
    base_url: Optional[str],
    display_name: Optional[str],
    provider_model_key: Optional[str],
    params: Optional[Dict[str, object]],
    last_test_result: Optional[Dict[str, object]],
) -> Dict[str, object]:
    connect, ensure_database = _load_database_helpers()
    ensure_database()
    owner_user_id = _normalize_required_text(owner_user_id, "OWNER_USER_ID_REQUIRED")
    model_name = _normalize_required_text(model_name, "MODEL_NAME_REQUIRED")
    model_api_key = _normalize_required_text(model_api_key, "MODEL_API_KEY_REQUIRED")
    provider = _normalize_optional_text(provider)
    base_url = _normalize_optional_text(base_url)
    display_name = _normalize_optional_text(display_name)
    provider_model_key = _normalize_optional_text(provider_model_key)
    params_json = _json_dumps(params or {})
    last_test_result_json = _json_dumps(last_test_result or {})
    model_id = str(uuid.uuid4())
    now = _now()
    connection = connect(use_database=True)
    try:
        with connection.cursor() as cursor:
            create_llm_tables(cursor)
            cursor.execute(
                f"""
                INSERT INTO {MODEL_TABLE_NAME} (
                    model_id,
                    owner_user_id,
                    model_name,
                    model_api_key,
                    provider,
                    provider_model_key,
                    base_url,
                    display_name,
                    params_json,
                    last_test_result_json,
                    status,
                    created_at,
                    updated_at
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                """,
                (
                    model_id,
                    owner_user_id,
                    model_name,
                    model_api_key,
                    provider,
                    provider_model_key,
                    base_url,
                    display_name,
                    params_json,
                    last_test_result_json,
                    MODEL_STATUS_ACTIVE,
                    now,
                    now,
                ),
            )
        connection.commit()
        return _get_user_model_sync(model_id, owner_user_id, include_api_key=False) or {}
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


def _list_user_models_sync(owner_user_id: str, include_api_key: bool = False) -> List[Dict[str, object]]:
    connect, ensure_database = _load_database_helpers()
    ensure_database()
    owner_user_id = _normalize_required_text(owner_user_id, "OWNER_USER_ID_REQUIRED")
    connection = connect(use_database=True)
    try:
        with connection.cursor() as cursor:
            create_llm_tables(cursor)
            cursor.execute(
                f"""
                SELECT
                    model_id,
                    owner_user_id,
                    model_name,
                    model_api_key,
                    provider,
                    provider_model_key,
                    base_url,
                    display_name,
                    params_json,
                    last_test_result_json,
                    status,
                    created_at,
                    updated_at
                FROM {MODEL_TABLE_NAME}
                WHERE owner_user_id = %s
                  AND status = %s
                ORDER BY created_at DESC
                """,
                (owner_user_id, MODEL_STATUS_ACTIVE),
            )
            return [model_row_to_dict(row, include_api_key) for row in cursor.fetchall()]
    finally:
        connection.close()


def _get_user_model_sync(
    model_id: str,
    owner_user_id: Optional[str] = None,
    include_api_key: bool = True,
) -> Optional[Dict[str, object]]:
    connect, ensure_database = _load_database_helpers()
    ensure_database()
    model_id = _normalize_required_text(model_id, "MODEL_ID_REQUIRED")
    owner_user_id = _normalize_optional_text(owner_user_id)
    connection = connect(use_database=True)
    try:
        where_owner = "AND owner_user_id = %s" if owner_user_id else ""
        values = [model_id]
        if owner_user_id:
            values.append(owner_user_id)
        values.append(MODEL_STATUS_ACTIVE)
        with connection.cursor() as cursor:
            create_llm_tables(cursor)
            cursor.execute(
                f"""
                SELECT
                    model_id,
                    owner_user_id,
                    model_name,
                    model_api_key,
                    provider,
                    provider_model_key,
                    base_url,
                    display_name,
                    params_json,
                    last_test_result_json,
                    status,
                    created_at,
                    updated_at
                FROM {MODEL_TABLE_NAME}
                WHERE model_id = %s
                  {where_owner}
                  AND status = %s
                LIMIT 1
                """,
                tuple(values),
            )
            row = cursor.fetchone()
            return model_row_to_dict(row, include_api_key) if row else None
    finally:
        connection.close()


def _delete_user_model_sync(model_id: str, owner_user_id: str) -> bool:
    connect, ensure_database = _load_database_helpers()
    ensure_database()
    model_id = _normalize_required_text(model_id, "MODEL_ID_REQUIRED")
    owner_user_id = _normalize_required_text(owner_user_id, "OWNER_USER_ID_REQUIRED")
    connection = connect(use_database=True)
    try:
        with connection.cursor() as cursor:
            create_llm_tables(cursor)
            cursor.execute(
                f"""
                SELECT 1
                FROM {MODEL_TABLE_NAME}
                WHERE model_id = %s
                  AND owner_user_id = %s
                  AND status = %s
                LIMIT 1
                """,
                (model_id, owner_user_id, MODEL_STATUS_ACTIVE),
            )
            if cursor.fetchone() is None:
                connection.rollback()
                return False

            cursor.execute(
                "DELETE FROM exam_judge_config_agents WHERE model_id = %s",
                (model_id,),
            )
            cursor.execute(
                f"""
                DELETE FROM {MODEL_TABLE_NAME}
                WHERE model_id = %s
                  AND owner_user_id = %s
                """,
                (model_id, owner_user_id),
            )
            deleted = cursor.rowcount > 0
        connection.commit()
        return deleted
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


def ensure_models_exist(cursor, model_ids: List[str], owner_user_id: str) -> None:
    if not model_ids:
        raise ValueError("JUDGE_MODEL_REQUIRED")
    placeholders = ",".join(["%s"] * len(model_ids))
    cursor.execute(
        f"""
        SELECT model_id
        FROM {MODEL_TABLE_NAME}
        WHERE model_id IN ({placeholders})
          AND owner_user_id = %s
          AND status = %s
        """,
        tuple(model_ids + [owner_user_id, MODEL_STATUS_ACTIVE]),
    )
    found_model_ids = {row[0] for row in cursor.fetchall()}
    missing = [model_id for model_id in model_ids if model_id not in found_model_ids]
    if missing:
        raise ValueError("MODEL_NOT_FOUND")


def model_row_to_dict(row, include_api_key: bool) -> Dict[str, object]:
    if len(row) == 10:
        fields = (
            "model_id",
            "owner_user_id",
            "model_name",
            "model_api_key",
            "provider",
            "base_url",
            "display_name",
            "status",
            "created_at",
            "updated_at",
        )
    else:
        fields = (
            "model_id",
            "owner_user_id",
            "model_name",
            "model_api_key",
            "provider",
            "provider_model_key",
            "base_url",
            "display_name",
            "params_json",
            "last_test_result_json",
            "status",
            "created_at",
            "updated_at",
        )
    result = dict(zip(fields, row))
    if "params_json" in result:
        result["params"] = _json_loads(result.pop("params_json"), {})
    if "last_test_result_json" in result:
        result["last_test_result"] = _json_loads(result.pop("last_test_result_json"), {})
    if not include_api_key:
        result.pop("model_api_key", None)
    for key in ("created_at", "updated_at"):
        value = result.get(key)
        if value is not None:
            result[key] = value.strftime("%Y-%m-%d %H:%M:%S")
    return result


def _normalize_required_text(value, error_code: str) -> str:
    value = _normalize_optional_text(value)
    if not value:
        raise ValueError(error_code)
    return value


def _normalize_optional_text(value) -> Optional[str]:
    if value is None:
        return None
    value = str(value).strip()
    return value or None


def _now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _json_dumps(value: Dict[str, object]) -> str:
    return json.dumps(value or {}, ensure_ascii=False)


def _json_loads(value, default):
    if value is None:
        return default
    if isinstance(value, (dict, list)):
        return value
    try:
        return json.loads(value)
    except Exception:
        return default


def _load_database_helpers():
    from AIOralExamSystem.Exam.Examdata.connection import connect, ensure_database

    return connect, ensure_database
