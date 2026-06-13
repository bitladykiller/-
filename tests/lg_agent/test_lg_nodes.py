import asyncio

from langchain_core.messages import AIMessage, HumanMessage

from app.lg_agent import lg_nodes
from app.lg_agent.lg_states import AgentState


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
