"""Milvus LTM 会话级软删除测试。"""

from __future__ import annotations

import asyncio

from app.knowledge.infrastructure.ltm.simple_long_term_memory import SimpleLongTermMemory


class FakeMilvusClient:
    def __init__(self) -> None:
        self.query_filter = ""
        self.upserted: list[dict] = []

    def has_collection(self, collection_name: str) -> bool:
        return True

    def query(self, *, collection_name: str, filter: str, output_fields: list[str], limit: int):
        self.query_filter = filter
        return [
            {"memory_id": "mem-1"},
            {"memory_id": "mem-2"},
        ]

    def upsert(self, *, collection_name: str, data: list[dict]) -> None:
        self.upserted.extend(data)


class FakeEmbedding:
    def embed_query(self, text: str) -> list[float]:
        return [0.1, 0.2]


def test_soft_delete_session_memories_marks_is_deleted() -> None:
    client = FakeMilvusClient()
    ltm = SimpleLongTermMemory(
        milvus_client=client,  # type: ignore[arg-type]
        embedding_model=FakeEmbedding(),  # type: ignore[arg-type]
        collection_name="ltm_test",
    )

    deleted = asyncio.run(
        ltm.soft_delete_session_memories("default", "7", "42")
    )

    assert deleted == 2
    assert 'session_id == "42"' in client.query_filter
    assert all(item["is_deleted"] is True for item in client.upserted)
    assert {item["memory_id"] for item in client.upserted} == {"mem-1", "mem-2"}
