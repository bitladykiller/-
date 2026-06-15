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
    def __init__(self, **kwargs) -> None:
        self.init_kwargs = kwargs
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


class FakeMilvusClient:
    def __init__(self, *, search_result=None) -> None:
        self.insert_calls: list[dict] = []
        self.upsert_calls: list[dict] = []
        self.search_calls: list[dict] = []
        self.search_result = [[{"distance": 0.91}]] if search_result is None else search_result

    def insert(self, **kwargs) -> None:
        self.insert_calls.append(kwargs)

    def upsert(self, **kwargs) -> None:
        self.upsert_calls.append(kwargs)

    def search(self, **kwargs):
        self.search_calls.append(kwargs)
        return self.search_result


def _run(awaitable):
    return asyncio.run(awaitable)


def _build_ltm(
    monkeypatch,
    *,
    client: object | None = None,
    embedding_model: FakeEmbeddingModel | None = None,
    retrieval_core: FakeRetrievalCore | None = None,
):
    monkeypatch.setattr(
        ltm_module.SimpleLongTermMemory,
        "_ensure_memory_collection",
        staticmethod(lambda *_args, **_kwargs: False),
    )
    monkeypatch.setitem(ltm_module.LONG_TERM_MEMORY_CONFIG, "collection_name", "memory_coll")
    fake_core = retrieval_core or FakeRetrievalCore()

    def build_fake_retrieval_core(**kwargs):
        fake_core.init_kwargs = kwargs
        return fake_core

    monkeypatch.setattr(ltm_module, "MilvusHybridSearchCore", build_fake_retrieval_core)
    current_embedding_model = embedding_model or FakeEmbeddingModel()
    return (
        ltm_module.SimpleLongTermMemory(
            milvus_client=client or object(),
            embedding_model=current_embedding_model,
        ),
        current_embedding_model,
        fake_core,
    )


def test_constructor_builds_runtime_retrieval_core(monkeypatch) -> None:
    ltm, embedding_model, fake_core = _build_ltm(monkeypatch)

    assert ltm.retrieval_core is fake_core
    assert fake_core.init_kwargs == {
        "milvus_client": ltm.milvus_client,
        "embedding_model": embedding_model,
        "collection_name": "memory_coll",
        "dense_field": "embedding",
        "sparse_field": "sparse_vector",
        "dense_metric_type": "COSINE",
        "dense_search_params": {"nprobe": 16},
        "hybrid_rrf_k": 60,
    }


def test_hybrid_search_falls_back_for_none_entity_fields(monkeypatch) -> None:
    fake_core = FakeRetrievalCore()
    fake_core.hybrid_hits = [
        {
            "entity": {
                "memory_id": None,
                "tenant_id": "tenant-1",
                "user_id": None,
                "memory_type": "solution_note",
                "content": None,
                "created_at": None,
                "updated_at": 12,
                "last_hit_at": None,
                "hit_count": 3,
                "is_deleted": None,
            },
            "score": 0.88,
        }
    ]
    ltm, _embedding_model, _fake_core = _build_ltm(monkeypatch, retrieval_core=fake_core)

    results = _run(
        ltm.hybrid_search(
            "tenant-1",
            "user-1",
            "网络一直断开",
        )
    )

    assert len(results) == 1
    assert results[0].memory.model_dump() == {
        "memory_id": "",
        "tenant_id": "tenant-1",
        "user_id": "",
        "memory_type": "solution_note",
        "content": "",
        "created_at": 0,
        "updated_at": 12,
        "last_hit_at": 0,
        "hit_count": 3,
        "is_deleted": False,
    }


def test_save_memory_inserts_record_built_from_embedding(monkeypatch) -> None:
    client = FakeMilvusClient()
    ltm, _embedding_model, _fake_core = _build_ltm(
        monkeypatch,
        client=client,
        embedding_model=FakeEmbeddingModel([0.3, 0.4]),
    )
    monkeypatch.setattr(ltm_module.time, "time", lambda: 123)

    memory_id = _run(
        ltm.save_memory(
            "tenant-1",
            "user-1",
            "solution_note",
            "建议先检查网络",
        )
    )

    assert memory_id is not None
    assert client.insert_calls == [
        {
            "collection_name": "memory_coll",
            "data": [
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
            ],
        }
    ]


