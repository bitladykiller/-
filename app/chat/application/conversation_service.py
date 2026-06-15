"""会话服务。

聚焦 `conversations` 表的基本 CRUD，避免在 API 层直接写数据库访问逻辑。
本文件当前同时承接：
- 对外服务入口与动作编排
- 会话摘要序列化与列表查询
- Conversation 的增删改查记录操作
"""

from sqlalchemy import select

from app.shared.core.database import AsyncSessionLocal
from app.shared.core.logger import format_log_context, get_logger
from app.user.infrastructure.models.conversation import Conversation, DialogueType

logger = get_logger(__name__)


async def create_conversation(user_id: int) -> int:
    """创建新会话并返回会话 id。"""
    try:
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
    except Exception as exc:
        logger.error(
            f"create_conversation 异常 | {format_log_context(user_id=user_id)} | {exc}",
            exc_info=True,
        )
        raise


async def get_user_conversations(user_id: int) -> list[dict[str, object]]:
    """获取用户的所有非默认标题会话。"""
    try:
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
    except Exception as exc:
        logger.error(
            f"get_user_conversations 异常 | {format_log_context(user_id=user_id)} | {exc}",
            exc_info=True,
        )
        raise


async def delete_conversation(conversation_id: int) -> None:
    """删除会话。"""
    try:
        async with AsyncSessionLocal() as db:
            result = await db.execute(
                select(Conversation).where(Conversation.id == conversation_id)
            )
            conversation = result.scalar_one_or_none()
            if conversation is None:
                raise ValueError(f"Conversation {conversation_id} not found")
            await db.delete(conversation)
            await db.commit()
    except Exception as exc:
        logger.error(
            f"delete_conversation 异常 | "
            f"{format_log_context(conversation_id=conversation_id)} | {exc}",
            exc_info=True,
        )
        raise


async def update_conversation_name(conversation_id: int, name: str) -> None:
    """更新会话标题。"""
    try:
        async with AsyncSessionLocal() as db:
            result = await db.execute(
                select(Conversation).where(Conversation.id == conversation_id)
            )
            conversation = result.scalar_one_or_none()
            if conversation is None:
                raise ValueError(f"Conversation {conversation_id} not found")
            conversation.title = name
            await db.commit()
    except Exception as exc:
        logger.error(
            f"update_conversation_name 异常 | "
            f"{format_log_context(conversation_id=conversation_id, name=name)} | {exc}",
            exc_info=True,
        )
        raise
