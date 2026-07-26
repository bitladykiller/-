"""深度健康检查 — 逐依赖探测。

这个模块负责：
- 对 MySQL / Redis / Milvus / Neo4j 各做一次带超时的轻量探测
- 汇总成运维可读的组件状态报告

与 `/health` 浅探针的分工：
浅探针只证明进程活着（容器编排每 15s 打一次，绝不能贵）；
深探针给人用——排障时一眼看出是哪个依赖挂了，而不是从
"LTM 检索降级"这类业务日志反推。
"""

from __future__ import annotations

import asyncio
from typing import Any

from app.shared.core.async_bridge import run_blocking
from app.shared.core.logger import get_logger

logger = get_logger(__name__)

#: 单依赖探测超时（秒）。深探针也不该被一个挂死的依赖拖住。
CHECK_TIMEOUT_SECONDS = 2.0

_STATUS_OK = "ok"
_STATUS_ERROR = "error"
_STATUS_DISABLED = "disabled"


async def _check_mysql() -> None:
    from app.shared.core.database import AsyncSessionLocal
    from sqlalchemy import text

    async with AsyncSessionLocal() as session:
        await session.execute(text("SELECT 1"))


async def _check_redis() -> None:
    from app.platform.container import get_container_if_initialized

    container = get_container_if_initialized()
    middleware = getattr(container, "memory_middleware", None) if container else None
    redis_client = getattr(getattr(middleware, "redis_stm", None), "redis", None)
    if redis_client is None:
        raise RuntimeError("Redis 客户端未初始化")
    await redis_client.ping()


async def _check_milvus() -> None:
    from app.platform.container import get_container_if_initialized

    container = get_container_if_initialized()
    middleware = getattr(container, "memory_middleware", None) if container else None
    ltm = getattr(middleware, "milvus_ltm", None)
    client = getattr(ltm, "milvus_client", None)
    if client is None:
        raise RuntimeError("Milvus 客户端未初始化")
    await run_blocking(client.list_collections)


async def _check_neo4j() -> str:
    """Neo4j 是可选增强，未配置时返回 disabled 而不是 error。"""
    from app.platform.container import get_container_if_initialized

    container = get_container_if_initialized()
    graph = getattr(container, "neo4j_graph", None) if container else None
    if graph is None:
        return _STATUS_DISABLED
    await run_blocking(graph.query, "RETURN 1")
    return _STATUS_OK


async def _probe(name: str, check) -> tuple[str, dict[str, Any]]:
    """执行单项探测：统一超时与异常收敛。"""
    try:
        outcome = await asyncio.wait_for(check(), timeout=CHECK_TIMEOUT_SECONDS)
        status = outcome if isinstance(outcome, str) else _STATUS_OK
        return name, {"status": status}
    except TimeoutError:
        return name, {"status": _STATUS_ERROR, "detail": f"timeout>{CHECK_TIMEOUT_SECONDS}s"}
    except Exception as exc:
        return name, {"status": _STATUS_ERROR, "detail": str(exc)[:200]}


async def run_deep_health_check() -> dict[str, Any]:
    """并发探测全部依赖，返回汇总报告。

    Returns:
        {"status": "ok"|"degraded", "components": {name: {...}}}
        Neo4j disabled 不影响整体 ok（可选依赖）。
    """
    results = dict(
        await asyncio.gather(
            _probe("mysql", _check_mysql),
            _probe("redis", _check_redis),
            _probe("milvus", _check_milvus),
            _probe("neo4j", _check_neo4j),
        )
    )
    core_ok = all(
        results[name]["status"] == _STATUS_OK for name in ("mysql", "redis", "milvus")
    )
    optional_ok = results["neo4j"]["status"] in (_STATUS_OK, _STATUS_DISABLED)
    return {
        "status": _STATUS_OK if core_ok and optional_ok else "degraded",
        "components": results,
    }


__all__ = ["run_deep_health_check"]
