"""会话模型。

这里只持久化会话元信息，不保存逐条聊天消息。
消息内容当前由短期记忆层维护。

注意：
- Conversation 和 User 之间存在 SQLAlchemy relationship 双向绑定。
- 因此 Conversation 需要引用 User，User 也需要引用 Conversation。
- 模型放在 chat/infrastructure/models/ 下，通过延迟 import 解决循环引用。
"""

from __future__ import annotations

import enum
from datetime import datetime

from app.shared.core.database import Base
from sqlalchemy import (
    DateTime,
    ForeignKey,
    Index,
    String,
    func,
)
from sqlalchemy import (
    Enum as SQLEnum,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship


class DialogueType(str, enum.Enum):
    """会话类型枚举。"""

    NORMAL = "普通对话"
    DEEP_THINKING = "深度思考"
    WEB_SEARCH = "联网检索"
    RAG = "RAG 问答"


class Conversation(Base):
    """会话元信息表。

    tenant_id 是组织边界（VARCHAR(64)，默认 "default"）；
    user_id 是租户内部的资源归属边界。
    """

    __tablename__ = "conversations"
    __table_args__ = (
        Index("idx_conversation_tenant_user", "tenant_id", "user_id"),
        Index("idx_conversation_tenant_id", "tenant_id", "id"),
    )

    id: Mapped[int] = mapped_column(primary_key=True, index=True)
    tenant_id: Mapped[str] = mapped_column(
        String(64),
        nullable=False,
        default="default",
        server_default="default",
    )
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    title: Mapped[str] = mapped_column(String(100), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime,
        server_default=func.now(),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime,
        server_default=func.now(),
        onupdate=func.now(),
    )
    status: Mapped[str] = mapped_column(String(20), default="ongoing")
    dialogue_type: Mapped[DialogueType] = mapped_column(
        SQLEnum(DialogueType),
        nullable=False,
    )

    user: Mapped[User] = relationship("User", back_populates="conversations")


from app.user.infrastructure.models.user import User  # noqa: E402,F401
