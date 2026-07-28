"""本地开发用的数据库重置脚本。

注意：
- 这个脚本会先 `drop_all` 再 `create_all`
- 适合本地开发重置，不适合放进容器启动流程
"""
from __future__ import annotations

import asyncio

from app.scripts.db_script_support import prepare_db_models, run_metadata_operations


async def reset_all_tables() -> None:
    """删除并重建当前项目实际使用的表结构。"""
    await run_metadata_operations("drop_all", "create_all")


prepare_db_models()


if __name__ == "__main__":
    asyncio.run(reset_all_tables())
