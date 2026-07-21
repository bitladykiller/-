"""MilvusStore 软删 / reindex / hard_purge 单元测试（Fake client）。"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any

from app.knowledge.infrastructure.doc_parser.retrieval.config import RetrievalConfig
from app.knowledge.infrastructure.doc_parser.retrieval.milvus_store import MilvusStore


class FakeMilvusClient:
    def __init__(self) -> None:
        self.has_collection_name: str | None = None
        self.upserts: list[list[dict[str, Any]]] = []
        self.inserts: list[list[dict[str, Any]]] = []
        self.deletes: list[str] = []
        self.query_rows: list[dict[str, Any]] = []
        self.query_calls: list[dict[str, Any]] = []

    def has_collection(self, name: str) -> bool:
        self.has_collection_name = name
        return True

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
    def embed_query(self, text: str) -> list[float]:
        return [0.1, 0.2, 0.3]


def _build_store(client: FakeMilvusClient) -> MilvusStore:
    store = MilvusStore.__new__(MilvusStore)
    store.config = RetrievalConfig(milvus_collection_name="rag_documents")
    store.embedding_model = FakeEmbedding()
    store.client = client
    store.retrieval_core = None  # type: ignore[assignment]
    return store


def test_soft_delete_by_doc_id_marks_active_chunks() -> None:
    client = FakeMilvusClient()
    client.query_rows = [
        {"chunk_id": "c1", "version": 2},
        {"chunk_id": "c2", "version": 2},
    ]
    store = _build_store(client)

    result = store.soft_delete_by_doc_id("doc_a")

    assert result["soft_deleted"] == 2
    assert result["max_version"] == 2
    assert len(client.upserts) == 1
    assert all(row["is_deleted"] is True for row in client.upserts[0])
    assert "is_deleted == false" in client.query_calls[0]["filter"]


def test_reindex_document_soft_deletes_then_inserts_next_version() -> None:
    client = FakeMilvusClient()
    client.query_rows = [{"chunk_id": "old-1", "version": 1}]
    store = _build_store(client)
    chunks = [
        SimpleNamespace(
            chunk_id="new-1",
            doc_id="doc_a",
            source_file="a.md",
            chunk_type="text",
            section_path="S",
            raw_text="hello",
            embedding_text="hello",
        )
    ]

    result = asyncio.run(store.reindex_document("doc_a", chunks, content_hash="abc"))

    assert result["soft_deleted"] == 1
    assert result["version"] == 2
    assert result["chunks"] == 1
    assert client.inserts[0][0]["version"] == 2
    assert client.inserts[0][0]["is_deleted"] is False
    assert client.inserts[0][0]["content_hash"] == "abc"


def test_hard_purge_soft_deleted_deletes_by_chunk_id() -> None:
    client = FakeMilvusClient()
    client.query_rows = [{"chunk_id": "dead-1"}, {"chunk_id": "dead-2"}]
    store = _build_store(client)

    deleted = store.hard_purge_soft_deleted(retention_seconds=0, batch_limit=10)

    assert deleted == 2
    assert len(client.deletes) == 1
    assert "dead-1" in client.deletes[0]
    assert "dead-2" in client.deletes[0]
