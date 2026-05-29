"""Neo4j 连接管理 — 模块级缓存 + 健康检查。"""
from __future__ import annotations

import logging
import sys
from typing import Optional

from langchain_neo4j import Neo4jGraph
from app.core.config import settings

logging.getLogger("neo4j").setLevel(logging.ERROR)
logging.getLogger("langchain_neo4j").setLevel(logging.ERROR)
logging.getLogger("neo4j.io").setLevel(logging.ERROR)
logging.getLogger("neo4j.bolt").setLevel(logging.ERROR)

_cached_graph: Optional[Neo4jGraph] = None


def get_neo4j_graph() -> Optional[Neo4jGraph]:
    """返回缓存的 Neo4jGraph 实例，首次调用时创建连接。"""
    global _cached_graph
    if _cached_graph is not None:
        try:
            _cached_graph.query("RETURN 1")
            return _cached_graph
        except Exception:
            _cached_graph = None

    try:
        _cached_graph = Neo4jGraph(
            url=settings.NEO4J_URL,
            username=settings.NEO4J_USERNAME,
            password=settings.NEO4J_PASSWORD,
            database=settings.NEO4J_DATABASE,
        )
        return _cached_graph
    except Exception:
        print("[neo4j] 连接失败，KG 查询将不可用", file=sys.stderr)
        return None
