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
class KnowledgeGraphComponents:
    """KG 检索链路的运行时组件缓存。

    这些对象构造昂贵（Cypher 示例检索器要建向量索引、Text2Cypher 子图要编译
    LangGraph），但一旦建成就是无状态可复用的，因此按进程缓存一份。

    WHY 单独成组而不是散落在 AppContainer 上：它们是一组必须一起初始化、
    一起失效的关联对象，聚成一个命名结构后，chat 域可以正常读写，
    不必再去访问容器的下划线私有字段。
    """

    cypher_example_retriever: Any = None
    text2cypher_agent: Any = None

    def clear(self) -> None:
        self.cypher_example_retriever = None
        self.text2cypher_agent = None


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

    # ---- 事件管线（Redis Streams）与资源护栏 ----
    event_queue: Any | None = None
    sse_limiter: Any | None = None
    _events_redis: Any | None = None

    # ---- LLM 模型实例（替代 models.py 中的 _models_cache 全局变量） ----
    llm_models: dict[str, Any] = field(default_factory=dict)

    # ---- 检索器运行时（替代 retriever_runtime 中的全局变量） ----
    retriever_registry: Any = None
    retriever_registry_lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    kg_components: KnowledgeGraphComponents = field(
        default_factory=KnowledgeGraphComponents
    )

    # ---- KG Neo4j 连接缓存（替代 kg_neo4j_conn 中的全局变量） ----
    neo4j_graph: Any = None
    neo4j_last_health_check_ts: float = 0.0

    # ---- ReAct 子图缓存 ----
    react_subgraph: Any = None
    react_subgraph_lock: asyncio.Lock = field(default_factory=asyncio.Lock)

    # ---- 摘要链缓存 ----
    summarize_chain: Any = None

    # ---- 后台定时任务（LTM 硬清理等）----
    _background_tasks: list[asyncio.Task[Any]] = field(default_factory=list)
    _background_stop: asyncio.Event | None = field(default=None, init=False)

    _closed: bool = field(default=False, init=False)

    @classmethod
    async def build(cls, config: Any) -> AppContainer:
        """按依赖顺序依次初始化所有组件。

        初始化顺序（与实现一致）：
        1. TaskManager（进程内后台任务 + Redis 状态，上传依赖）
        2. MemoryMiddleware（STM/LTM/Extractor，问答依赖）

        Args:
            config: 应用配置（settings 对象）

        Returns:
            初始化完成的 AppContainer 实例
        """
        container = cls()
        try:
            await container._init_task_manager(config)
            await container._init_event_infrastructure(config)
            await container._init_memory_middleware()
            return container
        except Exception:
            # 任一步失败：释放已创建连接，避免半初始化单例
            await container.close()
            raise

    async def _init_task_manager(self, config: Any) -> None:
        from app.shared.background_tasks import _TaskManager, create_redis_client

        manager = _TaskManager(create_redis_client(config.REDIS_URL))
        # 后台任务只活在进程内存里：上一代进程留下的 running 记录不会再有人推进，
        # 启动时先把它们收敛成 interrupted，避免前端永远轮询一个不会完成的任务
        await manager.reconcile_orphaned_tasks()
        self.task_manager = manager

    async def _init_event_infrastructure(self, config: Any) -> None:
        """事件队列与并发限流共用一个二进制安全 Redis 客户端。"""
        import redis.asyncio as aioredis
        from app.platform.events import EVENT_GROUP, EVENT_STREAM
        from app.shared.core.config import settings as app_settings
        from app.shared.core.rate_limit import SseConcurrencyLimiter
        from app.shared.streams import RedisStreamQueue

        self._events_redis = aioredis.from_url(config.REDIS_URL, decode_responses=False)
        self.event_queue = RedisStreamQueue(
            self._events_redis,
            stream=EVENT_STREAM,
            group=EVENT_GROUP,
        )
        limits = app_settings.app_config.limits
        self.sse_limiter = SseConcurrencyLimiter(
            self._events_redis,
            max_concurrent=limits.sse_max_concurrent_per_user,
            slot_ttl_seconds=limits.sse_slot_ttl_seconds,
        )

    async def _init_memory_middleware(self) -> None:
        self.memory_middleware = _create_memory_middleware()

    # ---- 生命周期管理 ----

    async def warm_up(self) -> None:
        """预热懒加载资源，减少首请求初始化延迟。"""
        if self.memory_middleware is None:
            await self._init_memory_middleware()

    def start_background_jobs(self) -> None:
        """启动进程内后台任务（事件消费 + LTM 定时硬清理）。"""
        if self._background_stop is not None:
            return
        stop_event = asyncio.Event()
        self._background_stop = stop_event
        self._start_event_consumer(stop_event)
        self._start_ltm_purge(stop_event)

    def _start_event_consumer(self, stop_event: asyncio.Event) -> None:
        """内嵌事件消费者（单容器部署默认形态）。

        拆分部署时：app 进程设 EVENTS_INLINE_CONSUMER=0 关闭内嵌消费，
        另起 `python -m app.worker`（同镜像不同 command）专职消费。
        """
        import os

        if os.getenv("EVENTS_INLINE_CONSUMER", "1") == "0":
            logger.info("已按环境变量关闭内嵌事件消费（EVENTS_INLINE_CONSUMER=0）")
            return
        if self.event_queue is None:
            logger.warning("事件队列未初始化，跳过消费者启动")
            return
        from app.platform.events import build_core_handlers

        task = asyncio.create_task(
            self.event_queue.run_consumer(build_core_handlers(), stop_event),
            name="event_consumer_loop",
        )
        self._background_tasks.append(task)
        logger.info("已启动后台任务: event_consumer_loop")

    def _start_ltm_purge(self, stop_event: asyncio.Event) -> None:
        """LTM 软删记录的定时硬清理。"""
        from app.knowledge.infrastructure.ltm.purge_scheduler import (
            run_ltm_hard_purge_loop,
        )
        from app.shared.core.config import settings

        purge_cfg = settings.app_config.memory.ltm.purge
        if not purge_cfg.enabled or not settings.app_config.memory.ltm.enabled:
            logger.info("跳过 LTM 硬清理调度（ltm 或 purge 未启用）")
            return

        def _get_ltm() -> Any | None:
            mw = self.memory_middleware
            if mw is None:
                return None
            return getattr(mw, "milvus_ltm", None)

        task = asyncio.create_task(
            run_ltm_hard_purge_loop(
                get_ltm=_get_ltm,
                purge_config=purge_cfg,
                stop_event=stop_event,
            ),
            name="ltm_hard_purge_loop",
        )
        self._background_tasks.append(task)
        logger.info("已启动后台任务: ltm_hard_purge_loop")

    async def stop_background_jobs(self) -> None:
        """停止后台任务并等待退出。"""
        if self._background_stop is not None:
            self._background_stop.set()
        tasks = list(self._background_tasks)
        self._background_tasks.clear()
        self._background_stop = None
        if not tasks:
            return
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        logger.info("后台任务已全部停止")

    async def close(self) -> None:
        """关闭所有外部连接（按依赖逆序）。"""
        if self._closed:
            return
        self._closed = True

        try:
            await self.stop_background_jobs()
        except Exception:
            logger.debug("停止后台任务时出错", exc_info=True)

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

        if self._events_redis is not None:
            try:
                await self._events_redis.close()
            except Exception:
                logger.debug("关闭事件 Redis 连接时出错", exc_info=True)
            self._events_redis = None
        self.event_queue = None
        self.sse_limiter = None

        self.llm_models.clear()
        self.retriever_registry = None
        self.kg_components.clear()
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


