import asyncio
from types import SimpleNamespace

from rag_doc_parser.retrieval.milvus_store import MilvusStore


class FakeRetrievalCore:
    def __init__(self) -> None:
        self.calls: list[dict] = []

    async def search_hybrid(self, query: str, **kwargs) -> list[dict]:
        self.calls.append({"query": query, **kwargs})
        return [
            {
                "score": 0.91,
                "entity": {
                    "source_file": "docs/policy.pdf",
                    "chunk_type": "text",
                    "section_path": "售后 > 保修",
                    "raw_text": "整机保修一年",
                },
            },
            {
                "score": 0.83,
                "entity": {
                    "source_file": "docs/policy.pdf",
                    "chunk_type": "table",
                    "section_path": "售后 > 保修",
                    "raw_text": "压缩机保修三年",
                },
            },
        ]


def _run(awaitable):
    return asyncio.run(awaitable)


def test_hybrid_search_maps_retrieval_core_entity_contract() -> None:
    retrieval_core = FakeRetrievalCore()
    store = MilvusStore.__new__(MilvusStore)
    store.config = SimpleNamespace(
        rrf_final_top_k=3,
        vector_top_k=5,
        bm25_top_k=4,
    )
    store.retrieval_core = retrieval_core

    results = _run(
        store.hybrid_search(
            "保修多久",
            top_k=2,
            filter_expr='doc_id == "doc-1"',
        )
    )

    assert retrieval_core.calls == [
        {
            "query": "保修多久",
            "limit": 2,
            "filter_expr": 'doc_id == "doc-1"',
            "output_fields": [
                "source_file",
                "chunk_type",
                "section_path",
                "raw_text",
            ],
            "search_limit": 5,
        }
    ]
    assert results == [
        {
            "source_file": "docs/policy.pdf",
            "chunk_type": "text",
            "section_path": "售后 > 保修",
            "raw_text": "整机保修一年",
            "rrf_score": 0.91,
        },
        {
            "source_file": "docs/policy.pdf",
            "chunk_type": "table",
            "section_path": "售后 > 保修",
            "raw_text": "压缩机保修三年",
            "rrf_score": 0.83,
        },
    ]
