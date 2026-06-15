import asyncio

import shared_retrieval.milvus_hybrid_core as core_module


class FakeEmbeddingModel:
    def embed_query(self, text: str) -> list[float]:
        assert text == "洗衣机一直异响"
        return [0.1, 0.2]


class FakeMilvusClient:
    def __init__(self) -> None:
        self.search_calls: list[dict] = []
        self.hybrid_search_calls: list[dict] = []

    def search(self, **kwargs):
        self.search_calls.append(kwargs)
        return [
            [
                {"distance": 0.5, "entity": None},
                {"distance": 0.93, "entity": {"memory_id": "mem-3"}},
            ]
        ]

    def hybrid_search(self, **kwargs):
        self.hybrid_search_calls.append(kwargs)
        return [
            [
                {"distance": 0.5, "entity": None},
                {"distance": 0.93, "entity": {"memory_id": "mem-3"}},
            ]
        ]


def _run(awaitable):
    return asyncio.run(awaitable)


def test_search_hybrid_skips_hits_without_entity_dict(monkeypatch) -> None:
    monkeypatch.setattr(core_module, "AnnSearchRequest", lambda **kwargs: kwargs)

    class FakeRRFRanker:
        def __init__(self, k: int) -> None:
            self.k = k

    monkeypatch.setattr(core_module, "RRFRanker", FakeRRFRanker)

    retrieval_core = core_module.MilvusHybridSearchCore(
        milvus_client=FakeMilvusClient(),
        embedding_model=FakeEmbeddingModel(),
        collection_name="memory_coll",
    )

    results = _run(
        retrieval_core.search_hybrid(
            "洗衣机一直异响",
            limit=2,
            output_fields=["memory_id"],
        )
    )

    assert results == [{"score": 0.93, "entity": {"memory_id": "mem-3"}}]
