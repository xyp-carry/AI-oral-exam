"""
models.py
=========
Pydantic 数据模型定义
"""

from pydantic import BaseModel, EmailStr, field_validator
import re


# ──────────────── 请求模型 ────────────────

class RegisterRequest(BaseModel):
    """注册请求"""
    username: str
    password: str
    email: EmailStr
    nickname: str | None = None

    @field_validator("username")
    @classmethod
    def validate_username(cls, v: str) -> str:
        if len(v) < 3 or len(v) > 32:
            raise ValueError("用户名长度必须在 3-32 个字符之间")
        if not re.match(r"^[a-zA-Z0-9_]+$", v):
            raise ValueError("用户名只能包含字母、数字和下划线")
        return v

    @field_validator("password")
    @classmethod
    def validate_password(cls, v: str) -> str:
        if len(v) < 6:
            raise ValueError("密码长度不能少于 6 个字符")
        if len(v) > 128:
            raise ValueError("密码长度不能超过 128 个字符")
        return v


class LoginRequest(BaseModel):
    """登录请求"""
    username: str
    password: str
    remember_me: bool = False  # 是否延长 Cookie 有效期


class UpdateProfileRequest(BaseModel):
    """更新个人资料请求"""
    email: EmailStr | None = None
    nickname: str | None = None


class ChangePasswordRequest(BaseModel):
    """修改密码请求"""
    old_password: str
    new_password: str

    @field_validator("new_password")
    @classmethod
    def validate_new_password(cls, v: str) -> str:
        if len(v) < 6:
            raise ValueError("新密码长度不能少于 6 个字符")
        return v


class ProxyRequest(BaseModel):
    """代理网关请求"""
    target_url: str
    method: str = "GET"
    headers: dict | None = None
    body: dict | str | None = None
    params: dict | None = None

    @field_validator("method")
    @classmethod
    def validate_method(cls, v: str) -> str:
        allowed = {"GET", "POST", "PUT", "DELETE", "PATCH", "HEAD", "OPTIONS"}
        v_upper = v.upper()
        if v_upper not in allowed:
            raise ValueError(f"不支持的 HTTP 方法: {v}，允许的方法: {', '.join(sorted(allowed))}")
        return v_upper


# ──────────────── 响应模型 ────────────────

class UserInfo(BaseModel):
    """用户信息（不含密码）"""
    uuid: str
    username: str
    email: str
    nickname: str
    created_at: str
    last_login: str | None = None
    is_active: bool = True
    login_count: int = 0


class TokenResponse(BaseModel):
    """会话令牌响应"""
    access_token: str
    token_type: str = "bearer"


class MessageResponse(BaseModel):
    """通用消息响应"""
    message: str
    success: bool = True


class SessionInfo(BaseModel):
    """会话信息"""
    username: str
    created_at: str
    is_active: bool
