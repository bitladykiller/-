"""本地开发用的数据库重置脚本。

注意：
- 这个脚本会先 `drop_all` 再 `create_all`
- 适合本地开发重置，不适合放进容器启动流程
"""
from __future__ import annotations

import sys
from pathlib import Path

try:
    from .db_script_support import (
        prepare_db_script_environment,
        reset_all_tables,
        run_async_entrypoint,
    )
except ImportError:
    script_dir = str(Path(__file__).resolve().parent)
    if script_dir not in sys.path:
        sys.path.append(script_dir)
    from db_script_support import (
        prepare_db_script_environment,
        reset_all_tables,
        run_async_entrypoint,
    )

prepare_db_script_environment()


async def reset_db() -> None:
    """重建当前项目实际使用的表结构。"""
    await reset_all_tables()


def main() -> None:
    """脚本入口。"""
    run_async_entrypoint(reset_db)


if __name__ == "__main__":
    main()
