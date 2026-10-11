import asyncio
import json
import uuid
from datetime import datetime
from typing import Dict, List, Optional

from sqlalchemy import delete, literal, select, update
from sqlalchemy.dialects.mysql import insert as mysql_insert

from LLM.model_repository import ensure_models_exist, model_row_to_dict
from LLM.models import UserModelLibrary

from .connection import ensure_database, get_engine
from .schema import ensure_tables
from .exam_item_repository import ensure_exam_item_editable
from .models import CourseExamItem, ExamJudgeConfig, ExamJudgeConfigAgent, ExamSession

MODEL_INPUT_TOKEN_BUDGET_RATIO = 0.75

_AGENT_MODEL_COLUMNS = (
    ExamJudgeConfigAgent.agent_role,
    ExamJudgeConfigAgent.agent_index,
    ExamJudgeConfigAgent.model_settings_json,
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


async def upsert_exam_judge_config(
    exam_item_id: str,
    created_by: str,
    scorer_model_ids: List[str],
    flow_type: str = "panel",
    adjudicator_model_id: Optional[str] = None,
    fail_policy: str = "majority",
    model_settings_by_agent: Optional[Dict[str, Dict[str, object]]] = None,
    main_judger_model_id: Optional[str] = None,
    report_judger_model_id: Optional[str] = None,
    mineru_model_id: Optional[str] = None,
    embedding_model_id: Optional[str] = None,
    tts_model_id: Optional[str] = None,
    clear_optional_roles: Optional[List[str]] = None,
) -> Dict[str, object]:
    return await asyncio.to_thread(
        _upsert_exam_judge_config_sync,
        exam_item_id,
        created_by,
        scorer_model_ids,
        flow_type,
        adjudicator_model_id,
        fail_policy,
        model_settings_by_agent,
        main_judger_model_id,
        report_judger_model_id,
        mineru_model_id,
        embedding_model_id,
        tts_model_id,
        clear_optional_roles,
    )


async def upsert_exam_report_model_config(
    exam_item_id: str,
    created_by: str,
    report_judger_model_id: str,
    model_settings: Optional[Dict[str, object]] = None,
) -> Dict[str, object]:
    return await asyncio.to_thread(
        _upsert_exam_report_model_config_sync,
        exam_item_id,
        created_by,
        report_judger_model_id,
        model_settings,
    )


async def upsert_exam_optional_agent_models(
    exam_item_id: str,
    created_by: str,
    model_ids_by_role: Dict[str, Optional[str]],
) -> Dict[str, object]:
    return await asyncio.to_thread(
        _upsert_exam_optional_agent_models_sync,
        exam_item_id,
        created_by,
        model_ids_by_role,
    )


async def upsert_exam_rag_model_config(
    exam_item_id: str,
    created_by: str,
    mineru_model_id: str,
    embedding_model_id: str,
) -> Dict[str, object]:
    return await upsert_exam_optional_agent_models(
        exam_item_id,
        created_by,
        {"mineru": mineru_model_id, "embedding": embedding_model_id},
    )


async def get_exam_judge_config_by_exam_item(
    exam_item_id: str,
    include_api_key: bool = True,
) -> Optional[Dict[str, object]]:
    return await asyncio.to_thread(
        _get_exam_judge_config_by_exam_item_sync,
        exam_item_id,
        include_api_key,
    )


async def get_exam_judge_config_by_exam_id(
    exam_id: str,
    include_api_key: bool = True,
) -> Optional[Dict[str, object]]:
    return await asyncio.to_thread(
        _get_exam_judge_config_by_exam_id_sync,
        exam_id,
        include_api_key,
    )


def _upsert_exam_judge_config_sync(
    exam_item_id: str,
    created_by: str,
    scorer_model_ids: List[str],
    flow_type: str,
    adjudicator_model_id: Optional[str],
    fail_policy: str,
    model_settings_by_agent: Optional[Dict[str, Dict[str, object]]],
    main_judger_model_id: Optional[str],
    report_judger_model_id: Optional[str],
    mineru_model_id: Optional[str],
    embedding_model_id: Optional[str],
    tts_model_id: Optional[str],
    clear_optional_roles: Optional[List[str]],
) -> Dict[str, object]:
    ensure_database()
    exam_item_id = _normalize_required_text(exam_item_id, "EXAM_ITEM_ID_REQUIRED")
    created_by = _normalize_required_text(created_by, "CREATED_BY_REQUIRED")
    scorer_model_ids = _normalize_model_ids(scorer_model_ids)
    flow_type = _normalize_flow_type(flow_type, len(scorer_model_ids))
    fail_policy = _normalize_optional_text(fail_policy) or "majority"
    adjudicator_model_id = _normalize_optional_text(adjudicator_model_id)
    main_judger_model_id = _normalize_optional_text(main_judger_model_id)
    report_judger_model_id = _normalize_optional_text(report_judger_model_id)
    mineru_model_id = _normalize_optional_text(mineru_model_id)
    embedding_model_id = _normalize_optional_text(embedding_model_id)
    tts_model_id = _normalize_optional_text(tts_model_id)
    clear_optional_roles = list(clear_optional_roles or [])
    if set(clear_optional_roles) - {"mineru", "embedding", "tts"}:
        raise ValueError("AGENT_ROLE_INVALID")
    if ("mineru" in clear_optional_roles and mineru_model_id) or (
        "embedding" in clear_optional_roles and embedding_model_id
    ) or (
        "tts" in clear_optional_roles and tts_model_id
    ):
        raise ValueError("AGENT_ROLE_INVALID")
    model_settings_by_agent = model_settings_by_agent or {}
    config_id = str(uuid.uuid4())
    now = _now()
    with get_engine().begin() as connection:
        ensure_tables(connection)
        _ensure_exam_item_exists(connection, exam_item_id)
        ensure_models_exist(
            connection,
            (
                scorer_model_ids
                + ([adjudicator_model_id] if adjudicator_model_id else [])
                + ([main_judger_model_id] if main_judger_model_id else [])
                + ([report_judger_model_id] if report_judger_model_id else [])
                + ([mineru_model_id] if mineru_model_id else [])
                + ([embedding_model_id] if embedding_model_id else [])
                + ([tts_model_id] if tts_model_id else [])
            ),
            created_by,
        )
        statement = mysql_insert(ExamJudgeConfig).values(
            config_id=config_id,
            exam_item_id=exam_item_id,
            flow_type=flow_type,
            judge_count=len(scorer_model_ids),
            adjudicator_enabled=1 if adjudicator_model_id else 0,
            fail_policy=fail_policy,
            status="active",
            created_by=created_by,
            created_at=now,
            updated_at=now,
        )
        connection.execute(
            statement.on_duplicate_key_update(
                flow_type=statement.inserted.flow_type,
                judge_count=statement.inserted.judge_count,
                adjudicator_enabled=statement.inserted.adjudicator_enabled,
                fail_policy=statement.inserted.fail_policy,
                status="active",
                updated_at=statement.inserted.updated_at,
            )
        )
        active_config_id = connection.execute(
            select(ExamJudgeConfig.config_id)
            .where(ExamJudgeConfig.exam_item_id == exam_item_id)
            .limit(1)
        ).fetchone()[0]
        roles_to_replace = ["scorer", "adjudicator", "setter", "main_judger"]
        if report_judger_model_id:
            roles_to_replace.append("report_judger")
        if mineru_model_id:
            roles_to_replace.append("mineru")
        if embedding_model_id:
            roles_to_replace.append("embedding")
        if tts_model_id:
            roles_to_replace.append("tts")
        roles_to_replace.extend(clear_optional_roles)
        connection.execute(
            delete(ExamJudgeConfigAgent).where(
                ExamJudgeConfigAgent.config_id == active_config_id,
                ExamJudgeConfigAgent.agent_role.in_(roles_to_replace),
            )
        )
        for index, model_id in enumerate(scorer_model_ids, start=1):
            _insert_config_agent(
                connection,
                active_config_id,
                "scorer",
                index,
                model_id,
                model_settings_by_agent.get(f"scorer:{index}"),
                now,
            )
        if adjudicator_model_id:
            _insert_config_agent(
                connection,
                active_config_id,
                "adjudicator",
                0,
                adjudicator_model_id,
                model_settings_by_agent.get("adjudicator"),
                now,
            )
        if main_judger_model_id:
            _insert_config_agent(
                connection,
                active_config_id,
                "main_judger",
                0,
                main_judger_model_id,
                model_settings_by_agent.get("main_judger"),
                now,
            )
        if report_judger_model_id:
            _insert_config_agent(
                connection,
                active_config_id,
                "report_judger",
                0,
                report_judger_model_id,
                model_settings_by_agent.get("report_judger"),
                now,
            )
        if mineru_model_id:
            _insert_config_agent(
                connection,
                active_config_id,
                "mineru",
                0,
                mineru_model_id,
                model_settings_by_agent.get("mineru"),
                now,
            )
        if embedding_model_id:
            _insert_config_agent(
                connection,
                active_config_id,
                "embedding",
                0,
                embedding_model_id,
                model_settings_by_agent.get("embedding"),
                now,
            )
        if tts_model_id:
            _insert_config_agent(
                connection,
                active_config_id,
                "tts",
                0,
                tts_model_id,
                model_settings_by_agent.get("tts"),
                now,
            )
    return _get_exam_judge_config_by_exam_item_sync(exam_item_id, include_api_key=True) or {}


def _upsert_exam_report_model_config_sync(
    exam_item_id: str,
    created_by: str,
    report_judger_model_id: str,
    model_settings: Optional[Dict[str, object]],
) -> Dict[str, object]:
    ensure_database()
    exam_item_id = _normalize_required_text(exam_item_id, "EXAM_ITEM_ID_REQUIRED")
    created_by = _normalize_required_text(created_by, "CREATED_BY_REQUIRED")
    report_judger_model_id = _normalize_required_text(
        report_judger_model_id,
        "REPORT_MODEL_CONFIG_REQUIRED",
    )
    config_id = str(uuid.uuid4())
    now = _now()
    with get_engine().begin() as connection:
        ensure_tables(connection)
        _ensure_exam_item_exists(connection, exam_item_id)
        ensure_models_exist(connection, [report_judger_model_id], created_by)
        statement = mysql_insert(ExamJudgeConfig).values(
            config_id=config_id,
            exam_item_id=exam_item_id,
            flow_type="single",
            judge_count=0,
            adjudicator_enabled=0,
            fail_policy="majority",
            status="active",
            created_by=created_by,
            created_at=now,
            updated_at=now,
        )
        connection.execute(
            statement.on_duplicate_key_update(
                status="active",
                updated_at=statement.inserted.updated_at,
            )
        )
        active_config_id = connection.execute(
            select(ExamJudgeConfig.config_id)
            .where(ExamJudgeConfig.exam_item_id == exam_item_id)
            .limit(1)
        ).fetchone()[0]
        connection.execute(
            delete(ExamJudgeConfigAgent).where(
                ExamJudgeConfigAgent.config_id == active_config_id,
                ExamJudgeConfigAgent.agent_role == "report_judger",
            )
        )
        _insert_config_agent(
            connection,
            active_config_id,
            "report_judger",
            0,
            report_judger_model_id,
            model_settings,
            now,
        )
    return _get_exam_judge_config_by_exam_item_sync(exam_item_id, include_api_key=True) or {}


def _upsert_exam_optional_agent_models_sync(
    exam_item_id: str,
    created_by: str,
    model_ids_by_role: Dict[str, Optional[str]],
) -> Dict[str, object]:
    if not model_ids_by_role or set(model_ids_by_role) - {"mineru", "embedding", "tts"}:
        raise ValueError("AGENT_ROLE_INVALID")
    ensure_database()
    exam_item_id = _normalize_required_text(exam_item_id, "EXAM_ITEM_ID_REQUIRED")
    created_by = _normalize_required_text(created_by, "CREATED_BY_REQUIRED")
    normalized_models = {
        role: _normalize_required_text(model_id, "MODEL_ID_REQUIRED")
        if model_id is not None else None
        for role, model_id in model_ids_by_role.items()
    }
    model_ids = [model_id for model_id in normalized_models.values() if model_id]
    now = _now()
    with get_engine().begin() as connection:
        ensure_tables(connection)
        _ensure_exam_item_exists(connection, exam_item_id)
        if model_ids:
            ensure_models_exist(connection, model_ids, created_by)
        existing_config = connection.execute(
            select(ExamJudgeConfig.config_id)
            .where(ExamJudgeConfig.exam_item_id == exam_item_id)
            .limit(1)
            .with_for_update()
        ).fetchone()
        if not existing_config and not model_ids:
            return {}
        if model_ids:
            config_id = str(uuid.uuid4())
            statement = mysql_insert(ExamJudgeConfig).values(
                config_id=config_id,
                exam_item_id=exam_item_id,
                flow_type="single",
                judge_count=0,
                adjudicator_enabled=0,
                fail_policy="majority",
                status="active",
                created_by=created_by,
                created_at=now,
                updated_at=now,
            )
            connection.execute(
                statement.on_duplicate_key_update(
                    status="active",
                    updated_at=statement.inserted.updated_at,
                )
            )
            active_config_id = connection.execute(
                select(ExamJudgeConfig.config_id).where(
                    ExamJudgeConfig.exam_item_id == exam_item_id
                )
            ).fetchone()[0]
        else:
            active_config_id = existing_config[0]
            connection.execute(
                update(ExamJudgeConfig)
                .where(ExamJudgeConfig.config_id == active_config_id)
                .values(updated_at=now)
            )

        for role, model_id in normalized_models.items():
            connection.execute(
                delete(ExamJudgeConfigAgent).where(
                    ExamJudgeConfigAgent.config_id == active_config_id,
                    ExamJudgeConfigAgent.agent_role == role,
                )
            )
            if model_id:
                _insert_config_agent(
                    connection, active_config_id, role, 0, model_id, None, now,
                )
    return _get_exam_judge_config_by_exam_item_sync(exam_item_id, include_api_key=True) or {}


def _get_exam_judge_config_by_exam_item_sync(
    exam_item_id: str,
    include_api_key: bool = True,
) -> Optional[Dict[str, object]]:
    ensure_database()
    exam_item_id = _normalize_required_text(exam_item_id, "EXAM_ITEM_ID_REQUIRED")
    with get_engine().begin() as connection:
        ensure_tables(connection)
        return _fetch_exam_judge_config_by_exam_item(connection, exam_item_id, include_api_key)


def _get_exam_judge_config_by_exam_id_sync(
    exam_id: str,
    include_api_key: bool = True,
) -> Optional[Dict[str, object]]:
    ensure_database()
    exam_id = _normalize_required_text(exam_id, "EXAM_ID_REQUIRED")
    with get_engine().begin() as connection:
        ensure_tables(connection)
        row = connection.execute(
            select(ExamSession.exam_item_id)
            .where(ExamSession.exam_id == exam_id)
            .limit(1)
        ).fetchone()
        if not row or not row[0]:
            return None
        return _fetch_exam_judge_config_by_exam_item(connection, row[0], include_api_key)


def _fetch_exam_judge_config_by_exam_item(
    connection,
    exam_item_id: str,
    include_api_key: bool,
) -> Optional[Dict[str, object]]:
    row = connection.execute(
        select(
            ExamJudgeConfig.config_id,
            ExamJudgeConfig.exam_item_id,
            ExamJudgeConfig.flow_type,
            ExamJudgeConfig.judge_count,
            ExamJudgeConfig.adjudicator_enabled,
            ExamJudgeConfig.fail_policy,
            ExamJudgeConfig.status,
            ExamJudgeConfig.created_by,
            ExamJudgeConfig.created_at,
            ExamJudgeConfig.updated_at,
        )
        .where(
            ExamJudgeConfig.exam_item_id == exam_item_id,
            ExamJudgeConfig.status == "active",
        )
        .limit(1)
    ).fetchone()
    if not row:
        return None
    config = _config_row_to_dict(row)
    agent_rows = connection.execute(
        select(*_AGENT_MODEL_COLUMNS)
        .select_from(ExamJudgeConfigAgent)
        .join(
            UserModelLibrary,
            ExamJudgeConfigAgent.model_id == UserModelLibrary.model_id,
        )
        .where(
            ExamJudgeConfigAgent.config_id == config["config_id"],
            ExamJudgeConfigAgent.status == "active",
            UserModelLibrary.status == "active",
        )
        .order_by(ExamJudgeConfigAgent.agent_role, ExamJudgeConfigAgent.agent_index)
    ).fetchall()
    scorers = []
    adjudicator = None
    main_judger = None
    report_judger = None
    mineru = None
    embedding = None
    tts = None
    for agent_row in agent_rows:
        agent = _agent_row_to_dict(agent_row, include_api_key)
        agent_role = agent["agent_role"]
        if agent_role == "adjudicator":
            adjudicator = agent
        elif agent_role == "main_judger":
            main_judger = agent
        elif agent_role == "report_judger":
            report_judger = agent
        elif agent_role == "mineru":
            mineru = agent
        elif agent_role == "embedding":
            embedding = agent
        elif agent_role == "tts":
            tts = agent
        elif agent_role == "scorer":
            scorers.append(agent)
    config["scorers"] = scorers
    config["adjudicator"] = adjudicator
    config["main_judger"] = main_judger
    config["report_judger"] = report_judger
    config["mineru"] = mineru
    config["embedding"] = embedding
    config["tts"] = tts
    return config


def _insert_config_agent(
    connection,
    config_id: str,
    agent_role: str,
    agent_index: int,
    model_id: str,
    model_settings: Optional[Dict[str, object]],
    now: datetime,
) -> None:
    connection.execute(
        ExamJudgeConfigAgent.__table__.insert().values(
            config_id=config_id,
            agent_role=agent_role,
            agent_index=agent_index,
            model_id=model_id,
            model_settings_json=json.dumps(model_settings or {}, ensure_ascii=False),
            status="active",
            created_at=now,
            updated_at=now,
        )
    )


def _ensure_exam_item_exists(connection, exam_item_id: str) -> None:
    row = connection.execute(
        select(literal(1))
        .where(
            CourseExamItem.exam_item_id == exam_item_id,
            CourseExamItem.status == "active",
        )
        .limit(1)
        .with_for_update()
    ).fetchone()
    if row is None:
        raise ValueError("EXAM_ITEM_NOT_FOUND")
    ensure_exam_item_editable(connection, exam_item_id)


def _config_row_to_dict(row) -> Dict[str, object]:
    fields = (
        "config_id",
        "exam_item_id",
        "flow_type",
        "judge_count",
        "adjudicator_enabled",
        "fail_policy",
        "status",
        "created_by",
        "created_at",
        "updated_at",
    )
    result = dict(zip(fields, row))
    result["adjudicator_enabled"] = bool(result.get("adjudicator_enabled"))
    for key in ("created_at", "updated_at"):
        value = result.get(key)
        if value is not None:
            result[key] = value.strftime("%Y-%m-%d %H:%M:%S")
    return result


def _agent_row_to_dict(row, include_api_key: bool) -> Dict[str, object]:
    (
        agent_role,
        agent_index,
        model_settings_json,
        model_id,
        owner_user_id,
        model_name,
        model_type,
        model_api_key,
        provider,
        provider_model_key,
        base_url,
        display_name,
        params_json,
        last_test_result_json,
        status,
        created_at,
        updated_at,
    ) = row
    model = model_row_to_dict(
        (
            model_id,
            owner_user_id,
            model_name,
            model_type,
            model_api_key,
            provider,
            provider_model_key,
            base_url,
            display_name,
            params_json,
            last_test_result_json,
            status,
            created_at,
            updated_at,
        ),
        include_api_key,
    )
    model_settings = _json_loads(model_settings_json, {})
    return {
        "agent_role": agent_role,
        "agent_index": agent_index,
        "model": model,
        "model_settings": model_settings,
        "runtime_model_settings": build_agent_model_settings(
            {
                "model": model,
                "model_settings": model_settings,
            },
            {},
        ),
    }


def build_agent_model_settings(
    agent_config: Dict[str, object],
    default_model_settings: Optional[Dict[str, object]] = None,
) -> Dict[str, object]:
    runtime_model_settings = agent_config.get("runtime_model_settings")
    if isinstance(runtime_model_settings, dict):
        model_settings = dict(default_model_settings or {})
        model_settings.update(runtime_model_settings)
        return _apply_token_budget_settings(model_settings)

    model = agent_config.get("model") or {}
    model_settings = dict(default_model_settings or {})
    model_settings.update(model.get("params") or {})
    model_settings.update(agent_config.get("model_settings") or {})
    if model.get("model_name"):
        model_settings["model_name"] = model["model_name"]
    if model.get("model_api_key"):
        model_settings["model_api_key"] = model["model_api_key"]
    if model.get("base_url"):
        model_settings["model_url"] = model["base_url"]
    if model.get("model_url"):
        model_settings["model_url"] = model["model_url"]
    return _apply_token_budget_settings(model_settings)


def _apply_token_budget_settings(model_settings: Dict[str, object]) -> Dict[str, object]:
    max_context_tokens = _to_positive_int(model_settings.get("max_context_tokens"))
    if not max_context_tokens:
        return model_settings

    safe_max_input_tokens = int(max_context_tokens * MODEL_INPUT_TOKEN_BUDGET_RATIO)
    configured_max_input_tokens = _to_positive_int(model_settings.get("max_input_tokens"))
    model_settings["max_context_tokens"] = max_context_tokens
    model_settings["max_input_tokens"] = (
        min(configured_max_input_tokens, safe_max_input_tokens)
        if configured_max_input_tokens
        else safe_max_input_tokens
    )
    return model_settings


def _to_positive_int(value) -> Optional[int]:
    try:
        number = int(value)
    except (TypeError, ValueError):
        return None
    return number if number > 0 else None


def _normalize_model_ids(model_ids: List[str]) -> List[str]:
    if not model_ids:
        raise ValueError("JUDGE_MODEL_REQUIRED")
    normalized = []
    for model_id in model_ids:
        value = _normalize_required_text(model_id, "MODEL_ID_REQUIRED")
        normalized.append(value)
    return normalized


def _normalize_flow_type(flow_type: str, judge_count: int) -> str:
    value = (_normalize_optional_text(flow_type) or "single").lower()
    if value not in {"single", "panel"}:
        raise ValueError("JUDGE_FLOW_TYPE_INVALID")
    if value == "single" and judge_count != 1:
        raise ValueError("SINGLE_JUDGE_FLOW_REQUIRES_ONE_MODEL")
    if value == "panel" and judge_count < 1:
        raise ValueError("PANEL_JUDGE_FLOW_REQUIRES_MODEL")
    return value


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


def _json_loads(value, default):
    if value is None:
        return default
    if isinstance(value, (dict, list)):
        return value
    try:
        return json.loads(value)
    except (TypeError, ValueError):
        return default


def _now() -> datetime:
    return datetime.now()
