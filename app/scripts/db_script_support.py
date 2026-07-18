"""数据库维护脚本共享 helper。"""
from __future__ import annotations

import importlib


def prepare_db_models() -> None:
    """导入模型以触发 SQLAlchemy metadata 注册。"""
    importlib.import_module("app.user.infrastructure.models.user")
    importlib.import_module("app.chat.infrastructure.models.conversation")


async def run_metadata_operations(*operation_names: str) -> None:
    """按顺序执行 metadata 操作。"""
    from app.shared.core.database import Base, engine

    async with engine.begin() as conn:
        for operation_name in operation_names:
            await conn.run_sync(getattr(Base.metadata, operation_name))
