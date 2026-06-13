"""本地开发启动入口 support helper。

职责：
- 负责开发模式启动前的工作目录切换
- 负责 uvicorn 启动参数的统一构造
- 负责把“切目录 + 调起 uvicorn”串成可测试的入口 helper

边界：
- 不负责 FastAPI 应用工厂
- 不负责业务配置加载
"""

from __future__ import annotations

import os
from collections.abc import Callable
from pathlib import Path
from typing import Any


def switch_to_backend_dir(
    backend_dir: Path,
    *,
    chdir: Callable[[str], object] = os.chdir,
) -> None:
    """切到 `llm_backend/` 目录，保持相对路径行为稳定。"""
    chdir(str(backend_dir))


def build_uvicorn_run_kwargs(
    *,
    host: str,
    port: int,
    reload: bool = True,
) -> dict[str, Any]:
    """统一构造本地开发模式下的 uvicorn 参数。"""
    return {
        "host": host,
        "port": port,
        "access_log": False,
        "log_level": "error",
        "reload": reload,
    }


def start_dev_server(
    *,
    app_import_path: str,
    backend_dir: Path,
    host: str,
    port: int,
    uvicorn_run: Callable[..., object],
    chdir: Callable[[str], object] = os.chdir,
) -> None:
    """以开发模式启动 uvicorn。"""
    switch_to_backend_dir(backend_dir, chdir=chdir)
    uvicorn_run(
        app_import_path,
        **build_uvicorn_run_kwargs(host=host, port=port),
    )
