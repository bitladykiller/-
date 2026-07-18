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


async def get_neo4j_graph() -> Neo4jGraph | None:
    """从 AppContainer 获取/创建 Neo4jGraph 缓存实例。"""
    from app.platform.container import get_container

    container = await get_container()
    return _get_neo4j_graph(container)


def _get_neo4j_graph(container: Any) -> Any:
    """从 AppContainer 获取/创建 Neo4jGraph 缓存实例。"""
    now = time.monotonic()

    if container.neo4j_graph is not None:
        if (now - container.neo4j_last_health_check_ts) < HEALTH_CHECK_INTERVAL:
            return container.neo4j_graph
        try:
            container.neo4j_graph.query("RETURN 1")
            container.neo4j_last_health_check_ts = now
            return container.neo4j_graph
        except Exception:
            logger.warning("[neo4j] 连接失败，连接可能已断开")

        logger.info("[neo4j] 缓存连接失效，尝试重连")
        container.neo4j_graph = None

    try:
        container.neo4j_graph = Neo4jGraph(
            url=settings.NEO4J_URL,
            username=settings.NEO4J_USERNAME,
            password=settings.NEO4J_PASSWORD,
            database=settings.NEO4J_DATABASE,
        )
    except Exception:
        logger.error("[neo4j] 连接失败，KG 查询将不可用", exc_info=True)
        return None

    if container.neo4j_graph is not None:
        try:
            container.neo4j_graph.query("RETURN 1")
            container.neo4j_last_health_check_ts = now
            return container.neo4j_graph
        except Exception:
            logger.warning("[neo4j] 健康检查失败，连接可能已断开")

        logger.error("[neo4j] 新建连接健康检查失败")
        container.neo4j_graph = None

    return None


__all__ = ["get_neo4j_graph"]