def get_container_if_initialized() -> AppContainer | None:
    """只读取已初始化的容器，绝不触发构建。

    供机会型访问点使用（事件发布、健康探测）：这些调用点"容器在就用、
    不在就降级"，不应该因为一次探测就拉起整套外部连接。
    """
    return _container


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
    from app.chat.infrastructure.modeling.models import create_llm_for_role
    from app.knowledge.infrastructure.ltm.simple_long_term_memory import SimpleLongTermMemory
    from app.knowledge.infrastructure.orchestration.memory_extractor import MemoryExtractor
    from app.knowledge.infrastructure.orchestration.memory_middleware import MemoryMiddleware
    from app.knowledge.infrastructure.stm.redis_short_term_memory import (
        RedisShortTermMemory,
        create_stm_redis_client,
    )
    from app.shared.core.config import settings
    from app.shared.core.embeddings import get_embedding_model
    from pymilvus import MilvusClient

    # 与 RAG 文档检索共用同一个 embedding 实例：两侧向量必须同源，
    # 且 HuggingFace 路径的模型权重没必要在进程里存两份
    embedding_model = get_embedding_model()
    memory_extractor_llm = create_llm_for_role("memory_extractor")

    return MemoryMiddleware(
        # STM 存的是二进制压缩消息，必须用二进制安全客户端；
        # 用带 decode_responses=True 的客户端会导致消息读取时 UTF-8 解码失败，
        # 短期记忆整体静默失效（详见 create_stm_redis_client 的说明）
        redis_stm=RedisShortTermMemory(create_stm_redis_client(settings.REDIS_URL)),
        milvus_ltm=SimpleLongTermMemory(
            milvus_client=MilvusClient(uri=settings.MILVUS_URL),
            embedding_model=embedding_model,
            # 单一来源：env MILVUS_COLLECTION_NAME 已被该配置项的默认值吸收
            collection_name=settings.app_config.memory.ltm.collection_name,
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
    "KnowledgeGraphComponents",
    "get_container",
    "get_container_if_initialized",
    "reset_container",
    "set_container",
]
