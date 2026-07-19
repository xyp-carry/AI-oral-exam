import asyncio
import json
import uuid
from datetime import datetime
from typing import Dict, List, Optional

from LLM.model_repository import ensure_models_exist, model_row_to_dict

from .connection import connect, ensure_database
from .schema import ensure_tables


async def upsert_exam_judge_config(
    exam_item_id: str,
    created_by: str,
    scorer_model_ids: List[str],
    flow_type: str = "panel",
    adjudicator_model_id: Optional[str] = None,
    fail_policy: str = "majority",
    model_settings_by_agent: Optional[Dict[str, Dict[str, object]]] = None,
    setter_model_id: Optional[str] = None,
    main_judger_model_id: Optional[str] = None,
    report_judger_model_id: Optional[str] = None,
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
        setter_model_id,
        main_judger_model_id,
        report_judger_model_id,
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
    setter_model_id: Optional[str],
    main_judger_model_id: Optional[str],
    report_judger_model_id: Optional[str],
) -> Dict[str, object]:
    ensure_database()
    exam_item_id = _normalize_required_text(exam_item_id, "EXAM_ITEM_ID_REQUIRED")
    created_by = _normalize_required_text(created_by, "CREATED_BY_REQUIRED")
    scorer_model_ids = _normalize_model_ids(scorer_model_ids)
    flow_type = _normalize_flow_type(flow_type, len(scorer_model_ids))
    fail_policy = _normalize_optional_text(fail_policy) or "majority"
    adjudicator_model_id = _normalize_optional_text(adjudicator_model_id)
    setter_model_id = _normalize_optional_text(setter_model_id)
    main_judger_model_id = _normalize_optional_text(main_judger_model_id)
    report_judger_model_id = _normalize_optional_text(report_judger_model_id)
    model_settings_by_agent = model_settings_by_agent or {}
    config_id = str(uuid.uuid4())
    now = _now()
    connection = connect(use_database=True)
    try:
        ensure_tables(connection)
        with connection.cursor() as cursor:
            _ensure_exam_item_exists(cursor, exam_item_id)
            ensure_models_exist(
                cursor,
                (
                    scorer_model_ids
                    + ([adjudicator_model_id] if adjudicator_model_id else [])
                    + ([setter_model_id] if setter_model_id else [])
                    + ([main_judger_model_id] if main_judger_model_id else [])
                    + ([report_judger_model_id] if report_judger_model_id else [])
                ),
                created_by,
            )
            cursor.execute(
                """
                INSERT INTO exam_judge_configs (
                    config_id,
                    exam_item_id,
                    flow_type,
                    judge_count,
                    adjudicator_enabled,
                    fail_policy,
                    status,
                    created_by,
                    created_at,
                    updated_at
                ) VALUES (%s, %s, %s, %s, %s, %s, 'active', %s, %s, %s)
                ON DUPLICATE KEY UPDATE
                    flow_type = VALUES(flow_type),
                    judge_count = VALUES(judge_count),
                    adjudicator_enabled = VALUES(adjudicator_enabled),
                    fail_policy = VALUES(fail_policy),
                    status = 'active',
                    updated_at = VALUES(updated_at)
                """,
                (
                    config_id,
                    exam_item_id,
                    flow_type,
                    len(scorer_model_ids),
                    1 if adjudicator_model_id else 0,
                    fail_policy,
                    created_by,
                    now,
                    now,
                ),
            )
            cursor.execute(
                """
                SELECT config_id
                FROM exam_judge_configs
                WHERE exam_item_id = %s
                LIMIT 1
                """,
                (exam_item_id,),
            )
            row = cursor.fetchone()
            active_config_id = row[0]
            roles_to_replace = ["scorer", "adjudicator", "setter", "main_judger"]
            if report_judger_model_id:
                roles_to_replace.append("report_judger")
            cursor.execute(
                f"""
                DELETE FROM exam_judge_config_agents
                WHERE config_id = %s
                  AND agent_role IN ({", ".join(["%s"] * len(roles_to_replace))})
                """,
                (active_config_id, *roles_to_replace),
            )
            for index, model_id in enumerate(scorer_model_ids, start=1):
                _insert_config_agent(
                    cursor,
                    active_config_id,
                    "scorer",
                    index,
                    model_id,
                    model_settings_by_agent.get(f"scorer:{index}"),
                    now,
                )
            if adjudicator_model_id:
                _insert_config_agent(
                    cursor,
                    active_config_id,
                    "adjudicator",
                    0,
                    adjudicator_model_id,
                    model_settings_by_agent.get("adjudicator"),
                    now,
                )
            if setter_model_id:
                _insert_config_agent(
                    cursor,
                    active_config_id,
                    "setter",
                    0,
                    setter_model_id,
                    model_settings_by_agent.get("setter"),
                    now,
                )
            if main_judger_model_id:
                _insert_config_agent(
                    cursor,
                    active_config_id,
                    "main_judger",
                    0,
                    main_judger_model_id,
                    model_settings_by_agent.get("main_judger"),
                    now,
                )
            if report_judger_model_id:
                _insert_config_agent(
                    cursor,
                    active_config_id,
                    "report_judger",
                    0,
                    report_judger_model_id,
                    model_settings_by_agent.get("report_judger"),
                    now,
                )
        connection.commit()
        return _get_exam_judge_config_by_exam_item_sync(exam_item_id, include_api_key=True) or {}
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


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
    connection = connect(use_database=True)
    try:
        ensure_tables(connection)
        with connection.cursor() as cursor:
            _ensure_exam_item_exists(cursor, exam_item_id)
            ensure_models_exist(cursor, [report_judger_model_id], created_by)
            cursor.execute(
                """
                INSERT INTO exam_judge_configs (
                    config_id,
                    exam_item_id,
                    flow_type,
                    judge_count,
                    adjudicator_enabled,
                    fail_policy,
                    status,
                    created_by,
                    created_at,
                    updated_at
                ) VALUES (%s, %s, 'single', 0, 0, 'majority', 'active', %s, %s, %s)
                ON DUPLICATE KEY UPDATE
                    status = 'active',
                    updated_at = VALUES(updated_at)
                """,
                (
                    config_id,
                    exam_item_id,
                    created_by,
                    now,
                    now,
                ),
            )
            cursor.execute(
                """
                SELECT config_id
                FROM exam_judge_configs
                WHERE exam_item_id = %s
                LIMIT 1
                """,
                (exam_item_id,),
            )
            row = cursor.fetchone()
            active_config_id = row[0]
            cursor.execute(
                """
                DELETE FROM exam_judge_config_agents
                WHERE config_id = %s
                  AND agent_role = 'report_judger'
                """,
                (active_config_id,),
            )
            _insert_config_agent(
                cursor,
                active_config_id,
                "report_judger",
                0,
                report_judger_model_id,
                model_settings,
                now,
            )
        connection.commit()
        return _get_exam_judge_config_by_exam_item_sync(exam_item_id, include_api_key=True) or {}
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


