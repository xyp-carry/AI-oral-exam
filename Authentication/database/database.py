"""
database.py
===========
基于 MySQL 的用户数据存储，使用 pymysql 驱动。
启动时自动检查并创建所需的数据库表。
"""

import pymysql
from pymysql.cursors import DictCursor
from datetime import datetime
from typing import Optional
from uuid import uuid4
from Authentication.models import UserInfo

MYSQL_CONFIG = {
    "host": "127.0.0.1",
    "port": 3306,
    "user": "root",
    "password": "123456",
    "database": "ai_oral_exam_user",
    "charset": "utf8mb4",
    "cursorclass": DictCursor,
}

CREATE_USERS_TABLE = """
CREATE TABLE IF NOT EXISTS users (
    uuid CHAR(36) NOT NULL UNIQUE PRIMARY KEY,
    username VARCHAR(32) NOT NULL,
    email VARCHAR(255) NOT NULL UNIQUE,
    hashed_password VARCHAR(255) NOT NULL,
    nickname VARCHAR(64) DEFAULT NULL,
    role VARCHAR(32) NOT NULL DEFAULT 'student',
    created_at DATETIME NOT NULL,
    last_login DATETIME DEFAULT NULL,
    is_active TINYINT(1) NOT NULL DEFAULT 1,
    confirm_flag TINYINT(1) NOT NULL DEFAULT 0,
    login_count INT NOT NULL DEFAULT 0,
    INDEX idx_uuid (uuid)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
"""



