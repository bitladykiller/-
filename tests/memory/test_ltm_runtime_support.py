import asyncio

from app.memory.ltm_runtime_support import (
    ensure_collection_ready,
    load_merge_clusters,
    search_dense_memories,
    search_hybrid_memories,
    should_insert_memory,
)


class FakeLogger:
    def __init__(self) -> None:
        self.messages: list[str] = []

    def info(self, message: str) -> None:
        self.messages.append(message)


class FakeRetrievalCore:
    def __init__(self) -> None:
        self.dense_calls: list[dict] = []
        self.hybrid_calls: list[dict] = []

    async def search_dense(self, query: str, **kwargs):
        self.dense_calls.append({"query": query, **kwargs})
        return [
            {
                "entity": {
                    "memory_id": "mem-1",
                    "tenant_id": "tenant-1",
                    "user_id": "user-1",
                    "memory_type": "issue_history",
                    "content": "之前咨询过空调维修",
                },
                "score": 0.91,
            }
        ]

    async def search_hybrid(self, query: str, **kwargs):
        self.hybrid_calls.append({"query": query, **kwargs})
        return [
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


def _run(awaitable):
    return asyncio.run(awaitable)


def test_ensure_collection_ready_logs_created_or_existing(monkeypatch) -> None:
    logger = FakeLogger()
    monkeypatch.setattr(
        "app.memory.ltm_runtime_support.ensure_memory_collection",
        lambda *_args, **_kwargs: False,
    )
    ensure_collection_ready(
        milvus_client=object(),
        collection_name="memory_coll",
        logger=logger,
    )

    monkeypatch.setattr(
        "app.memory.ltm_runtime_support.ensure_memory_collection",
        lambda *_args, **_kwargs: True,
    )
    ensure_collection_ready(
        milvus_client=object(),
        collection_name="memory_coll",
        logger=logger,
    )

    assert logger.messages == [
        "Collection memory_coll 已存在",
        "Collection memory_coll 创建成功（含 BM25 全文索引）",
    ]


def test_search_runtime_helpers_delegate_to_retrieval_core() -> None:
    retrieval_core = FakeRetrievalCore()

    dense_results = _run(
        search_dense_memories(
            retrieval_core=retrieval_core,
            query="怎么修空调",
            top_k=4,
            filter_expr='tenant_id == "tenant-1"',
            output_fields=["content"],
            score_threshold=0.75,
        )
    )
    hybrid_results = _run(
        search_hybrid_memories(
            retrieval_core=retrieval_core,
            query="路由器经常断网",
            top_k=3,
            filter_expr='tenant_id == "tenant-1"',
            output_fields=["content"],
            score_threshold=0.82,
            search_limit_multiplier=2,
        )
    )

    assert dense_results[0].memory.memory_id == "mem-1"
    assert hybrid_results[0].memory.memory_id == "mem-2"
    assert retrieval_core.dense_calls == [
        {
            "query": "怎么修空调",
            "limit": 4,
            "filter_expr": 'tenant_id == "tenant-1"',
            "output_fields": ["content"],
            "score_threshold": 0.75,
        }
    ]
    assert retrieval_core.hybrid_calls == [
        {
            "query": "路由器经常断网",
            "limit": 3,
            "filter_expr": 'tenant_id == "tenant-1"',
            "output_fields": ["content"],
            "score_threshold": 0.82,
            "search_limit": 6,
        }
    ]


def test_should_insert_memory_is_inverse_of_dedup_match(monkeypatch) -> None:
    calls: list[dict] = []
    monkeypatch.setattr(
        "app.memory.ltm_runtime_support.search_records",
        lambda _client, _collection_name, embedding, filter_expr, *, limit, output_fields: (
            calls.append(
                {
                    "embedding": embedding,
                    "filter_expr": filter_expr,
                    "limit": limit,
                    "output_fields": output_fields,
                }
            )
            or [[{"distance": 0.95}]]
        ),
    )

    should_insert = should_insert_memory(
        milvus_client=object(),
        collection_name="memory_coll",
        embedding=[0.1, 0.2],
        filter_expr='tenant_id == "tenant-1"',
        top_k=2,
        output_fields=["content"],
        similarity_threshold=0.9,
    )

    assert should_insert is False
    assert calls == [
        {
            "embedding": [0.1, 0.2],
            "filter_expr": 'tenant_id == "tenant-1"',
            "limit": 2,
            "output_fields": ["content"],
        }
    ]


def test_load_merge_clusters_filters_small_result_sets_and_clusters(monkeypatch) -> None:
    monkeypatch.setattr(
        "app.memory.ltm_runtime_support.query_records",
        lambda *_args, **_kwargs: [
            {"memory_id": "mem-1", "embedding": [1.0, 0.0]},
            {"memory_id": "mem-2", "embedding": [0.99, 0.01]},
            {"memory_id": "mem-3", "embedding": [0.0, 1.0]},
        ],
    )

    clusters = load_merge_clusters(
        milvus_client=object(),
        collection_name="memory_coll",
        filter_expr='tenant_id == "tenant-1"',
        output_fields=["embedding"],
        similarity_threshold=0.95,
    )

    assert len(clusters) == 1
    assert [record["memory_id"] for record in clusters[0]] == ["mem-1", "mem-2"]
