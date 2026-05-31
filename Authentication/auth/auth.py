"""
auth.py
========
密码哈希、Cookie 签名生成、会话管理等安全相关功能。
"""

import secrets
from datetime import datetime, timedelta
from passlib.context import CryptContext
from itsdangerous import URLSafeTimedSerializer, BadSignature, SignatureExpired
from fastapi import HTTPException, status, Request, Response
from fastapi.responses import JSONResponse
from Authentication.database import db


# ──────────────── 密码哈希 ────────────────

# 使用 bcrypt 算法进行密码哈希
# pwd_context = CryptContext(
#     schemes=["bcrypt"],
#     deprecated="auto",
#     bcrypt__rounds=12,       # cost factor，值越大越安全但越慢
# )
pwd_context = CryptContext(schemes=["argon2"], deprecated="auto")

def hash_password(password: str) -> str:
    """对明文密码进行 bcrypt 哈希"""
    return pwd_context.hash(password)

def verify_password(plain_password: str, hashed_password: str) -> bool:
    """验证明文密码与哈希是否匹配"""
    return pwd_context.verify(plain_password, hashed_password)


# ──────────────── Cookie 签名密钥 ────────────────

# 生产环境中务必从环境变量读取，切勿硬编码！
SECRET_KEY = secrets.token_urlsafe(64)
COOKIE_MAX_AGE_SHORT = 3600          # 1 小时（默认）
COOKIE_MAX_AGE_LONG = 30 * 24 * 3600  # 30 天（记住我）

# 用于签名 Cookie 的序列化器
cookie_serializer = URLSafeTimedSerializer(SECRET_KEY, salt="fastapi-auth-cookie")

# 用于签名邮箱验证令牌
email_serializer = URLSafeTimedSerializer(SECRET_KEY, salt="fastapi-email-verify")


# ──────────────── 会话 Token ────────────────

def generate_session_token() -> str:
    """生成高强度的随机会话 Token"""
    return secrets.token_urlsafe(48)  # 64 字符的 URL 安全随机字符串


# ──────────────── Cookie 管理 ────────────────

COOKIE_NAME = "session_token"
COOKIE_PATH = "/"
COOKIE_SECURE = True
COOKIE_SAMESITE = "none"

def set_auth_cookie(response: Response, token: str, remember_me: bool = False) -> None:
    """
    将签名后的会话 Token 写入 Cookie。
    Cookie 值为 itsdangerous 签名后的 token，防止客户端篡改。
    """
    max_age = COOKIE_MAX_AGE_LONG if remember_me else COOKIE_MAX_AGE_SHORT
    signed_token = cookie_serializer.dumps(token)
    response.set_cookie(
        key=COOKIE_NAME,
        value=signed_token,
        max_age=max_age,
        httponly=True,       # 禁止 JS 访问，防止 XSS
        secure=COOKIE_SECURE,  # 生产环境设为 True（HTTPS）
        samesite=COOKIE_SAMESITE,  # 防止 CSRF
        path=COOKIE_PATH,
    )

def clear_auth_cookie(response: Response) -> None:
    """清除认证 Cookie"""
    response.delete_cookie(
        key=COOKIE_NAME,
        path=COOKIE_PATH,
        secure=COOKIE_SECURE,
        httponly=True,
        samesite=COOKIE_SAMESITE,
    )

def extract_token_from_cookie(request: Request) -> str | None:
    """从请求 Cookie 中提取并验证会话 Token"""
    signed_token = request.cookies.get(COOKIE_NAME)
    if not signed_token:
        return None
    try:
        token = cookie_serializer.loads(
            signed_token,
            max_age=COOKIE_MAX_AGE_LONG,
        )
        return token
    except (BadSignature, SignatureExpired):
        return None


# ──────────────── 登录状态检查（依赖注入） ────────────────

async def get_current_user(request: Request) -> dict:
    """
    FastAPI 依赖项：检查请求中的 Cookie，返回当前登录用户信息。
    如果未登录或会话无效，抛出 401 异常。
    """
    token = extract_token_from_cookie(request)
    if not token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="未登录或会话已过期，请先登录",
        )
    session = db.get_session(token)
    if not session:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="会话无效或已被注销，请重新登录",
        )
    user = db.get_user(session["username"])
    if not user or not user["is_active"]:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="用户不存在或已被禁用",
        )
    
    if int(user.get("confirm_flag", 0) or 0) != 1:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={"code": "NEED_ACTIVATION", "message": "账号需要激活后才能登录"},
        )
    return user


async def get_optional_user(request: Request) -> dict | None:
    """
    FastAPI 依赖项：可选登录检查。未登录返回 None，不抛异常。
    """
    token = extract_token_from_cookie(request)
    if not token:
        return None
    session = db.get_session(token)
    if not session:
        return None
    user = db.get_user(session["username"])
    if not user or not user["is_active"]:
        return None
    if int(user.get("confirm_flag", 0) or 0) != 1:
        return None
    return user
