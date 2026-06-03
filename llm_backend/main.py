import time

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from pathlib import Path

from app.core.logger import setup_logging, get_logger
from app.api import api_router

setup_logging()
logger = get_logger(__name__)


app = FastAPI(title="AssistGen REST API")


app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.middleware("http")
async def log_requests(request: Request, call_next):
    start = time.time()
    response = await call_next(request)
    elapsed = (time.time() - start) * 1000
    logger.info(f"{request.method} {request.url.path} → {response.status_code} ({elapsed:.1f}ms)")
    return response


@app.on_event("startup")
async def startup():
    """预热连接 — 避免首请求承担初始化延迟。"""
    logger.info("预热 MemoryMiddleware...")
    from app.lg_agent.lg_context import _get_memory_middleware
    await _get_memory_middleware()
    logger.info("启动完成")


@app.on_event("shutdown")
async def shutdown():
    """释放所有连接：MemoryMiddleware + TaskManager。"""
    logger.info("关闭连接...")
    from app.lg_agent.lg_context import close_memory_middleware
    from app.services.task_queue import close_task_manager
    await close_memory_middleware()
    await close_task_manager()
    logger.info("关闭完成")


app.include_router(api_router, prefix="/api")


@app.get("/health")
async def health_check():
    return {"status": "ok"}


STATIC_DIR = Path(__file__).parent / "static" / "dist"
app.mount("/", StaticFiles(directory=str(STATIC_DIR), html=True), name="static")
