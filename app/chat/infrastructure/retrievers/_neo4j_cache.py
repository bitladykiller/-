"""retriever_runtime 模块中的 Neo4j 图连接内部函数。

供 kg_neo4j_conn.py 和 retriever_runtime.py 内部共享。
不对外导出，外部通过 get_neo4j_graph() 入口使用。
"""

from __future__ import annotations

from typing import Any
import time as _time
import logging as _logging


def _get_neo4j_graph(container: Any) -> Any:
    """从容器获取/创建 Neo4jGraph 缓存实例。"""
    from langchain_neo4j import Neo4jGraph
    from app.shared.core.config import settings
    from app.shared.core.logger import get_logger

    _logging.getLogger("neo4j").setLevel(_logging.ERROR)
    _logging.getLogger("langchain_neo4j").setLevel(_logging.ERROR)
    _logging.getLogger("neo4j.io").setLevel(_logging.ERROR)
    _logging.getLogger("neo4j.bolt").setLevel(_logging.ERROR)

    logger = get_logger(__name__)
    HEALTH_CHECK_INTERVAL = 30.0

    now = _time.monotonic()

    if container.neo4j_graph is not None:
        if (now - container.neo4j_last_health_check_ts) < HEALTH_CHECK_INTERVAL:
            return container.neo4j_graph
        try:
            container.neo4j_graph.query("RETURN 1")
            container.neo4j_last_health_check_ts = now
            return container.neo4j_graph
        except Exception:
            logger.warning("[neo4j] 健康检查失败，连接可能已断开")

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
