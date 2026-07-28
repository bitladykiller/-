"""Docker Compose 启动时的数据库建表脚本。

和 `init_db.py` 的差异：
- 这里只执行 `create_all`
- 不执行 `drop_all`

这样容器重启时不会把 MySQL 里的会话和画像数据清空。
"""
from __future__ import annotations

import asyncio

from app.scripts.db_script_support import prepare_db_models, run_metadata_operations


async def create_all_tables() -> None:
    """创建当前项目实际使用的表结构。"""
    await run_metadata_operations("create_all")


prepare_db_models()


if __name__ == "__main__":
    asyncio.run(create_all_tables())
