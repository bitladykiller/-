"""消息历史数据访问层。

只有两个操作：追加一轮对话、按会话列出。
append-only：不提供更新与单条删除（历史即事实）；
会话删除时随 FK CASCADE / 兼容清理一并消失。
"""

from __future__ import annotations

from app.chat.infrastructure.models.message import (
    MESSAGE_SENDER_ASSISTANT,
    MESSAGE_SENDER_USER,
    Message,
)
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession


class MessageRepository:
    """messages 表 Repository。"""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def add_turn(
        self,
        conversation_id: int,
        user_content: str,
        assistant_content: str,
    ) -> None:
        """追加一轮对话（user + assistant 两条，一次提交）。"""
        self._session.add_all(
            [
                Message(
                    conversation_id=conversation_id,
                    sender=MESSAGE_SENDER_USER,
                    content=user_content,
                ),
                Message(
                    conversation_id=conversation_id,
                    sender=MESSAGE_SENDER_ASSISTANT,
                    content=assistant_content,
                ),
            ]
        )
        await self._session.commit()

    async def list_by_conversation(self, conversation_id: int) -> list[dict[str, str]]:
        """按时间正序返回会话全部消息。"""
        result = await self._session.execute(
            select(Message)
            .where(Message.conversation_id == conversation_id)
            .order_by(Message.id.asc())
        )
        return [
            {
                "role": row.sender,
                "content": row.content,
                "created_at": row.created_at.isoformat() if row.created_at else "",
            }
            for row in result.scalars().all()
        ]


__all__ = ["MessageRepository"]
