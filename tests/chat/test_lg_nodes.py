import asyncio
from types import SimpleNamespace

import app.chat.infrastructure.graph.decision_nodes as lg_decision_nodes
import app.chat.infrastructure.graph.lifecycle_nodes as lg_nodes
from app.chat.infrastructure.graph.state import AgentState
from langchain_core.messages import AIMessage, HumanMessage


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


def test_resolve_execution_plan_capability_matrix() -> None:
    resolve = lg_decision_nodes.resolve_execution_plan
    assert (
        resolve(need_graph=True, need_rag=False, mode="single", complexity="simple")
        == "GRAPH_ONLY"
    )
    assert (
        resolve(need_graph=False, need_rag=True, mode="single", complexity="simple")
        == "RAG_ONLY"
    )
    assert (
        resolve(need_graph=True, need_rag=True, mode="parallel", complexity="simple")
        == "PARALLEL"
    )
    assert (
        resolve(need_graph=True, need_rag=True, mode="sequential", complexity="simple")
        == "GRAPH_THEN_RAG"
    )
    assert (
        resolve(need_graph=True, need_rag=True, mode="parallel", complexity="multi_hop")
        == "AGENT_REACT"
    )
    assert (
        resolve(need_graph=False, need_rag=False, mode="single", complexity="simple")
        == "AGENT_REACT"
    )


def test_route_edges_map_state_to_expected_node_names() -> None:
    state = AgentState(
        messages=[],
        router={"type": "general", "logic": ""},
        next_action="end",
        retrieval_plan={
            "logic": "",
            "need_graph": True,
            "need_rag": False,
            "mode": "single",
            "complexity": "simple",
            "resolved_plan": "GRAPH_ONLY",
        },
    )

    assert lg_decision_nodes.route_query(state) == "respond_to_general_query"
    assert lg_decision_nodes.guardrails_edge(state) == "after_response"
    assert lg_decision_nodes.retrieval_plan_edge(state) == "execute_graph_only"

    state.router = {"type": "rag_doc-query", "logic": ""}
    state.next_action = "continue"
    state.retrieval_plan = {
        "logic": "",
        "need_graph": True,
        "need_rag": True,
        "mode": "sequential",
        "complexity": "simple",
        "resolved_plan": "GRAPH_THEN_RAG",
    }
    assert lg_decision_nodes.route_query(state) == "retrieval_plan_router"
    assert lg_decision_nodes.guardrails_edge(state) == "retrieval_plan_route"
    assert lg_decision_nodes.retrieval_plan_edge(state) == "execute_then"

    state.retrieval_plan = {
        "logic": "",
        "need_graph": True,
        "need_rag": True,
        "mode": "parallel",
        "complexity": "multi_hop",
        "resolved_plan": "AGENT_REACT",
    }
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


def test_retrieval_plan_route_wraps_question_and_returns_capability_plan(
    monkeypatch,
) -> None:
    captured: dict[str, str] = {}

    async def fake_ainvoke_structured_question_output(**kwargs):
        captured["question"] = kwargs["question"]
        return SimpleNamespace(
            logic="先查图再查文档",
            need_graph=True,
            need_rag=True,
            mode="sequential",
            complexity="simple",
        )

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
            "need_graph": True,
            "need_rag": True,
            "mode": "sequential",
            "complexity": "simple",
            "resolved_plan": "GRAPH_THEN_RAG",
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

    monkeypatch.setattr(lg_nodes, "_get_memory_middleware", fake_get_memory_middleware)
    monkeypatch.setattr(
        lg_nodes,
        "configurable_scope",
        lambda config: ("tenant-1", "user-2", "thread-3"),
    )

    async def _scenario():
        result = await lg_nodes.after_response(state, config={})
        # 写入已改为后台任务，flush 等它完成后再断言
        await lg_nodes.flush_pending_memory_writes()
        return result

    result = _run(_scenario())

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

    monkeypatch.setattr(lg_nodes, "_get_memory_middleware", fake_get_memory_middleware)
    monkeypatch.setattr(
        lg_nodes,
        "configurable_scope",
        lambda config: ("tenant-1", "user-2", "thread-3"),
    )

    async def _scenario():
        outcome = await lg_nodes.after_response(
            AgentState(messages=[HumanMessage(content="只有用户消息")]),
            config={},
        )
        await lg_nodes.flush_pending_memory_writes()
        return outcome

    result = _run(
        _scenario()
    )

    assert result == {}
    assert middleware.calls == []


def test_after_response_does_not_block_on_slow_memory_write(monkeypatch) -> None:
    """记忆写入必须是后台任务：节点返回不等 after_agent 完成。

    回归背景：after_response 曾同步 await 记忆写入，压缩轮次的
    摘要/抽取 LLM 调用（数秒）会把 SSE 连接一直挂到写完才关闭。
    """

    class SlowMiddleware:
        def __init__(self) -> None:
            self.started = asyncio.Event()
            self.release = asyncio.Event()
            self.finished = False

        async def after_agent(self, **kwargs) -> None:
            self.started.set()
            await self.release.wait()
            self.finished = True

    middleware = SlowMiddleware()

    async def fake_get_memory_middleware():
        return middleware

    monkeypatch.setattr(lg_nodes, "_get_memory_middleware", fake_get_memory_middleware)
    monkeypatch.setattr(
        lg_nodes,
        "configurable_scope",
        lambda config: ("tenant-1", "user-2", "thread-3"),
    )

    async def _scenario() -> None:
        state = AgentState(
            messages=[HumanMessage(content="问题"), AIMessage(content="答案")]
        )
        # 节点必须在写入仍被阻塞时就返回（1 秒内），否则说明退回了同步等待
        await asyncio.wait_for(lg_nodes.after_response(state, config={}), timeout=1)
        assert middleware.finished is False

        # 放行后台任务并确认它真的完成了
        middleware.release.set()
        await lg_nodes.flush_pending_memory_writes()
        assert middleware.finished is True

    _run(_scenario())
