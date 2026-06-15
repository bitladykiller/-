import asyncio

import app.chat.infrastructure.kg_sub_graph.agentic_rag_agents.workflows.single_agent.text2cypher as text2cypher


class FakeMatcher:
    def __init__(self, matches: list[dict], params: dict[str, str] | None = None) -> None:
        self.matches = matches
        self.params = params or {}
        self.match_calls: list[tuple[str, int]] = []
        self.extract_calls: list[tuple[str, str, object]] = []

    def match_query(self, normalized_task: str, top_k: int = 1) -> list[dict]:
        self.match_calls.append((normalized_task, top_k))
        return self.matches

    def extract_parameters(
        self,
        normalized_task: str,
        query_name: str,
        llm=None,
    ) -> dict[str, str]:
        self.extract_calls.append((normalized_task, query_name, llm))
        return self.params


class FakeExampleRetriever:
    def __init__(self, examples: str) -> None:
        self.examples = examples
        self.calls: list[tuple[str, int]] = []

    def get_examples(self, query: str, k: int = 5) -> str:
        self.calls.append((query, k))
        return self.examples


class FakeGraph:
    schema = "fake-schema"

    def __init__(self, query_results: list[list[dict]]) -> None:
        self._query_results = list(query_results)
        self.calls: list[tuple[str, dict[str, str] | None]] = []

    def query(
        self,
        statement: str,
        params: dict[str, str] | None = None,
    ) -> list[dict]:
        self.calls.append((statement, params))
        return self._query_results.pop(0)


def _run(awaitable):
    return asyncio.run(awaitable)


def test_create_text2cypher_agent_falls_back_to_generation_when_predefined_miss(
    monkeypatch,
) -> None:
    matcher = FakeMatcher(matches=[])
    graph = FakeGraph(query_results=[[{"id": 1}]])
    example_retriever = FakeExampleRetriever("Question: 样例\nCypher: MATCH example")
    generation_inputs: list[object] = []

    monkeypatch.setattr(
        text2cypher,
        "create_vector_query_matcher",
        lambda *_args, **_kwargs: matcher,
    )
    monkeypatch.setattr(text2cypher, "validate_cypher_query_syntax", lambda **_kwargs: [])
    monkeypatch.setattr(text2cypher, "validate_no_writes_in_cypher_query", lambda *_args: [])
    monkeypatch.setattr(
        text2cypher,
        "correct_cypher_query_relationship_direction",
        lambda **kwargs: kwargs["cypher_statement"],
    )
    monkeypatch.setattr(
        text2cypher,
        "validate_cypher_query_with_schema",
        lambda **_kwargs: [],
    )

    def fake_llm(prompt_value):
        generation_inputs.append(prompt_value)
        return "MATCH (n) RETURN n"

    agent = text2cypher.create_text2cypher_agent(
        llm=fake_llm,
        graph=graph,
        cypher_example_retriever=example_retriever,
        llm_cypher_validation=False,
        predefined_cypher_dict={"query_a": "MATCH (n) RETURN n"},
    )

    result = _run(agent.ainvoke({"task": ["查询订单"]}))

    assert matcher.match_calls == [("查询订单", 1)]
    assert example_retriever.calls == [("查询订单", 3)]
    assert len(generation_inputs) == 1
    assert result["cyphers"][0]["statement"] == "MATCH (n) RETURN n"
    assert result["cyphers"][0]["records"] == [{"id": 1}]
    assert result["cyphers"][0]["steps"] == [
        "predefined_match",
        "generate_cypher",
        "validate_cypher",
        "execute_cypher",
    ]


def test_create_text2cypher_agent_uses_fallback_record_when_execute_returns_empty(
    monkeypatch,
) -> None:
    llm = object()
    matcher = FakeMatcher(
        matches=[
            {
                "similarity": 0.9,
                "query_name": "query_a",
                "cypher": "MATCH (n) RETURN n",
            }
        ],
    )
    graph = FakeGraph(query_results=[[], []])

    monkeypatch.setattr(
        text2cypher,
        "create_vector_query_matcher",
        lambda *_args, **_kwargs: matcher,
    )

    agent = text2cypher.create_text2cypher_agent(
        llm=llm,
        graph=graph,
        cypher_example_retriever=object(),
        predefined_cypher_dict={"query_a": "MATCH (n) RETURN n"},
    )

    result = _run(agent.ainvoke({"task": ["查询订单"]}))

    assert matcher.extract_calls == [("查询订单", "query_a", llm)]
    assert result["cyphers"][0]["records"] == [
        {"error": "I couldn't find any relevant information in the database."}
    ]
    assert result["cyphers"][0]["steps"] == ["predefined_match", "execute_cypher"]
    assert graph.calls == [
        ("MATCH (n) RETURN n", {}),
        ("MATCH (n) RETURN n", None),
    ]