class UserDatabase:
    """MySQL 数据库，支持自动建表"""

    def __init__(self):
        self._conn = None
        self._sessions: dict[str, dict] = {}
        self._connect()
        self.init_tables()

    def _connect(self):
        try:
            self._conn = pymysql.connect(**MYSQL_CONFIG, autocommit=True)
            print(f"[DB] 已连接 MySQL: {MYSQL_CONFIG['host']}:{MYSQL_CONFIG['port']}/{MYSQL_CONFIG['database']}")
        except pymysql.err.OperationalError as e:
            if e.args[0] == 1049:
                print(f"[DB] 数据库 '{MYSQL_CONFIG['database']}' 不存在，正在自动创建...")
                config_without_db = {k: v for k, v in MYSQL_CONFIG.items() if k != "database"}
                conn = pymysql.connect(**config_without_db)
                with conn.cursor() as cursor:
                    cursor.execute(
                        f"CREATE DATABASE `{MYSQL_CONFIG['database']}` "
                        f"DEFAULT CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci"
                    )
                conn.close()
                print(f"[DB] 数据库 '{MYSQL_CONFIG['database']}' 创建成功")
                self._conn = pymysql.connect(**MYSQL_CONFIG)
                print(f"[DB] 已连接 MySQL: {MYSQL_CONFIG['host']}:{MYSQL_CONFIG['port']}/{MYSQL_CONFIG['database']}")
            else:
                raise

    def _ensure_connection(self):
        try:
            self._conn.ping(reconnect=True)
        except Exception:
            self._connect()

    def init_tables(self):
        self._ensure_connection()
        with self._conn.cursor() as cursor:
            cursor.execute(CREATE_USERS_TABLE)
            cursor.execute("SELECT COLUMN_NAME FROM INFORMATION_SCHEMA.COLUMNS WHERE TABLE_SCHEMA = %s AND TABLE_NAME = 'users' AND COLUMN_NAME = 'uuid'", (MYSQL_CONFIG["database"],))
            if cursor.fetchone() is None:
                cursor.execute("ALTER TABLE users ADD COLUMN uuid CHAR(36) NOT NULL DEFAULT '' UNIQUE AFTER username")
                cursor.execute("UPDATE users SET uuid = UUID() WHERE uuid = ''")
                cursor.execute("ALTER TABLE users ADD INDEX idx_uuid (uuid)")
                print("[DB] 已为 users 表添加 uuid 字段")
            cursor.execute("SELECT COLUMN_NAME FROM INFORMATION_SCHEMA.COLUMNS WHERE TABLE_SCHEMA = %s AND TABLE_NAME = 'users' AND COLUMN_NAME = 'role'", (MYSQL_CONFIG["database"],))
            if cursor.fetchone() is None:
                cursor.execute("ALTER TABLE users ADD COLUMN role VARCHAR(32) NOT NULL DEFAULT 'student' AFTER nickname")
                print("[DB] 已为 users 表添加 role 字段")
            cursor.execute("SELECT COLUMN_NAME FROM INFORMATION_SCHEMA.COLUMNS WHERE TABLE_SCHEMA = %s AND TABLE_NAME = 'users' AND COLUMN_NAME = 'confirm_flag'", (MYSQL_CONFIG["database"],))
            if cursor.fetchone() is None:
                cursor.execute("ALTER TABLE users ADD COLUMN confirm_flag TINYINT(1) NOT NULL DEFAULT 0 AFTER is_active")
                print("[DB] 已为 users 表添加 confirm_flag 字段")
        self._conn.commit()
        print("[DB] 数据表检查完成，users 表已就绪")

    # ──────────────── 用户管理 ────────────────

    def user_exists(self, username: str) -> bool:
        self._ensure_connection()
        with self._conn.cursor() as cursor:
            cursor.execute("SELECT 1 FROM users WHERE username = %s", (username,))
            return cursor.fetchone() is not None

    def create_user(self, user: UserInfo, hashed_password: str) -> dict:
        self._ensure_connection()
        user_uuid = str(uuid4())
        user_dict = {
            "uuid": user_uuid,
            "username": user.username,
            "email": user.email,
            "hashed_password": hashed_password,
            "nickname": user.nickname or user.username,
            "role": user.role,
            "created_at": datetime.utcnow(),
            "last_login": None,
            "is_active": True,
            "confirm_flag": 0,
            "login_count": 0,
        }
        with self._conn.cursor() as cursor:
            cursor.execute(
                "INSERT INTO users (uuid, username, email, hashed_password, nickname, role, created_at, last_login, is_active, confirm_flag, login_count) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
                (
                    user_dict["uuid"],
                    user_dict["username"],
                    user_dict["email"],
                    user_dict["hashed_password"],
                    user_dict["nickname"],
                    user_dict["role"],
                    user_dict["created_at"],
                    user_dict["last_login"],
                    user_dict["is_active"],
                    user_dict["confirm_flag"],
                    user_dict["login_count"],
                ),
            )
        self._conn.commit()
        user_dict["created_at"] = user_dict["created_at"].isoformat()
        if user_dict["last_login"]:
            user_dict["last_login"] = user_dict["last_login"].isoformat()
        return user_dict

    def get_user(self, username: str) -> Optional[dict]:
        self._ensure_connection()
        with self._conn.cursor() as cursor:
            cursor.execute(
                "SELECT uuid, username, email, hashed_password, nickname, role, created_at, last_login, is_active, confirm_flag, login_count "
                "FROM users WHERE username = %s",
                (username,),
            )
            row = cursor.fetchone()
        if not row:
            return None
        return self._format_user(row)

    def get_user_by_uuid(self, uuid: str) -> Optional[dict]:
        self._ensure_connection()
        with self._conn.cursor() as cursor:
            cursor.execute(
                "SELECT uuid, username, email, hashed_password, nickname, role, created_at, last_login, is_active, confirm_flag, login_count "
                "FROM users WHERE uuid = %s",
                (uuid,),
            )
            row = cursor.fetchone()
        if not row:
            return None
        return self._format_user(row)

    def update_user(self, username: str, **kwargs) -> Optional[dict]:
        self._ensure_connection()
        if not kwargs:
            return self.get_user(username)
        set_clauses = []
        values = []
        for key, value in kwargs.items():
            set_clauses.append(f"{key} = %s")
            values.append(value)
        values.append(username)
        with self._conn.cursor() as cursor:
            cursor.execute(
                f"UPDATE users SET {', '.join(set_clauses)} WHERE username = %s",
                values,
            )
        self._conn.commit()
        return self.get_user(username)

    def get_all_users_info(self) -> list[dict]:
        self._ensure_connection()
        with self._conn.cursor() as cursor:
            cursor.execute(
                "SELECT uuid, username, email, nickname, role, created_at, last_login, is_active, confirm_flag, login_count FROM users"
            )
            rows = cursor.fetchall()
        return [self._format_user_public(row) for row in rows]

    def get_user_count(self) -> int:
        self._ensure_connection()
        with self._conn.cursor() as cursor:
            cursor.execute("SELECT COUNT(*) AS cnt FROM users")
            row = cursor.fetchone()
        return row["cnt"] if row else 0

    def delete_user(self, username: str) -> bool:
        self._ensure_connection()
        with self._conn.cursor() as cursor:
            cursor.execute("DELETE FROM users WHERE username = %s", (username,))
            affected = cursor.rowcount
        self._conn.commit()
        return affected > 0

    # ──────────────── 会话管理（内存存储） ────────────────

    def create_session(self, username: str, token: str, max_age: int) -> dict:
        now = datetime.utcnow().isoformat()
        session_info = {
            "username": username,
            "token": token,
            "created_at": now,
            "max_age": max_age,
            "is_active": True,
        }
        self._sessions[token] = session_info
        return session_info

    def get_session(self, token: str) -> Optional[dict]:
        session = self._sessions.get(token)
        if session and not session["is_active"]:
            return None
        return session

    def delete_session(self, token: str) -> bool:
        if token in self._sessions:
            del self._sessions[token]
            return True
        return False

    def delete_all_user_sessions(self, username: str) -> int:
        to_delete = [t for t, s in self._sessions.items()
                     if s["username"] == username]
        for t in to_delete:
            del self._sessions[t]
        return len(to_delete)

    def get_all_sessions(self) -> list[dict]:
        return list(self._sessions.values())

    # ──────────────── 内部工具 ────────────────

    @staticmethod
    def _format_user(row: dict) -> dict:
        return {
            "uuid": row["uuid"],
            "username": row["username"],
            "email": row["email"],
            "hashed_password": row["hashed_password"],
            "nickname": row["nickname"],
            "role": row.get("role", "student"),
            "created_at": row["created_at"].isoformat() if isinstance(row["created_at"], datetime) else str(row["created_at"]),
            "last_login": row["last_login"].isoformat() if isinstance(row["last_login"], datetime) else row["last_login"],
            "is_active": bool(row["is_active"]),
            "confirm_flag": int(row.get("confirm_flag", 0) or 0),
            "login_count": row["login_count"],
        }

    @staticmethod
    def _format_user_public(row: dict) -> dict:
        return {
            "uuid": row["uuid"],
            "username": row["username"],
            "email": row["email"],
            "nickname": row["nickname"],
            "role": row.get("role", "student"),
            "created_at": row["created_at"].isoformat() if isinstance(row["created_at"], datetime) else str(row["created_at"]),
            "last_login": row["last_login"].isoformat() if isinstance(row["last_login"], datetime) else row["last_login"],
            "is_active": bool(row["is_active"]),
            "confirm_flag": int(row.get("confirm_flag", 0) or 0),
            "login_count": row["login_count"],
        }


db = UserDatabase()
