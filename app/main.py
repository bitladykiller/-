"""FastAPI 应用入口。

这个文件负责：
- 声明应用入口常量
- 构造 FastAPI app
- 装配生命周期、路由、中间件和静态资源
"""

from __future__ import annotations

import time
from collections.abc import Awaitable, Callable
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.routing import APIRouter
from fastapi.staticfiles import StaticFiles

from app.api import api_router
from app.shared.core.logger import get_logger, setup_logging

STATIC_DIR = Path(__file__).parent / "static" / "dist"
APP_TITLE = "AssistGen REST API"
HEALTH_STATUS = "ok"
OPEN_CORS_ORIGINS = ["*"]
OPEN_CORS_METHODS = ["*"]
OPEN_CORS_HEADERS = ["*"]

setup_logging()
logger = get_logger(__name__)


def build_lifespan(
    runtime_logger: Any,
    *,
    warm_up: Callable[[Any], Awaitable[None]] | None = None,
    close_runtime: Callable[[], Awaitable[None]] | None = None,
) -> Callable[[FastAPI], object]:
    """构造 FastAPI lifespan 处理器。"""

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        if warm_up is None:
            from app.chat.infrastructure.memory_bridge.runtime import get_memory_middleware

            runtime_logger.info("预热 MemoryMiddleware...")
            await get_memory_middleware()
        else:
            await warm_up(runtime_logger)
        runtime_logger.info("启动完成")
        try:
            yield
        finally:
            runtime_logger.info("关闭连接...")
            if close_runtime is None:
                from app.chat.application.task_queue import close_task_manager
                from app.chat.infrastructure.memory_bridge.runtime import (
                    close_memory_middleware,
                )

                await close_memory_middleware()
                await close_task_manager()
            else:
                await close_runtime()
            runtime_logger.info("关闭完成")

    return lifespan


def create_app(
    *,
    runtime_logger: Any = logger,
    app_api_router: APIRouter = api_router,
    static_dir: Path = STATIC_DIR,
    health_status: str = HEALTH_STATUS,
    clock: Callable[[], float] = time.time,
) -> FastAPI:
    """构造并装配当前服务的 FastAPI app。"""
    app = FastAPI(
        title=APP_TITLE,
        lifespan=build_lifespan(runtime_logger),
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=OPEN_CORS_ORIGINS,
        allow_credentials=True,
        allow_methods=OPEN_CORS_METHODS,
        allow_headers=OPEN_CORS_HEADERS,
    )

    @app.middleware("http")
    async def log_requests(request: Request, call_next):
        start_time = clock()
        response = await call_next(request)
        elapsed = (clock() - start_time) * 1000
        runtime_logger.info(
            "%s %s → %s (%.1fms)",
            request.method,
            request.url.path,
            response.status_code,
            elapsed,
        )
        return response

    async def health_check() -> dict[str, str]:
        return {"status": health_status}

    app.include_router(app_api_router, prefix="/api")
    app.add_api_route("/health", health_check, methods=["GET"])

    if not static_dir.is_dir():
        runtime_logger.info("静态资源目录不存在，跳过挂载: %s", static_dir)
        return app

    app.mount("/", StaticFiles(directory=str(static_dir), html=True), name="static")
    return app


app = create_app()


__all__ = [
    "APP_TITLE",
    "HEALTH_STATUS",
    "OPEN_CORS_HEADERS",
    "OPEN_CORS_METHODS",
    "OPEN_CORS_ORIGINS",
    "STATIC_DIR",
    "app",
    "build_lifespan",
    "create_app",
]
