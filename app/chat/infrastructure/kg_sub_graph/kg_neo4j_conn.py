"""Neo4jGraph 连接缓存层。

职责：
- 通过 AppContainer 代理 Neo4jGraph 获取
- 所有模块通过此入口获取 KG 连接，不再持有模块级全局状态
"""

from __future__ import annotations

import logging
import time
from typing import Any

from langchain_neo4j import Neo4jGraph

from app.shared.core.config import settings
from app.shared.core.logger import get_logger

logging.getLogger("neo4j").setLevel(logging.ERROR)
logging.getLogger("langchain_neo4j").setLevel(logging.ERROR)
logging.getLogger("neo4j.io").setLevel(logging.ERROR)
logging.getLogger("neo4j.bolt").setLevel(logging.ERROR)

logger = get_logger(__name__)

HEALTH_CHECK_INTERVAL: int = 30
_cached_graph: Neo4jGraph | None = None
_last_health_check_ts: float = 0.0


def get_neo4j_graph() -> Neo4jGraph | None:
    """返回缓存的 Neo4jGraph 实例。"""
    global _cached_graph, _last_health_check_ts

    now = time.monotonic()

    if _cached_graph is not None:
        if (now - _last_health_check_ts) < HEALTH_CHECK_INTERVAL:
            return _cached_graph
        try:
            _cached_graph.query("RETURN 1")
            _last_health_check_ts = now
            return _cached_graph
        except Exception:
            logger.warning("[neo4j] 健康检查失败，连接可能已断开")

        logger.info("[neo4j] 缓存连接失效，尝试重连")
        _cached_graph = None

    try:
        _cached_graph = Neo4jGraph(
            url=settings.NEO4J_URL,
            username=settings.NEO4J_USERNAME,
            password=settings.NEO4J_PASSWORD,
            database=settings.NEO4J_DATABASE,
        )
    except Exception:
        logger.error("[neo4j] 连接失败，KG 查询将不可用", exc_info=True)
        return None

    if _cached_graph is not None:
        try:
            _cached_graph.query("RETURN 1")
            _last_health_check_ts = now
            return _cached_graph
        except Exception:
            logger.warning("[neo4j] 健康检查失败，连接可能已断开")

        logger.error("[neo4j] 新建连接健康检查失败")
        _cached_graph = None

    return None


__all__ = ["get_neo4j_graph"]
