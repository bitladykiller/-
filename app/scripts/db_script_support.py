"""数据库维护脚本共享 helper。

这个模块收口“建表 / 重置表 / 运行异步入口”这类重复样板，
并负责提前导入模型模块完成 metadata 注册。
"""

from __future__ import annotations

import asyncio
import importlib
from collections.abc import Awaitable, Callable
from typing import Literal

AsyncEntrypoint = Callable[[], Awaitable[None]]


def prepare_db_script_environment() -> None:
    """为数据库维护脚本导入模型，触发 metadata 注册。"""
    importlib.import_module("app.user.infrastructure.models.user")
    importlib.import_module("app.user.infrastructure.models.conversation")


def run_async_entrypoint(entrypoint: AsyncEntrypoint) -> None:
    """统一运行脚本的异步入口函数。"""
    asyncio.run(entrypoint())


async def create_all_tables() -> None:
    """创建当前项目实际使用的表结构。"""
    await _run_metadata_operation("create_all")


async def reset_all_tables() -> None:
    """删除并重建当前项目实际使用的表结构。"""
    await _run_metadata_operation("drop_and_create_all")


async def _run_metadata_operation(
    operation_name: Literal["create_all", "drop_and_create_all"],
) -> None:
    """统一执行 metadata 级别的建表/删表操作。"""
    from app.shared.core.database import Base, engine

    async with engine.begin() as conn:
        if operation_name == "create_all":
            await conn.run_sync(Base.metadata.create_all)
            return

        if operation_name == "drop_and_create_all":
            await conn.run_sync(Base.metadata.drop_all)
            await conn.run_sync(Base.metadata.create_all)
            return

        raise ValueError(f"Unsupported metadata operation: {operation_name}")


__all__ = [
    "prepare_db_script_environment",
    "run_async_entrypoint",
    "create_all_tables",
    "reset_all_tables",
]
