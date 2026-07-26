"""认证接口：注册 / 登录 / 当前用户。

唯一开放（无需令牌）的业务端点组。其余 API 一律经
`app.api.deps.get_current_user` 从令牌推导身份。
"""

from __future__ import annotations

from app.api.common import run_api_action
from app.api.deps import CurrentUser
from app.shared.core.logger import get_logger
from app.user.application.auth_service import (
    AuthError,
    RegistrationError,
    auth_service,
    issue_access_token,
)
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from typing_extensions import TypedDict

logger = get_logger(__name__)
router = APIRouter(tags=["auth"])

_TOKEN_TYPE = "bearer"


class CredentialsRequest(BaseModel):
    """注册 / 登录共用的凭据结构。"""

    username: str
    password: str


class TokenResponse(TypedDict):
    """登录 / 注册成功响应。"""

    access_token: str
    token_type: str
    user_id: int
    username: str


class ProfileResponse(TypedDict):
    """当前用户信息。"""

    user_id: int
    username: str


def _token_response(user_id: int, username: str) -> TokenResponse:
    return {
        "access_token": issue_access_token(user_id, username),
        "token_type": _TOKEN_TYPE,
        "user_id": user_id,
        "username": username,
    }


@router.post("/auth/register")
async def register(request: CredentialsRequest) -> TokenResponse:
    """注册并直接签发令牌（注册即登录）。"""

    async def operation() -> TokenResponse:
        try:
            user = await auth_service.register(request.username, request.password)
        except RegistrationError as exc:
            raise HTTPException(status_code=400, detail=exc.message) from exc
        return _token_response(user.id, user.username)

    return await run_api_action(
        "auth_register",
        operation(),
        logger=logger,
        username=request.username,
    )


@router.post("/auth/login")
async def login(request: CredentialsRequest) -> TokenResponse:
    """用户名密码登录，签发访问令牌。"""

    async def operation() -> TokenResponse:
        try:
            user = await auth_service.authenticate(request.username, request.password)
        except AuthError as exc:
            raise HTTPException(status_code=401, detail=exc.message) from exc
        return _token_response(user.id, user.username)

    return await run_api_action(
        "auth_login",
        operation(),
        logger=logger,
        username=request.username,
    )


@router.get("/auth/me")
async def me(current_user: CurrentUser) -> ProfileResponse:
    """校验令牌有效性并返回当前用户（前端启动时探活用）。"""
    return {"user_id": current_user.id, "username": current_user.username}


__all__ = ["router"]
