"""会话模型。

这里只持久化会话元信息，不保存逐条聊天消息。
消息内容当前由短期记忆层维护。
"""
import enum
from datetime import datetime

from sqlalchemy import (
    DateTime,
    Enum as SQLEnum,
    ForeignKey,
    String,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.shared.core.database import Base


class DialogueType(str, enum.Enum):
    """会话类型枚举。"""

    NORMAL = "普通对话"
    DEEP_THINKING = "深度思考"
    WEB_SEARCH = "联网检索"
    RAG = "RAG 问答"


class Conversation(Base):
    """会话元信息表。"""

    __tablename__ = "conversations"

    id: Mapped[int] = mapped_column(primary_key=True, index=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE")
    )
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

    user: Mapped["User"] = relationship("User", back_populates="conversations")


# 显式导入关联模型，避免 relationship("User") 继续隐式依赖包级聚合导入。
from app.user.infrastructure.models.user import User  # noqa: E402,F401
