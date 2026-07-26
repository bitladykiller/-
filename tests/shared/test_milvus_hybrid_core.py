"""MilvusHybridSearchCore 单测：批量 embedding、analyzer 缓存与 hybrid 降级。"""

from __future__ import annotations

import threading

from app.shared.retrieval import MilvusHybridSearchCore
from app.shared.retrieval import milvus_hybrid_core as core_module


class FakeMilvusClient:
    def __init__(self, *, hybrid_raises: bool = False) -> None:
        self.hybrid_raises = hybrid_raises
        self.search_calls: list[dict] = []
        self.hybrid_calls: list[dict] = []
        self.search_threads: list[int | None] = []

    def search(self, **kwargs):
        self.search_calls.append(kwargs)
        self.search_threads.append(threading.current_thread().ident)
        return [[{"distance": 0.9, "entity": {"memory_id": "m1"}}]]

    def hybrid_search(self, **kwargs):
        self.hybrid_calls.append(kwargs)
        if self.hybrid_raises:
            raise RuntimeError("hybrid unavailable")
        return [[{"distance": 0.8, "entity": {"memory_id": "m2"}}]]


class FakeEmbedding:
    def __init__(self) -> None:
        self.query_calls: list[str] = []
        self.document_batches: list[list[str]] = []

    def embed_query(self, text: str) -> list[float]:
        self.query_calls.append(text)
        return [0.1, 0.2]

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        self.document_batches.append(list(texts))
        return [[0.1, 0.2] for _ in texts]


def _build_core(client: FakeMilvusClient, embedding: object | None = None):
    return MilvusHybridSearchCore(
        milvus_client=client,  # type: ignore[arg-type]
        embedding_model=embedding or FakeEmbedding(),
        collection_name="coll",
    )


async def test_embed_documents_uses_batch_api_when_available() -> None:
    embedding = FakeEmbedding()
    core = _build_core(FakeMilvusClient(), embedding)

    vectors = await core.embed_documents(["a", "b", "c"])

    assert vectors is not None and len(vectors) == 3
    assert embedding.document_batches == [["a", "b", "c"]]
    assert embedding.query_calls == []


async def test_embed_documents_falls_back_to_embed_query() -> None:
    class QueryOnlyEmbedding:
        def __init__(self) -> None:
            self.query_calls: list[str] = []

        def embed_query(self, text: str) -> list[float]:
            self.query_calls.append(text)
            return [0.3]

    embedding = QueryOnlyEmbedding()
    core = _build_core(FakeMilvusClient(), embedding)

    vectors = await core.embed_documents(["a", "b"])

    assert vectors == [[0.3], [0.3]]
    assert embedding.query_calls == ["a", "b"]


async def test_embed_documents_short_circuits_on_empty_input() -> None:
    embedding = FakeEmbedding()
    core = _build_core(FakeMilvusClient(), embedding)

    assert await core.embed_documents([]) == []
    assert embedding.document_batches == []


async def test_embed_documents_returns_none_on_failure() -> None:
    class BrokenEmbedding:
        def embed_documents(self, texts: list[str]) -> list[list[float]]:
            raise RuntimeError("model down")

    core = _build_core(FakeMilvusClient(), BrokenEmbedding())

    assert await core.embed_documents(["a"]) is None


async def test_search_dense_runs_client_off_event_loop_thread() -> None:
    client = FakeMilvusClient()
    core = _build_core(client)

    hits = await core.search_dense("查询", limit=3)

    assert hits == [{"score": 0.9, "entity": {"memory_id": "m1"}}]
    # Milvus 客户端是同步 SDK，必须在工作线程执行，不能占住事件循环
    assert client.search_threads[0] != threading.current_thread().ident


async def test_search_dense_applies_score_threshold() -> None:
    core = _build_core(FakeMilvusClient())

    assert await core.search_dense("查询", limit=3, score_threshold=0.95) == []


async def test_search_hybrid_falls_back_to_dense_when_hybrid_fails(monkeypatch) -> None:
    monkeypatch.setattr(core_module, "_get_sparse_analyzer", lambda _lang: (lambda q: ["tok"]))
    client = FakeMilvusClient(hybrid_raises=True)
    core = _build_core(client)

    hits = await core.search_hybrid("查询", limit=3)

    assert len(client.hybrid_calls) == 1
    assert hits == [{"score": 0.9, "entity": {"memory_id": "m1"}}]


async def test_search_hybrid_skips_sparse_when_analyzer_unavailable(monkeypatch) -> None:
    def missing_analyzer(_lang: str):
        raise ImportError("no sparse extra")

    monkeypatch.setattr(core_module, "_get_sparse_analyzer", missing_analyzer)
    client = FakeMilvusClient()
    core = _build_core(client)

    hits = await core.search_hybrid("查询", limit=3)

    # 稀疏不可用直接走 dense，不应该尝试 hybrid_search
    assert client.hybrid_calls == []
    assert hits == [{"score": 0.9, "entity": {"memory_id": "m1"}}]


def test_encode_query_sparse_counts_token_frequency(monkeypatch) -> None:
    monkeypatch.setattr(
        core_module,
        "_get_sparse_analyzer",
        lambda _lang: (lambda q: ["a", "b", "a"]),
    )
    core = _build_core(FakeMilvusClient())

    sparse = core.encode_query_sparse("任意")

    assert sorted(sparse.values()) == [1.0, 2.0]


def test_sparse_analyzer_is_built_once_per_language(monkeypatch) -> None:
    """analyzer 构造昂贵（要加载词典），必须按语言缓存而不是每查询重建。"""
    builds: list[str] = []

    core_module._get_sparse_analyzer.cache_clear()

    class FakeTokenizerModule:
        @staticmethod
        def build_default_analyzer(language: str):
            builds.append(language)
            return lambda text: text.split()

    import sys
    import types

    fake_pkg = types.ModuleType("pymilvus.model.sparse.bm25.tokenizers")
    fake_pkg.build_default_analyzer = FakeTokenizerModule.build_default_analyzer  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "pymilvus.model.sparse.bm25.tokenizers", fake_pkg)

    core = _build_core(FakeMilvusClient())
    for _ in range(5):
        core.encode_query_sparse("路由器 断网")

    assert builds == ["zh"]
    core_module._get_sparse_analyzer.cache_clear()
