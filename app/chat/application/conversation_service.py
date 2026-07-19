"""会话服务。

职责：
- 对外提供会话 CRUD 服务入口
- 编排数据库事务和异常处理
- 删除会话时同步清理 Redis STM / Milvus LTM / MySQL 兼容消息

设计约束：
- Service 层不直接写 SQL
- 所有数据库访问通过 ConversationRepository
- Session factory 通过构造函数注入，便于测试替换
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from contextlib import AbstractAsyncContextManager
from typing import Any, Protocol, TypeAlias, TypeVar

from app.chat.infrastructure.graph.memory_context import _DEFAULT_TENANT_ID
from app.chat.infrastructure.repository.conversation_repository import (
    ConversationRepository,
)
from app.shared.core.database import AsyncSessionLocal
from app.shared.core.logger import format_log_context, get_logger
from sqlalchemy.ext.asyncio import AsyncSession
from typing_extensions import TypedDict

logger = get_logger(__name__)
OperationResult = TypeVar("OperationResult")
_SessionFactory: TypeAlias = Callable[[], AbstractAsyncContextManager[AsyncSession]]


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
    """会话 CRUD 服务。"""

    def __init__(self, session_factory: _SessionFactory = AsyncSessionLocal):
        self._session_factory = session_factory

    async def create_conversation(self, user_id: int) -> int:
        """创建新会话并返回会话 id。"""
        return await run_db_operation(
            self._session_factory,
            logger,
            "create_conversation",
            _create_conversation,
            user_id,
            user_id=user_id,
        )

    async def get_user_conversations(self, user_id: int) -> list[ConversationSummary]:
        """获取用户的所有非默认标题会话。"""
        return await run_db_operation(
            self._session_factory,
            logger,
            "get_user_conversations",
            _get_user_conversations,
            user_id,
            user_id=user_id,
        )

    async def delete_conversation(self, conversation_id: int) -> None:
        """删除会话，并清理关联记忆。

        清理范围：
        1. MySQL conversations 元信息
        2. MySQL messages 兼容表（若存在）
        3. Redis STM 中该 session 的短期记忆
        4. Milvus LTM 中带 session_id 的长期记忆（软删除）
        """
        deleted = await run_db_operation(
            self._session_factory,
            logger,
            "delete_conversation",
            _delete_conversation,
            conversation_id,
            conversation_id=conversation_id,
        )
        await _clear_conversation_memories(
            user_id=str(deleted.user_id),
            session_id=str(conversation_id),
        )

    async def update_conversation_name(self, conversation_id: int, name: str) -> None:
        """更新会话标题。"""
        await run_db_operation(
            self._session_factory,
            logger,
            "update_conversation_name",
            _update_conversation_name,
            conversation_id,
            name,
            conversation_id=conversation_id,
            name=name,
        )


conversation_service = ConversationService()


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
    # Repository 返回通用 dict，这里收敛为会话摘要契约
    rows = await repo.list_by_user(user_id)
    return [
        ConversationSummary(
            id=int(row["id"]),
            title=str(row["title"]),
            created_at=str(row["created_at"]),
            status=str(row["status"]),
            dialogue_type=str(row["dialogue_type"]),
        )
        for row in rows
    ]


async def _delete_conversation(db: AsyncSession, conversation_id: int):
    """删除会话元信息及 MySQL 兼容消息，返回被删会话。"""
    repo = ConversationRepository(db)
    return await repo.delete(conversation_id)


async def _update_conversation_name(
    db: AsyncSession,
    conversation_id: int,
    name: str,
) -> None:
    """更新会话标题。"""
    repo = ConversationRepository(db)
    await repo.rename(conversation_id, name)


async def _clear_conversation_memories(*, user_id: str, session_id: str) -> None:
    """清理会话关联的 Redis STM 与 Milvus LTM。

    记忆清理失败只记录日志，不回滚已删除的 MySQL 会话元信息，
    避免“库删了但接口 500”导致前端重试反复失败。
    """
    tenant_id = _DEFAULT_TENANT_ID
    try:
        from app.platform.container import get_container

        container = await get_container()
        middleware = getattr(container, "memory_middleware", None)
        if middleware is None:
            logger.warning(
                "[conversation] 记忆中间件未初始化，跳过记忆清理 | user=%s session=%s",
                user_id,
                session_id,
            )
            return

        stm_deleted = 0
        ltm_deleted = 0
        redis_stm = getattr(middleware, "redis_stm", None)
        milvus_ltm = getattr(middleware, "milvus_ltm", None)

        if redis_stm is not None and hasattr(redis_stm, "clear_session"):
            stm_deleted = await redis_stm.clear_session(tenant_id, user_id, session_id)

        if milvus_ltm is not None and hasattr(milvus_ltm, "soft_delete_session_memories"):
            ltm_deleted = await milvus_ltm.soft_delete_session_memories(
                tenant_id,
                user_id,
                session_id,
            )

        logger.info(
            "[conversation] 会话记忆清理完成 | user=%s session=%s stm_keys=%s ltm=%s",
            user_id,
            session_id,
            stm_deleted,
            ltm_deleted,
        )
    except Exception as exc:
        logger.error(
            "[conversation] 会话记忆清理失败 | user=%s session=%s | %s",
            user_id,
            session_id,
            exc,
            exc_info=True,
        )


__all__ = ["ConversationService", "ConversationSummary", "conversation_service"]
