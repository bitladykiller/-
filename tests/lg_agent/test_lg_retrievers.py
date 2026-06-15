import asyncio

import app.chat.infrastructure.retrievers.retriever_implementations as retriever_implementations
import app.chat.infrastructure.retrievers.retriever_runtime as retriever_runtime
from app.chat.infrastructure.retrievers.retriever_contracts import (
    KG_RETRIEVER_NAME,
    RAG_RETRIEVER_NAME,
    Retriever,
)


class FakeSearcher:
    def __init__(self, *, result=None, error: Exception | None = None) -> None:
        self.result = result or []
        self.error = error

    async def search(self, task: str):
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


class FakeRetriever(Retriever):
    def __init__(self, payload: dict) -> None:
        self.payload = payload

    async def search(self, task: str) -> dict:
        return {"task": task, **self.payload}


class FakeRagRetriever(FakeRetriever):
    def __init__(self) -> None:
        super().__init__({"records": [{"id": 2}]})


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
    assert result["steps"] == ["execute_rag_search"]


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
            "cyphers": [{"records": [{"name": "Alice"}], "errors": []}],
            "steps": ["text2cypher"],
        }
    )
    retriever = retriever_implementations.KnowledgeGraphRetriever(t2c_agent)

    result = _run(retriever.search("查用户"))

    assert t2c_agent.calls == [{"task": "查用户"}]
    assert result["task"] == "查用户"
    assert result["records"] == [{"name": "Alice"}]
    assert result["errors"] == []
    assert result["steps"] == ["text2cypher"]


def test_knowledge_graph_retriever_flattens_cypher_payload() -> None:
    retriever = retriever_implementations.KnowledgeGraphRetriever(
        FakeT2CAgent(
            {
                "cyphers": [
                    {"records": [{"id": 1}], "errors": ["warn-1"]},
                    {"records": [{"id": 2}], "errors": []},
                    {"records": [], "errors": ["warn-2"]},
                ]
                ,
                "steps": ["kg"],
            }
        )
    )

    payload = _run(retriever.search("查订单"))

    assert payload == {
        "task": "查订单",
        "records": [{"id": 1}, {"id": 2}],
        "errors": ["warn-1", "warn-2"],
        "steps": ["kg"],
    }


def test_get_retriever_uses_runtime_registry(monkeypatch) -> None:
    import app.chat.infrastructure.kg_sub_graph.agentic_rag_agents.components.predefined_cypher.cypher_dict as cypher_dict
    import app.chat.infrastructure.kg_sub_graph.agentic_rag_agents.retrievers.cypher_examples.northwind_retriever as northwind_retriever
    import app.chat.infrastructure.kg_sub_graph.agentic_rag_agents.workflows.single_agent.text2cypher as text2cypher
    import app.chat.infrastructure.kg_sub_graph.kg_neo4j_conn as kg_neo4j_conn
    import app.chat.infrastructure.modeling.models as lg_models

    monkeypatch.setattr(
        retriever_runtime,
        "_registry",
        {},
    )
    monkeypatch.setattr(retriever_runtime, "_cypher_example_retriever", None)
    monkeypatch.setattr(retriever_runtime, "_t2c_agent", None)
    created: dict[str, object] = {}
    fake_graph = object()
    fake_model = object()
    fake_agent = object()

    class FakeNorthwindRetriever:
        def __init__(self) -> None:
            created["cypher_examples"] = int(created.get("cypher_examples", 0)) + 1

        def get_examples(self, query: str, k: int = 5) -> str:
            created["example_query"] = query
            created["example_k"] = k
            return "Question: 示例\nCypher: MATCH (n) RETURN n"

    class FakeKgRetriever(FakeRetriever):
        def __init__(self, agent) -> None:
            created["kg_retrievers"] = int(created.get("kg_retrievers", 0)) + 1
            created["kg_agent"] = agent
            super().__init__({"records": [{"id": 1}]})

    def fake_create_text2cypher_agent(**kwargs):
        created["t2c_calls"] = int(created.get("t2c_calls", 0)) + 1
        created["t2c_kwargs"] = kwargs
        return fake_agent

    monkeypatch.setattr(kg_neo4j_conn, "get_neo4j_graph", lambda: fake_graph)
    monkeypatch.setattr(
        northwind_retriever,
        "NorthwindCypherRetriever",
        FakeNorthwindRetriever,
    )
    monkeypatch.setattr(
        cypher_dict,
        "predefined_cypher_dict",
        {"query_a": "MATCH (n) RETURN n"},
    )
    monkeypatch.setattr(cypher_dict, "QUERY_DESCRIPTIONS", {"query_a": "desc"})
    monkeypatch.setattr(
        text2cypher,
        "create_text2cypher_agent",
        fake_create_text2cypher_agent,
    )
    monkeypatch.setattr(lg_models, "cypher_model", fake_model)
    monkeypatch.setattr(
        retriever_implementations,
        "KnowledgeGraphRetriever",
        FakeKgRetriever,
    )
    monkeypatch.setattr(
        retriever_implementations,
        "MilvusDocRetriever",
        FakeRagRetriever,
    )

    kg = _run(
        retriever_runtime.get_retriever(KG_RETRIEVER_NAME)
    )
    rag = _run(
        retriever_runtime.get_retriever(RAG_RETRIEVER_NAME)
    )

    assert isinstance(kg, FakeKgRetriever)
    assert isinstance(rag, FakeRagRetriever)
    assert created == {
        "cypher_examples": 1,
        "kg_agent": fake_agent,
        "kg_retrievers": 1,
        "t2c_calls": 1,
        "t2c_kwargs": {
            "llm": fake_model,
            "graph": fake_graph,
            "get_examples": retriever_runtime._cypher_example_retriever.get_examples,
            "predefined_cypher_dict": {"query_a": "MATCH (n) RETURN n"},
            "query_descriptions": {"query_a": "desc"},
        },
    }


def test_get_retriever_does_not_touch_kg_runtime_when_only_rag_is_requested(monkeypatch) -> None:
    import app.chat.infrastructure.kg_sub_graph.kg_neo4j_conn as kg_neo4j_conn

    monkeypatch.setattr(retriever_runtime, "_registry", {})
    monkeypatch.setattr(retriever_runtime, "_cypher_example_retriever", None)
    monkeypatch.setattr(retriever_runtime, "_t2c_agent", None)

    kg_graph_calls = {"count": 0}

    def fake_get_neo4j_graph():
        kg_graph_calls["count"] += 1
        return object()

    monkeypatch.setattr(kg_neo4j_conn, "get_neo4j_graph", fake_get_neo4j_graph)
    monkeypatch.setattr(
        retriever_implementations,
        "MilvusDocRetriever",
        FakeRagRetriever,
    )

    rag = _run(retriever_runtime.get_retriever(RAG_RETRIEVER_NAME))

    assert isinstance(rag, FakeRagRetriever)
    assert kg_graph_calls == {"count": 0}


def test_get_retriever_raises_for_unknown_name(monkeypatch) -> None:
    monkeypatch.setattr(retriever_runtime, "_registry", {})

    try:
        _run(retriever_runtime.get_retriever("unknown"))
    except KeyError as exc:
        assert str(exc) == "'unknown retriever: unknown'"
    else:  # pragma: no cover - test contract guard
        raise AssertionError("expected KeyError")
