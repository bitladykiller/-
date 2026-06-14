import asyncio

from langchain_core.messages import AIMessage, HumanMessage

import app.chat.infrastructure.react.react as lg_react
from app.chat.infrastructure.graph.state import AgentState


class FakeAnswerCheck:
    def __init__(self, decision: str, reason: str = "") -> None:
        self.decision = decision
        self.reason = reason


class FakeJudgeModel:
    def __init__(self, result: FakeAnswerCheck) -> None:
        self.result = result
        self.messages: list[list[dict[str, str]]] = []
        self.structured_output_types: list[object] = []

    def with_structured_output(self, output_type):
        self.structured_output_types.append(output_type)
        return self

    async def ainvoke(self, messages):
        self.messages.append(messages)
        return self.result


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


def test_execute_react_returns_checked_answer_with_progress_message(monkeypatch) -> None:
    judge_model = FakeJudgeModel(FakeAnswerCheck("sufficient"))
    subgraph = FakeCompiledSubgraph(
        {
            "messages": [
                HumanMessage(content="怎么修空调"),
                AIMessage(content="先检查电源"),
            ]
        }
    )
    async def fake_enrich_question(*args):
        return "怎么修空调"

    async def fake_get_react_subgraph(_builder):
        return subgraph

    monkeypatch.setattr(lg_react, "get_neo4j_graph", lambda: object())
    monkeypatch.setattr(lg_react, "enrich_question", fake_enrich_question)
    monkeypatch.setattr(lg_react, "get_react_subgraph", fake_get_react_subgraph)
    monkeypatch.setattr(lg_react, "react_judge_model", judge_model)

    result = _run(
        lg_react.execute_react(
            AgentState(messages=[HumanMessage(content="帮我修空调")]),
            config={},
        )
    )

    assert [message.content for message in result["messages"]] == [
        "正在综合分析...",
        "先检查电源",
    ]
    assert subgraph.calls == [
        (
            {"messages": [{"role": "user", "content": "怎么修空调"}]},
            {"recursion_limit": lg_react.REACT_RECURSION_LIMIT},
        )
    ]
    assert judge_model.messages == [
        [
            {"role": "system", "content": lg_react.REACT_ANSWER_CHECK_PROMPT},
            {
                "role": "user",
                "content": (
                    "用户问题：怎么修空调\n\n"
                    "ReAct 过程记录：\n[human] 怎么修空调\n[ai] 先检查电源\n\n"
                    "当前候选答案：先检查电源"
                ),
            },
        ]
    ]


def test_execute_react_retries_on_step_exhaustion_and_returns_fallback(monkeypatch) -> None:
    monkeypatch.setattr(lg_react, "REACT_MAX_ATTEMPTS", 2)
    subgraph = FakeCompiledSubgraph(
        {"messages": [AIMessage(content="Need more steps before finish")]}
    )
    async def fake_enrich_question(*args):
        return "帮我查订单"

    async def fake_get_react_subgraph(_builder):
        return subgraph

    monkeypatch.setattr(lg_react, "get_neo4j_graph", lambda: object())
    monkeypatch.setattr(lg_react, "enrich_question", fake_enrich_question)
    monkeypatch.setattr(lg_react, "get_react_subgraph", fake_get_react_subgraph)

    result = _run(
        lg_react.execute_react(
            AgentState(messages=[HumanMessage(content="帮我查订单")]),
            config={},
        )
    )

    assert [message.content for message in result["messages"]] == [
        "正在综合分析...",
        lg_react.REACT_FALLBACK_ANSWER,
    ]
    assert subgraph.calls == [
        (
            {"messages": [{"role": "user", "content": "帮我查订单"}]},
            {"recursion_limit": lg_react.REACT_RECURSION_LIMIT},
        ),
        (
            {
                "messages": [
                    {"role": "user", "content": "帮我查订单"},
                    {"role": "assistant", "content": "Need more steps before finish"},
                    {
                        "role": "user",
                        "content": (
                            f"{lg_react._REACT_RETRY_PROMPT}"
                            f"不足原因：{lg_react.REACT_STEP_EXHAUSTED_REASON}"
                        ),
                    },
                ]
            },
            {"recursion_limit": lg_react.REACT_RECURSION_LIMIT},
        ),
    ]
