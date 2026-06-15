"""LangGraph 记忆运行时依赖管理。

负责：
- MemoryMiddleware 单例的懒初始化和生命周期管理
- 记忆依赖栈工厂（Redis STM / Milvus LTM / MemoryExtractor）
- 从 RunnableConfig 中统一提取 tenant / user / session 标识

不负责：
- 记忆上下文文本拼装
- 节点级业务流程
- 记忆持久化细节
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

from app.shared.core.config import settings
from app.shared.core.config_models import ServiceType
from app.shared.core.logger import get_logger

if TYPE_CHECKING:
    from app.knowledge.infrastructure.orchestration.memory_middleware import (
        MemoryMiddleware,
    )

logger = get_logger(__name__)

_MEMORY_EXTRACTOR_TEMPERATURE = 0.3

_memory_middleware_instance: MemoryMiddleware | None = None
_memory_middleware_lock: asyncio.Lock = asyncio.Lock()


def create_memory_middleware_instance() -> MemoryMiddleware:
    """创建完整的 MemoryMiddleware 依赖栈。"""

    import redis.asyncio as redis
    from pymilvus import MilvusClient
    from app.knowledge.infrastructure.ltm.simple_long_term_memory import (
        SimpleLongTermMemory,
    )
    from app.knowledge.infrastructure.orchestration.memory_extractor import (
        MemoryExtractor,
    )
    from app.knowledge.infrastructure.orchestration.memory_middleware import (
        MemoryMiddleware,
    )
    from app.knowledge.infrastructure.stm.redis_short_term_memory import (
        RedisShortTermMemory,
    )

    if settings.EMBEDDING_TYPE == "ollama":
        from langchain_ollama import OllamaEmbeddings

        embedding_model = OllamaEmbeddings(
            model=settings.EMBEDDING_MODEL,
            base_url=settings.OLLAMA_BASE_URL,
        )
    else:
        from langchain_community.embeddings import HuggingFaceEmbeddings

        embedding_model = HuggingFaceEmbeddings(model_name=settings.EMBEDDING_MODEL)

    if settings.AGENT_SERVICE == ServiceType.DEEPSEEK:
        from langchain_deepseek import ChatDeepSeek

        memory_extractor_llm = ChatDeepSeek(
            api_key=settings.DEEPSEEK_API_KEY,
            model_name=settings.DEEPSEEK_MODEL,
            temperature=_MEMORY_EXTRACTOR_TEMPERATURE,
        )
    else:
        from langchain_ollama import ChatOllama

        memory_extractor_llm = ChatOllama(
            model=settings.OLLAMA_AGENT_MODEL,
            base_url=settings.OLLAMA_BASE_URL,
            temperature=_MEMORY_EXTRACTOR_TEMPERATURE,
        )

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


async def close_memory_resources(middleware: MemoryMiddleware) -> None:
    """关闭 MemoryMiddleware 底层持有的外部连接。"""

    try:
        await middleware.redis_stm.redis.close()
    except Exception:
        pass
    try:
        milvus_client = getattr(middleware.milvus_ltm, "milvus_client", None)
        if milvus_client:
            milvus_client.close()
    except Exception:
        pass


async def get_memory_middleware() -> MemoryMiddleware | None:
    """获取 MemoryMiddleware 单例。"""
    global _memory_middleware_instance
    if _memory_middleware_instance is not None:
        return _memory_middleware_instance
    async with _memory_middleware_lock:
        if _memory_middleware_instance is not None:
            return _memory_middleware_instance
        try:
            _memory_middleware_instance = create_memory_middleware_instance()
            return _memory_middleware_instance
        except Exception:
            logger.error("MemoryMiddleware 初始化失败，将以无记忆模式运行", exc_info=True)
            return None


async def warm_up_memory_middleware() -> None:
    """在应用启动阶段预热记忆依赖，减少首请求初始化开销。"""
    await get_memory_middleware()


async def close_memory_middleware() -> None:
    """关闭 MemoryMiddleware 及其底层连接。在应用 shutdown 时调用。"""
    global _memory_middleware_instance
    if _memory_middleware_instance is None:
        return
    await close_memory_resources(_memory_middleware_instance)
    _memory_middleware_instance = None


__all__ = [
    "close_memory_resources",
    "create_memory_middleware_instance",
    "get_memory_middleware",
    "warm_up_memory_middleware",
    "close_memory_middleware",
]
