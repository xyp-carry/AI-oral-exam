from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict
from functools import lru_cache
from pathlib import Path
import sys

_BASE_DIR = Path(__file__).resolve().parent

class Settings(BaseSettings):
    """所有配置项集中声明，自动类型转换、自动校验"""

    model_config = SettingsConfigDict(
        env_file=_BASE_DIR / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    model_url: str = Field(..., description="大模型URL")
    model_name: str = Field(..., description="大模型名称")
    model_api_key: str = Field(..., description="大模型API密钥")

    mineru_api_key: str = Field(..., description="Mineru API密钥")

    deepseek_url: str = Field(..., description="Deepseek URL")
    deepseek_model: str = Field(..., description="Deepseek模型名称")
    deepseek_api_key: str = Field(..., description="Deepseek API密钥")

    kimi_url: str = Field(..., description="Kimi URL")
    kimi_model: str = Field(..., description="Kimi模型名称")
    kimi_api_key: str = Field(..., description="Kimi API密钥")



@lru_cache
def get_settings() -> Settings:
    return Settings()
