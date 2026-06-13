"""FastAPI 应用入口。

这个文件负责：
- 声明应用入口常量
- 调用应用工厂创建 FastAPI app
- 保持入口导入路径稳定

这个文件不负责：
- 中间件实现细节
- 生命周期回调实现细节
- 路由或静态资源注册细节
"""

from __future__ import annotations

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
STATIC_DIR = Path(__file__).parent / "static" / "dist"
APP_TITLE = "AssistGen REST API"
HEALTH_STATUS = "ok"
OPEN_CORS_ORIGINS = ["*"]
OPEN_CORS_METHODS = ["*"]
OPEN_CORS_HEADERS = ["*"]


from main_support import create_app, ensure_repo_root_on_path

ensure_repo_root_on_path(REPO_ROOT)

from app.api import api_router
from app.core.logger import get_logger, setup_logging

setup_logging()
logger = get_logger(__name__)

app = create_app(
    api_router=api_router,
    logger=logger,
    app_title=APP_TITLE,
    static_dir=STATIC_DIR,
    health_status=HEALTH_STATUS,
    allow_origins=OPEN_CORS_ORIGINS,
    allow_methods=OPEN_CORS_METHODS,
    allow_headers=OPEN_CORS_HEADERS,
)
