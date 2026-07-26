"""MilvusStore 软删 / reindex / hard_purge 单元测试（Fake client）。"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest
from app.knowledge.infrastructure.doc_parser.retrieval.config import RetrievalConfig
from app.knowledge.infrastructure.doc_parser.retrieval.milvus_store import MilvusStore
from app.shared.retrieval import MilvusHybridSearchCore


class FakeMilvusClient:
    def __init__(self) -> None:
        self.has_collection_name: str | None = None
        self.upserts: list[list[dict[str, Any]]] = []
        self.inserts: list[list[dict[str, Any]]] = []
        self.deletes: list[str] = []
        self.query_rows: list[dict[str, Any]] = []
        self.query_calls: list[dict[str, Any]] = []
        self.search_calls: list[dict[str, Any]] = []
        self.search_hits: list[list[dict[str, Any]]] = [[]]

    def has_collection(self, name: str) -> bool:
        self.has_collection_name = name
        return True

    def search(self, **kwargs):
        self.search_calls.append(kwargs)
        return list(self.search_hits)

    def query(self, **kwargs):
        self.query_calls.append(kwargs)
        return list(self.query_rows)

    def upsert(self, *, collection_name: str, data: list[dict[str, Any]]):
        self.upserts.append(data)
        return {"upsert_count": len(data)}

    def insert(self, *, collection_name: str, data: list[dict[str, Any]]):
        self.inserts.append(data)
        return {"insert_count": len(data)}

    def delete(self, *, collection_name: str, filter: str):
        self.deletes.append(filter)
        return {"delete_count": 1}


class FakeEmbedding:
    """只实现 embed_query，用来验证批量接口对旧模型的回退路径。"""

    def __init__(self) -> None:
        self.query_calls: list[str] = []

    def embed_query(self, text: str) -> list[float]:
        self.query_calls.append(text)
        return [0.1, 0.2, 0.3]


class FakeBatchEmbedding(FakeEmbedding):
    """同时实现 embed_documents，走批量快路径。"""

    def __init__(self) -> None:
        super().__init__()
        self.document_batches: list[list[str]] = []

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        self.document_batches.append(list(texts))
        return [[0.1, 0.2, 0.3] for _ in texts]


def _build_store(client: FakeMilvusClient, embedding: FakeEmbedding | None = None) -> MilvusStore:
    store = MilvusStore.__new__(MilvusStore)
    store.config = RetrievalConfig(milvus_collection_name="rag_documents")
    store.embedding_model = embedding or FakeEmbedding()
    store.client = client
    store.retrieval_core = MilvusHybridSearchCore(
        milvus_client=client,  # type: ignore[arg-type]
        embedding_model=store.embedding_model,
        collection_name="rag_documents",
    )
    return store


def _chunk(chunk_id: str, text: str = "hello") -> SimpleNamespace:
    return SimpleNamespace(
        chunk_id=chunk_id,
        doc_id="doc_a",
        source_file="a.md",
        chunk_type="text",
        section_path="S",
        raw_text=text,
        embedding_text=text,
    )


async def test_soft_delete_by_doc_id_marks_active_chunks() -> None:
    client = FakeMilvusClient()
    client.query_rows = [
        {"chunk_id": "c1", "version": 2},
        {"chunk_id": "c2", "version": 2},
    ]
    store = _build_store(client)

    result = await store.soft_delete_by_doc_id("doc_a")

    assert result["soft_deleted"] == 2
    assert result["max_version"] == 2
    assert len(client.upserts) == 1
    assert all(row["is_deleted"] is True for row in client.upserts[0])
    assert "is_deleted == false" in client.query_calls[0]["filter"]


async def test_reindex_document_soft_deletes_then_inserts_next_version() -> None:
    client = FakeMilvusClient()
    client.query_rows = [{"chunk_id": "old-1", "version": 1}]
    store = _build_store(client)

    result = await store.reindex_document("doc_a", [_chunk("new-1")], content_hash="abc")

    assert result["soft_deleted"] == 1
    assert result["version"] == 2
    assert result["chunks"] == 1
    assert client.inserts[0][0]["version"] == 2
    assert client.inserts[0][0]["is_deleted"] is False
    assert client.inserts[0][0]["content_hash"] == "abc"


async def test_insert_chunks_embeds_all_texts_in_one_batch() -> None:
    client = FakeMilvusClient()
    embedding = FakeBatchEmbedding()
    store = _build_store(client, embedding)
    chunks = [_chunk(f"c{index}", f"text-{index}") for index in range(5)]

    inserted = await store.insert_chunks(chunks, version=1)

    assert inserted == 5
    # 关键断言：5 个 chunk 只触发 1 次批量调用，而不是 5 次逐条 embed_query
    assert embedding.document_batches == [["text-0", "text-1", "text-2", "text-3", "text-4"]]
    assert embedding.query_calls == []
    assert [row["chunk_id"] for row in client.inserts[0]] == ["c0", "c1", "c2", "c3", "c4"]


async def test_insert_chunks_falls_back_to_embed_query_without_batch_api() -> None:
    client = FakeMilvusClient()
    embedding = FakeEmbedding()
    store = _build_store(client, embedding)

    inserted = await store.insert_chunks([_chunk("c0", "a"), _chunk("c1", "b")])

    assert inserted == 2
    assert embedding.query_calls == ["a", "b"]


async def test_hard_purge_soft_deleted_deletes_by_chunk_id() -> None:
    client = FakeMilvusClient()
    client.query_rows = [{"chunk_id": "dead-1"}, {"chunk_id": "dead-2"}]
    store = _build_store(client)

    deleted = await store.hard_purge_soft_deleted(retention_seconds=0, batch_limit=10)

    assert deleted == 2
    assert len(client.deletes) == 1
    assert "dead-1" in client.deletes[0]
    assert "dead-2" in client.deletes[0]


class FailingQueryClient(FakeMilvusClient):
    def query(self, **kwargs):
        self.query_calls.append(kwargs)
        raise RuntimeError("milvus down")


class FailingUpsertClient(FakeMilvusClient):
    def upsert(self, **kwargs):
        raise RuntimeError("upsert down")


async def test_get_max_version_returns_zero_when_query_fails() -> None:
    store = _build_store(FailingQueryClient())

    assert await store.get_max_version("doc_a") == 0


async def test_get_max_version_ignores_dirty_version_values() -> None:
    client = FakeMilvusClient()
    client.query_rows = [
        {"version": "not-a-number"},
        {"version": 3},
        {"version": None},
    ]
    store = _build_store(client)

    assert await store.get_max_version("doc_a") == 3


async def test_soft_delete_falls_back_to_max_version_on_query_failure() -> None:
    store = _build_store(FailingQueryClient())

    result = await store.soft_delete_by_doc_id("doc_a")

    assert result == {"soft_deleted": 0, "max_version": 0}


async def test_soft_delete_returns_zero_when_no_active_chunks() -> None:
    client = FakeMilvusClient()
    client.query_rows = []
    store = _build_store(client)

    result = await store.soft_delete_by_doc_id("doc_a")

    assert result["soft_deleted"] == 0
    assert client.upserts == []


async def test_soft_delete_reports_zero_when_upsert_fails() -> None:
    client = FailingUpsertClient()
    client.query_rows = [{"chunk_id": "c1", "version": 4}]
    store = _build_store(client)

    result = await store.soft_delete_by_doc_id("doc_a")

    assert result == {"soft_deleted": 0, "max_version": 4}


async def test_insert_chunks_short_circuits_on_empty_list() -> None:
    client = FakeMilvusClient()
    store = _build_store(client)

    assert await store.insert_chunks([]) == 0
    assert client.inserts == []


async def test_insert_chunks_raises_when_embedding_count_mismatches() -> None:
    class ShortBatchEmbedding(FakeEmbedding):
        def embed_documents(self, texts: list[str]) -> list[list[float]]:
            return [[0.1, 0.2, 0.3]]  # 少返回一条

    store = _build_store(FakeMilvusClient(), ShortBatchEmbedding())

    with pytest.raises(RuntimeError, match="数量不匹配"):
        await store.insert_chunks([_chunk("c0"), _chunk("c1")])


async def test_hard_purge_rejects_invalid_arguments() -> None:
    client = FakeMilvusClient()
    store = _build_store(client)

    assert await store.hard_purge_soft_deleted(retention_seconds=-1) == 0
    assert await store.hard_purge_soft_deleted(batch_limit=0) == 0
    assert client.query_calls == []


async def test_hard_purge_returns_zero_when_nothing_expired() -> None:
    client = FakeMilvusClient()
    client.query_rows = []
    store = _build_store(client)

    assert await store.hard_purge_soft_deleted(retention_seconds=0) == 0
    assert client.deletes == []


async def test_hard_purge_returns_zero_on_query_failure() -> None:
    store = _build_store(FailingQueryClient())

    assert await store.hard_purge_soft_deleted(retention_seconds=0) == 0


async def test_search_excludes_soft_deleted_by_default() -> None:
    client = FakeMilvusClient()
    client.search_hits = [
        [{"distance": 0.77, "entity": {"chunk_id": "c1", "raw_text": "hello", "version": 2}}]
    ]
    store = _build_store(client)

    results = await store.search("查询", top_k=3)

    assert results == [
        {
            "chunk_id": "c1",
            "doc_id": "",
            "source_file": "",
            "chunk_type": "",
            "section_path": "",
            "raw_text": "hello",
            "embedding_text": "",
            "version": 2,
            "content_hash": "",
            "vector_score": 0.77,
        }
    ]
    assert "is_deleted == false" in client.search_calls[0]["filter"]


async def test_search_can_include_soft_deleted() -> None:
    client = FakeMilvusClient()
    store = _build_store(client)

    await store.search("查询", top_k=3, include_deleted=True)

    assert client.search_calls[0]["filter"] == ""


# ---------------------------------------------------------------------- #
# 构造期：Collection 建表与 embedding 解析
# ---------------------------------------------------------------------- #


class FakeSchema:
    def __init__(self) -> None:
        self.fields: list[tuple[str, object, dict]] = []
        self.functions: list[object] = []

    def add_field(self, name: str, dtype: object, **kwargs) -> None:
        self.fields.append((name, dtype, kwargs))

    def add_function(self, func: object) -> None:
        self.functions.append(func)


class FakeIndexParams:
    def __init__(self) -> None:
        self.indices: list[dict] = []

    def add_index(self, **kwargs) -> None:
        self.indices.append(kwargs)


class SchemaAwareClient(FakeMilvusClient):
    def __init__(self, *, collection_exists: bool = False) -> None:
        super().__init__()
        self.collection_exists = collection_exists
        self.schema = FakeSchema()
        self.index_params = FakeIndexParams()
        self.create_collection_calls: list[dict[str, Any]] = []
        self.uri: str | None = None

    def has_collection(self, name: str) -> bool:
        self.has_collection_name = name
        return self.collection_exists

    def create_schema(self, **kwargs):
        self.create_schema_kwargs = kwargs
        return self.schema

    def prepare_index_params(self):
        return self.index_params

    def create_collection(self, **kwargs) -> None:
        self.create_collection_calls.append(kwargs)


def _construct_store(monkeypatch, client: SchemaAwareClient) -> MilvusStore:
    import app.knowledge.infrastructure.doc_parser.retrieval.milvus_store as store_module

    def fake_milvus_client(uri: str):
        client.uri = uri
        return client

    monkeypatch.setattr(store_module, "MilvusClient", fake_milvus_client)
    return MilvusStore(
        RetrievalConfig(milvus_collection_name="rag_documents", enable_rerank=False),
        FakeBatchEmbedding(),
    )


def test_constructor_creates_collection_with_bm25_function(monkeypatch) -> None:
    client = SchemaAwareClient(collection_exists=False)

    store = _construct_store(monkeypatch, client)

    assert len(client.create_collection_calls) == 1
    field_names = [name for name, _dtype, _kw in client.schema.fields]
    assert "chunk_id" in field_names
    assert "sparse_vector" in field_names
    assert "is_deleted" in field_names
    # BM25 由服务端 Function 产出，这是稀疏检索能对上 id 空间的前提
    assert len(client.schema.functions) == 1
    indexed = [entry["field_name"] for entry in client.index_params.indices]
    assert sorted(indexed) == ["embedding", "sparse_vector"]
    assert store.retrieval_core.bm25_drop_ratio == store.config.bm25_drop_ratio


def test_constructor_skips_creation_when_collection_exists(monkeypatch) -> None:
    client = SchemaAwareClient(collection_exists=True)

    _construct_store(monkeypatch, client)

    assert client.create_collection_calls == []


def test_constructor_builds_uri_from_config(monkeypatch) -> None:
    client = SchemaAwareClient(collection_exists=True)

    _construct_store(monkeypatch, client)

    from app.shared.core.config import settings

    assert client.uri == f"http://{settings.MILVUS_HOST}:{settings.MILVUS_PORT}"


def test_resolve_embedding_model_falls_back_to_shared_factory(monkeypatch) -> None:
    """未注入时必须走全局共享工厂，不能自己读环境变量另建一份。"""
    import app.shared.core.embeddings as embeddings_module

    sentinel = FakeBatchEmbedding()
    monkeypatch.setattr(embeddings_module, "create_embedding_model", lambda: sentinel)
    embeddings_module.reset_embedding_model()

    store = MilvusStore.__new__(MilvusStore)
    resolved = store._resolve_embedding_model(None)

    assert resolved is sentinel
    embeddings_module.reset_embedding_model()


async def test_hybrid_search_formats_rrf_scores() -> None:
    client = FakeMilvusClient()
    store = _build_store(client)

    class StubCore:
        def __init__(self) -> None:
            self.calls: list[dict[str, Any]] = []

        async def search_hybrid(self, query, **kwargs):
            self.calls.append({"query": query, **kwargs})
            return [{"score": 0.66, "entity": {"chunk_id": "c9", "raw_text": "hi"}}]

    stub = StubCore()
    store.retrieval_core = stub  # type: ignore[assignment]

    results = await store.hybrid_search("查询", top_k=2)

    assert results[0]["chunk_id"] == "c9"
    assert results[0]["rrf_score"] == 0.66
    assert "is_deleted == false" in stub.calls[0]["filter_expr"]


# ---------------------------------------------------------------------- #
# 知识分域（owner_id，特性开关）
# ---------------------------------------------------------------------- #


def test_owner_scope_filter_shapes() -> None:
    from app.knowledge.infrastructure.doc_parser.retrieval.doc_lifecycle import (
        owner_scope_filter,
    )

    assert owner_scope_filter(None) == 'owner_id == "global"'
    assert owner_scope_filter("7") == 'owner_id in ["global", "7"]'


async def test_search_without_visibility_flag_keeps_filter_unchanged() -> None:
    """默认关闭：不叠加 owner 过滤（存量集合无该字段，开了会全排除）。"""
    client = FakeMilvusClient()
    store = _build_store(client)

    await store.search("查询", top_k=3)

    assert "owner_id" not in client.search_calls[0]["filter"]


async def test_search_with_visibility_flag_scopes_to_user(monkeypatch) -> None:
    from app.shared.core.app_config import RagVisibilityConfig
    from app.shared.core.config import settings as app_settings
    from app.shared.core.identity import set_current_user_id

    scoped_config = app_settings.app_config.model_copy(
        update={"rag_visibility": RagVisibilityConfig(enabled=True)}
    )
    monkeypatch.setattr(
        type(app_settings), "app_config", property(lambda self: scoped_config)
    )
    set_current_user_id(7)
    try:
        client = FakeMilvusClient()
        store = _build_store(client)

        await store.search("查询", top_k=3)

        applied = client.search_calls[0]["filter"]
        assert 'owner_id in ["global", "7"]' in applied
        assert "is_deleted == false" in applied
    finally:
        set_current_user_id(None)


async def test_insert_chunks_stamps_owner(monkeypatch) -> None:
    client = FakeMilvusClient()
    store = _build_store(client, FakeBatchEmbedding())

    await store.insert_chunks([_chunk("c0")], owner_id="7")

    assert client.inserts[0][0]["owner_id"] == "7"


async def test_insert_chunks_defaults_to_global_owner() -> None:
    client = FakeMilvusClient()
    store = _build_store(client, FakeBatchEmbedding())

    await store.insert_chunks([_chunk("c0")])

    assert client.inserts[0][0]["owner_id"] == "global"
