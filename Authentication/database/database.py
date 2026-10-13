"""
database.py
===========
基于 MySQL 的用户数据存储，通过 SQLAlchemy（pymysql 驱动）访问。
启动时自动检查并创建所需的数据库表。
"""

from datetime import datetime
import re
from typing import Callable, Optional
from urllib.parse import quote_plus
from uuid import uuid4

from sqlalchemy import (
    CHAR,
    Column,
    DateTime,
    Integer,
    String,
    create_engine,
    delete,
    func,
    literal,
    select,
    text,
    update,
)
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import DeclarativeBase

from Authentication.models import UserInfo
from mysql_config import get_mysql_config

MYSQL_CONFIG = get_mysql_config(
    database_env="MYSQL_AUTH_DATABASE", default_database="ai_oral_exam_user"
)

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

_USER_COLUMNS = (
    "uuid",
    "username",
    "email",
    "hashed_password",
    "nickname",
    "role",
    "created_at",
    "last_login",
    "is_active",
    "confirm_flag",
    "login_count",
)


def _build_url(database: Optional[str] = None) -> str:
    config = MYSQL_CONFIG
    credentials = quote_plus(str(config["user"]))
    password = quote_plus(str(config["password"]))
    url = f"mysql+pymysql://{credentials}:{password}@{config['host']}:{config['port']}"
    if database:
        url += f"/{database}"
    url += f"?charset={config.get('charset') or 'utf8mb4'}"
    return url


class Base(DeclarativeBase):
    pass


class UserModel(Base):
    """users 表：平台账号资料与登录状态。"""

    __tablename__ = "users"

    uuid = Column(CHAR(36), primary_key=True)
    username = Column(String(32), nullable=False)
    email = Column(String(255), nullable=False)
    hashed_password = Column(String(255), nullable=False)
    nickname = Column(String(64))
    role = Column(String(32), nullable=False, default="student")
    created_at = Column(DateTime, nullable=False)
    last_login = Column(DateTime)
    is_active = Column(Integer, nullable=False, default=1)
    confirm_flag = Column(Integer, nullable=False, default=0)
    login_count = Column(Integer, nullable=False, default=0)


