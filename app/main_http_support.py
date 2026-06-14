"""应用入口的 HTTP 装配 helper。

这个模块只负责：
- 请求日志中间件构造
- 健康检查 handler 构造
- CORS / 路由 / 静态资源挂载
"""

from __future__ import annotations

import time
from collections.abc import Awaitable, Callable
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.routing import APIRouter
from fastapi.staticfiles import StaticFiles

from app.main_runtime_support import InfoLogger

def configure_cors(
    app: FastAPI,
    *,
    allow_origins: list[str],
    allow_methods: list[str],
    allow_headers: list[str],
) -> None:
    """注册默认开放的 CORS 配置。"""
    app.add_middleware(
        CORSMiddleware,
        allow_origins=allow_origins,
        allow_credentials=True,
        allow_methods=allow_methods,
        allow_headers=allow_headers,
    )


def register_middleware(
    app: FastAPI,
    logger: InfoLogger,
    *,
    clock: Callable[[], float] = time.time,
) -> None:
    """注册应用级中间件。"""

    @app.middleware("http")
    async def log_requests(request: Request, call_next):
        start_time = clock()
        response = await call_next(request)
        elapsed = (clock() - start_time) * 1000
        logger.info(
            "%s %s → %s (%.1fms)",
            request.method,
            request.url.path,
            response.status_code,
            elapsed,
        )
        return response


def register_routes(
    app: FastAPI,
    *,
    api_router: APIRouter,
    health_status: str,
) -> None:
    """注册 API 路由和内建健康检查路由。"""

    async def health_check() -> dict[str, str]:
        return {"status": health_status}

    app.include_router(api_router, prefix="/api")
    app.add_api_route("/health", health_check, methods=["GET"])


def register_static_files(app: FastAPI, static_dir: Path) -> None:
    """挂载前端静态资源目录。"""
    app.mount("/", StaticFiles(directory=str(static_dir), html=True), name="static")


__all__ = [
    "configure_cors",
    "register_middleware",
    "register_routes",
    "register_static_files",
]
