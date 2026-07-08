"""Retriever 运行时 — 通过 AppContainer 统一管理检索器生命周期。

职责：
- 从 AppContainer 获取检索器注册表和 KG 子图组件
- 懒初始化 KG / RAG 检索器
- 缓存 Text2Cypher 子图和 Cypher 示例检索器

不负责：
- 检索器的具体实现（见 retriever_implementations）
- 容器自身的生命周期
"""

from __future__ import annotations

from typing import Any

from app.chat.infrastructure.kg.neo4j_conn import _get_neo4j_graph
from app.chat.infrastructure.retrievers.retriever_contracts import (
    KG_RETRIEVER_NAME,
    RAG_RETRIEVER_NAME,
)
from app.shared.core.async_bridge import run_blocking


async def get_retriever(name: str) -> Any:
    """获取检索器，首次调用时懒初始化并注册到容器的注册表。

    并发安全：注册表的创建与填充都在 `retriever_registry_lock` 内完成。
    锁外只做一次"是否已就绪"的快速判断，命中时直接返回，不付加锁成本。
    """
    from app.platform.container import get_container

    container = await get_container()

    registry = container.retriever_registry
    if registry is not None and _registry_ready(registry):
        return registry.get(name)

    async with container.retriever_registry_lock:
        registry = _ensure_registry(container)
        if KG_RETRIEVER_NAME not in registry:
            await _register_kg_retriever(container, registry)
        if RAG_RETRIEVER_NAME not in registry:
            await _register_rag_retriever(registry)

    return registry.get(name)


def _registry_ready(registry: Any) -> bool:
    """两个检索器都已注册时才算就绪。"""
    return KG_RETRIEVER_NAME in registry and RAG_RETRIEVER_NAME in registry


def _ensure_registry(container: Any) -> Any:
    """取出容器上的注册表，不存在则创建。

    必须在 `retriever_registry_lock` 内调用：之前这段在锁外执行，
    两个并发请求可能各建一个 registry，后写入的把先写入的连同已注册的
    检索器一起覆盖掉。
    """
    if container.retriever_registry is None:
        from app.chat.infrastructure.retrievers.retriever_contracts import RetrieverRegistry

        container.retriever_registry = RetrieverRegistry()
    return container.retriever_registry


async def _register_kg_retriever(container: Any, registry: Any) -> None:
    """构造并注册 KG 检索器；Neo4j 不可用时静默跳过。

    跳过而不是抛错：图谱是增强能力，缺失时 RAG 链路仍应可用。

    WHY 构造走线程池：首次注册要做 Neo4jGraph 连接 + schema 拉取 +
    预定义模板 embedding（同步 HTTP）+ 子图编译——全是同步重活。
    首个 KG 请求触发时若直接在协程里跑，会把**所有并发用户**一起卡住。
    """
    neo4j_graph = await run_blocking(_get_neo4j_graph, container)
    if neo4j_graph is None:
        return

    from app.chat.infrastructure.retrievers.retriever_implementations import (
        KnowledgeGraphRetriever,
    )

    agent = await run_blocking(_ensure_text2cypher_agent, container, neo4j_graph)
    registry.register(KG_RETRIEVER_NAME, KnowledgeGraphRetriever(agent))


def _ensure_text2cypher_agent(container: Any, neo4j_graph: Any) -> Any:
    """取出（或首次构造）Text2Cypher 子图与其依赖的示例检索器。"""
    components = container.kg_components

    if components.cypher_example_retriever is None:
        from app.chat.infrastructure.kg.northwind_retriever import NorthwindCypherRetriever

        components.cypher_example_retriever = NorthwindCypherRetriever()

    if components.text2cypher_agent is None:
        from app.chat.infrastructure.kg.predefined_cypher.cypher_dict import (
            predefined_cypher_dict,
        )
        from app.chat.infrastructure.kg.predefined_cypher.descriptions import (
            QUERY_DESCRIPTIONS,
        )
        from app.chat.infrastructure.kg.text2cypher_workflow import (
            create_text2cypher_agent,
        )
        from app.chat.infrastructure.modeling.models import cypher_model

        components.text2cypher_agent = create_text2cypher_agent(
            # cypher_model 是 LazyModelProxy：转发到真实 BaseChatModel，
            # 但要等首次使用时才建连接。这是刻意的鸭子类型，不是类型错误。
            llm=cypher_model,  # type: ignore[arg-type]  # pyright: ignore[reportArgumentType]
            graph=neo4j_graph,
            cypher_example_retriever=components.cypher_example_retriever,
            predefined_cypher_dict=predefined_cypher_dict,
            query_descriptions=QUERY_DESCRIPTIONS,
        )

    return components.text2cypher_agent


async def _register_rag_retriever(registry: Any) -> None:
    """构造并注册 RAG 文档检索器。

    WHY 构造走线程池：首次会触发共享 HybridSearcher 的建立——
    Milvus 连接、collection 探测、embedding 模型加载（HuggingFace 路径
    要载入约 2GB 权重），全是同步重活，不能在事件循环里裸跑。
    """
    from app.chat.infrastructure.retrievers.retriever_implementations import (
        MilvusDocRetriever,
    )

    retriever = await run_blocking(MilvusDocRetriever)
    registry.register(RAG_RETRIEVER_NAME, retriever)


__all__ = ["get_retriever"]