def _get_exam_judge_config_by_exam_item_sync(
    exam_item_id: str,
    include_api_key: bool = True,
) -> Optional[Dict[str, object]]:
    ensure_database()
    exam_item_id = _normalize_required_text(exam_item_id, "EXAM_ITEM_ID_REQUIRED")
    connection = connect(use_database=True)
    try:
        ensure_tables(connection)
        with connection.cursor() as cursor:
            return _fetch_exam_judge_config_by_exam_item(cursor, exam_item_id, include_api_key)
    finally:
        connection.close()


def _get_exam_judge_config_by_exam_id_sync(
    exam_id: str,
    include_api_key: bool = True,
) -> Optional[Dict[str, object]]:
    ensure_database()
    exam_id = _normalize_required_text(exam_id, "EXAM_ID_REQUIRED")
    connection = connect(use_database=True)
    try:
        ensure_tables(connection)
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT exam_item_id
                FROM exam_sessions
                WHERE exam_id = %s
                LIMIT 1
                """,
                (exam_id,),
            )
            row = cursor.fetchone()
            if not row or not row[0]:
                return None
            return _fetch_exam_judge_config_by_exam_item(cursor, row[0], include_api_key)
    finally:
        connection.close()


def _fetch_exam_judge_config_by_exam_item(
    cursor,
    exam_item_id: str,
    include_api_key: bool,
) -> Optional[Dict[str, object]]:
    cursor.execute(
        """
        SELECT
            config_id,
            exam_item_id,
            flow_type,
            judge_count,
            adjudicator_enabled,
            fail_policy,
            status,
            created_by,
            created_at,
            updated_at
        FROM exam_judge_configs
        WHERE exam_item_id = %s
          AND status = 'active'
        LIMIT 1
        """,
        (exam_item_id,),
    )
    row = cursor.fetchone()
    if not row:
        return None
    config = _config_row_to_dict(row)
    cursor.execute(
        """
        SELECT
            a.agent_role,
            a.agent_index,
            a.model_settings_json,
            m.model_id,
            m.owner_user_id,
            m.model_name,
            m.model_api_key,
            m.provider,
            m.provider_model_key,
            m.base_url,
            m.display_name,
            m.params_json,
            m.last_test_result_json,
            m.status,
            m.created_at,
            m.updated_at
        FROM exam_judge_config_agents a
        JOIN user_model_library m
          ON a.model_id = m.model_id
        WHERE a.config_id = %s
          AND a.status = 'active'
          AND m.status = 'active'
        ORDER BY a.agent_role, a.agent_index
        """,
        (config["config_id"],),
    )
    scorers = []
    adjudicator = None
    setter = None
    main_judger = None
    report_judger = None
    for row in cursor.fetchall():
        agent = _agent_row_to_dict(row, include_api_key)
        agent_role = agent["agent_role"]
        if agent_role == "adjudicator":
            adjudicator = agent
        elif agent_role == "setter":
            setter = agent
        elif agent_role == "main_judger":
            main_judger = agent
        elif agent_role == "report_judger":
            report_judger = agent
        elif agent_role == "scorer":
            scorers.append(agent)
    config["scorers"] = scorers
    config["adjudicator"] = adjudicator
    config["setter"] = setter
    config["main_judger"] = main_judger
    config["report_judger"] = report_judger
    return config


def _insert_config_agent(
    cursor,
    config_id: str,
    agent_role: str,
    agent_index: int,
    model_id: str,
    model_settings: Optional[Dict[str, object]],
    now: str,
) -> None:
    cursor.execute(
        """
        INSERT INTO exam_judge_config_agents (
            config_id,
            agent_role,
            agent_index,
            model_id,
            model_settings_json,
            status,
            created_at,
            updated_at
        ) VALUES (%s, %s, %s, %s, %s, 'active', %s, %s)
        """,
        (
            config_id,
            agent_role,
            agent_index,
            model_id,
            json.dumps(model_settings or {}, ensure_ascii=False),
            now,
            now,
        ),
    )


def _ensure_exam_item_exists(cursor, exam_item_id: str) -> None:
    cursor.execute(
        "SELECT 1 FROM course_exam_items WHERE exam_item_id = %s AND status = 'active' LIMIT 1",
        (exam_item_id,),
    )
    if cursor.fetchone() is None:
        raise ValueError("EXAM_ITEM_NOT_FOUND")


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
        return model_settings

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
    return model_settings


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


def _now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")
