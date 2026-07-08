"""会话数据访问层（Repository）。

职责：
- 封装 conversations 表的所有数据库访问逻辑
- 提供统一的 CRUD 接口
- 处理 SQL 执行和 ORM 映射

边界：
- 不处理业务规则（如缓存、事务编排）
- 不直接暴露给 API 层

多租户纪律：
- **所有查询强制携带 tenant_id**，不允许"无租户上下文"的查询路径。
  tenant_id 是组织边界，user_id 是租户内部归属边界，二者组合才是
  完整的资源可见性。
- 归属于本模块 `get_owned(tenant_id, conversation_id, user_id)`，
  不再存在纯 `get_by_id` 的业务调用路径。
"""

from __future__ import annotations

from app.chat.infrastructure.models.conversation import Conversation, DialogueType
from app.shared.core.errors import ResourceNotFoundError
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

_DEFAULT_CONVERSATION_TITLE = "新会话"
# 统一文案：不区分"不存在"与"不属于该租户/用户"，避免资源 ID 被枚举探测
_NOT_FOUND_MESSAGE = "会话不存在或不属于当前用户"
# 历史初始化脚本中的 messages 表；主路径消息在 Redis STM，这里做兼容清理
_DELETE_MYSQL_MESSAGES_SQL = text("DELETE FROM messages WHERE conversation_id = :conversation_id")


class ConversationRepository:
    """Conversation 表的 Repository 实现。"""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def create(self, tenant_id: str, user_id: int) -> int:
        """在指定租户下创建新会话并返回主键。"""
        conversation = Conversation(
            tenant_id=tenant_id,
            user_id=user_id,
            title=_DEFAULT_CONVERSATION_TITLE,
            dialogue_type=DialogueType.NORMAL,
        )
        self._session.add(conversation)
        await self._session.commit()
        await self._session.refresh(conversation)
        return conversation.id

    async def get_by_id(
        self,
        tenant_id: str,
        conversation_id: int,
    ) -> Conversation | None:
        """按租户 + 主键查询会话。"""
        result = await self._session.execute(
            select(Conversation).where(
                Conversation.tenant_id == tenant_id,
                Conversation.id == conversation_id,
            )
        )
        return result.scalar_one_or_none()

    async def list_by_user(
        self,
        tenant_id: str,
        user_id: int,
    ) -> list[dict[str, int | str]]:
        """查询用户在指定租户内的会话列表，排除默认标题。"""
        stmt = (
            select(Conversation)
            .where(
                Conversation.tenant_id == tenant_id,
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

    async def get_owned(
        self,
        tenant_id: str,
        conversation_id: int,
        user_id: int,
    ) -> Conversation | None:
        """按租户 + 主键查询会话并校验租户内归属。"""
        result = await self._session.execute(
            select(Conversation).where(
                Conversation.tenant_id == tenant_id,
                Conversation.id == conversation_id,
                Conversation.user_id == user_id,
            )
        )
        return result.scalar_one_or_none()

    async def delete(
        self,
        tenant_id: str,
        conversation_id: int,
        user_id: int,
    ) -> Conversation:
        """删除指定租户+用户名下的会话及其 MySQL messages 兼容数据。

        WHY 必须带 tenant_id + user_id：删除会联动清空该会话的 STM/LTM
        记忆。无归属校验时任何调用方可以按 id 删任意人的会话与记忆（IDOR）。
        不存在或不属于该租户/用户时抛 ResourceNotFoundError（API 层映射 404）。
        """
        conversation = await self.get_owned(tenant_id, conversation_id, user_id)
        if conversation is None:
            raise ResourceNotFoundError(_NOT_FOUND_MESSAGE)

        # 兼容历史 messages 表；表不存在时忽略
        try:
            await self._session.execute(
                _DELETE_MYSQL_MESSAGES_SQL,
                {"conversation_id": conversation_id},
            )
        except Exception:
            await self._session.rollback()
            # 重新加载会话，避免事务失效后对象状态异常
            conversation = await self.get_owned(tenant_id, conversation_id, user_id)
            if conversation is None:
                raise ResourceNotFoundError(_NOT_FOUND_MESSAGE) from None

        await self._session.delete(conversation)
        await self._session.commit()
        return conversation

    async def rename(
        self,
        tenant_id: str,
        conversation_id: int,
        user_id: int,
        name: str,
    ) -> None:
        """重命名指定租户+用户名下的会话。

        不存在或不属于该租户/用户时抛 ResourceNotFoundError（API 层映射 404）。
        """
        conversation = await self.get_owned(tenant_id, conversation_id, user_id)
        if conversation is None:
            raise ResourceNotFoundError(_NOT_FOUND_MESSAGE)
        conversation.title = name
        await self._session.commit()


__all__ = ["ConversationRepository"]
