"""本地开发用的数据库重置脚本。

注意：
- 这个脚本会先 `drop_all` 再 `create_all`
- 适合本地开发重置，不适合放进容器启动流程
"""
from __future__ import annotations

from .db_script_support import (
    prepare_db_script_environment,
    reset_all_tables,
    run_async_entrypoint,
)

prepare_db_script_environment()


if __name__ == "__main__":
    run_async_entrypoint(reset_all_tables)
