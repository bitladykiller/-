"""本地开发用的数据库重置脚本。

注意：
- 这个脚本会先 `drop_all` 再 `create_all`
- 适合本地开发重置，不适合放进容器启动流程
- 这里只保留内部维护函数，不再提供独立脚本入口
"""
from __future__ import annotations

import importlib


async def reset_all_tables() -> None:
    """删除并重建当前项目实际使用的表结构。"""
    from app.shared.core.database import Base, engine

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)


# 导入模型以触发 SQLAlchemy metadata 注册。
importlib.import_module("app.user.infrastructure.models.user")
importlib.import_module("app.user.infrastructure.models.conversation")
