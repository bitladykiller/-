from sqlalchemy import Column, Integer, String, DateTime, ForeignKey, Text, func
from app.core.database import Base


class Message(Base):
    """消息模型 —— 已废弃，MySQL 不再存储消息。

    MySQL 中仅保留 conversations 表的会话元信息（标题、时间）。
    消息内容全部在 Redis STM 中，随 TTL 过期。
    此模型保留仅用于向后兼容已有的 messages 表。
    """
    __tablename__ = "messages"

    id = Column(Integer, primary_key=True, index=True)
    conversation_id = Column(Integer, ForeignKey("conversations.id", ondelete="CASCADE"))
    sender = Column(String(50), nullable=False)
    content = Column(Text, nullable=False)
    created_at = Column(DateTime, server_default=func.now())
    message_type = Column(String(20), default="text") 