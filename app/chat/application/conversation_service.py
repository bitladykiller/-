"""会话服务。

职责：
- 对外提供会话 CRUD 服务入口
- 编排数据库事务和异常处理
- 调用 Repository 层进行数据访问

设计约束：
- Service 层不直接写 SQL
- 所有数据库访问通过 ConversationRepository
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any, AsyncContextManager, Protocol, TypeAlias, TypeVar

from typing_extensions import TypedDict

from sqlalchemy.ext.asyncio import AsyncSession

from app.shared.core.database import AsyncSessionLocal
from app.shared.core.logger import format_log_context, get_logger
from app.chat.infrastructure.repository.conversation_repository import (
    ConversationRepository,
)

logger = get_logger(__name__)
OperationResult = TypeVar("OperationResult")
_SessionFactory: TypeAlias = Callable[[], AsyncContextManager[AsyncSession]]


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


class ConversationService:
    """会话 CRUD 服务。

    通过 Repository 层访问数据库，保持 Service 层只关注业务编排。
    """

    @staticmethod
    async def create_conversation(user_id: int) -> int:
        """创建新会话并返回会话 id。"""
        return await run_db_operation(
            AsyncSessionLocal,
            logger,
            "create_conversation",
            _create_conversation,
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
            _get_user_conversations,
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
            _delete_conversation,
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
            _update_conversation_name,
            conversation_id,
            name,
            conversation_id=conversation_id,
            name=name,
        )


# ---- Repository 适配函数 ----

async def _create_conversation(db: AsyncSession, user_id: int) -> int:
    """创建新会话。"""
    repo = ConversationRepository(db)
    return await repo.create(user_id)


async def _get_user_conversations(
    db: AsyncSession,
    user_id: int,
) -> list[ConversationSummary]:
    """查询用户会话列表。"""
    repo = ConversationRepository(db)
    return await repo.list_by_user(user_id)


async def _delete_conversation(db: AsyncSession, conversation_id: int) -> None:
    """删除会话。"""
    repo = ConversationRepository(db)
    await repo.delete(conversation_id)


async def _update_conversation_name(
    db: AsyncSession,
    conversation_id: int,
    name: str,
) -> None:
    """更新会话标题。"""
    repo = ConversationRepository(db)
    await repo.rename(conversation_id, name)


__all__ = ["ConversationService", "ConversationSummary"]
