import asyncio

import app.chat.infrastructure.retrievers.retriever_contracts as retriever_contracts
import app.chat.infrastructure.retrievers.retriever_implementations as retriever_implementations
import app.chat.infrastructure.retrievers.retriever_runtime as retriever_runtime


class FakeSearcher:
    def __init__(self, *, result=None, error: Exception | None = None) -> None:
        self.result = result or []
        self.error = error
        self.calls: list[str] = []

    async def search(self, task: str):
        self.calls.append(task)
        if self.error is not None:
            raise self.error
        return self.result


class FakeT2CAgent:
    def __init__(self, payload: dict) -> None:
        self.payload = payload
        self.calls: list[dict] = []

    async def ainvoke(self, payload: dict):
        self.calls.append(payload)
        return self.payload


class FakeRetriever(retriever_contracts.Retriever):
    def __init__(self, payload: dict) -> None:
        self.payload = payload

    async def search(self, task: str) -> dict:
        return {"task": task, **self.payload}


def _run(awaitable):
    return asyncio.run(awaitable)


def test_milvus_doc_retriever_search_truncates_records() -> None:
    retriever = retriever_implementations.MilvusDocRetriever.__new__(
        retriever_implementations.MilvusDocRetriever
    )
    retriever._searcher = FakeSearcher(
        result=[
            {
                "chunk_type": "text",
                "section_path": f"章节-{index}",
                "source_file": f"doc-{index}.md",
                "raw_text": f"内容-{index}",
                "rrf_score": index / 10,
                "rerank_score": index / 20,
            }
            for index in range(6)
        ]
    )

    result = _run(retriever.search("保修政策"))

    assert len(result["records"]) == 5
    assert result["records"][0] == {
        "chunk_type": "text",
        "section_path": "章节-0",
        "source_file": "doc-0.md",
        "raw_text": "内容-0",
        "rrf_score": 0.0,
        "rerank_score": 0.0,
    }
    assert result["errors"] == []
    assert result["steps"] == [retriever_contracts.RAG_SEARCH_STEP]


def test_milvus_doc_retriever_search_keeps_stable_record_shape() -> None:
    retriever = retriever_implementations.MilvusDocRetriever.__new__(
        retriever_implementations.MilvusDocRetriever
    )
    retriever._searcher = FakeSearcher(
        result=[
            {
                "chunk_type": "text",
                "section_path": "章节-1",
                "source_file": "doc-1.md",
                "raw_text": "内容-1",
                "rrf_score": 0.1,
                "rerank_score": 0.2,
            }
        ]
    )

    result = _run(retriever.search("保修政策"))

    assert result["records"] == [
        {
            "chunk_type": "text",
            "section_path": "章节-1",
            "source_file": "doc-1.md",
            "raw_text": "内容-1",
            "rrf_score": 0.1,
            "rerank_score": 0.2,
        }
    ]


def test_milvus_doc_retriever_search_returns_fallback_record_on_error() -> None:
    retriever = retriever_implementations.MilvusDocRetriever.__new__(
        retriever_implementations.MilvusDocRetriever
    )
    retriever._searcher = FakeSearcher(error=RuntimeError("boom"))

    result = _run(retriever.search("保修政策"))

    assert result["records"] == [{"message": "文档检索暂时不可用。"}]
    assert result["errors"] == ["boom"]


def test_knowledge_graph_retriever_wraps_text2cypher_output() -> None:
    t2c_agent = FakeT2CAgent(
        {
            "cyphers": [{"records": [{"name": "Alice"}]}],
            "errors": [],
            "steps": ["text2cypher"],
        }
    )
    retriever = retriever_implementations.KnowledgeGraphRetriever(t2c_agent)

    result = _run(retriever.search("查用户"))

    assert t2c_agent.calls == [{"task": "查用户"}]
    assert result["task"] == "查用户"
    assert result["records"] == [{"name": "Alice"}]
    assert result["steps"] == ["text2cypher"]


def test_knowledge_graph_retriever_normalizes_records_and_cyphers() -> None:
    retriever = retriever_implementations.KnowledgeGraphRetriever(
        FakeT2CAgent(
            {
                "records": {"name": "alice"},
                "errors": ["warn"],
                "steps": ["kg"],
            }
        )
    )
    cypher_retriever = retriever_implementations.KnowledgeGraphRetriever(
        FakeT2CAgent(
            {
                "cyphers": [
                    {"records": [{"id": 1}]},
                    {"records": {"id": 2}},
                ]
            }
        )
    )

    record_payload = _run(retriever.search("查用户"))
    cypher_payload = _run(cypher_retriever.search("查订单"))

    assert record_payload == {
        "task": "查用户",
        "records": [{"name": "alice"}],
        "errors": ["warn"],
        "steps": ["kg"],
        "raw": {
            "records": {"name": "alice"},
            "errors": ["warn"],
            "steps": ["kg"],
        },
    }
    assert cypher_payload["records"] == [{"id": 1}, {"id": 2}]


def test_get_retriever_uses_runtime_registry(monkeypatch) -> None:
    monkeypatch.setattr(
        retriever_runtime,
        "_registry",
        retriever_contracts.RetrieverRegistry(),
    )
    monkeypatch.setattr(retriever_runtime, "_cypher_example_retriever", None)
    monkeypatch.setattr(retriever_runtime, "_t2c_agent", None)
    call_count = {"register": 0}

    def fake_register_missing() -> None:
        call_count["register"] += 1
        retriever_runtime._get_registry().register(
            retriever_contracts.KG_RETRIEVER_NAME,
            FakeRetriever({"records": [{"id": 1}]}),
        )
        retriever_runtime._get_registry().register(
            retriever_contracts.RAG_RETRIEVER_NAME,
            FakeRetriever({"records": [{"id": 2}]}),
        )

    monkeypatch.setattr(retriever_runtime, "_register_missing_retrievers", fake_register_missing)

    kg = _run(
        retriever_runtime.get_retriever(retriever_contracts.KG_RETRIEVER_NAME)
    )
    rag = _run(
        retriever_runtime.get_retriever(retriever_contracts.RAG_RETRIEVER_NAME)
    )

    assert isinstance(kg, FakeRetriever)
    assert isinstance(rag, FakeRetriever)
    assert call_count["register"] == 1
