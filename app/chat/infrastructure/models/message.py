"""消息持久化模型。

映射 configs/mysql-init/init.sql 中一直存在但从未被写入的 `messages` 表。

职责分工（与 Redis STM 的区别）：
- 本表是 **append-only 的对话历史**，给"人"看：前端切回旧会话时展示、
  运营审计。永不参与推理。
- Redis STM 是 **模型的上下文窗口**：滑动窗口 + 24h TTL + 压缩摘要。
此前只有 STM，产品表现为"隔天失忆"——会话列表还在，点开全空。
"""

from __future__ import annotations

from datetime import datetime

from app.shared.core.database import Base
from sqlalchemy import DateTime, ForeignKey, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

#: sender 列的取值（历史 schema 用 sender，语义即 role）
MESSAGE_SENDER_USER = "user"
MESSAGE_SENDER_ASSISTANT = "assistant"


class Message(Base):
    """messages 表（append-only 对话历史）。"""

    __tablename__ = "messages"

    id: Mapped[int] = mapped_column(primary_key=True, index=True)
    conversation_id: Mapped[int] = mapped_column(
        ForeignKey("conversations.id", ondelete="CASCADE"),
        index=True,
        nullable=False,
    )
    sender: Mapped[str] = mapped_column(String(50), nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    message_type: Mapped[str] = mapped_column(String(20), default="text")
    created_at: Mapped[datetime] = mapped_column(
        DateTime,
        server_default=func.now(),
    )


__all__ = ["MESSAGE_SENDER_ASSISTANT", "MESSAGE_SENDER_USER", "Message"]
