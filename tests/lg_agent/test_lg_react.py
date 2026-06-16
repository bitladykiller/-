import asyncio

import app.chat.infrastructure.react.react as lg_react
from app.chat.infrastructure.graph.state import AgentState
from langchain_core.messages import AIMessage, ChatMessage, HumanMessage


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

    class FakeRetriever:
        async def search(self, _query: str) -> list[dict]:
            return []

    async def fake_get_retriever(_name: str):
        return FakeRetriever()

    def fake_create_react_agent(**kwargs):
        build_count["count"] += 1
        return built

    monkeypatch.setattr(lg_react, "get_retriever", fake_get_retriever)
    monkeypatch.setattr(lg_react, "create_react_agent", fake_create_react_agent)

    first = _run(lg_react.get_react_subgraph())
    second = _run(lg_react.get_react_subgraph())

    assert first is built
    assert second is built
    assert build_count["count"] == 1


def test_get_react_subgraph_raises_when_kg_retriever_unavailable(monkeypatch) -> None:
    monkeypatch.setattr(lg_react, "_react_subgraph", None)

    async def fake_get_retriever(name: str):
        if name == lg_react.KG_RETRIEVER_NAME:
            return None
        raise AssertionError("rag retriever should not be requested when kg is unavailable")

    monkeypatch.setattr(lg_react, "get_retriever", fake_get_retriever)

    try:
        _run(lg_react.get_react_subgraph())
    except RuntimeError as exc:
        assert str(exc) == "kg retriever unavailable"
    else:  # pragma: no cover - contract guard
        raise AssertionError("expected RuntimeError")


def test_execute_react_returns_no_neo4j_response_when_graph_missing(monkeypatch) -> None:
    async def fake_get_react_subgraph():
        raise RuntimeError("kg retriever unavailable")

    monkeypatch.setattr(lg_react, "get_react_subgraph", fake_get_react_subgraph)

    result = _run(
        lg_react.execute_react(
            AgentState(messages=[HumanMessage(content="帮我查订单")]),
            config={},
        )
    )

    assert [message.content for message in result["messages"]] == [
        "抱歉，知识库服务暂时不可用，请稍后重试。"
    ]


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
    async def fake_enrich_question(*_args):
        return "怎么修空调"

    async def fake_get_react_subgraph():
        return subgraph

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
                        "ReAct 过程记录：\n[user] 怎么修空调\n[assistant] 先检查电源\n\n"
                        "当前候选答案：先检查电源"
                    ),
                },
            ]
        ]


def test_execute_react_preserves_chat_message_role_in_transcript(monkeypatch) -> None:
    judge_model = FakeJudgeModel(FakeAnswerCheck("retry", "需要更多事实"))
    subgraph = FakeCompiledSubgraph(
        {
            "messages": [
                HumanMessage(content="查订单"),
                ChatMessage(role="tool", content="订单状态：已发货"),
                AIMessage(content="订单已发货"),
            ]
        }
    )

    async def fake_enrich_question(*_args):
        return "查订单"

    async def fake_get_react_subgraph():
        return subgraph

    monkeypatch.setattr(lg_react, "REACT_MAX_ATTEMPTS", 1)
    monkeypatch.setattr(lg_react, "enrich_question", fake_enrich_question)
    monkeypatch.setattr(lg_react, "get_react_subgraph", fake_get_react_subgraph)
    monkeypatch.setattr(lg_react, "react_judge_model", judge_model)

    result = _run(
        lg_react.execute_react(
            AgentState(messages=[HumanMessage(content="帮我查订单")]),
            config={},
        )
    )

    assert [message.content for message in result["messages"]] == [
        "正在综合分析...",
        "亲～这个问题回答不了哦～",
    ]
    assert "[tool] 订单状态：已发货" in judge_model.messages[0][1]["content"]


def test_execute_react_retries_on_step_exhaustion_and_returns_fallback(monkeypatch) -> None:
    monkeypatch.setattr(lg_react, "REACT_MAX_ATTEMPTS", 2)
    subgraph = FakeCompiledSubgraph(
        {"messages": [AIMessage(content="Need more steps before finish")]}
    )
    async def fake_enrich_question(*_args):
        return "帮我查订单"

    async def fake_get_react_subgraph():
        return subgraph

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
        "亲～这个问题回答不了哦～",
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
                            "上一次候选答案仍然不充分，请继续按标准 ReAct 检索并补足关键事实。"
                            "不足原因：单次 ReAct 内部步数耗尽，仍未得到足够答案。"
                        ),
                    },
                ]
            },
            {"recursion_limit": lg_react.REACT_RECURSION_LIMIT},
        ),
    ]
