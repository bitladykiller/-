"""认证接口：注册 / 登录 / 当前用户 / 租户管理。

唯一开放（无需令牌）的业务端点组。其余 API 一律经
`app.api.deps.get_current_user` 从令牌推导身份。

SaaS 多租户约定：
- 注册即创建"个人空间"租户，用户成为其 owner
- 登录时把用户最早加入的有效租户写进 JWT（tenant_id claim）
- /auth/tenants 列出全部归属，/auth/switch-tenant 切换活跃租户
  （重新签发令牌）
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
from app.user.application.tenant_service import tenant_service
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


class SwitchTenantRequest(BaseModel):
    """切换活跃租户请求。"""

    tenant_id: str


class TokenResponse(TypedDict):
    """登录 / 注册成功响应。"""

    access_token: str
    token_type: str
    user_id: int
    username: str
    tenant_id: str


class ProfileResponse(TypedDict):
    """当前用户信息。"""

    user_id: int
    username: str
    tenant_id: str
    role: str


class TenantItem(TypedDict):
    """用户可访问的租户。"""

    tenant_id: str
    tenant_name: str
    role: str
    status: str


def _token_response(user_id: int, username: str, tenant_id: str) -> TokenResponse:
    return {
        "access_token": issue_access_token(user_id, username, tenant_id=tenant_id),
        "token_type": _TOKEN_TYPE,
        "user_id": user_id,
        "username": username,
        "tenant_id": tenant_id,
    }


@router.post("/auth/register")
async def register(request: CredentialsRequest) -> TokenResponse:
    """注册并直接签发令牌（注册即登录，自动创建个人租户）。"""

    async def operation() -> TokenResponse:
        try:
            user = await auth_service.register(request.username, request.password)
        except RegistrationError as exc:
            raise HTTPException(status_code=400, detail=exc.message) from exc
        tenant_id = await tenant_service.create_personal_tenant(
            user.id,
            user.username,
        )
        return _token_response(user.id, user.username, tenant_id)

    return await run_api_action(
        "auth_register",
        operation(),
        logger=logger,
        username=request.username,
    )


@router.post("/auth/login")
async def login(request: CredentialsRequest) -> TokenResponse:
    """用户名密码登录，签发访问令牌（携带活跃租户）。"""

    async def operation() -> TokenResponse:
        try:
            user = await auth_service.authenticate(request.username, request.password)
        except AuthError as exc:
            raise HTTPException(status_code=401, detail=exc.message) from exc
        tenant_id = await tenant_service.resolve_active_tenant(user.id)
        return _token_response(user.id, user.username, tenant_id)

    return await run_api_action(
        "auth_login",
        operation(),
        logger=logger,
        username=request.username,
    )


@router.get("/auth/me")
async def me(current_user: CurrentUser) -> ProfileResponse:
    """校验令牌有效性并返回当前用户（前端启动时探活用）。"""
    return {
        "user_id": current_user.id,
        "username": current_user.username,
        "tenant_id": current_user.tenant_id,
        "role": getattr(current_user, "role", "") or "",
    }


@router.get("/auth/tenants")
async def list_tenants(current_user: CurrentUser) -> list[TenantItem]:
    """列出当前用户加入的全部租户及其角色。"""

    async def operation() -> list[TenantItem]:
        views = await tenant_service.list_user_tenants(current_user.id)
        return [
            {
                "tenant_id": view.tenant_id,
                "tenant_name": view.tenant_name,
                "role": view.role,
                "status": view.status,
            }
            for view in views
        ]

    return await run_api_action(
        "auth_list_tenants",
        operation(),
        logger=logger,
        user_id=current_user.id,
    )


@router.post("/auth/switch-tenant")
async def switch_tenant(
    request: SwitchTenantRequest,
    current_user: CurrentUser,
) -> TokenResponse:
    """切换到指定租户，重新签发携带该租户的令牌。"""

    async def operation() -> TokenResponse:
        tenant_id = await tenant_service.switch_tenant(
            current_user.id,
            request.tenant_id,
        )
        return _token_response(current_user.id, current_user.username, tenant_id)

    return await run_api_action(
        "auth_switch_tenant",
        operation(),
        logger=logger,
        user_id=current_user.id,
        tenant_id=request.tenant_id,
    )


__all__ = ["router"]
