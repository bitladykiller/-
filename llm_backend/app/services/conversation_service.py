"""会话服务。

聚焦 `conversations` 表的基本 CRUD，避免在 API 层直接写数据库访问逻辑。
本文件当前只保留对外服务入口与动作编排；
数据库 session 生命周期、单条会话查找、CRUD helper、
会话摘要序列化、默认对象构造、列表查询语句已下沉到 `conversation_support.py`。
"""
from __future__ import annotations

from typing import Awaitable, Callable, TypeVar

from app.core.database import AsyncSessionLocal
from app.core.logger import get_logger
from app.services.conversation_support import (
    ConversationSummary,
    create_conversation_record,
    delete_conversation_record,
    fetch_user_conversations,
    get_conversation_or_raise,
    rename_conversation_record,
    run_db_operation,
)

logger = get_logger(__name__)
OperationResult = TypeVar("OperationResult")
_get_conversation_or_raise = get_conversation_or_raise
_create_conversation_record = create_conversation_record
_fetch_user_conversations = fetch_user_conversations
_delete_conversation_record = delete_conversation_record
_rename_conversation_record = rename_conversation_record


async def _run_db_operation(
    action_name: str,
    operation: Callable[..., Awaitable[OperationResult]],
    *operation_args: object,
    **context: object,
) -> OperationResult:
    """兼容旧调用形态，转发到 support 中的通用 session helper。"""
    return await run_db_operation(
        AsyncSessionLocal,
        logger,
        action_name,
        operation,
        *operation_args,
        **context,
    )


class ConversationService:
    """会话 CRUD 服务。"""

    @staticmethod
    async def create_conversation(user_id: int) -> int:
        """创建新会话并返回会话 id。"""
        return await _run_db_operation(
            "create_conversation",
            _create_conversation_record,
            user_id,
            user_id=user_id,
        )

    @staticmethod
    async def get_user_conversations(user_id: int) -> list[ConversationSummary]:
        """获取用户的所有非默认标题会话。"""
        return await _run_db_operation(
            "get_user_conversations",
            _fetch_user_conversations,
            user_id,
            user_id=user_id,
        )

    @staticmethod
    async def delete_conversation(conversation_id: int) -> None:
        """删除会话。"""
        await _run_db_operation(
            "delete_conversation",
            _delete_conversation_record,
            conversation_id,
            conversation_id=conversation_id,
        )

    @staticmethod
    async def update_conversation_name(conversation_id: int, name: str) -> None:
        """更新会话标题。"""
        await _run_db_operation(
            "update_conversation_name",
            _rename_conversation_record,
            conversation_id,
            name,
            conversation_id=conversation_id,
            name=name,
        )
