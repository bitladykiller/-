"""本地开发启动入口。"""

from __future__ import annotations

import uvicorn

HOST = "0.0.0.0"
PORT = 8000
APP_IMPORT_PATH = "app.main:app"


if __name__ == "__main__":
    uvicorn.run(
        APP_IMPORT_PATH,
        host=HOST,
        port=PORT,
        access_log=False,
        log_level="error",
        reload=True,
    )
