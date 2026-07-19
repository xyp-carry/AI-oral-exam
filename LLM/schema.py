from .config import MODEL_STATUS_ACTIVE, MODEL_TABLE_NAME


def create_llm_tables(cursor) -> None:
    cursor.execute(f"""
        CREATE TABLE IF NOT EXISTS {MODEL_TABLE_NAME} (
            model_id CHAR(36) PRIMARY KEY,
            owner_user_id VARCHAR(128) NOT NULL,
            model_name VARCHAR(128) NOT NULL,
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
            INDEX idx_user_model_library_status (status)
        ) DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
    """)
    _ensure_user_model_library_extra_fields(cursor)


def _ensure_user_model_library_extra_fields(cursor) -> None:
    cursor.execute(f"SHOW COLUMNS FROM {MODEL_TABLE_NAME}")
    existing = {row[0] for row in cursor.fetchall()}
    fields = {
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
