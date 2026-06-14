import asyncio
from types import SimpleNamespace

from langchain_core.messages import AIMessage, HumanMessage

import app.chat.infrastructure.graph.decision_nodes as lg_decision_nodes
import app.chat.infrastructure.graph.lifecycle_nodes as lg_nodes
from app.chat.infrastructure.graph.state import AgentState


class FakeMemoryMiddleware:
    def __init__(self) -> None:
        self.calls: list[dict[str, str]] = []

    async def after_agent(
        self,
        *,
        tenant_id: str,
        user_id: str,
        session_id: str,
        user_message: str,
        assistant_message: str,
        long_term_memories=None,
    ) -> None:
        self.calls.append(
            {
                "tenant_id": tenant_id,
                "user_id": user_id,
                "session_id": session_id,
                "user_message": user_message,
                "assistant_message": assistant_message,
            }
        )


def _run(awaitable):
    return asyncio.run(awaitable)


def test_route_edges_map_state_to_expected_node_names() -> None:
    state = AgentState(
        messages=[],
        router={"type": "general", "logic": ""},
        next_action="end",
        retrieval_plan={"logic": "", "plan": "GRAPH_ONLY"},
    )

    assert lg_decision_nodes.route_query(state) == "respond_to_general_query"
    assert lg_decision_nodes.guardrails_edge(state) == "after_response"
    assert lg_decision_nodes.retrieval_plan_edge(state) == "execute_graph_only"

    state.router = {"type": "rag_doc-query", "logic": ""}
    state.next_action = "continue"
    state.retrieval_plan = {"logic": "", "plan": "GRAPH_THEN_RAG"}
    assert lg_decision_nodes.route_query(state) == "retrieval_plan_router"
    assert lg_decision_nodes.guardrails_edge(state) == "retrieval_plan_route"
    assert lg_decision_nodes.retrieval_plan_edge(state) == "execute_then"

    state.retrieval_plan = {"logic": "", "plan": "UNKNOWN"}
    assert lg_decision_nodes.retrieval_plan_edge(state) == "execute_react"
    state.retrieval_plan = None
    assert lg_decision_nodes.retrieval_plan_edge(state) == "execute_react"


def test_build_general_query_system_prompt_appends_memory_context(monkeypatch) -> None:
    state = AgentState(
        messages=[HumanMessage(content="请查一下空调")],
        router={"type": "general", "logic": "需要结合上下文"},
    )

    async def fake_load_memory_state(_state, _config, user_message):
        assert user_message == "请查一下空调"
        return SimpleNamespace(
            session_summary=None,
            recent_messages=[],
            long_term_memories=[],
            user_profile=None,
        )

    monkeypatch.setattr(lg_decision_nodes, "load_memory_state", fake_load_memory_state)
    monkeypatch.setattr(lg_decision_nodes, "build_memory_context", lambda *_args: " memory")

    prompt = _run(
        lg_decision_nodes.build_general_query_system_prompt(
            state=state,
            config={},
            general_query_system_prompt="system {logic}",
        )
    )

    assert prompt == "system 需要结合上下文 memory"


def test_guardrails_node_wraps_question_and_blocks_end(monkeypatch) -> None:
    captured: dict[str, str] = {}

    async def fake_ainvoke_structured_question_output(**kwargs):
        captured["question"] = kwargs["question"]
        return SimpleNamespace(decision="end")

    monkeypatch.setattr(
        lg_decision_nodes,
        "ainvoke_structured_question_output",
        fake_ainvoke_structured_question_output,
    )

    result = _run(
        lg_decision_nodes.guardrails_node(
            AgentState(messages=[HumanMessage(content="请查一下空调")]),
            config={},
        )
    )

    assert captured["question"].startswith("<user_message>")
    assert "请查一下空调" in captured["question"]
    assert captured["question"].endswith("</user_message>")
    assert result["next_action"] == "end"
    assert result["messages"][0].content == "抱歉，我家暂时没有这方面的商品，可以在别家看看哦～"


def test_retrieval_plan_route_wraps_question_and_returns_plan(monkeypatch) -> None:
    captured: dict[str, str] = {}

    async def fake_ainvoke_structured_question_output(**kwargs):
        captured["question"] = kwargs["question"]
        return SimpleNamespace(logic="先查图再查文档", plan="GRAPH_THEN_RAG")

    monkeypatch.setattr(
        lg_decision_nodes,
        "ainvoke_structured_question_output",
        fake_ainvoke_structured_question_output,
    )

    result = _run(
        lg_decision_nodes.retrieval_plan_route(
            AgentState(messages=[HumanMessage(content="查订单再看保修")]),
            config={},
        )
    )

    assert captured["question"].startswith("<user_message>")
    assert "查订单再看保修" in captured["question"]
    assert captured["question"].endswith("</user_message>")
    assert result == {
        "retrieval_plan": {
            "logic": "先查图再查文档",
            "plan": "GRAPH_THEN_RAG",
        }
    }


def test_after_response_writes_latest_user_and_final_assistant_message(monkeypatch) -> None:
    middleware = FakeMemoryMiddleware()
    state = AgentState(
        messages=[
            HumanMessage(content="帮我查一下订单"),
            AIMessage(content="正在查询..."),
            AIMessage(content="订单已经发货"),
        ]
    )

    async def fake_get_memory_middleware():
        return middleware

    monkeypatch.setattr(lg_nodes, "get_memory_middleware", fake_get_memory_middleware)
    monkeypatch.setattr(
        lg_nodes,
        "configurable_scope",
        lambda config: ("tenant-1", "user-2", "thread-3"),
    )

    result = _run(lg_nodes.after_response(state, config={}))

    assert result == {}
    assert middleware.calls == [
        {
            "tenant_id": "tenant-1",
            "user_id": "user-2",
            "session_id": "thread-3",
            "user_message": "帮我查一下订单",
            "assistant_message": "订单已经发货",
        }
    ]


def test_after_response_skips_when_missing_complete_message_pair(monkeypatch) -> None:
    middleware = FakeMemoryMiddleware()

    async def fake_get_memory_middleware():
        return middleware

    monkeypatch.setattr(lg_nodes, "get_memory_middleware", fake_get_memory_middleware)
    monkeypatch.setattr(
        lg_nodes,
        "configurable_scope",
        lambda config: ("tenant-1", "user-2", "thread-3"),
    )

    result = _run(
        lg_nodes.after_response(
            AgentState(messages=[HumanMessage(content="只有用户消息")]),
            config={},
        )
    )

    assert result == {}
    assert middleware.calls == []
