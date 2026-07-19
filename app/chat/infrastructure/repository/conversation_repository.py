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

from app.chat.infrastructure.models.conversation import Conversation, DialogueType
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

_DEFAULT_CONVERSATION_TITLE = "新会话"
# 历史初始化脚本中的 messages 表；主路径消息在 Redis STM，这里做兼容清理
_DELETE_MYSQL_MESSAGES_SQL = text(
    "DELETE FROM messages WHERE conversation_id = :conversation_id"
)


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

    async def get_by_id(self, conversation_id: int) -> Conversation | None:
        """按主键查询会话。"""
        result = await self._session.execute(
            select(Conversation).where(Conversation.id == conversation_id)
        )
        return result.scalar_one_or_none()

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

    async def delete(self, conversation_id: int) -> Conversation:
        """删除指定会话及其 MySQL messages 兼容数据，返回被删会话。

        不存在时抛出 ValueError。
        """
        conversation = await self.get_by_id(conversation_id)
        if conversation is None:
            raise ValueError(f"Conversation {conversation_id} not found")

        # 兼容历史 messages 表；表不存在时忽略
        try:
            await self._session.execute(
                _DELETE_MYSQL_MESSAGES_SQL,
                {"conversation_id": conversation_id},
            )
        except Exception:
            await self._session.rollback()
            # 重新加载会话，避免事务失效后对象状态异常
            conversation = await self.get_by_id(conversation_id)
            if conversation is None:
                raise ValueError(f"Conversation {conversation_id} not found") from None

        await self._session.delete(conversation)
        await self._session.commit()
        return conversation

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
