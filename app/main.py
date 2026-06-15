"""FastAPI 应用工厂。

这个文件只负责：
- 构造 FastAPI app
- 装配生命周期、路由、中间件和静态资源

它不再暴露可直接启动服务的模块级单例，
服务启动只能走 Docker Compose 的内部 launcher。
"""

import time
from contextlib import asynccontextmanager
from pathlib import Path

from app.api.conversations import router as conversations_router
from app.api.langgraph import router as langgraph_router
from app.api.upload import router as upload_router
from app.shared.core.logger import get_logger, setup_logging
from fastapi import APIRouter, FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

STATIC_DIR = Path(__file__).parent / "static" / "dist"

setup_logging()
logger = get_logger(__name__)
api_router = APIRouter()
api_router.include_router(conversations_router)
api_router.include_router(upload_router)
api_router.include_router(langgraph_router)


@asynccontextmanager
async def lifespan(_app: FastAPI):
    """FastAPI lifespan 处理器。"""

    from app.chat.infrastructure.memory_bridge.runtime import get_memory_middleware

    logger.info("预热 MemoryMiddleware...")
    await get_memory_middleware()
    logger.info("启动完成")
    try:
        yield
    finally:
        from app.chat.application.task_queue import close_task_manager
        from app.chat.infrastructure.memory_bridge.runtime import (
            close_memory_middleware,
        )

        logger.info("关闭连接...")
        await close_memory_middleware()
        await close_task_manager()
        logger.info("关闭完成")


def create_app() -> FastAPI:
    """构造并装配当前服务的 FastAPI app。"""
    app = FastAPI(
        title="AssistGen REST API",
        lifespan=lifespan,
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @app.middleware("http")
    async def log_requests(request: Request, call_next):
        start_time = time.time()
        response = await call_next(request)
        elapsed = (time.time() - start_time) * 1000
        logger.info(
            "%s %s → %s (%.1fms)",
            request.method,
            request.url.path,
            response.status_code,
            elapsed,
        )
        return response

    async def health_check() -> dict[str, str]:
        return {"status": "ok"}

    app.include_router(api_router, prefix="/api")
    app.add_api_route("/health", health_check, methods=["GET"])

    if not STATIC_DIR.is_dir():
        logger.info("静态资源目录不存在，跳过挂载: %s", STATIC_DIR)
        return app

    app.mount("/", StaticFiles(directory=str(STATIC_DIR), html=True), name="static")
    return app
