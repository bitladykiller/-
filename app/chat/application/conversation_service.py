"""会话服务。

聚焦 `conversations` 表的基本 CRUD，避免在 API 层直接写数据库访问逻辑。
本文件当前同时承接：
- 对外服务入口与动作编排
- 数据库 session 生命周期包装
- 会话摘要序列化与列表查询
- Conversation 的增删改查记录操作
"""
from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any, AsyncContextManager, Protocol, TypeAlias, TypeVar, TypedDict

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.shared.core.database import AsyncSessionLocal
from app.shared.core.logger import format_log_context, get_logger
from app.user.infrastructure.models.conversation import Conversation, DialogueType

logger = get_logger(__name__)
OperationResult = TypeVar("OperationResult")
_SessionFactory: TypeAlias = Callable[[], AsyncContextManager[AsyncSession]]
_DEFAULT_CONVERSATION_TITLE = "新会话"


class ConversationSummary(TypedDict):
    """会话列表接口返回的标准会话摘要。"""

    id: int
    title: str
    created_at: str
    status: str
    dialogue_type: str


class _LoggerLike(Protocol):
    """服务层日志对象的最小接口。"""

    def error(self, msg: str, *args: Any, **kwargs: Any) -> Any: ...


async def run_db_operation(
    session_factory: _SessionFactory,
    logger: _LoggerLike,
    action_name: str,
    operation: Callable[..., Awaitable[OperationResult]],
    *operation_args: object,
    **context: object,
) -> OperationResult:
    """统一封装数据库 session 生命周期和异常日志。"""
    try:
        async with session_factory() as db:
            return await operation(db, *operation_args)
    except Exception as exc:
        logger.error(
            f"{action_name} 异常 | {format_log_context(**context)} | {exc}",
            exc_info=True,
        )
        raise


async def create_conversation_record(db: AsyncSession, user_id: int) -> int:
    """在数据库中创建新会话并返回主键。"""
    conversation = Conversation(
        user_id=user_id,
        title=_DEFAULT_CONVERSATION_TITLE,
        dialogue_type=DialogueType.NORMAL,
    )
    db.add(conversation)
    await db.commit()
    await db.refresh(conversation)
    return conversation.id


async def fetch_user_conversations(
    db: AsyncSession,
    user_id: int,
) -> list[ConversationSummary]:
    """查询并序列化用户会话列表。"""
    stmt = select(Conversation).where(
        Conversation.user_id == user_id,
        Conversation.title != _DEFAULT_CONVERSATION_TITLE,
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


async def delete_conversation_record(
    db: AsyncSession,
    conversation_id: int,
) -> None:
    """删除指定会话。"""
    result = await db.execute(
        select(Conversation).where(Conversation.id == conversation_id)
    )
    conversation = result.scalar_one_or_none()
    if conversation is None:
        raise ValueError(f"Conversation {conversation_id} not found")
    await db.delete(conversation)
    await db.commit()


async def rename_conversation_record(
    db: AsyncSession,
    conversation_id: int,
    name: str,
) -> None:
    """更新指定会话标题。"""
    result = await db.execute(
        select(Conversation).where(Conversation.id == conversation_id)
    )
    conversation = result.scalar_one_or_none()
    if conversation is None:
        raise ValueError(f"Conversation {conversation_id} not found")
    conversation.title = name
    await db.commit()


class ConversationService:
    """会话 CRUD 服务。"""

    @staticmethod
    async def create_conversation(user_id: int) -> int:
        """创建新会话并返回会话 id。"""
        return await run_db_operation(
            AsyncSessionLocal,
            logger,
            "create_conversation",
            create_conversation_record,
            user_id,
            user_id=user_id,
        )

    @staticmethod
    async def get_user_conversations(user_id: int) -> list[ConversationSummary]:
        """获取用户的所有非默认标题会话。"""
        return await run_db_operation(
            AsyncSessionLocal,
            logger,
            "get_user_conversations",
            fetch_user_conversations,
            user_id,
            user_id=user_id,
        )

    @staticmethod
    async def delete_conversation(conversation_id: int) -> None:
        """删除会话。"""
        await run_db_operation(
            AsyncSessionLocal,
            logger,
            "delete_conversation",
            delete_conversation_record,
            conversation_id,
            conversation_id=conversation_id,
        )

    @staticmethod
    async def update_conversation_name(conversation_id: int, name: str) -> None:
        """更新会话标题。"""
        await run_db_operation(
            AsyncSessionLocal,
            logger,
            "update_conversation_name",
            rename_conversation_record,
            conversation_id,
            name,
            conversation_id=conversation_id,
            name=name,
        )
