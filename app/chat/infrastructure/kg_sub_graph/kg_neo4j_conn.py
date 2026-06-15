"""Neo4jGraph 连接缓存层。

这个模块负责：
- 缓存 Neo4jGraph 实例，避免重复创建连接
- 对健康检查做限频，减少高并发下的无意义 `RETURN 1`
- 在缓存失效时触发重连

这个模块不负责：
- Cypher 查询拼装
- KG 检索策略选择
- 上层业务降级决策
"""
import logging
import time

from langchain_neo4j import Neo4jGraph
from app.shared.core.config import settings
from app.shared.core.logger import get_logger

logging.getLogger("neo4j").setLevel(logging.ERROR)
logging.getLogger("langchain_neo4j").setLevel(logging.ERROR)
logging.getLogger("neo4j.io").setLevel(logging.ERROR)
logging.getLogger("neo4j.bolt").setLevel(logging.ERROR)

logger = get_logger(__name__)

# --- 配置常量 --- #
# 健康检查间隔（秒）。在此时间窗口内直接返回缓存实例，不发 RETURN 1。
HEALTH_CHECK_INTERVAL: int = 30

# 模块级状态
_cached_graph: Neo4jGraph | None = None
_last_health_check_ts: float = 0.0


def get_neo4j_graph() -> Neo4jGraph | None:
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
        try:
            _cached_graph.query("RETURN 1")
            _last_health_check_ts = now
            return _cached_graph
        except Exception:
            logger.warning("[neo4j] 健康检查失败，连接可能已断开")

        # 健康检查失败，清空缓存，走重连逻辑
        logger.info("[neo4j] 缓存连接失效，尝试重连")
        _cached_graph = None

    # --- 无缓存：创建新连接 --- #
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
        # 创建后立即健康检查，确保连接可用
        try:
            _cached_graph.query("RETURN 1")
            _last_health_check_ts = now
            return _cached_graph
        except Exception:
            logger.warning("[neo4j] 健康检查失败，连接可能已断开")

        # 连接创建成功但健康检查失败（罕见）
        logger.error("[neo4j] 新建连接健康检查失败")
        _cached_graph = None

    return None
