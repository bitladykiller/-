import asyncio
from types import SimpleNamespace

from langchain_core.messages import AIMessage, HumanMessage

import app.chat.infrastructure.graph.decision_nodes as lg_decision_nodes
import app.chat.infrastructure.graph.lifecycle_nodes as lg_nodes
import app.chat.infrastructure.graph.retrieval_nodes as lg_retrieval_nodes
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


class FakeRetriever:
    def __init__(self, name: str) -> None:
        self.name = name


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


def test_respond_to_general_query_appends_memory_context(monkeypatch) -> None:
    state = AgentState(
        messages=[HumanMessage(content="请查一下空调")],
        router={"type": "general", "logic": "需要结合上下文"},
    )
    captured_messages: list[tuple[str, object]] = []

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
    monkeypatch.setattr(
        lg_decision_nodes,
        "build_safe_messages",
        lambda system_prompt, messages: captured_messages.append((system_prompt, messages)) or [],
    )

    class FakeAgentModel:
        async def ainvoke(self, messages):
            assert messages == []
            return AIMessage(content="ok")

    monkeypatch.setattr(lg_decision_nodes, "agent_model", FakeAgentModel())

    result = _run(
        lg_decision_nodes.respond_to_general_query(
            state,
            config={},
        )
    )

    assert captured_messages == [
        (
            lg_decision_nodes.GENERAL_QUERY_SYSTEM_PROMPT.format(logic="需要结合上下文") + " memory",
            state.messages,
        )
    ]
    assert result == {"messages": [AIMessage(content="ok")]}


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


def test_execute_parallel_adds_strategy_specific_queries(monkeypatch) -> None:
    queries: dict[str, str] = {}

    async def fake_get_retriever(name: str):
        return FakeRetriever(name)

    async def fake_enrich_question(_state, _config, user_message: str) -> str:
        assert user_message == "查一下空调"
        return "查一下空调"

    async def fake_search_retriever(retriever: FakeRetriever, query: str) -> dict:
        queries[retriever.name] = query
        return {"records": [{"source": retriever.name}]}

    async def fake_summarize_and_build_response(query, records, **kwargs) -> dict:
        return {"query": query, "records": records, **kwargs}

    monkeypatch.setattr(lg_retrieval_nodes, "get_retriever", fake_get_retriever)
    monkeypatch.setattr(lg_retrieval_nodes, "enrich_question", fake_enrich_question)
    monkeypatch.setattr(lg_retrieval_nodes, "search_retriever", fake_search_retriever)
    monkeypatch.setattr(
        lg_retrieval_nodes,
        "summarize_and_build_response",
        fake_summarize_and_build_response,
    )

    result = _run(
        lg_retrieval_nodes.execute_parallel(
            AgentState(messages=[HumanMessage(content="查一下空调")]),
            config={},
        )
    )

    assert queries == {
        "kg": "查一下空调（仅查询结构化数据：价格、库存、订单等）",
        "rag": "查一下空调（仅查询文档知识：售后政策、保修条款等）",
    }
    assert result == {
        "query": "查一下空调",
        "records": [{"source": "kg"}, {"source": "rag"}],
        "progress_message": "正在同时查询...",
    }


def test_execute_then_injects_graph_records_into_rag_query(monkeypatch) -> None:
    queries: dict[str, str] = {}

    async def fake_get_retriever(name: str):
        return FakeRetriever(name)

    async def fake_enrich_question(_state, _config, user_message: str) -> str:
        assert user_message == "保修多久"
        return "保修多久"

    async def fake_search_retriever(retriever: FakeRetriever, query: str) -> dict:
        queries[retriever.name] = query
        if retriever.name == "kg":
            return {"records": [{"product": "X1"}]}
        return {"records": [{"doc": "warranty"}]}

    async def fake_summarize_and_build_response(query, records, **kwargs) -> dict:
        return {"query": query, "records": records, **kwargs}

    monkeypatch.setattr(lg_retrieval_nodes, "get_retriever", fake_get_retriever)
    monkeypatch.setattr(lg_retrieval_nodes, "enrich_question", fake_enrich_question)
    monkeypatch.setattr(lg_retrieval_nodes, "search_retriever", fake_search_retriever)
    monkeypatch.setattr(
        lg_retrieval_nodes,
        "summarize_and_build_response",
        fake_summarize_and_build_response,
    )

    result = _run(
        lg_retrieval_nodes.execute_then(
            AgentState(messages=[HumanMessage(content="保修多久")]),
            config={},
        )
    )

    assert queries == {
        "kg": "保修多久",
        "rag": "已知信息：[{'product': 'X1'}]\n\n查询：保修多久",
    }
    assert result == {
        "query": "保修多久",
        "records": [{"product": "X1"}, {"doc": "warranty"}],
        "progress_message": "正在先查数据库，再查文档...",
    }