def test_hybrid_search_uses_multiplier_for_search_limit(monkeypatch) -> None:
    fake_core = FakeRetrievalCore()
    ltm, _embedding_model, _fake_core = _build_ltm(monkeypatch, retrieval_core=fake_core)

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
            "output_fields": [
                "memory_id",
                "tenant_id",
                "user_id",
                "memory_type",
                "content",
                "created_at",
                "updated_at",
                "last_hit_at",
                "hit_count",
                "is_deleted",
            ],
            "score_threshold": 0.82,
            "search_limit": 6,
        }
    ]


def test_hybrid_search_uses_default_search_config_when_overrides_missing(monkeypatch) -> None:
    fake_core = FakeRetrievalCore()
    ltm, _embedding_model, _fake_core = _build_ltm(monkeypatch, retrieval_core=fake_core)
    ltm.search_config = {"top_k": 5, "score_threshold": 0.72}

    _run(
        ltm.hybrid_search(
            "tenant-1",
            "user-1",
            "路由器经常断网",
        )
    )

    assert fake_core.hybrid_calls == [
        {
            "query": "路由器经常断网",
            "limit": 5,
            "filter_expr": 'tenant_id == "tenant-1" and user_id == "user-1" and is_deleted == false',
            "output_fields": [
                "memory_id",
                "tenant_id",
                "user_id",
                "memory_type",
                "content",
                "created_at",
                "updated_at",
                "last_hit_at",
                "hit_count",
                "is_deleted",
            ],
            "score_threshold": 0.72,
            "search_limit": 10,
        }
    ]


def test_hybrid_search_skips_hits_without_entity_dict(monkeypatch) -> None:
    fake_core = FakeRetrievalCore()
    fake_core.hybrid_hits = [
        {"entity": None, "score": 0.5},
        {
            "entity": {
                "memory_id": "mem-3",
                "tenant_id": "tenant-1",
                "user_id": "user-1",
                "memory_type": "issue_history",
                "content": "之前问过洗衣机问题",
            },
            "score": 0.93,
        },
    ]
    ltm, _embedding_model, _fake_core = _build_ltm(monkeypatch, retrieval_core=fake_core)

    results = _run(
        ltm.hybrid_search(
            "tenant-1",
            "user-1",
            "洗衣机一直异响",
        )
    )

    assert len(results) == 1
    assert results[0].memory.memory_id == "mem-3"
    assert results[0].memory.memory_type == "issue_history"


def test_deduplicate_memory_returns_false_when_hit_threshold_reached(monkeypatch) -> None:
    client = FakeMilvusClient(search_result=[[{"distance": 0.95}]])
    ltm, _embedding_model, _fake_core = _build_ltm(
        monkeypatch,
        client=client,
        embedding_model=FakeEmbeddingModel([0.5, 0.6]),
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
    assert client.search_calls == [
        {
            "collection_name": "memory_coll",
            "data": [[0.5, 0.6]],
            "filter": 'tenant_id == "tenant-1" and user_id == "user-1" and '
            'memory_type == "issue_history" and is_deleted == false',
            "limit": 2,
            "output_fields": ["memory_id", "content"],
        }
    ]


def test_deduplicate_memory_returns_true_when_hits_below_threshold(monkeypatch) -> None:
    client = FakeMilvusClient(search_result=[[{"distance": 0.82}, {"distance": 0.88}]])

    ltm, _embedding_model, _fake_core = _build_ltm(
        monkeypatch,
        client=client,
        embedding_model=FakeEmbeddingModel([0.5, 0.6]),
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

    assert should_save is True


def test_update_memory_hit_info_updates_memory_and_upserts_partial_record(monkeypatch) -> None:
    client = FakeMilvusClient()
    ltm, _embedding_model, _fake_core = _build_ltm(monkeypatch, client=client)
    monkeypatch.setattr(ltm_module.time, "time", lambda: 200)
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
    assert client.upsert_calls == [
        {
            "collection_name": "memory_coll",
            "data": [
                {
                    "memory_id": "mem-1",
                    "updated_at": 200,
                    "hit_count": 3,
                    "last_hit_at": 200,
                }
            ],
        }
    ]
