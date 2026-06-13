"""数据库维护脚本共享 helper。

这些脚本通常以“直接执行文件”的方式运行，
因此需要显式补齐导入路径，并提前导入模型模块完成 metadata 注册。
同时把“建表 / 重置表 / 运行异步入口”这类重复样板收口到这里。
"""

from __future__ import annotations

import asyncio
import importlib
import sys
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Literal

BACKEND_ROOT = Path(__file__).resolve().parent.parent
AsyncEntrypoint = Callable[[], Awaitable[None]]


def ensure_backend_root_on_path() -> None:
    """把 `llm_backend/` 加入 `sys.path`，保证脚本可直接导入 `app.*`。"""
    backend_root = str(BACKEND_ROOT)
    if backend_root not in sys.path:
        sys.path.append(backend_root)


def load_registered_models() -> None:
    """导入模型模块，触发 SQLAlchemy metadata 注册。"""
    importlib.import_module("app.models")


def prepare_db_script_environment() -> None:
    """为数据库维护脚本补齐导入路径和模型注册。"""
    ensure_backend_root_on_path()
    load_registered_models()


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
    from app.core.database import Base, engine

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
