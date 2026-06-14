"""Docker Compose 启动时的数据库建表脚本。

和 `init_db.py` 的差异：
- 这里只执行 `create_all`
- 不执行 `drop_all`

这样容器重启时不会把 MySQL 里的会话和画像数据清空。
"""
from __future__ import annotations

import asyncio
import importlib


def _prepare_db_script_environment() -> None:
    """导入模型模块，触发 metadata 注册。"""
    importlib.import_module("app.user.infrastructure.models.user")
    importlib.import_module("app.user.infrastructure.models.conversation")


async def create_all_tables() -> None:
    """创建当前项目实际使用的表结构。"""
    from app.shared.core.database import Base, engine

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)


_prepare_db_script_environment()


if __name__ == "__main__":
    asyncio.run(create_all_tables())
