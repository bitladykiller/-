"""会话服务。

聚焦 `conversations` 表的基本 CRUD，避免在 API 层直接写数据库访问逻辑。
本文件当前同时承接：
- 对外服务入口与动作编排
- 会话摘要序列化与列表查询
- Conversation 的增删改查记录操作
"""

from collections.abc import Awaitable
from typing import Any

from app.shared.core.database import AsyncSessionLocal
from app.shared.core.logger import format_log_context, get_logger
from app.user.infrastructure.models.conversation import Conversation, DialogueType
from sqlalchemy import select

logger = get_logger(__name__)


async def _run_service_action(
    action_name: str,
    operation: Awaitable[Any],
    **context: object,
) -> Any:
    """统一执行会话服务动作，并在失败时记录上下文后原样抛出。"""
    try:
        return await operation
    except Exception as exc:
        logger.error(
            f"{action_name} 异常 | {format_log_context(**context)} | {exc}",
            exc_info=True,
        )
        raise


async def create_conversation(user_id: int) -> int:
    """创建新会话并返回会话 id。"""
    async def operation() -> int:
        async with AsyncSessionLocal() as db:
            conversation = Conversation(
                user_id=user_id,
                title="新会话",
                dialogue_type=DialogueType.NORMAL,
            )
            db.add(conversation)
            await db.commit()
            await db.refresh(conversation)
            return conversation.id

    return await _run_service_action(
        "create_conversation",
        operation(),
        user_id=user_id,
    )


async def get_user_conversations(user_id: int) -> list[dict[str, object]]:
    """获取用户的所有非默认标题会话。"""
    async def operation() -> list[dict[str, object]]:
        async with AsyncSessionLocal() as db:
            stmt = select(Conversation).where(
                Conversation.user_id == user_id,
                Conversation.title != "新会话",
            ).order_by(Conversation.created_at.desc())
            result = await db.execute(stmt)
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

    return await _run_service_action(
        "get_user_conversations",
        operation(),
        user_id=user_id,
    )


async def delete_conversation(conversation_id: int) -> None:
    """删除会话。"""
    async def operation() -> None:
        async with AsyncSessionLocal() as db:
            result = await db.execute(
                select(Conversation).where(Conversation.id == conversation_id)
            )
            conversation = result.scalar_one_or_none()
            if conversation is None:
                raise ValueError(f"Conversation {conversation_id} not found")
            await db.delete(conversation)
            await db.commit()

    await _run_service_action(
        "delete_conversation",
        operation(),
        conversation_id=conversation_id,
    )


async def update_conversation_name(conversation_id: int, name: str) -> None:
    """更新会话标题。"""
    async def operation() -> None:
        async with AsyncSessionLocal() as db:
            result = await db.execute(
                select(Conversation).where(Conversation.id == conversation_id)
            )
            conversation = result.scalar_one_or_none()
            if conversation is None:
                raise ValueError(f"Conversation {conversation_id} not found")
            conversation.title = name
            await db.commit()

    await _run_service_action(
        "update_conversation_name",
        operation(),
        conversation_id=conversation_id,
        name=name,
    )
