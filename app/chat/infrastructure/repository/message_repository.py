"""消息历史数据访问层。

只有两个操作：追加一轮对话、按会话列出。
append-only：不提供更新与单条删除（历史即事实）；
会话删除时随 FK CASCADE / 兼容清理一并消失。

多租户策略：messages 不冗余 tenant_id 列，而是通过 JOIN conversations
强制租户条件——即使调用方漏做了会话归属校验，查询/写入本身也带租户
边界，杜绝跨租户读写。
"""

from __future__ import annotations

from app.chat.infrastructure.models.conversation import Conversation
from app.chat.infrastructure.models.message import (
    MESSAGE_SENDER_ASSISTANT,
    MESSAGE_SENDER_USER,
    Message,
)
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession


class MessageRepository:
    """messages 表 Repository。"""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def add_turn(
        self,
        tenant_id: str,
        conversation_id: int,
        user_content: str,
        assistant_content: str,
        *,
        turn_event_id: str | None = None,
    ) -> None:
        """在指定租户的会话下追加一轮对话（user + assistant 两次提交）。

        turn_event_id 非空时由数据库唯一键兜底：Stream 在 XACK 前崩溃并重放，
        也不会再追加同一回合的两条历史消息。
        """
        if turn_event_id and await self._turn_exists(
            tenant_id,
            conversation_id,
            turn_event_id,
        ):
            return

        self._session.add_all(
            [
                Message(
                    conversation_id=conversation_id,
                    sender=MESSAGE_SENDER_USER,
                    content=user_content,
                    turn_event_id=turn_event_id,
                ),
                Message(
                    conversation_id=conversation_id,
                    sender=MESSAGE_SENDER_ASSISTANT,
                    content=assistant_content,
                    turn_event_id=turn_event_id,
                ),
            ]
        )
        try:
            await self._session.commit()
        except IntegrityError:
            # 并发消费者可能在查询与插入之间完成了同一 event；唯一键是最终裁决。
            await self._session.rollback()
            if turn_event_id and await self._turn_exists(
                tenant_id,
                conversation_id,
                turn_event_id,
            ):
                return
            raise

    async def _turn_exists(
        self,
        tenant_id: str,
        conversation_id: int,
        turn_event_id: str,
    ) -> bool:
        """判断该回合的 user/assistant 历史是否已经完整持久化。"""
        result = await self._session.execute(
            select(Message.sender)
            .join(Conversation, Conversation.id == Message.conversation_id)
            .where(
                Conversation.tenant_id == tenant_id,
                Message.conversation_id == conversation_id,
                Message.turn_event_id == turn_event_id,
            )
        )
        return {str(sender) for sender in result.scalars().all()} >= {
            MESSAGE_SENDER_USER,
            MESSAGE_SENDER_ASSISTANT,
        }

    async def list_by_conversation(
        self,
        tenant_id: str,
        conversation_id: int,
    ) -> list[dict[str, str]]:
        """按时间正序返回租户内会话的全部消息。"""
        result = await self._session.execute(
            select(Message)
            .join(Conversation, Conversation.id == Message.conversation_id)
            .where(
                Conversation.tenant_id == tenant_id,
                Message.conversation_id == conversation_id,
            )
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
