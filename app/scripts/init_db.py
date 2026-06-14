"""本地开发用的数据库重置脚本。

注意：
- 这个脚本会先 `drop_all` 再 `create_all`
- 适合本地开发重置，不适合放进容器启动流程
"""
from __future__ import annotations

import asyncio
import importlib


def _prepare_db_script_environment() -> None:
    """导入模型模块，触发 metadata 注册。"""
    importlib.import_module("app.user.infrastructure.models.user")
    importlib.import_module("app.user.infrastructure.models.conversation")


async def reset_all_tables() -> None:
    """删除并重建当前项目实际使用的表结构。"""
    from app.shared.core.database import Base, engine

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)


_prepare_db_script_environment()


if __name__ == "__main__":
    asyncio.run(reset_all_tables())
