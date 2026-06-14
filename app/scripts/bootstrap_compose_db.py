"""Docker Compose 启动时的数据库建表脚本。

和 `init_db.py` 的差异：
- 这里只执行 `create_all`
- 不执行 `drop_all`

这样容器重启时不会把 MySQL 里的会话和画像数据清空。
"""
from __future__ import annotations

from .db_script_support import (
    create_all_tables,
    prepare_db_script_environment,
    run_async_entrypoint,
)

prepare_db_script_environment()


if __name__ == "__main__":
    run_async_entrypoint(create_all_tables)
