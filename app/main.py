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

from fastapi import FastAPI

from app.main_http_support import (
    configure_cors,
    register_middleware,
    register_routes,
    register_static_files,
)
from app.main_runtime_support import build_lifespan

STATIC_DIR = Path(__file__).parent / "static" / "dist"
APP_TITLE = "AssistGen REST API"
HEALTH_STATUS = "ok"
OPEN_CORS_ORIGINS = ["*"]
OPEN_CORS_METHODS = ["*"]
OPEN_CORS_HEADERS = ["*"]

from app.api import api_router
from app.shared.core.logger import get_logger, setup_logging

setup_logging()
logger = get_logger(__name__)

app = FastAPI(
    title=APP_TITLE,
    lifespan=build_lifespan(logger),
)
configure_cors(
    app,
    allow_origins=OPEN_CORS_ORIGINS,
    allow_methods=OPEN_CORS_METHODS,
    allow_headers=OPEN_CORS_HEADERS,
)
register_middleware(app, logger)
register_routes(
    app,
    api_router=api_router,
    health_status=HEALTH_STATUS,
)
register_static_files(app, STATIC_DIR)
