"""FastAPI 应用入口 support helper。

职责：
- 负责应用工厂相关的可复用 helper
- 负责请求日志、健康检查、静态资源和路由注册样板
- 组合运行时生命周期 helper，创建最终 FastAPI app

边界：
- 不负责具体业务服务实现
- 不负责 LangGraph 节点逻辑
- startup / shutdown 资源管理细节已下沉到 `main_runtime_support.py`
- 不直接持有全局 FastAPI app 单例
"""

from __future__ import annotations

import sys
import time
from collections.abc import Awaitable, Callable
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.routing import APIRouter
from fastapi.staticfiles import StaticFiles
from main_runtime_support import (
    InfoLogger,
    build_lifespan,
    close_runtime_resources,
    warm_up_runtime_resources,
)


def ensure_repo_root_on_path(repo_root: Path) -> None:
    """保证从不同工作目录启动时都能导入仓库内兄弟包。"""
    if str(repo_root) not in sys.path:
        sys.path.insert(0, str(repo_root))


def elapsed_ms(start_time: float, end_time: float) -> float:
    """把两个 `time.time()` 时间点差值统一换算成毫秒。"""
    return (end_time - start_time) * 1000


def build_request_logger(
    logger: InfoLogger,
    *,
    clock: Callable[[], float] = time.time,
) -> Callable[[Request, Callable[[Request], Awaitable[object]]], Awaitable[object]]:
    """构造应用级请求日志中间件。"""

    async def log_requests(request: Request, call_next):
        start_time = clock()
        response = await call_next(request)
        elapsed = elapsed_ms(start_time, clock())
        logger.info(
            "%s %s → %s (%.1fms)",
            request.method,
            request.url.path,
            response.status_code,
            elapsed,
        )
        return response

    return log_requests


def build_health_check(health_status: str) -> Callable[[], Awaitable[dict[str, str]]]:
    """构造健康检查处理器。"""

    async def health_check() -> dict[str, str]:
        return {"status": health_status}

    return health_check


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
    app.middleware("http")(build_request_logger(logger, clock=clock))


def register_routes(
    app: FastAPI,
    *,
    api_router: APIRouter,
    health_check: Callable[[], Awaitable[dict[str, str]]],
) -> None:
    """注册 API 路由和内建健康检查路由。"""
    app.include_router(api_router, prefix="/api")
    app.add_api_route("/health", health_check, methods=["GET"])


def register_static_files(app: FastAPI, static_dir: Path) -> None:
    """挂载前端静态资源目录。"""
    app.mount("/", StaticFiles(directory=str(static_dir), html=True), name="static")


def create_app(
    *,
    api_router: APIRouter,
    logger: InfoLogger,
    app_title: str,
    static_dir: Path,
    health_status: str,
    allow_origins: list[str],
    allow_methods: list[str],
    allow_headers: list[str],
    clock: Callable[[], float] = time.time,
    warm_up: Callable[[InfoLogger], Awaitable[None]] = warm_up_runtime_resources,
    close_runtime: Callable[[], Awaitable[None]] = close_runtime_resources,
) -> FastAPI:
    """创建并完成初始化配置的 FastAPI 应用。"""
    app = FastAPI(
        title=app_title,
        lifespan=build_lifespan(
            logger,
            warm_up=warm_up,
            close_runtime=close_runtime,
        ),
    )
    configure_cors(
        app,
        allow_origins=allow_origins,
        allow_methods=allow_methods,
        allow_headers=allow_headers,
    )
    register_middleware(app, logger, clock=clock)
    register_routes(
        app,
        api_router=api_router,
        health_check=build_health_check(health_status),
    )
    register_static_files(app, static_dir)
    return app
