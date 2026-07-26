"""MilvusHybridSearchCore 单测：批量 embedding、analyzer 缓存与 hybrid 降级。"""

from __future__ import annotations

import threading

from app.shared.retrieval import MilvusHybridSearchCore


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


async def test_search_hybrid_sends_raw_query_text_to_bm25_field() -> None:
    """稀疏分支必须把原文交给 Milvus，由服务端 BM25 Function 分词。

    历史实现在客户端用 abs(hash(token)) % 2**24 造 token id：
    既跨进程不稳定（Python 字符串 hash 按进程随机化），又和 Milvus
    自己词表的编号对不上，导致稀疏召回几乎恒为空、RRF 静默退化成纯向量检索。
    """
    client = FakeMilvusClient()
    core = _build_core(client)

    await core.search_hybrid("路由器 经常 断网", limit=3, search_limit=6)

    assert len(client.hybrid_calls) == 1
    dense_req, sparse_req = client.hybrid_calls[0]["reqs"]

    assert sparse_req.anns_field == "sparse_vector"
    # 关键：传的是原始查询字符串，不是客户端算出来的 {token_id: weight}
    assert sparse_req.data == ["路由器 经常 断网"]
    assert "drop_ratio_search" in sparse_req.param
    assert sparse_req.limit == 6

    assert dense_req.anns_field == "embedding"
    assert dense_req.data == [[0.1, 0.2]]


async def test_search_hybrid_query_encoding_is_deterministic() -> None:
    """同一查询两次构造出的稀疏请求必须完全一致（旧实现做不到）。"""
    core = _build_core(FakeMilvusClient())

    first = core.build_sparse_request("路由器", limit=5, filter_expr=None)
    second = core.build_sparse_request("路由器", limit=5, filter_expr=None)

    assert first.data == second.data == ["路由器"]
    assert first.param == second.param


async def test_search_hybrid_propagates_filter_to_both_branches() -> None:
    client = FakeMilvusClient()
    core = _build_core(client)

    await core.search_hybrid("查询", limit=3, filter_expr="is_deleted == false")

    dense_req, sparse_req = client.hybrid_calls[0]["reqs"]
    assert dense_req.expr == "is_deleted == false"
    assert sparse_req.expr == "is_deleted == false"


async def test_search_hybrid_falls_back_to_dense_when_hybrid_fails() -> None:
    client = FakeMilvusClient(hybrid_raises=True)
    core = _build_core(client)

    hits = await core.search_hybrid("查询", limit=3)

    assert len(client.hybrid_calls) == 1
    assert hits == [{"score": 0.9, "entity": {"memory_id": "m1"}}]


async def test_search_hybrid_returns_empty_when_embedding_fails() -> None:
    class BrokenEmbedding:
        def embed_query(self, text: str):
            raise RuntimeError("model down")

    client = FakeMilvusClient()
    core = _build_core(client, BrokenEmbedding())

    assert await core.search_hybrid("查询", limit=3) == []
    assert client.hybrid_calls == []
