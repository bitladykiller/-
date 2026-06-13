"""Docker Compose 启动时的数据库建表脚本。

和 `init_db.py` 的差异：
- 这里只执行 `create_all`
- 不执行 `drop_all`

这样容器重启时不会把 MySQL 里的会话和画像数据清空。
"""
from __future__ import annotations

import sys
from pathlib import Path

try:
    from .db_script_support import (
        create_all_tables,
        prepare_db_script_environment,
        run_async_entrypoint,
    )
except ImportError:
    script_dir = str(Path(__file__).resolve().parent)
    if script_dir not in sys.path:
        sys.path.append(script_dir)
    from db_script_support import (
        create_all_tables,
        prepare_db_script_environment,
        run_async_entrypoint,
    )

prepare_db_script_environment()


async def ensure_tables() -> None:
    """创建当前项目实际使用的表结构。"""
    await create_all_tables()


def main() -> None:
    """Compose 启动入口。"""
    run_async_entrypoint(ensure_tables)


if __name__ == "__main__":
    main()
