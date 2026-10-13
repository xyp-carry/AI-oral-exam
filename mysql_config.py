"""Shared MySQL connection settings for the exam and authentication databases."""

import os
from pathlib import Path

from dotenv import load_dotenv


load_dotenv(Path(__file__).resolve().parent / ".env", override=False)


def get_mysql_config(*, database_env: str, default_database: str) -> dict:
    """Read connection settings from the process environment or local .env."""
    return {
        "host": os.getenv("MYSQL_HOST", "127.0.0.1"),
        "port": int(os.getenv("MYSQL_PORT", "3306")),
        "user": os.getenv("MYSQL_USER", "root"),
        "password": os.getenv("MYSQL_PASSWORD", ""),
        "database": os.getenv(database_env, default_database),
        "charset": os.getenv("MYSQL_CHARSET", "utf8mb4"),
    }
