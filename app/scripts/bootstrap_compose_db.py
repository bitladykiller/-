"""Docker Compose 启动时的数据库建表脚本。

只执行 `create_all`，不会删除已有表结构或数据。
这个模块只供 Docker Compose 启动链路内部调用，不保留独立脚本入口。
"""

import importlib


async def create_all_tables() -> None:
    """创建当前项目实际使用的表结构。"""
    from app.shared.core.database import Base, engine

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)


# 导入模型以触发 SQLAlchemy metadata 注册。
importlib.import_module("app.user.infrastructure.models.user")
importlib.import_module("app.user.infrastructure.models.conversation")
