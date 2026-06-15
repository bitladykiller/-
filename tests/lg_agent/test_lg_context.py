import asyncio

from app.chat.infrastructure.graph.state import AgentState
from app.chat.infrastructure.memory_bridge import context as lg_context
from app.knowledge.domain.schemas import AgentMemoryState


class FakeMemoryMiddleware:
    def __init__(self, memory_state: AgentMemoryState) -> None:
        self.memory_state = memory_state
        self.calls: list[dict[str, str]] = []

    async def before_agent(
        self,
        *,
        tenant_id: str,
        user_id: str,
        session_id: str,
        user_input: str,
    ) -> AgentMemoryState:
        self.calls.append(
            {
                "tenant_id": tenant_id,
                "user_id": user_id,
                "session_id": session_id,
                "user_input": user_input,
            }
        )
        return self.memory_state


def _run(awaitable):
    return asyncio.run(awaitable)


def test_load_memory_state_returns_cached_state_without_runtime_lookup(monkeypatch) -> None:
    cached_state = AgentMemoryState(
        user_profile={
            "preferred_brand": "海尔",
            "budget_range": None,
            "preferred_category": None,
            "tags": [],
            "facts": [],
        }
    )
    state = AgentState(messages=[], memory_state=cached_state)

    async def unexpected_get_memory_middleware():
        raise AssertionError("runtime lookup should not happen")

    monkeypatch.setattr(lg_context, "get_memory_middleware", unexpected_get_memory_middleware)

    result = _run(lg_context.load_memory_state(state, {}, "你好"))

    assert result is cached_state


def test_load_memory_state_returns_none_when_runtime_unavailable(monkeypatch) -> None:
    state = AgentState(messages=[])

    async def fake_get_memory_middleware():
        return None

    monkeypatch.setattr(lg_context, "get_memory_middleware", fake_get_memory_middleware)

    result = _run(lg_context.load_memory_state(state, {}, "你好"))

    assert result is None
    assert state.memory_state is None


def test_load_memory_state_loads_and_caches_memory_state(monkeypatch) -> None:
    loaded_state = AgentMemoryState(
        user_profile={
            "preferred_brand": None,
            "budget_range": None,
            "preferred_category": "空调",
            "tags": [],
            "facts": [],
        }
    )
    middleware = FakeMemoryMiddleware(loaded_state)
    state = AgentState(messages=[])
    config = {
        "configurable": {
            "tenant_id": "tenant-7",
            "user_id": "user-8",
            "thread_id": "thread-9",
        }
    }

    async def fake_get_memory_middleware():
        return middleware

    monkeypatch.setattr(lg_context, "get_memory_middleware", fake_get_memory_middleware)

    result = _run(lg_context.load_memory_state(state, config, "空调怎么选"))

    assert result is loaded_state
    assert state.memory_state is loaded_state
    assert middleware.calls == [
        {
            "tenant_id": "tenant-7",
            "user_id": "user-8",
            "session_id": "thread-9",
            "user_input": "空调怎么选",
        }
    ]


def test_load_memory_state_uses_default_scope_when_config_missing(monkeypatch) -> None:
    loaded_state = AgentMemoryState()
    middleware = FakeMemoryMiddleware(loaded_state)
    state = AgentState(messages=[])

    async def fake_get_memory_middleware():
        return middleware

    monkeypatch.setattr(lg_context, "get_memory_middleware", fake_get_memory_middleware)

    _run(lg_context.load_memory_state(state, {}, "默认范围测试"))

    assert middleware.calls == [
        {
            "tenant_id": "default",
            "user_id": "anonymous",
            "session_id": "default",
            "user_input": "默认范围测试",
        }
    ]


def test_enrich_question_returns_original_when_memory_missing(monkeypatch) -> None:
    state = AgentState(messages=[])

    async def fake_load_memory_state(
        current_state: AgentState,
        config: dict,
        user_input: str,
    ):
        assert current_state is state
        assert user_input == "你好"
        return None

    monkeypatch.setattr(lg_context, "load_memory_state", fake_load_memory_state)

    result = _run(lg_context.enrich_question(state, {}, "你好"))

    assert result == "你好"


def test_enrich_question_injects_memory_context(monkeypatch) -> None:
    state = AgentState(messages=[])
    memory_state = AgentMemoryState(
        user_profile={
            "preferred_brand": None,
            "budget_range": None,
            "preferred_category": "洗衣机",
            "tags": [],
            "facts": [],
        }
    )

    async def fake_load_memory_state(
        current_state: AgentState,
        config: dict,
        user_input: str,
    ):
        return memory_state

    monkeypatch.setattr(lg_context, "load_memory_state", fake_load_memory_state)

    result = _run(lg_context.enrich_question(state, {}, "预算 3000 左右有什么推荐？"))

    assert "【记忆说明】" in result
    assert "偏好品类: 洗衣机" in result
    assert result.endswith("用户当前问题：预算 3000 左右有什么推荐？")
