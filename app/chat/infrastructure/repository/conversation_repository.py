"""会话数据访问层（Repository）。

职责：
- 封装 conversations 表的所有数据库访问逻辑
- 提供统一的 CRUD 接口
- 处理 SQL 执行和 ORM 映射

边界：
- 不处理业务规则（如缓存、事务编排）
- 不直接暴露给 API 层
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.user.infrastructure.models.conversation import Conversation, DialogueType

_DEFAULT_CONVERSATION_TITLE = "新会话"


class ConversationRepository:
    """Conversation 表的 Repository 实现。"""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def create(self, user_id: int) -> int:
        """创建新会话并返回主键。"""
        conversation = Conversation(
            user_id=user_id,
            title=_DEFAULT_CONVERSATION_TITLE,
            dialogue_type=DialogueType.NORMAL,
        )
        self._session.add(conversation)
        await self._session.commit()
        await self._session.refresh(conversation)
        return conversation.id

    async def list_by_user(
        self,
        user_id: int,
    ) -> list[dict[str, int | str]]:
        """查询用户会话列表，排除默认标题。"""
        stmt = (
            select(Conversation)
            .where(
                Conversation.user_id == user_id,
                Conversation.title != _DEFAULT_CONVERSATION_TITLE,
            )
            .order_by(Conversation.created_at.desc())
        )
        result = await self._session.execute(stmt)
        conversations = result.scalars().all()
        return [
            {
                "id": conversation.id,
                "title": conversation.title,
                "created_at": conversation.created_at.isoformat(),
                "status": conversation.status,
                "dialogue_type": conversation.dialogue_type.value,
            }
            for conversation in conversations
        ]

    async def delete(self, conversation_id: int) -> None:
        """删除指定会话，不存在时抛出 ValueError。"""
        result = await self._session.execute(
            select(Conversation).where(Conversation.id == conversation_id)
        )
        conversation = result.scalar_one_or_none()
        if conversation is None:
            raise ValueError(f"Conversation {conversation_id} not found")
        await self._session.delete(conversation)
        await self._session.commit()

    async def rename(self, conversation_id: int, name: str) -> None:
        """重命名会话，不存在时抛出 ValueError。"""
        result = await self._session.execute(
            select(Conversation).where(Conversation.id == conversation_id)
        )
        conversation = result.scalar_one_or_none()
        if conversation is None:
            raise ValueError(f"Conversation {conversation_id} not found")
        conversation.title = name
        await self._session.commit()


__all__ = ["ConversationRepository"]
