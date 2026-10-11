"""SQLAlchemy engine factory for the exam database.

所有仓储统一通过 get_engine() 获取引擎，用 SQLAlchemy Core 表达式执行查询，
不再直接使用 pymysql 连接（pymysql 仅作为 SQLAlchemy 的底层驱动）。
"""

import re
from urllib.parse import quote_plus

from sqlalchemy import create_engine, text

from .config import LOCAL_MYSQL_CONFIG

_ENGINE = None


def _build_url(database=None) -> str:
    config = LOCAL_MYSQL_CONFIG
    credentials = quote_plus(str(config["user"]))
    password = quote_plus(str(config["password"]))
    url = f"mysql+pymysql://{credentials}:{password}@{config['host']}:{config['port']}"
    if database:
        url += f"/{database}"
    url += f"?charset={config.get('charset') or 'utf8mb4'}"
    return url


def get_engine():
    """返回库级单例引擎；pool_pre_ping 保证拿到的是可用连接。"""
    global _ENGINE
    if _ENGINE is None:
        _ENGINE = create_engine(
            _build_url(LOCAL_MYSQL_CONFIG.get("database")),
            pool_pre_ping=True,
            pool_recycle=3600,
        )
    return _ENGINE


def ensure_database() -> None:
    database = quote_identifier(LOCAL_MYSQL_CONFIG["database"])
    engine = create_engine(_build_url(None))
    try:
        with engine.begin() as connection:
            connection.execute(
                text(
                    f"CREATE DATABASE IF NOT EXISTS {database} "
                    "CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci"
                )
            )
    finally:
        engine.dispose()


def quote_identifier(identifier: str) -> str:
    if not re.fullmatch(r"[A-Za-z0-9_]+", identifier):
        raise ValueError(f"Unsafe MySQL identifier: {identifier}")
    return f"`{identifier}`"


def mysql_error_code(exc: BaseException):
    """从（可能被 SQLAlchemy 包装的）异常里取出 MySQL 错误码。

    SQLAlchemy 会把 pymysql 异常包成 sqlalchemy.exc.IntegrityError 等，
    原始错误在 .orig 上，错误码在 .orig.args[0]。
    """
    orig = getattr(exc, "orig", None)
    for candidate in (orig, exc):
        args = getattr(candidate, "args", None)
        if args and isinstance(args[0], int):
            return args[0]
    return None
