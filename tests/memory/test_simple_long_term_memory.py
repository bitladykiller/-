import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
import app.knowledge.infrastructure.ltm.simple_long_term_memory as ltm_module


class FakeEmbeddingModel:
    def __init__(self, embedding: list[float] | None = None) -> None:
        self.embedding = embedding or [0.1, 0.2]
        self.queries: list[str] = []

    def embed_query(self, text: str) -> list[float]:
        self.queries.append(text)
        return self.embedding


class FakeRetrievalCore:
    def __init__(self) -> None:
        self.hybrid_calls: list[dict] = []
        self.hybrid_hits = [
            {
                "entity": {
                    "memory_id": "mem-2",
                    "tenant_id": "tenant-1",
                    "user_id": "user-1",
                    "memory_type": "solution_note",
                    "content": "建议先检查路由器",
                },
                "score": 0.88,
            }
        ]

    async def search_hybrid(self, query: str, **kwargs):
        self.hybrid_calls.append({"query": query, **kwargs})
        return self.hybrid_hits


def _run(awaitable):
    return asyncio.run(awaitable)


def _build_ltm(monkeypatch, *, retrieval_core: FakeRetrievalCore | None = None):
    monkeypatch.setattr(ltm_module, "ensure_collection_ready", lambda **_kwargs: None)
    embedding_model = FakeEmbeddingModel()
    return ltm_module.SimpleLongTermMemory(
        milvus_client=object(),
        embedding_model=embedding_model,
        collection_name="memory_coll",
        retrieval_core=retrieval_core,
    ), embedding_model


def test_constructor_uses_injected_retrieval_core(monkeypatch) -> None:
    fake_core = FakeRetrievalCore()
    ltm, _embedding_model = _build_ltm(monkeypatch, retrieval_core=fake_core)

    assert ltm.retrieval_core is fake_core


def test_save_memory_inserts_record_built_from_embedding(monkeypatch) -> None:
    inserted_records: list[list[dict]] = []
    monkeypatch.setattr(ltm_module, "ensure_collection_ready", lambda **_kwargs: None)
    monkeypatch.setattr(
        ltm_module,
        "insert_records",
        lambda _client, _collection_name, records: inserted_records.append(records),
    )

    ltm = ltm_module.SimpleLongTermMemory(
        milvus_client=object(),
        embedding_model=FakeEmbeddingModel([0.3, 0.4]),
        collection_name="memory_coll",
        retrieval_core=FakeRetrievalCore(),
    )
    monkeypatch.setattr(ltm, "_now_ts", lambda: 123)

    memory_id = _run(
        ltm.save_memory(
            "tenant-1",
            "user-1",
            "solution_note",
            "建议先检查网络",
        )
    )

    assert memory_id is not None
    assert inserted_records == [
        [
            {
                "memory_id": memory_id,
                "tenant_id": "tenant-1",
                "user_id": "user-1",
                "memory_type": "solution_note",
                "content": "建议先检查网络",
                "embedding": [0.3, 0.4],
                "created_at": 123,
                "updated_at": 123,
                "last_hit_at": 0,
                "hit_count": 0,
                "is_deleted": False,
            }
        ]
    ]


def test_hybrid_search_uses_multiplier_for_search_limit(monkeypatch) -> None:
    fake_core = FakeRetrievalCore()
    ltm, _embedding_model = _build_ltm(monkeypatch, retrieval_core=fake_core)

    results = _run(
        ltm.hybrid_search(
            "tenant-1",
            "user-1",
            "路由器经常断网",
            top_k=3,
            score_threshold=0.82,
        )
    )

    assert len(results) == 1
    assert results[0].memory.memory_id == "mem-2"
    assert fake_core.hybrid_calls == [
        {
            "query": "路由器经常断网",
            "limit": 3,
            "filter_expr": 'tenant_id == "tenant-1" and user_id == "user-1" and is_deleted == false',
            "output_fields": ltm_module.MEMORY_OUTPUT_FIELDS,
            "score_threshold": 0.82,
            "search_limit": 6,
        }
    ]


def test_deduplicate_memory_returns_false_when_hit_threshold_reached(monkeypatch) -> None:
    helper_calls: list[dict] = []
    monkeypatch.setattr(ltm_module, "ensure_collection_ready", lambda **_kwargs: None)
    monkeypatch.setattr(
        ltm_module,
        "should_insert_memory",
        lambda **kwargs: helper_calls.append(kwargs) or False,
    )

    ltm = ltm_module.SimpleLongTermMemory(
        milvus_client=object(),
        embedding_model=FakeEmbeddingModel([0.5, 0.6]),
        collection_name="memory_coll",
        retrieval_core=FakeRetrievalCore(),
    )
    ltm.deduplication_config = {"top_k": 2, "similarity_threshold": 0.9}

    should_save = _run(
        ltm.deduplicate_memory(
            "tenant-1",
            "user-1",
            "issue_history",
            "空调一直滴水",
        )
    )

    assert should_save is False
    assert helper_calls == [
        {
            "milvus_client": ltm.milvus_client,
            "collection_name": "memory_coll",
            "embedding": [0.5, 0.6],
            "filter_expr": 'tenant_id == "tenant-1" and user_id == "user-1" and '
            'memory_type == "issue_history" and is_deleted == false',
            "top_k": 2,
            "output_fields": ltm_module.DEDUP_OUTPUT_FIELDS,
            "similarity_threshold": 0.9,
        }
    ]


def test_update_memory_hit_info_updates_memory_and_upserts_partial_record(monkeypatch) -> None:
    upserted_records: list[list[dict]] = []
    ltm, _embedding_model = _build_ltm(monkeypatch, retrieval_core=FakeRetrievalCore())
    ltm.update_on_hit_config = {
        "enabled": True,
        "update_last_hit_at": True,
        "increase_hit_count": True,
    }
    monkeypatch.setattr(ltm, "_now_ts", lambda: 200)
    monkeypatch.setattr(
        ltm_module,
        "upsert_records",
        lambda _client, _collection_name, records: upserted_records.append(records),
    )
    memory = ltm_module.LongTermMemory(
        memory_id="mem-1",
        tenant_id="tenant-1",
        user_id="user-1",
        memory_type="issue_history",
        content="门铃掉线",
        hit_count=2,
        last_hit_at=100,
    )

    updated = _run(ltm.update_memory_hit_info(memory))

    assert updated is True
    assert memory.hit_count == 3
    assert memory.last_hit_at == 200
    assert upserted_records == [
        [
            {
                "memory_id": "mem-1",
                "updated_at": 200,
                "hit_count": 3,
                "last_hit_at": 200,
            }
        ]
    ]
