"""API 层共享依赖。

`get_current_user` 是全部受保护端点的唯一身份来源：
从 `Authorization: Bearer <token>` 解析并验证 JWT，同时把 user_id
写入 contextvars（供日志与检索分域等横切层读取）。

端点**不得**再接受客户端自报的 user_id 参数——那正是此前 IDOR 的根源。
"""

from __future__ import annotations

from typing import Annotated

from app.shared.core.identity import set_current_user_id
from app.user.application.auth_service import (
    AuthenticatedUser,
    AuthError,
    verify_access_token,
)
from fastapi import Depends, HTTPException
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

# auto_error=False：缺头时自己给出中文 401 文案，而不是框架默认 403
_bearer_scheme = HTTPBearer(auto_error=False)


def get_current_user(
    credentials: Annotated[
        HTTPAuthorizationCredentials | None, Depends(_bearer_scheme)
    ],
) -> AuthenticatedUser:
    """验证 Bearer 令牌并返回当前用户；失败统一 401。"""
    if credentials is None or not credentials.credentials:
        raise HTTPException(
            status_code=401,
            detail="缺少访问令牌，请先登录",
            headers={"WWW-Authenticate": "Bearer"},
        )
    try:
        user = verify_access_token(credentials.credentials)
    except AuthError as exc:
        raise HTTPException(
            status_code=401,
            detail=exc.message,
            headers={"WWW-Authenticate": "Bearer"},
        ) from exc

    set_current_user_id(user.id)
    return user


CurrentUser = Annotated[AuthenticatedUser, Depends(get_current_user)]

__all__ = ["CurrentUser", "get_current_user"]
