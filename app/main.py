"""FastAPI 应用入口。

这个文件负责：
- 声明应用入口常量
- 构造 FastAPI app
- 装配生命周期、路由、中间件和静态资源
- 通过 AppContainer 管理所有应用级依赖的生命周期
"""

from __future__ import annotations

import time
from collections.abc import Awaitable, Callable
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Protocol

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.routing import APIRouter
from fastapi.staticfiles import StaticFiles

from app.api import api_router
from app.shared.core.logger import get_logger, setup_logging
from app.platform.container import AppContainer, set_container, reset_container

STATIC_DIR = Path(__file__).parent / "static" / "dist"
APP_TITLE = "AssistGen REST API"
HEALTH_STATUS = "ok"
OPEN_CORS_ORIGINS = ["*"]
OPEN_CORS_METHODS = ["*"]
OPEN_CORS_HEADERS = ["*"]

setup_logging()
logger = get_logger(__name__)


class InfoLogger(Protocol):
    """入口装配 helper 所需的最小日志接口。"""

    def info(self, msg: str, *args: object, **kwargs: object) -> object: ...


async def warm_up_runtime_resources(runtime_logger: InfoLogger) -> None:
    """预热懒加载资源，避免首请求承担初始化延迟。

    通过 AppContainer 统一管理预热逻辑，不再直接调用 memory_bridge.runtime。
    """
    from app.platform.container import get_container

    container = await get_container()
    runtime_logger.info("预热 MemoryMiddleware...")
    await container.warm_up()


async def close_runtime_resources() -> None:
    """释放应用级运行时资源。

    通过 AppContainer.close() 统一释放所有外部连接。
    """
    await reset_container()


def build_lifespan(
    runtime_logger: InfoLogger,
    *,
    warm_up: Callable[[InfoLogger], Awaitable[None]] = warm_up_runtime_resources,
    close_runtime: Callable[[], Awaitable[None]] = close_runtime_resources,
) -> Callable[[FastAPI], object]:
    """构造 FastAPI lifespan 处理器。"""

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        # 在 lifespan 启动阶段创建 AppContainer
        from app.shared.core.config import settings

        container = await AppContainer.build(settings)
        await set_container(container)

        await warm_up(runtime_logger)
        runtime_logger.info("启动完成")
        try:
            yield
        finally:
            runtime_logger.info("关闭连接...")
            await close_runtime()
            runtime_logger.info("关闭完成")

    return lifespan


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
    runtime_logger: InfoLogger,
    *,
    clock: Callable[[], float] = time.time,
) -> None:
    """注册应用级请求日志中间件。"""

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


def register_routes(
    app: FastAPI,
    *,
    app_api_router: APIRouter,
    health_status: str,
) -> None:
    """注册 API 路由和内建健康检查路由。"""

    async def health_check() -> dict[str, str]:
        return {"status": health_status}

    app.include_router(app_api_router, prefix="/api")
    app.add_api_route("/health", health_check, methods=["GET"])


def register_static_files(
    app: FastAPI,
    *,
    static_dir: Path,
    runtime_logger: InfoLogger,
) -> None:
    """在静态目录存在时挂载前端资源。"""
    if not static_dir.is_dir():
        runtime_logger.info("静态资源目录不存在，跳过挂载: %s", static_dir)
        return

    app.mount("/", StaticFiles(directory=str(static_dir), html=True), name="static")


def create_app(
    *,
    runtime_logger: InfoLogger = logger,
    app_api_router: APIRouter = api_router,
    static_dir: Path = STATIC_DIR,
    health_status: str = HEALTH_STATUS,
) -> FastAPI:
    """构造并装配当前服务的 FastAPI app。"""
    app = FastAPI(
        title=APP_TITLE,
        lifespan=build_lifespan(runtime_logger),
    )
    configure_cors(
        app,
        allow_origins=OPEN_CORS_ORIGINS,
        allow_methods=OPEN_CORS_METHODS,
        allow_headers=OPEN_CORS_HEADERS,
    )
    register_middleware(app, runtime_logger)
    register_routes(
        app,
        app_api_router=app_api_router,
        health_status=health_status,
    )
    register_static_files(
        app,
        static_dir=static_dir,
        runtime_logger=runtime_logger,
    )
    return app


app = create_app()


__all__ = [
    "APP_TITLE",
    "HEALTH_STATUS",
    "InfoLogger",
    "OPEN_CORS_HEADERS",
    "OPEN_CORS_METHODS",
    "OPEN_CORS_ORIGINS",
    "STATIC_DIR",
    "app",
    "build_lifespan",
    "close_runtime_resources",
    "configure_cors",
    "create_app",
    "register_middleware",
    "register_routes",
    "register_static_files",
    "warm_up_runtime_resources",
]
