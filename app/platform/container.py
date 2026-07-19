"""应用容器 — 统一管理所有应用级依赖的生命周期。

职责：
- 在 lifespan 启动阶段按顺序初始化所有依赖
- 提供统一的依赖获取入口
- 关闭阶段释放所有外部连接
- 收敛所有模块级全局状态（模型缓存、检索器运行时、KG连接、记忆中间件）

不负责：
- 具体依赖的创建逻辑（由各自的 factory 模块负责）
- 请求级依赖管理
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Any

from app.shared.core.logger import get_logger

logger = get_logger(__name__)


@dataclass
class AppContainer:
    """应用级依赖容器。

    所有模块级单例（MemoryMiddleware、TaskManager、LLM 模型缓存、
    检索器运行时、KG连接等）统一收敛到此容器。
    测试时可以直接替换整个容器实例。
    """

    # ---- 记忆系统 ----
    memory_middleware: Any | None = None

    # ---- 任务管理 ----
    task_manager: Any | None = None

    # ---- LLM 模型实例（替代 models.py 中的 _models_cache 全局变量） ----
    llm_models: dict[str, Any] = field(default_factory=dict)

    # ---- 检索器运行时（替代 retriever_runtime 中的全局变量） ----
    retriever_registry: Any = None
    retriever_registry_lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    _cypher_example_retriever: Any = None
    _t2c_agent: Any = None

    # ---- KG Neo4j 连接缓存（替代 kg_neo4j_conn 中的全局变量） ----
    neo4j_graph: Any = None
    neo4j_last_health_check_ts: float = 0.0

    # ---- ReAct 子图缓存 ----
    react_subgraph: Any = None
    react_subgraph_lock: asyncio.Lock = field(default_factory=asyncio.Lock)

    # ---- 摘要链缓存 ----
    summarize_chain: Any = None

    _closed: bool = field(default=False, init=False)

    @classmethod
    async def build(cls, config: Any) -> AppContainer:
        """按依赖顺序依次初始化所有组件。

        初始化顺序：
        1. MemoryMiddleware（含 STM/LTM/Extractor）
        2. TaskManager

        Args:
            config: 应用配置（settings 对象）

        Returns:
            初始化完成的 AppContainer 实例
        """
        container = cls()
        try:
            await container._init_task_manager(config)
            await container._init_memory_middleware()
            return container
        except Exception:
            await container.close()
            raise

    async def _init_task_manager(self, config: Any) -> None:
        from app.shared.task_queue import _TaskManager, create_redis_client

        self.task_manager = _TaskManager(create_redis_client(config.REDIS_URL))

    async def _init_memory_middleware(self) -> None:
        self.memory_middleware = _create_memory_middleware()

    # ---- 生命周期管理 ----

    async def warm_up(self) -> None:
        """预热懒加载资源，减少首请求初始化延迟。"""
        if self.memory_middleware is None:
            await self._init_memory_middleware()

    async def close(self) -> None:
        """关闭所有外部连接（按依赖逆序）。"""
        if self._closed:
            return
        self._closed = True

        if self.task_manager is not None:
            try:
                await self.task_manager.close()
            except Exception:
                logger.debug("关闭 task_manager 时出错", exc_info=True)
            self.task_manager = None

        if self.memory_middleware is not None:
            try:
                await _close_memory_resources(self.memory_middleware)
            except Exception:
                logger.debug("关闭 memory_middleware 资源时出错", exc_info=True)
            self.memory_middleware = None

        self.llm_models.clear()
        self.retriever_registry = None
        self.neo4j_graph = None
        self.react_subgraph = None
        self.summarize_chain = None


# ──────────────────────────────────────────────
# 容器全局访问 — 单例管理收敛在此模块
# ──────────────────────────────────────────────

_container: AppContainer | None = None
_container_lock: asyncio.Lock = asyncio.Lock()


async def _get_or_build_container() -> AppContainer:
    """双检锁获取或创建容器单例。

    用于那些在 AppContainer.build() 调用之前可能被触及的懒加载路径
    （如 LangGraph 节点首次执行时通过 get_retriever 触碰容器）。
    """
    global _container
    if _container is None:
        async with _container_lock:
            if _container is None:
                from app.shared.core.config import settings

                _container = await AppContainer.build(settings)
    return _container


async def get_container() -> AppContainer:
    """获取当前应用容器实例。

    优先返回 lifespan 中由 create_app 初始化的实例，
    如果尚未初始化则自动构建（兼容懒加载路径）。
    """
    if _container is not None:
        return _container
    return await _get_or_build_container()


async def set_container(container: AppContainer) -> None:
    global _container
    _container = container


async def reset_container() -> None:
    global _container
    if _container is not None:
        await _container.close()
        _container = None


# ──────────────────────────────────────────────
# 统一工厂函数 — 替代各模块散布的创建/关闭逻辑
# ──────────────────────────────────────────────


def _create_memory_middleware() -> Any:
    """创建完整的 MemoryMiddleware 依赖栈。

    使用统一的 create_llm_for_role 工厂函数。
    """
    import redis.asyncio as redis
    from app.chat.infrastructure.modeling.models import create_llm_for_role
    from app.knowledge.infrastructure.ltm.simple_long_term_memory import SimpleLongTermMemory
    from app.knowledge.infrastructure.orchestration.memory_extractor import MemoryExtractor
    from app.knowledge.infrastructure.orchestration.memory_middleware import MemoryMiddleware
    from app.knowledge.infrastructure.stm.redis_short_term_memory import RedisShortTermMemory
    from app.shared.core.config import settings
    from pymilvus import MilvusClient

    if settings.EMBEDDING_TYPE == "ollama":
        from langchain_ollama import OllamaEmbeddings

        embedding_model = OllamaEmbeddings(
            model=settings.EMBEDDING_MODEL,
            base_url=settings.OLLAMA_BASE_URL,
        )
    else:
        from langchain_community.embeddings import HuggingFaceEmbeddings

        embedding_model = HuggingFaceEmbeddings(model_name=settings.EMBEDDING_MODEL)  # type: ignore[assignment]

    memory_extractor_llm = create_llm_for_role("memory_extractor")

    return MemoryMiddleware(
        redis_stm=RedisShortTermMemory(
            redis.from_url(settings.REDIS_URL, decode_responses=True)
        ),
        milvus_ltm=SimpleLongTermMemory(
            milvus_client=MilvusClient(uri=settings.MILVUS_URL),
            embedding_model=embedding_model,
            collection_name=settings.MILVUS_COLLECTION_NAME,
        ),
        memory_extractor=MemoryExtractor(llm_client=memory_extractor_llm),
    )


async def _close_memory_resources(middleware: Any) -> None:
    """关闭 MemoryMiddleware 底层持有的外部连接。"""
    try:
        await middleware.redis_stm.redis.close()
    except Exception:
        logger.debug("关闭 Redis STM 连接时出错", exc_info=True)
    try:
        milvus_client = getattr(middleware.milvus_ltm, "milvus_client", None)
        if milvus_client:
            milvus_client.close()
    except Exception:
        logger.debug("关闭 Milvus 客户端时出错", exc_info=True)


__all__ = [
    "AppContainer",
    "get_container",
    "reset_container",
    "set_container",
]
