"""租户与租户成员关系模型（SaaS 多租户身份层）。

表结构：

    tenants              租户主体
    ├── id               租户 ID（字符串，如 "default" / "t_xxx"）
    ├── name / status    展示名与状态
    └── plan             套餐档位（free/pro/enterprise）

    tenant_memberships   用户-租户多对多
    ├── tenant_id        属于哪个租户
    ├── user_id          哪个用户
    └── role             owner/admin/member/viewer

为什么用 memberships 而不是 users.tenant_id：
一个用户可加入多个组织（跨组织账号）；"当前活跃租户"是会话态
（JWT 里的 tenant_id），不是用户属性。
"""

from __future__ import annotations

from datetime import datetime

from app.shared.core.database import Base
from sqlalchemy import DateTime, ForeignKey, String, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

TENANT_STATUS_ACTIVE = "active"
TENANT_STATUS_DISABLED = "disabled"

MEMBERSHIP_STATUS_ACTIVE = "active"
MEMBERSHIP_STATUS_SUSPENDED = "suspended"

ROLE_OWNER = "owner"
ROLE_ADMIN = "admin"
ROLE_MEMBER = "member"
ROLE_VIEWER = "viewer"


class Tenant(Base):
    """租户表：SaaS 数据隔离的一级边界。"""

    __tablename__ = "tenants"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    status: Mapped[str] = mapped_column(String(20), default=TENANT_STATUS_ACTIVE)
    plan: Mapped[str] = mapped_column(String(32), default="free")
    created_at: Mapped[datetime] = mapped_column(
        DateTime,
        server_default=func.now(),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime,
        server_default=func.now(),
        onupdate=func.now(),
    )


class TenantMembership(Base):
    """用户与租户的归属关系（含角色）。"""

    __tablename__ = "tenant_memberships"
    __table_args__ = (UniqueConstraint("tenant_id", "user_id", name="uk_membership"),)

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    tenant_id: Mapped[str] = mapped_column(
        String(64),
        ForeignKey("tenants.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    role: Mapped[str] = mapped_column(String(20), default=ROLE_MEMBER)
    status: Mapped[str] = mapped_column(
        String(20),
        default=MEMBERSHIP_STATUS_ACTIVE,
    )
    joined_at: Mapped[datetime] = mapped_column(
        DateTime,
        server_default=func.now(),
    )
