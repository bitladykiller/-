"""本地开发启动入口。"""

from __future__ import annotations

from pathlib import Path

import uvicorn

from run_support import start_dev_server

HOST = "0.0.0.0"
PORT = 8000
APP_IMPORT_PATH = "main:app"
BACKEND_DIR = Path(__file__).parent


def start_server() -> None:
    """以开发模式启动 uvicorn。"""
    start_dev_server(
        app_import_path=APP_IMPORT_PATH,
        backend_dir=BACKEND_DIR,
        host=HOST,
        port=PORT,
        uvicorn_run=uvicorn.run,
    )


if __name__ == "__main__":
    start_server()
