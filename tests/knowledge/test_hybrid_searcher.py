"""HybridSearcher 编排层与共享单例单测（不连真实 Milvus）。"""

from __future__ import annotations

from typing import Any

import app.knowledge.infrastructure.doc_parser.retrieval.hybrid_search as hybrid_module
import pytest
from app.knowledge.infrastructure.doc_parser.retrieval.config import RetrievalConfig
from app.knowledge.infrastructure.doc_parser.retrieval.hybrid_search import (
    HybridSearcher,
    get_shared_searcher,
    reset_shared_searcher,
)


class FakeMilvusStore:
    """替身：记录调用，不碰网络。"""

    instances: list[FakeMilvusStore] = []

    def __init__(self, config: RetrievalConfig, embedding_model: Any = None) -> None:
        self.config = config
        self.embedding_model = embedding_model
        self.inserted: list[dict[str, Any]] = []
        self.reindexed: list[dict[str, Any]] = []
        self.soft_deleted: list[str] = []
        self.purge_calls: list[dict[str, int]] = []
        self.hybrid_calls: list[dict[str, Any]] = []
        self.hybrid_results: list[dict[str, Any]] = []
        FakeMilvusStore.instances.append(self)

    async def insert_chunks(
        self, chunks, *, version: int = 1, content_hash: str = "", owner_id: str = "global"
    ) -> int:
        self.inserted.append(
            {"chunks": list(chunks), "version": version, "hash": content_hash, "owner": owner_id}
        )
        return len(chunks)

    async def reindex_document(
        self, doc_id, chunks, *, content_hash: str = "", owner_id: str = "global"
    ):
        self.reindexed.append(
            {"doc_id": doc_id, "chunks": list(chunks), "hash": content_hash, "owner": owner_id}
        )
        return {"soft_deleted": 2, "version": 3, "chunks": len(chunks)}

    async def soft_delete_by_doc_id(self, doc_id: str) -> dict[str, int]:
        self.soft_deleted.append(doc_id)
        return {"soft_deleted": 4, "max_version": 1}

    async def hard_purge_soft_deleted(self, *, retention_seconds: int, batch_limit: int) -> int:
        self.purge_calls.append(
            {"retention_seconds": retention_seconds, "batch_limit": batch_limit}
        )
        return 7

    async def hybrid_search(self, query, top_k=None, filter_expr=None, **kwargs):
        self.hybrid_calls.append({"query": query, "top_k": top_k, "filter_expr": filter_expr})
        return list(self.hybrid_results)


@pytest.fixture(autouse=True)
def _patch_store(monkeypatch):
    FakeMilvusStore.instances = []
    monkeypatch.setattr(hybrid_module, "MilvusStore", FakeMilvusStore)
    reset_shared_searcher()
    yield
    reset_shared_searcher()


def _build(**config_kwargs) -> HybridSearcher:
    return HybridSearcher(RetrievalConfig(enable_rerank=False, **config_kwargs))


async def test_index_delegates_version_and_hash() -> None:
    searcher = _build()

    count = await searcher.index(["c1", "c2"], version=2, content_hash="h")

    assert count == 2
    assert searcher.milvus.inserted == [
        {"chunks": ["c1", "c2"], "version": 2, "hash": "h", "owner": "global"}
    ]


async def test_reindex_returns_store_result() -> None:
    searcher = _build()

    result = await searcher.reindex("doc_a", ["c1"], content_hash="h")

    assert result == {"soft_deleted": 2, "version": 3, "chunks": 1}
    assert searcher.milvus.reindexed[0]["doc_id"] == "doc_a"


async def test_soft_delete_document_awaits_store() -> None:
    searcher = _build()

    assert await searcher.soft_delete_document("doc_a") == {
        "soft_deleted": 4,
        "max_version": 1,
    }
    assert searcher.milvus.soft_deleted == ["doc_a"]


async def test_hard_purge_forwards_retention_and_limit() -> None:
    searcher = _build()

    assert await searcher.hard_purge_soft_deleted(retention_seconds=60, batch_limit=5) == 7
    assert searcher.milvus.purge_calls == [{"retention_seconds": 60, "batch_limit": 5}]


async def test_search_truncates_to_final_top_k() -> None:
    searcher = _build(rrf_final_top_k=2)
    searcher.milvus.hybrid_results = [{"chunk_id": f"c{i}"} for i in range(5)]

    results = await searcher.search("查询")

    assert [row["chunk_id"] for row in results] == ["c0", "c1"]
    assert searcher.milvus.hybrid_calls[0]["query"] == "查询"


async def test_search_applies_reranker_when_enabled(monkeypatch) -> None:
    class FakeReranker:
        available = True

        def __init__(self, *_args, **_kwargs) -> None:
            self.calls: list[str] = []

        def rerank(self, query, results, *, top_k, text_field):
            self.calls.append(query)
            return list(reversed(results))

    monkeypatch.setattr(hybrid_module, "Reranker", FakeReranker)
    searcher = HybridSearcher(RetrievalConfig(enable_rerank=True, rrf_final_top_k=3))
    searcher.milvus.hybrid_results = [{"chunk_id": "a"}, {"chunk_id": "b"}]

    results = await searcher.search("查询")

    assert [row["chunk_id"] for row in results] == ["b", "a"]
    assert searcher.reranker.calls == ["查询"]


def test_get_shared_searcher_builds_store_once() -> None:
    """共享单例：重复获取不能重复建 Milvus 连接 / 重载 embedding 模型。"""
    first = get_shared_searcher()
    second = get_shared_searcher()

    assert first is second
    assert len(FakeMilvusStore.instances) == 1


def test_reset_shared_searcher_forces_rebuild() -> None:
    first = get_shared_searcher()
    reset_shared_searcher()
    second = get_shared_searcher()

    assert first is not second
    assert len(FakeMilvusStore.instances) == 2


def test_retrieval_config_milvus_host_follows_settings(monkeypatch) -> None:
    """Docker 下 MILVUS_HOST=milvus，RAG 侧不能再硬编码 localhost。

    历史上 RetrievalConfig 写死 localhost:19530，而 LTM 走 settings.MILVUS_URL，
    导致容器部署时长期记忆连得上、文档检索连不上。
    """
    from app.shared.core.config import settings

    monkeypatch.setattr(settings, "MILVUS_HOST", "milvus", raising=False)
    monkeypatch.setattr(settings, "MILVUS_PORT", 19531, raising=False)

    config = RetrievalConfig()

    assert config.milvus_host == "milvus"
    assert config.milvus_port == 19531


def test_retrieval_config_reads_settings_by_default() -> None:
    """默认值确实来自 settings，而不是字面量。"""
    from app.shared.core.config import settings

    config = RetrievalConfig()

    assert config.milvus_host == settings.MILVUS_HOST
    assert config.milvus_port == int(settings.MILVUS_PORT)


def test_retrieval_config_rejects_invalid_drop_ratio() -> None:
    with pytest.raises(ValueError, match="bm25_drop_ratio"):
        RetrievalConfig(bm25_drop_ratio=1.0)
