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

from app.shared.core.config import ServiceType, settings
from app.shared.core.logger import get_logger

if TYPE_CHECKING:
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

logger = get_logger(__name__)

_MEMORY_EXTRACTOR_TEMPERATURE = 0.3

_memory_middleware_instance: MemoryMiddleware | None = None
_memory_middleware_lock: asyncio.Lock = asyncio.Lock()


def _create_embedding_model():
    """根据全局配置创建 embedding 模型。"""

    if settings.EMBEDDING_TYPE == "ollama":
        from langchain_ollama import OllamaEmbeddings

        return OllamaEmbeddings(
            model=settings.EMBEDDING_MODEL,
            base_url=settings.OLLAMA_BASE_URL,
        )

    from langchain_community.embeddings import HuggingFaceEmbeddings

    return HuggingFaceEmbeddings(model_name=settings.EMBEDDING_MODEL)


def _create_memory_extractor_llm():
    """创建记忆抽取专用 LLM。"""

    if settings.AGENT_SERVICE == ServiceType.DEEPSEEK:
        from langchain_deepseek import ChatDeepSeek

        return ChatDeepSeek(
            api_key=settings.DEEPSEEK_API_KEY,
            model_name=settings.DEEPSEEK_MODEL,
            temperature=_MEMORY_EXTRACTOR_TEMPERATURE,
        )

    from langchain_ollama import ChatOllama

    return ChatOllama(
        model=settings.OLLAMA_AGENT_MODEL,
        base_url=settings.OLLAMA_BASE_URL,
        temperature=_MEMORY_EXTRACTOR_TEMPERATURE,
    )


def _create_redis_short_term_memory() -> RedisShortTermMemory:
    """创建 Redis STM（短期记忆）存储层。"""

    import redis.asyncio as redis
    from app.knowledge.infrastructure.stm.redis_short_term_memory import (
        RedisShortTermMemory,
    )

    redis_client = redis.from_url(settings.REDIS_URL, decode_responses=True)
    return RedisShortTermMemory(redis_client)


def _create_simple_long_term_memory() -> SimpleLongTermMemory:
    """创建 Milvus LTM（长期记忆）存储层。"""

    from pymilvus import MilvusClient
    from app.knowledge.infrastructure.ltm.simple_long_term_memory import (
        SimpleLongTermMemory,
    )

    return SimpleLongTermMemory(
        milvus_client=MilvusClient(uri=settings.MILVUS_URL),
        embedding_model=_create_embedding_model(),
        collection_name=settings.MILVUS_COLLECTION_NAME,
    )


def _create_memory_extractor() -> MemoryExtractor:
    """创建长期记忆抽取器。"""

    from app.knowledge.infrastructure.orchestration.memory_extractor import (
        MemoryExtractor,
    )

    return MemoryExtractor(llm_client=_create_memory_extractor_llm())


def create_memory_middleware_instance() -> MemoryMiddleware:
    """创建完整的 MemoryMiddleware 依赖栈。"""

    from app.knowledge.infrastructure.orchestration.memory_middleware import (
        MemoryMiddleware,
    )

    return MemoryMiddleware(
        redis_stm=_create_redis_short_term_memory(),
        milvus_ltm=_create_simple_long_term_memory(),
        memory_extractor=_create_memory_extractor(),
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
