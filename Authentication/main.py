"""
main.py
========
FastAPI 主应用：用户注册、登录、状态管理 API。
端口：11024
"""

from fastapi import FastAPI, Depends, HTTPException, status, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from pydantic import EmailStr
from datetime import datetime

from Authentication.models import (
    RegisterRequest, LoginRequest, UpdateProfileRequest,
    ChangePasswordRequest, MessageResponse, TokenResponse,
    UserInfo, SessionInfo, ProxyRequest,
)
from Authentication.auth import (
    hash_password, verify_password,
    generate_session_token, set_auth_cookie, clear_auth_cookie,
    extract_token_from_cookie, get_current_user, get_optional_user,
)
from Authentication.database import db


# ═══════════════════════════════════════════
#  FastAPI 应用实例
# ═══════════════════════════════════════════
def auth(app, args):
    # app = FastAPI(
    #     title="用户状态管理系统",
    #     description="基于 FastAPI 的完整用户认证与状态管理系统，支持注册、登录、Cookie 分发和会话管理。",
    #     version="1.0.0",
    # )
    origins = [
        "http://localhost:5173" ,  # 您的前端地址
        "http://127.0.0.1:5173" ,
        # 如果有其他前端地址也添加在这里
    ]
    # CORS 中间件（允许前端跨域访问）
    app.add_middleware(
        CORSMiddleware,
        allow_origins=origins,           # 生产环境请限制具体域名
        allow_credentials=True,        # 允许携带 Cookie
        allow_methods=["*"],
        allow_headers=["*"],
    )


    # ═══════════════════════════════════════════
    #  启动事件
    # ═══════════════════════════════════════════

    @app.on_event("startup")
    async def startup_event():
        db.init_tables()
        print("\n" + "=" * 50)
        print("  🚀 用户状态管理系统已启动")
        print("  📍 访问地址: http://127.0.0.1:7860")
        print("  📖 API 文档: http://127.0.0.1:7860/docs")
        print("  🗄️ 数据库: MySQL")
        print("=" * 50 + "\n")


    # ═══════════════════════════════════════════
    #  首页 & 健康检查
    # ═══════════════════════════════════════════

    @app.get("/", tags=["系统"])
    async def root():
        """系统根路径"""
        return {
            "system": "用户状态管理系统",
            "version": "1.0.0",
            "docs": "/docs",
            "endpoints": {
                "注册": "POST /register",
                "登录": "POST /login",
                "登出": "POST /logout",
                "查看当前用户": "GET /me",
                "查看所有用户": "GET /users",
                "更新资料": "PUT /profile",
                "修改密码": "PUT /password",
                "查看会话": "GET /sessions",
                "注销所有设备": "DELETE /sessions",
                "删除账号": "DELETE /account",
            }
        }


    @app.get("/health", tags=["系统"])
    async def health_check():
        """健康检查"""
        return {"status": "ok", "timestamp": datetime.utcnow().isoformat()}


    # ═══════════════════════════════════════════
    #  📝 用户注册
    # ═══════════════════════════════════════════

    @app.post(
        "/register",
        response_model=MessageResponse,
        status_code=status.HTTP_201_CREATED,
        tags=["认证"],
        summary="用户注册",
        description="注册新用户，密码使用 bcrypt 加密存储。",
        responses={
            400: {"description": "用户名或邮箱已存在 / 参数校验失败"},
            201: {"description": "注册成功"},
        },
    )
    async def register(req: RegisterRequest):
        # 1️⃣ 检查用户名是否已存在
        if db.user_exists(req.username):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"用户名 '{req.username}' 已被注册",
            )

        # 2️⃣ 检查邮箱是否已被使用
        for user in db.get_all_users_info():
            if user["email"] == req.email:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=f"邮箱 '{req.email}' 已被使用",
                )

        # 3️⃣ 对密码进行 bcrypt 哈希加密
        hashed_pw = hash_password(req.password)

        # 4️⃣ 存储用户
        user_info = db.create_user(
            user=req,
            hashed_password=hashed_pw,
        )

        return MessageResponse(
            message=f"注册成功，账号需要激活后才能登录：{req.nickname or req.username}",
            success=True,
            code="NEED_ACTIVATION",
        )




    # ═══════════════════════════════════════════
    #  🔐 用户登录
    # ═══════════════════════════════════════════

    @app.post(
        "/login",
        response_model=MessageResponse,
        tags=["认证"],
        summary="用户登录",
        description="验证用户名和密码，成功后通过 Cookie 分发会话。",
        responses={
            401: {"description": "用户名或密码错误"},
            200: {"description": "登录成功"},
        },
    )
    async def login(req: LoginRequest, response: Response):
        # 1️⃣ 查找用户
        user = db.get_user(req.username)
        if not user:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="用户名或密码错误",
            )

        # 2️⃣ 验证密码（bcrypt 自动处理盐值比对）
        if not verify_password(req.password, user["hashed_password"]):
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="用户名或密码错误",
            )

        # 3️⃣ 检查账号是否被禁用
        if not user["is_active"]:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="账号已被禁用，请联系管理员",
            )

        # 4️⃣ 生成会话 Token 并存储
        if int(user.get("confirm_flag", 0) or 0) != 1:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail={"code": "NEED_ACTIVATION", "message": "账号需要激活后才能登录"},
            )

        session_token = generate_session_token()
        max_age = 30 * 24 * 3600 if req.remember_me else 3600
        db.create_session(
            username=req.username,
            token=session_token,
            max_age=max_age,
        )

        # 5️⃣ 更新用户登录信息
        db.update_user(
            username=req.username,
            last_login=datetime.utcnow().isoformat(),
            login_count=user.get("login_count", 0) + 1,
        )

        # 6️⃣ 设置 Cookie
        set_auth_cookie(response, session_token, remember_me=req.remember_me)

        remember_msg = "（已记住登录状态 30 天）" if req.remember_me else "（会话有效期 1 小时）"
        return MessageResponse(
            message=f"登录成功！欢迎回来，{user['nickname']} 👋 {remember_msg}",
            success=True,
        )


    # ═══════════════════════════════════════════
    #  🚪 用户登出
    # ═══════════════════════════════════════════

    @app.post(
        "/logout",
        response_model=MessageResponse,
        tags=["认证"],
        summary="用户登出",
        description="注销当前设备的会话，清除 Cookie。",
    )
    async def logout(
        request: Request,
        response: Response,
    ):
        token = extract_token_from_cookie(request)
        if token:
            db.delete_session(token)

        clear_auth_cookie(response)
        return MessageResponse(message="已成功登出 👋", success=True)


    # ═══════════════════════════════════════════
    #  👤 查看当前用户信息
    # ═══════════════════════════════════════════

    @app.get(
        "/me",
        response_model=UserInfo,
        tags=["用户"],
        summary="获取当前登录用户信息",
        description="需要登录。返回当前用户的详细信息。",
        responses={401: {"description": "未登录"}},
    )
    async def get_me(current_user: dict = Depends(get_current_user)):
        return UserInfo(
            uuid=current_user["uuid"],
            username=current_user["username"],
            email=current_user["email"],
            nickname=current_user["nickname"],
            created_at=current_user["created_at"],
            last_login=current_user.get("last_login"),
            is_active=current_user["is_active"],
            login_count=current_user.get("login_count", 0),
            role=current_user["role"],
        )


    # ═══════════════════════════════════════════
    #  👥 查看所有用户（管理员功能）
    # ═══════════════════════════════════════════

    @app.get(
        "/users",
        tags=["用户"],
        summary="获取所有用户列表",
        description="返回所有用户的公开信息（不含密码）。需要登录。",
        responses={401: {"description": "未登录"}},
    )
    async def list_users(current_user: dict = Depends(get_current_user)):
        return {
            "count": db.get_user_count(),
            "users": db.get_all_users_info(),
        }


    # ═══════════════════════════════════════════
    #  ✏️ 更新个人资料
    # ═══════════════════════════════════════════

    @app.put(
        "/profile",
        response_model=MessageResponse,
        tags=["用户"],
        summary="更新个人资料",
        description="修改当前用户的邮箱或昵称。",
        responses={
            401: {"description": "未登录"},
            400: {"description": "邮箱已被其他用户使用"},
        },
    )
    async def update_profile(
        req: UpdateProfileRequest,
        current_user: dict = Depends(get_current_user),
    ):
        # 检查邮箱是否已被他人使用
        if req.email:
            for user in db.get_all_users_info():
                if user["email"] == req.email and user["username"] != current_user["username"]:
                    raise HTTPException(
                        status_code=status.HTTP_400_BAD_REQUEST,
                        detail=f"邮箱 '{req.email}' 已被其他用户使用",
                    )

        update_data = {}
        if req.email:
            update_data["email"] = req.email
        if req.nickname:
            update_data["nickname"] = req.nickname

        db.update_user(current_user["username"], **update_data)
        return MessageResponse(message="个人资料已更新 ✅", success=True)


    # ═══════════════════════════════════════════
    #  🔑 修改密码
    # ═══════════════════════════════════════════

    @app.put(
        "/password",
        response_model=MessageResponse,
        tags=["用户"],
        summary="修改密码",
        description="验证旧密码后修改为新密码（新密码将使用 bcrypt 加密存储）。",
        responses={
            401: {"description": "未登录"},
            400: {"description": "旧密码错误"},
        },
    )
    async def change_password(
        req: ChangePasswordRequest,
        request: Request,
        response: Response,
        current_user: dict = Depends(get_current_user),
    ):
        # 验证旧密码
        if not verify_password(req.old_password, current_user["hashed_password"]):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="旧密码错误",
            )

        # 哈希新密码并更新
        new_hashed = hash_password(req.new_password)
        db.update_user(current_user["username"], hashed_password=new_hashed)

        # 修改密码后注销所有设备，强制重新登录
        db.delete_all_user_sessions(current_user["username"])
        clear_auth_cookie(response)

        return MessageResponse(
            message="密码修改成功！所有设备已注销，请重新登录 🔐",
            success=True,
        )


    # ═══════════════════════════════════════════
    #  📋 会话管理
    # ═══════════════════════════════════════════

    @app.get(
        "/sessions",
        tags=["会话"],
        summary="查看当前用户的所有活跃会话",
        description="查看你当前在多少个设备上登录。",
        responses={401: {"description": "未登录"}},
    )
    async def list_sessions(current_user: dict = Depends(get_current_user)):
        user_sessions = [
            s for s in db.get_all_sessions()
            if s["username"] == current_user["username"]
        ]
        return {
            "username": current_user["username"],
            "active_sessions": len(user_sessions),
            "sessions": [
                SessionInfo(
                    username=s["username"],
                    created_at=s["created_at"],
                    is_active=s["is_active"],
                ) for s in user_sessions
            ],
        }


    @app.delete(
        "/sessions",
        response_model=MessageResponse,
        tags=["会话"],
        summary="注销所有其他设备",
        description="除当前设备外，注销该用户的所有其他会话。",
    )
    async def logout_all_devices(
        request: Request,
        response: Response,
        current_user: dict = Depends(get_current_user),
    ):
        # 获取当前 token
        current_token = request.cookies.get("session_token")

        # 先删除所有会话
        count = db.delete_all_user_sessions(current_user["username"])

        # 重新创建当前会话
        new_token = generate_session_token()
        db.create_session(
            username=current_user["username"],
            token=new_token,
            max_age=3600,
        )
        set_auth_cookie(response, new_token, remember_me=False)

        return MessageResponse(
            message=f"已注销 {count} 个设备，当前设备保持登录 📱",
            success=True,
        )


    # ═══════════════════════════════════════════
    #  🗑️ 删除账号
    # ═══════════════════════════════════════════

    @app.delete(
        "/account",
        response_model=MessageResponse,
        tags=["用户"],
        summary="删除账号",
        description="永久删除当前用户的账号及所有相关数据。",
        responses={401: {"description": "未登录"}},
    )
    async def delete_account(
        request: Request,
        response: Response,
        current_user: dict = Depends(get_current_user),
    ):
        username = current_user["username"]

        db.delete_all_user_sessions(username)

        db.delete_user(username)

        clear_auth_cookie(response)
        return MessageResponse(
            message=f"账号 '{username}' 已永久删除 💔",
            success=True,
        )


    # ═══════════════════════════════════════════
    #  📊 系统统计
    # ═══════════════════════════════════════════

    @app.get("/stats", tags=["系统"])
    async def system_stats(current_user: dict = Depends(get_current_user)):
        """系统统计信息"""
        users = db.get_all_users_info()
        sessions = db.get_all_sessions()
        return {
            "total_users": len(users),
            "active_sessions": len(sessions),
            "users": users,
        }


    # ═══════════════════════════════════════════
    #  🌐 认证网关代理
    # ═══════════════════════════════════════════

    import httpx

    GATEWAY_TIMEOUT = 30.0


    @app.post(
        "/gateway",
        tags=["网关"],
        summary="认证网关代理",
        description="验证用户登录状态后，将请求转发到目标服务并返回结果。"
                    "支持 GET/POST/PUT/DELETE/PATCH 等方法。",
        responses={
            401: {"description": "未登录"},
            502: {"description": "目标服务不可达"},
        },
    )
    async def gateway_proxy(
        req: ProxyRequest,
        current_user: dict = Depends(get_current_user),
    ):
        forward_headers = req.headers or {}
        forward_headers["X-Forwarded-User"] = current_user["username"]
        forward_headers["X-Forwarded-UUID"] = current_user["uuid"]

        request_body = req.body if req.method in ("POST", "PUT", "PATCH") else None

        async with httpx.AsyncClient(timeout=GATEWAY_TIMEOUT) as client:
            try:
                resp = await client.request(
                    method=req.method,
                    url=req.target_url,
                    headers=forward_headers,
                    params=req.params,
                    json=request_body if isinstance(request_body, dict) else None,
                    content=request_body if isinstance(request_body, str) else None,
                )
            except httpx.ConnectError:
                raise HTTPException(
                    status_code=status.HTTP_502_BAD_GATEWAY,
                    detail=f"无法连接目标服务: {req.target_url}",
                )
            except httpx.TimeoutException:
                raise HTTPException(
                    status_code=status.HTTP_504_GATEWAY_TIMEOUT,
                    detail=f"目标服务响应超时 ({GATEWAY_TIMEOUT}s): {req.target_url}",
                )

        content_type = resp.headers.get("content-type", "")
        if "application/json" in content_type:
            return {
                "status_code": resp.status_code,
                "data": resp.json(),
            }
        else:
            return {
                "status_code": resp.status_code,
                "data": resp.text,
            }

    return app
# ═══════════════════════════════════════════
#  启动入口
# ═══════════════════════════════════════════

# if __name__ == "__main__":
#     import uvicorn
#     uvicorn.run(app, host="0.0.0.0", port=11024, ssl_keyfile="./key.pem",      # 私钥路径
#         ssl_certfile="./cert.pem")     # 证书路径)
