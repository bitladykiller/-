import asyncio

from langchain_core.messages import AIMessage, HumanMessage

import app.chat.infrastructure.react.react as lg_react
from app.chat.infrastructure.graph.state import AgentState


class FakeCompiledSubgraph:
    def __init__(self, result: dict | None = None) -> None:
        self.result = result or {"messages": [AIMessage(content="最终答案")]}
        self.calls: list[tuple[dict, dict]] = []

    async def ainvoke(self, payload: dict, config: dict):
        self.calls.append((payload, config))
        return self.result


def _run(awaitable):
    return asyncio.run(awaitable)


def test_react_runtime_caches_builder_result(monkeypatch) -> None:
    monkeypatch.setattr(lg_react, "_react_subgraph", None)
    build_count = {"count": 0}
    built = FakeCompiledSubgraph()

    async def fake_builder():
        build_count["count"] += 1
        return built

    first = _run(lg_react.get_react_subgraph(fake_builder))
    second = _run(lg_react.get_react_subgraph(fake_builder))

    assert first is built
    assert second is built
    assert build_count["count"] == 1


def test_execute_react_returns_no_neo4j_response_when_graph_missing(monkeypatch) -> None:
    monkeypatch.setattr(lg_react, "get_neo4j_graph", lambda: None)
    expected = {"messages": [AIMessage(content="图谱不可用")]}
    monkeypatch.setattr(lg_react, "no_neo4j_response", lambda: expected)

    result = _run(
        lg_react.execute_react(
            AgentState(messages=[HumanMessage(content="帮我查订单")]),
            config={},
        )
    )

    assert result == expected
