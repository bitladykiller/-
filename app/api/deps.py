"""API 层共享依赖。

`get_current_user` 是全部受保护端点的唯一身份来源，流程为：

    Bearer JWT
        ↓ 解析 + 验签            (verify_access_token)
    AuthenticatedUser{id, username, tenant_id}
        ↓ 归属校验                (TenantService.validate_membership)
    user ∈ tenant 且 membership.status=active
        ↓ 建立可信上下文
    TenantContext{tenant_id, user_id, role} → contextvars

端点**不得**再接受客户端自报的 user_id / tenant_id 参数——那正是此前
IDOR 的根源；租户边界必须由服务端从令牌 + memberships 推导。
"""

from __future__ import annotations

from typing import Annotated

from app.shared.core.identity import TenantContext, set_tenant_context
from app.user.application.auth_service import (
    AuthenticatedUser,
    AuthError,
    verify_access_token,
)
from app.user.application.tenant_service import tenant_service
from fastapi import Depends, HTTPException
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

# auto_error=False：缺头时自己给出中文 401 文案，而不是框架默认 403
_bearer_scheme = HTTPBearer(auto_error=False)


async def get_current_user(
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer_scheme)],
) -> AuthenticatedUser:
    """验证 Bearer 令牌并返回当前用户；失败统一 401。

    除令牌本身外，还会查询 tenant_memberships 确认令牌声明的租户
    与用户存在有效归属——令牌声明不可信，membership 才是租户边界
    的最终裁判。
    """
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

    role = await tenant_service.validate_membership(user.id, user.tenant_id)
    if role is None:
        raise HTTPException(
            status_code=401,
            detail="无权访问该租户，请切换租户或重新登录",
            headers={"WWW-Authenticate": "Bearer"},
        )

    set_tenant_context(TenantContext(tenant_id=user.tenant_id, user_id=user.id, role=role))
    return user


CurrentUser = Annotated[AuthenticatedUser, Depends(get_current_user)]

__all__ = ["CurrentUser", "get_current_user"]
