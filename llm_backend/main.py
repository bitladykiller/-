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
    from app.lg_agent.lg_builder import _get_memory_middleware
    _get_memory_middleware()
    logger.info("启动完成")


@app.on_event("shutdown")
async def shutdown():
    """释放连接。"""
    logger.info("关闭连接...")
    from app.lg_agent.lg_builder import _memory_middleware_instance
    if _memory_middleware_instance:
        try:
            await _memory_middleware_instance.redis_stm.redis.close()
        except Exception:
            pass
    logger.info("关闭完成")


app.include_router(api_router, prefix="/api")


@app.get("/health")
async def health_check():
    return {"status": "ok"}


STATIC_DIR = Path(__file__).parent / "static" / "dist"
app.mount("/", StaticFiles(directory=str(STATIC_DIR), html=True), name="static")
