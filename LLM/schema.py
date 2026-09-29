from .config import MODEL_STATUS_ACTIVE, MODEL_TABLE_NAME
from .model_token_limits import MODEL_TOKEN_LIMITS, get_model_token_limits
from .config import MODEL_TYPE_DEFAULT


def create_llm_tables(cursor) -> None:
    cursor.execute(f"""
        CREATE TABLE IF NOT EXISTS {MODEL_TABLE_NAME} (
            model_id CHAR(36) PRIMARY KEY,
            owner_user_id VARCHAR(128) NOT NULL,
            model_name VARCHAR(128) NOT NULL,
            model_type VARCHAR(20) NOT NULL DEFAULT '{MODEL_TYPE_DEFAULT}',
            model_api_key TEXT NOT NULL,
            provider VARCHAR(64) DEFAULT NULL,
            provider_model_key VARCHAR(128) DEFAULT NULL,
            base_url TEXT DEFAULT NULL,
            display_name VARCHAR(128) DEFAULT NULL,
            params_json JSON DEFAULT NULL,
            last_test_result_json JSON DEFAULT NULL,
            status VARCHAR(20) NOT NULL DEFAULT '{MODEL_STATUS_ACTIVE}',
            created_at DATETIME NOT NULL,
            updated_at DATETIME NOT NULL,
            INDEX idx_user_model_library_owner (owner_user_id),
            INDEX idx_user_model_library_provider (provider),
            INDEX idx_user_model_library_type (model_type),
            INDEX idx_user_model_library_status (status)
        ) DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
    """)
    _ensure_user_model_library_extra_fields(cursor)
    _ensure_user_model_library_token_limits(cursor)


def _ensure_user_model_library_extra_fields(cursor) -> None:
    cursor.execute(f"SHOW COLUMNS FROM {MODEL_TABLE_NAME}")
    existing = {row[0] for row in cursor.fetchall()}
    fields = {
        "model_type": f"VARCHAR(20) NOT NULL DEFAULT '{MODEL_TYPE_DEFAULT}' AFTER model_name",
        "provider_model_key": "VARCHAR(128) DEFAULT NULL AFTER provider",
        "params_json": "JSON DEFAULT NULL AFTER display_name",
        "last_test_result_json": "JSON DEFAULT NULL AFTER params_json",
    }
    for field_name, field_type in fields.items():
        if field_name not in existing:
            cursor.execute(f"ALTER TABLE {MODEL_TABLE_NAME} ADD COLUMN {field_name} {field_type}")

    cursor.execute(f"SHOW INDEX FROM {MODEL_TABLE_NAME} WHERE Key_name = 'idx_user_model_library_provider'")
    if not cursor.fetchone():
        cursor.execute(f"ALTER TABLE {MODEL_TABLE_NAME} ADD INDEX idx_user_model_library_provider (provider)")

    cursor.execute(f"SHOW INDEX FROM {MODEL_TABLE_NAME} WHERE Key_name = 'idx_user_model_library_type'")
    if not cursor.fetchone():
        cursor.execute(f"ALTER TABLE {MODEL_TABLE_NAME} ADD INDEX idx_user_model_library_type (model_type)")

    # Backfill rows created before model_type existed.
    cursor.execute(
        f"""
        UPDATE {MODEL_TABLE_NAME}
        SET model_type = CASE
            WHEN provider = 'mineru' THEN 'file'
            WHEN provider LIKE '%embedding%' THEN 'embedding'
            ELSE '{MODEL_TYPE_DEFAULT}'
        END
        WHERE model_type IS NULL
           OR model_type = ''
           OR (model_type = '{MODEL_TYPE_DEFAULT}' AND provider = 'mineru')
           OR (model_type = '{MODEL_TYPE_DEFAULT}' AND provider LIKE '%embedding%')
        """
    )


def _ensure_user_model_library_token_limits(cursor) -> None:
    for model_name in MODEL_TOKEN_LIMITS:
        limits = get_model_token_limits(model_name)
        if not limits:
            continue
        cursor.execute(
            f"""
            UPDATE {MODEL_TABLE_NAME}
            SET params_json = JSON_SET(
                COALESCE(params_json, JSON_OBJECT()),
                '$.max_context_tokens', %s,
                '$.max_input_tokens', %s
            )
            WHERE provider_model_key = %s OR model_name = %s
            """,
            (
                limits["max_context_tokens"],
                limits["max_input_tokens"],
                model_name,
                model_name,
            ),
        )
