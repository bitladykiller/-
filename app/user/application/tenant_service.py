"""租户服务：租户创建、成员归属校验、活跃租户解析。

职责边界：
- 注册时为用户创建个人租户并授予 owner
- 登录时解析用户的活跃租户（JWT 携带）
- 请求鉴权时校验 `user ∈ tenant` 且 membership 有效（deps.py 调用）
- 提供租户列表 / 切换租户（重新签发令牌）

这个模块不负责：
- 令牌签发（auth_service）
- HTTP 细节（app.api.auth / app.api.deps）
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass

from app.shared.core.errors import ResourceNotFoundError
from app.shared.core.logger import get_logger
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

logger = get_logger(__name__)

DEFAULT_TENANT_ID = "default"


@dataclass(frozen=True)
class TenantMembershipView:
    """用户视角的租户成员关系。"""

    tenant_id: str
    tenant_name: str
    role: str
    status: str


class TenantServiceError(Exception):
    """租户业务错误。"""

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


def new_tenant_id() -> str:
    """生成新租户 ID：`t_` + 12 位 hex，全局唯一、可读性好。"""
    return f"t_{uuid.uuid4().hex[:12]}"


class TenantService:
    """租户与成员关系服务。"""

    def __init__(self, session_factory: async_sessionmaker | None = None) -> None:
        if session_factory is None:
            from app.shared.core.database import AsyncSessionLocal

            session_factory = AsyncSessionLocal
        self._session_factory = session_factory

    async def create_personal_tenant(
        self,
        user_id: int,
        username: str,
    ) -> str:
        """注册时创建"个人空间"租户并绑定 owner 成员关系。

        Returns:
            新租户 ID。
        """
        from app.user.infrastructure.models.tenant import (
            MEMBERSHIP_STATUS_ACTIVE,
            ROLE_OWNER,
            TENANT_STATUS_ACTIVE,
            Tenant,
            TenantMembership,
        )

        tenant_id = new_tenant_id()
        async with self._session_factory() as db:
            db.add(
                Tenant(
                    id=tenant_id,
                    name=f"{username} 的个人空间",
                    status=TENANT_STATUS_ACTIVE,
                )
            )
            db.add(
                TenantMembership(
                    tenant_id=tenant_id,
                    user_id=user_id,
                    role=ROLE_OWNER,
                    status=MEMBERSHIP_STATUS_ACTIVE,
                )
            )
            await db.commit()
        logger.info(
            "创建个人租户 | tenant=%s user_id=%s username=%s",
            tenant_id,
            user_id,
            username,
        )
        return tenant_id

    async def resolve_active_tenant(self, user_id: int) -> str:
        """解析用户的活跃租户：取最早加入且状态有效的成员关系。

        登录/签发令牌时调用；无有效租户返回 DEFAULT_TENANT_ID
        （保证老账号/测试环境不回退成不可用）。
        """
        from app.user.infrastructure.models.tenant import (
            MEMBERSHIP_STATUS_ACTIVE,
            Tenant,
            TenantMembership,
        )

        async with self._session_factory() as db:
            result = await db.execute(
                select(TenantMembership.tenant_id, Tenant.status)
                .join(Tenant, Tenant.id == TenantMembership.tenant_id)
                .where(
                    TenantMembership.user_id == user_id,
                    TenantMembership.status == MEMBERSHIP_STATUS_ACTIVE,
                    Tenant.status == "active",
                )
                .order_by(TenantMembership.joined_at.asc())
                .limit(1)
            )
            row = result.first()
            return str(row[0]) if row else DEFAULT_TENANT_ID

    async def validate_membership(
        self,
        user_id: int,
        tenant_id: str,
    ) -> str | None:
        """校验用户是否属于租户且双方状态有效。

        Args:
            user_id: 请求方用户 ID。
            tenant_id: 令牌声明的租户。

        Returns:
            该租户内的角色（owner/admin/member/viewer）；不满足返回 None。
        """
        from app.user.infrastructure.models.tenant import (
            MEMBERSHIP_STATUS_ACTIVE,
            TENANT_STATUS_ACTIVE,
            Tenant,
            TenantMembership,
        )

        tenant_id = tenant_id or DEFAULT_TENANT_ID
        async with self._session_factory() as db:
            result = await db.execute(
                select(TenantMembership.role)
                .join(Tenant, Tenant.id == TenantMembership.tenant_id)
                .where(
                    TenantMembership.user_id == user_id,
                    TenantMembership.tenant_id == tenant_id,
                    TenantMembership.status == MEMBERSHIP_STATUS_ACTIVE,
                    Tenant.status == TENANT_STATUS_ACTIVE,
                )
            )
            row = result.scalar_one_or_none()
            return str(row) if row else None

    async def list_user_tenants(self, user_id: int) -> list[TenantMembershipView]:
        """列出用户加入的所有租户（含角色与状态）。"""
        from app.user.infrastructure.models.tenant import (
            Tenant,
            TenantMembership,
        )

        async with self._session_factory() as db:
            result = await db.execute(
                select(
                    TenantMembership.tenant_id,
                    Tenant.name,
                    TenantMembership.role,
                    TenantMembership.status,
                )
                .join(Tenant, Tenant.id == TenantMembership.tenant_id)
                .where(TenantMembership.user_id == user_id)
                .order_by(TenantMembership.joined_at.asc())
            )
            return [
                TenantMembershipView(
                    tenant_id=str(row[0]),
                    tenant_name=str(row[1]),
                    role=str(row[2]),
                    status=str(row[3]),
                )
                for row in result.all()
            ]

    async def switch_tenant(self, user_id: int, tenant_id: str) -> str:
        """切换活跃租户：校验归属后返回该租户 ID，供重新签发令牌。"""
        role = await self.validate_membership(user_id, tenant_id)
        if role is None:
            raise ResourceNotFoundError("租户不存在或用户无权访问")
        return tenant_id


tenant_service = TenantService()

__all__ = [
    "DEFAULT_TENANT_ID",
    "TenantMembershipView",
    "TenantService",
    "TenantServiceError",
    "new_tenant_id",
    "tenant_service",
]
