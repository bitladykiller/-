"""用户模型。

字段与 configs/mysql-init/init.sql 的 users 表对齐——
此前 ORM 只映射了 id/username，password_hash 等列在库里躺了很久没被用上。
"""
from __future__ import annotations

from datetime import datetime

from app.shared.core.database import Base
from sqlalchemy import DateTime, String, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

USER_STATUS_ACTIVE = "active"


class User(Base):
    """用户表。"""

    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True, index=True)
    username: Mapped[str] = mapped_column(String(50), unique=True, nullable=False)
    email: Mapped[str] = mapped_column(String(100), unique=True, nullable=False)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    status: Mapped[str] = mapped_column(String(20), default=USER_STATUS_ACTIVE)
    last_login: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime,
        server_default=func.now(),
    )

    conversations: Mapped[list[Conversation]] = relationship(
        "Conversation",
        back_populates="user",
        cascade="all, delete-orphan",
    )


from app.chat.infrastructure.models.conversation import Conversation  # noqa: E402,F401
