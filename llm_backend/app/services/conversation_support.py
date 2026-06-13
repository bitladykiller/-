"""会话服务 support helper。

职责：
- 负责会话摘要序列化、默认对象构造和列表查询语句构造
- 承接 CRUD 主流程复用的数据库 helper 与 session 样板

边界：
- 不提供最终的服务入口方法
- 不暴露 HTTP 或路由相关逻辑
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any, AsyncContextManager, Protocol, TypeAlias, TypedDict, TypeVar

from sqlalchemy import Select, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logger import format_log_context
from app.models.conversation import Conversation, DialogueType

DEFAULT_CONVERSATION_TITLE = "新会话"
OperationResult = TypeVar("OperationResult")
SessionFactory: TypeAlias = Callable[[], AsyncContextManager[AsyncSession]]


class LoggerLike(Protocol):
    """服务层日志对象的最小接口。"""

    def error(self, msg: str, *args: Any, **kwargs: Any) -> Any: ...


class ConversationSummary(TypedDict):
    """会话列表接口返回的标准会话摘要。"""

    id: int
    title: str
    created_at: str
    status: str
    dialogue_type: str


def serialize_conversation(conversation: Conversation) -> ConversationSummary:
    """把 SQLAlchemy 会话对象转换成 API 友好的字典。"""
    return {
        "id": conversation.id,
        "title": conversation.title,
        "created_at": conversation.created_at.isoformat(),
        "status": conversation.status,
        "dialogue_type": conversation.dialogue_type.value,
    }


def serialize_conversations(
    conversations: list[Conversation],
) -> list[ConversationSummary]:
    """批量序列化会话列表。"""
    return [serialize_conversation(conversation) for conversation in conversations]


def build_default_conversation(user_id: int) -> Conversation:
    """构造默认的新会话记录。"""
    return Conversation(
        user_id=user_id,
        title=DEFAULT_CONVERSATION_TITLE,
        dialogue_type=DialogueType.NORMAL,
    )


def build_user_conversations_stmt(user_id: int) -> Select[tuple[Conversation]]:
    """构造“用户会话列表”查询语句。"""
    return select(Conversation).where(
        Conversation.user_id == user_id,
        Conversation.title != DEFAULT_CONVERSATION_TITLE,
    ).order_by(Conversation.created_at.desc())


async def get_conversation_or_raise(
    db: AsyncSession,
    conversation_id: int,
) -> Conversation:
    """按 id 获取会话，不存在时抛出 ValueError。"""
    stmt = select(Conversation).where(Conversation.id == conversation_id)
    result = await db.execute(stmt)
    conversation = result.scalar_one_or_none()
    if conversation is None:
        raise ValueError(f"Conversation {conversation_id} not found")
    return conversation


async def run_db_operation(
    session_factory: SessionFactory,
    logger: LoggerLike,
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
    conversation = build_default_conversation(user_id)
    db.add(conversation)
    await db.commit()
    await db.refresh(conversation)
    return conversation.id


async def fetch_user_conversations(
    db: AsyncSession,
    user_id: int,
) -> list[ConversationSummary]:
    """查询并序列化用户会话列表。"""
    stmt = build_user_conversations_stmt(user_id)
    result = await db.execute(stmt)
    conversations = result.scalars().all()
    return serialize_conversations(conversations)


async def delete_conversation_record(
    db: AsyncSession,
    conversation_id: int,
) -> None:
    """删除指定会话。"""
    conversation = await get_conversation_or_raise(db, conversation_id)
    await db.delete(conversation)
    await db.commit()


async def rename_conversation_record(
    db: AsyncSession,
    conversation_id: int,
    name: str,
) -> None:
    """更新指定会话标题。"""
    conversation = await get_conversation_or_raise(db, conversation_id)
    conversation.title = name
    await db.commit()


__all__ = [
    "DEFAULT_CONVERSATION_TITLE",
    "ConversationSummary",
    "LoggerLike",
    "SessionFactory",
    "build_default_conversation",
    "build_user_conversations_stmt",
    "create_conversation_record",
    "delete_conversation_record",
    "fetch_user_conversations",
    "get_conversation_or_raise",
    "rename_conversation_record",
    "run_db_operation",
    "serialize_conversation",
    "serialize_conversations",
]
