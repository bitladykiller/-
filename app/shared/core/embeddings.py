"""Embedding 模型工厂 — 全应用唯一的构造入口。

这个模块负责：
- 依据 `settings` 决定用 Ollama 还是 HuggingFace
- 提供进程内共享实例，避免重复加载模型权重

这个模块不负责：
- 向量库连接
- 检索策略

WHY 必须收敛成一处：
长期记忆（LTM）和文档检索（RAG）写入的是两个 Milvus collection，但**必须**
使用同一个 embedding 模型——否则两边向量落在不同语义空间，维度对不上直接报错，
维度碰巧相同则更糟：检索结果看起来正常，实际是噪音。

历史上这两条链路各建各的：LTM 读 `settings.EMBEDDING_TYPE`，
RAG 直接 `os.getenv("OLLAMA_EMBEDDING_MODEL") or os.getenv("EMBEDDING_MODEL")`
并自带一套默认值和降级顺序。改配置文件对 RAG 侧不生效，两边静默用上不同模型。
"""

from __future__ import annotations

from functools import lru_cache
from typing import Any

from app.shared.core.config import settings
from app.shared.core.logger import get_logger

logger = get_logger(__name__)

EMBEDDING_TYPE_OLLAMA = "ollama"


def create_embedding_model(config: Any = settings) -> Any:
    """按配置构造 embedding 模型。

    Args:
        config: 配置对象，默认使用全局 `settings`。测试可注入替身。

    Returns:
        LangChain Embeddings 实例（有 `embed_query` / `embed_documents`）。
    """
    model_name = config.EMBEDDING_MODEL
    if config.EMBEDDING_TYPE == EMBEDDING_TYPE_OLLAMA:
        from langchain_ollama import OllamaEmbeddings

        logger.info(
            "使用 Ollama embedding | model=%s base_url=%s",
            model_name,
            config.OLLAMA_BASE_URL,
        )
        return OllamaEmbeddings(model=model_name, base_url=config.OLLAMA_BASE_URL)

    from langchain_community.embeddings import HuggingFaceEmbeddings

    logger.info("使用 HuggingFace embedding | model=%s", model_name)
    return HuggingFaceEmbeddings(model_name=model_name)


@lru_cache(maxsize=1)
def get_embedding_model() -> Any:
    """返回进程内共享的 embedding 模型。

    WHY 共享：HuggingFace 路径会把模型权重加载进内存（bge-m3 约 2GB）。
    LTM 与 RAG 各建一份就是双倍内存和双倍加载时间，而模型本身是无状态的。
    """
    return create_embedding_model()


def reset_embedding_model() -> None:
    """清空共享实例（配置变更或测试隔离时使用）。"""
    get_embedding_model.cache_clear()


__all__ = [
    "create_embedding_model",
    "get_embedding_model",
    "reset_embedding_model",
]
