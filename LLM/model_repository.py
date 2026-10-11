import asyncio
import json
import uuid
from datetime import datetime
from typing import Dict, List, Optional

from sqlalchemy import delete, select

from AIOralExamSystem.Exam.Examdata.models import ExamJudgeConfigAgent
from .config import MODEL_STATUS_ACTIVE, MODEL_TABLE_NAME
from .model_token_limits import get_model_token_limits
from .models import UserModelLibrary
from .schema import create_llm_tables
from .config import MODEL_TYPE_DEFAULT, MODEL_TYPES

_MODEL_COLUMNS = (
    UserModelLibrary.model_id,
    UserModelLibrary.owner_user_id,
    UserModelLibrary.model_name,
    UserModelLibrary.model_type,
    UserModelLibrary.model_api_key,
    UserModelLibrary.provider,
    UserModelLibrary.provider_model_key,
    UserModelLibrary.base_url,
    UserModelLibrary.display_name,
    UserModelLibrary.params_json,
    UserModelLibrary.last_test_result_json,
    UserModelLibrary.status,
    UserModelLibrary.created_at,
    UserModelLibrary.updated_at,
)


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
    model_type: str = MODEL_TYPE_DEFAULT,
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
        model_type,
    )


async def list_user_models(
    owner_user_id: str,
    include_api_key: bool = False,
    model_type: Optional[str] = None,
) -> List[Dict[str, object]]:
    return await asyncio.to_thread(
        _list_user_models_sync,
        owner_user_id,
        include_api_key,
        model_type,
    )


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
    model_type: str,
) -> Dict[str, object]:
    get_engine, ensure_database = _load_database_helpers()
    ensure_database()
    owner_user_id = _normalize_required_text(owner_user_id, "OWNER_USER_ID_REQUIRED")
    model_name = _normalize_required_text(model_name, "MODEL_NAME_REQUIRED")
    model_api_key = _normalize_required_text(model_api_key, "MODEL_API_KEY_REQUIRED")
    model_type = _normalize_model_type(model_type)
    provider = _normalize_optional_text(provider)
    base_url = _normalize_optional_text(base_url)
    display_name = _normalize_optional_text(display_name)
    provider_model_key = _normalize_optional_text(provider_model_key)
    normalized_params = dict(params or {})
    for key, value in get_model_token_limits(model_name).items():
        normalized_params.setdefault(key, value)
    params_json = _json_dumps(normalized_params)
    last_test_result_json = _json_dumps(last_test_result or {})
    model_id = str(uuid.uuid4())
    now = _now()
    with get_engine().begin() as connection:
        create_llm_tables(connection)
        connection.execute(
            UserModelLibrary.__table__.insert().values(
                model_id=model_id,
                owner_user_id=owner_user_id,
                model_name=model_name,
                model_type=model_type,
                model_api_key=model_api_key,
                provider=provider,
                provider_model_key=provider_model_key,
                base_url=base_url,
                display_name=display_name,
                params_json=params_json,
                last_test_result_json=last_test_result_json,
                status=MODEL_STATUS_ACTIVE,
                created_at=now,
                updated_at=now,
            )
        )
    return _get_user_model_sync(model_id, owner_user_id, include_api_key=False) or {}


def _list_user_models_sync(
    owner_user_id: str,
    include_api_key: bool = False,
    model_type: Optional[str] = None,
) -> List[Dict[str, object]]:
    get_engine, ensure_database = _load_database_helpers()
    ensure_database()
    owner_user_id = _normalize_required_text(owner_user_id, "OWNER_USER_ID_REQUIRED")
    normalized_model_type = _normalize_model_type(model_type) if model_type is not None else None
    with get_engine().begin() as connection:
        create_llm_tables(connection)
        conditions = [
            UserModelLibrary.owner_user_id == owner_user_id,
            UserModelLibrary.status == MODEL_STATUS_ACTIVE,
        ]
        if normalized_model_type:
            conditions.append(UserModelLibrary.model_type == normalized_model_type)
        rows = connection.execute(
            select(*_MODEL_COLUMNS)
            .where(*conditions)
            .order_by(UserModelLibrary.created_at.desc())
        ).fetchall()
        return [model_row_to_dict(row, include_api_key) for row in rows]


def _get_user_model_sync(
    model_id: str,
    owner_user_id: Optional[str] = None,
    include_api_key: bool = True,
) -> Optional[Dict[str, object]]:
    get_engine, ensure_database = _load_database_helpers()
    ensure_database()
    model_id = _normalize_required_text(model_id, "MODEL_ID_REQUIRED")
    owner_user_id = _normalize_optional_text(owner_user_id)
    with get_engine().begin() as connection:
        create_llm_tables(connection)
        conditions = [
            UserModelLibrary.model_id == model_id,
            UserModelLibrary.status == MODEL_STATUS_ACTIVE,
        ]
        if owner_user_id:
            conditions.append(UserModelLibrary.owner_user_id == owner_user_id)
        row = connection.execute(
            select(*_MODEL_COLUMNS).where(*conditions).limit(1)
        ).fetchone()
        return model_row_to_dict(row, include_api_key) if row else None


def _delete_user_model_sync(model_id: str, owner_user_id: str) -> bool:
    get_engine, ensure_database = _load_database_helpers()
    ensure_database()
    model_id = _normalize_required_text(model_id, "MODEL_ID_REQUIRED")
    owner_user_id = _normalize_required_text(owner_user_id, "OWNER_USER_ID_REQUIRED")
    with get_engine().begin() as connection:
        create_llm_tables(connection)
        row = connection.execute(
            select(UserModelLibrary.model_id)
            .where(
                UserModelLibrary.model_id == model_id,
                UserModelLibrary.owner_user_id == owner_user_id,
                UserModelLibrary.status == MODEL_STATUS_ACTIVE,
            )
            .limit(1)
            .with_for_update()
        ).fetchone()
        if row is None:
            return False

        connection.execute(
            delete(ExamJudgeConfigAgent).where(ExamJudgeConfigAgent.model_id == model_id)
        )
        result = connection.execute(
            delete(UserModelLibrary).where(
                UserModelLibrary.model_id == model_id,
                UserModelLibrary.owner_user_id == owner_user_id,
            )
        )
        return result.rowcount > 0


def ensure_models_exist(connection, model_ids: List[str], owner_user_id: str) -> None:
    if not model_ids:
        raise ValueError("JUDGE_MODEL_REQUIRED")
    rows = connection.execute(
        select(UserModelLibrary.model_id).where(
            UserModelLibrary.model_id.in_(model_ids),
            UserModelLibrary.owner_user_id == owner_user_id,
            UserModelLibrary.status == MODEL_STATUS_ACTIVE,
        )
    ).fetchall()
    found_model_ids = {row[0] for row in rows}
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
    elif len(row) == 13:
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
    else:
        fields = (
            "model_id",
            "owner_user_id",
            "model_name",
            "model_type",
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
    result.setdefault("model_type", MODEL_TYPE_DEFAULT)
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


def _normalize_model_type(value) -> str:
    model_type = (_normalize_optional_text(value) or MODEL_TYPE_DEFAULT).lower()
    if model_type not in MODEL_TYPES:
        raise ValueError("MODEL_TYPE_INVALID")
    return model_type


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


def _now() -> datetime:
    return datetime.now()


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
    from AIOralExamSystem.Exam.Examdata.connection import get_engine, ensure_database

    return get_engine, ensure_database
