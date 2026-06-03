"""Neo4j 连接管理 — 模块级缓存 + 定时健康检查。

优化说明（v3.15）：
- 原实现每次调用 get_neo4j_graph() 都执行 RETURN 1 健康检查，
  高并发下产生大量无意义查询。
- 改为定时检查：每 HEALTH_CHECK_INTERVAL 秒检查一次，期间直接返回缓存实例。
- 首次调用时创建连接 + 立即健康检查。
- 健康检查失败时清空缓存，下次调用自动重连。
"""
from __future__ import annotations

import logging
import sys
import time
from typing import Optional

from langchain_neo4j import Neo4jGraph
from app.core.config import settings

logging.getLogger("neo4j").setLevel(logging.ERROR)
logging.getLogger("langchain_neo4j").setLevel(logging.ERROR)
logging.getLogger("neo4j.io").setLevel(logging.ERROR)
logging.getLogger("neo4j.bolt").setLevel(logging.ERROR)

logger = logging.getLogger(__name__)

# --- 配置常量 --- #
# 健康检查间隔（秒）。在此时间窗口内直接返回缓存实例，不发 RETURN 1。
HEALTH_CHECK_INTERVAL: int = 30

# 模块级状态
_cached_graph: Optional[Neo4jGraph] = None
_last_health_check_ts: float = 0.0


def _create_graph() -> Optional[Neo4jGraph]:
    """创建新的 Neo4jGraph 连接实例。"""
    try:
        graph = Neo4jGraph(
            url=settings.NEO4J_URL,
            username=settings.NEO4J_USERNAME,
            password=settings.NEO4J_PASSWORD,
            database=settings.NEO4J_DATABASE,
        )
        return graph
    except Exception:
        logger.error("[neo4j] 连接失败，KG 查询将不可用", exc_info=True)
        return None


def _check_health(graph: Neo4jGraph) -> bool:
    """执行一次 RETURN 1 健康检查，返回是否存活。"""
    try:
        graph.query("RETURN 1")
        return True
    except Exception:
        logger.warning("[neo4j] 健康检查失败，连接可能已断开")
        return False


def get_neo4j_graph() -> Optional[Neo4jGraph]:
    """返回缓存的 Neo4jGraph 实例。

    行为：
    1. 首次调用：创建连接 + 健康检查。
    2. 后续调用：距上次健康检查 < HEALTH_CHECK_INTERVAL 秒时直接返回缓存。
    3. 超过间隔：执行一次健康检查，失败则重连。
    """
    global _cached_graph, _last_health_check_ts

    now = time.monotonic()

    # --- 有缓存且在健康窗口内：直接返回 --- #
    if _cached_graph is not None:
        if (now - _last_health_check_ts) < HEALTH_CHECK_INTERVAL:
            return _cached_graph
        # 超出窗口，执行健康检查
        if _check_health(_cached_graph):
            _last_health_check_ts = now
            return _cached_graph
        # 健康检查失败，清空缓存，走重连逻辑
        logger.info("[neo4j] 缓存连接失效，尝试重连")
        _cached_graph = None

    # --- 无缓存：创建新连接 --- #
    _cached_graph = _create_graph()
    if _cached_graph is not None:
        # 创建后立即健康检查，确保连接可用
        if _check_health(_cached_graph):
            _last_health_check_ts = now
            return _cached_graph
        # 连接创建成功但健康检查失败（罕见）
        logger.error("[neo4j] 新建连接健康检查失败")
        _cached_graph = None

    return None