class UserDatabase:
    """MySQL 数据库（SQLAlchemy 引擎），支持自动建库建表"""

    def __init__(self):
        self._engine = create_engine(
            _build_url(MYSQL_CONFIG["database"]),
            pool_pre_ping=True,
            pool_recycle=3600,
        )
        self._sessions: dict[str, dict] = {}
        self._ensure_database()
        self.init_tables()

    def _ensure_database(self):
        try:
            with self._engine.connect() as connection:
                connection.execute(text("SELECT 1"))
            print(f"[DB] 已连接 MySQL: {MYSQL_CONFIG['host']}:{MYSQL_CONFIG['port']}/{MYSQL_CONFIG['database']}")
        except OperationalError as exc:
            orig_code = getattr(getattr(exc, "orig", None), "args", (None,))[0]
            if orig_code == 1049:
                print(f"[DB] 数据库 '{MYSQL_CONFIG['database']}' 不存在，正在自动创建...")
                bootstrap = create_engine(_build_url(None))
                try:
                    with bootstrap.begin() as connection:
                        connection.execute(
                            text(
                                f"CREATE DATABASE `{MYSQL_CONFIG['database']}` "
                                "DEFAULT CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci"
                            )
                        )
                finally:
                    bootstrap.dispose()
                print(f"[DB] 数据库 '{MYSQL_CONFIG['database']}' 创建成功")
                with self._engine.connect() as connection:
                    connection.execute(text("SELECT 1"))
                print(f"[DB] 已连接 MySQL: {MYSQL_CONFIG['host']}:{MYSQL_CONFIG['port']}/{MYSQL_CONFIG['database']}")
            else:
                raise

    def init_tables(self):
        with self._engine.begin() as connection:
            connection.execute(text(CREATE_USERS_TABLE))
            row = connection.execute(
                text(
                    "SELECT COLUMN_NAME FROM INFORMATION_SCHEMA.COLUMNS "
                    "WHERE TABLE_SCHEMA = :schema AND TABLE_NAME = 'users' AND COLUMN_NAME = 'uuid'"
                ),
                {"schema": MYSQL_CONFIG["database"]},
            ).fetchone()
            if row is None:
                connection.execute(
                    text("ALTER TABLE users ADD COLUMN uuid CHAR(36) NOT NULL DEFAULT '' UNIQUE AFTER username")
                )
                connection.execute(text("UPDATE users SET uuid = UUID() WHERE uuid = ''"))
                connection.execute(text("ALTER TABLE users ADD INDEX idx_uuid (uuid)"))
                print("[DB] 已为 users 表添加 uuid 字段")
            row = connection.execute(
                text(
                    "SELECT COLUMN_NAME FROM INFORMATION_SCHEMA.COLUMNS "
                    "WHERE TABLE_SCHEMA = :schema AND TABLE_NAME = 'users' AND COLUMN_NAME = 'role'"
                ),
                {"schema": MYSQL_CONFIG["database"]},
            ).fetchone()
            if row is None:
                connection.execute(
                    text("ALTER TABLE users ADD COLUMN role VARCHAR(32) NOT NULL DEFAULT 'student' AFTER nickname")
                )
                print("[DB] 已为 users 表添加 role 字段")
            row = connection.execute(
                text(
                    "SELECT COLUMN_NAME FROM INFORMATION_SCHEMA.COLUMNS "
                    "WHERE TABLE_SCHEMA = :schema AND TABLE_NAME = 'users' AND COLUMN_NAME = 'confirm_flag'"
                ),
                {"schema": MYSQL_CONFIG["database"]},
            ).fetchone()
            if row is None:
                connection.execute(
                    text("ALTER TABLE users ADD COLUMN confirm_flag TINYINT(1) NOT NULL DEFAULT 0 AFTER is_active")
                )
                print("[DB] 已为 users 表添加 confirm_flag 字段")
        print("[DB] 数据表检查完成，users 表已就绪")

    # ──────────────── 用户管理 ────────────────

    def user_exists(self, username: str) -> bool:
        with self._engine.begin() as connection:
            row = connection.execute(
                select(literal(1)).where(UserModel.username == username)
            ).fetchone()
            return row is not None

    def ensure_admin_user(
        self, username: str, password: Optional[str], hash_password: Callable[[str], str]
    ) -> bool:
        """Create the initial administrator without modifying existing accounts."""
        with self._engine.begin() as connection:
            existing_admin = connection.execute(
                select(literal(1)).where(
                    UserModel.role.in_(("admin", "administrator", "super_admin"))
                ).limit(1)
            ).fetchone()
            if existing_admin is not None:
                return False

            if not re.fullmatch(r"[A-Za-z0-9_]{3,32}", username):
                raise ValueError("ADMIN_USERNAME 必须是 3-32 位字母、数字或下划线")
            if password is None or not 12 <= len(password) <= 128:
                raise ValueError("首次创建管理员前，须在 .env 设置 12-128 位的 ADMIN_PASSWORD")

            email = f"{username}@local.invalid"
            if connection.execute(
                select(literal(1)).where(UserModel.username == username).limit(1)
            ).fetchone() is not None:
                raise RuntimeError(f"管理员用户名 '{username}' 已被其他账户占用")
            if connection.execute(
                select(literal(1)).where(UserModel.email == email).limit(1)
            ).fetchone() is not None:
                raise RuntimeError(f"管理员邮箱 '{email}' 已被其他账户占用")

            connection.execute(
                UserModel.__table__.insert().values(
                    uuid=str(uuid4()),
                    username=username,
                    email=email,
                    hashed_password=hash_password(password),
                    nickname=username,
                    role="admin",
                    created_at=datetime.utcnow(),
                    last_login=None,
                    is_active=1,
                    confirm_flag=1,
                    login_count=0,
                )
            )
        return True

    def create_user(self, user: UserInfo, hashed_password: str, *, confirmed: bool = False) -> dict:
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
            "confirm_flag": 1 if confirmed else 0,
            "login_count": 0,
        }
        with self._engine.begin() as connection:
            connection.execute(
                UserModel.__table__.insert().values(
                    uuid=user_dict["uuid"],
                    username=user_dict["username"],
                    email=user_dict["email"],
                    hashed_password=user_dict["hashed_password"],
                    nickname=user_dict["nickname"],
                    role=user_dict["role"],
                    created_at=user_dict["created_at"],
                    last_login=user_dict["last_login"],
                    is_active=1 if user_dict["is_active"] else 0,
                    confirm_flag=user_dict["confirm_flag"],
                    login_count=user_dict["login_count"],
                )
            )
        user_dict["created_at"] = user_dict["created_at"].isoformat()
        if user_dict["last_login"]:
            user_dict["last_login"] = user_dict["last_login"].isoformat()
        return user_dict

    def get_user(self, username: str) -> Optional[dict]:
        with self._engine.begin() as connection:
            row = connection.execute(
                select(*[getattr(UserModel, name) for name in _USER_COLUMNS]).where(
                    UserModel.username == username
                )
            ).fetchone()
        if not row:
            return None
        return self._format_user(row._mapping)

    def get_user_by_uuid(self, uuid: str) -> Optional[dict]:
        with self._engine.begin() as connection:
            row = connection.execute(
                select(*[getattr(UserModel, name) for name in _USER_COLUMNS]).where(
                    UserModel.uuid == uuid
                )
            ).fetchone()
        if not row:
            return None
        return self._format_user(row._mapping)

    def update_user(self, username: str, **kwargs) -> Optional[dict]:
        if not kwargs:
            return self.get_user(username)
        with self._engine.begin() as connection:
            connection.execute(
                update(UserModel)
                .where(UserModel.username == username)
                .values(**kwargs)
            )
        return self.get_user(username)

    def get_all_users_info(self) -> list[dict]:
        public_columns = tuple(
            name for name in _USER_COLUMNS if name != "hashed_password"
        )
        with self._engine.begin() as connection:
            rows = connection.execute(
                select(*[getattr(UserModel, name) for name in public_columns])
            ).fetchall()
        return [self._format_user_public(row._mapping) for row in rows]

    def get_user_count(self) -> int:
        with self._engine.begin() as connection:
            row = connection.execute(select(func.count().label("cnt"))).fetchone()
        return row[0] if row else 0

    def delete_user(self, username: str) -> bool:
        with self._engine.begin() as connection:
            result = connection.execute(
                delete(UserModel).where(UserModel.username == username)
            )
            affected = result.rowcount
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
