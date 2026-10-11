"""user_model_library 表的 SQLAlchemy 声明式模型。

建表与迁移仍由 LLM/schema.py 的 DDL 负责；JSON 列声明为 Text，
读出为字符串，与原 pymysql 行为一致。
"""

from sqlalchemy import CHAR, Column, DateTime, String, Text
from sqlalchemy.orm import DeclarativeBase

from .config import MODEL_TABLE_NAME


class Base(DeclarativeBase):
    pass


class UserModelLibrary(Base):
    """用户配置的可用 LLM 模型，供评分 Agent 配置引用。"""

    __tablename__ = MODEL_TABLE_NAME

    model_id = Column(CHAR(36), primary_key=True)
    owner_user_id = Column(String(128), nullable=False)
    model_name = Column(String(128), nullable=False)
    model_type = Column(String(20), nullable=False)
    model_api_key = Column(Text, nullable=False)
    provider = Column(String(64))
    provider_model_key = Column(String(128))
    base_url = Column(Text)
    display_name = Column(String(128))
    params_json = Column(Text)
    last_test_result_json = Column(Text)
    status = Column(String(20), nullable=False)
    created_at = Column(DateTime, nullable=False)
    updated_at = Column(DateTime, nullable=False)
