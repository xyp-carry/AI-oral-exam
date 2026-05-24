import re

from .config import LOCAL_MYSQL_CONFIG


def connect(use_database: bool = True):
    import pymysql

    config = dict(LOCAL_MYSQL_CONFIG)
    if not use_database:
        config.pop("database", None)
    return pymysql.connect(
        **config,
        autocommit=False,
    )


def ensure_database() -> None:
    database = quote_identifier(LOCAL_MYSQL_CONFIG["database"])
    connection = connect(use_database=False)
    try:
        with connection.cursor() as cursor:
            cursor.execute(
                f"CREATE DATABASE IF NOT EXISTS {database} "
                "CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci"
            )
        connection.commit()
    finally:
        connection.close()


def quote_identifier(identifier: str) -> str:
    if not re.fullmatch(r"[A-Za-z0-9_]+", identifier):
        raise ValueError(f"Unsafe MySQL identifier: {identifier}")
    return f"`{identifier}`"
