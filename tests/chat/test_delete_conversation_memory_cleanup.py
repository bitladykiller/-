"""删除会话时记忆清理编排测试。"""

from __future__ import annotations

import asyncio

from app.chat.application import conversation_service as service_module


class FakeContainer:
    def __init__(self, middleware) -> None:
        self.memory_middleware = middleware


class FakeSTM:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str, str]] = []

    async def clear_session(self, tenant_id: str, user_id: str, session_id: str) -> int:
        self.calls.append((tenant_id, user_id, session_id))
        return 4


class FakeLTM:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str, str]] = []

    async def soft_delete_session_memories(
        self,
        tenant_id: str,
        user_id: str,
        session_id: str,
    ) -> int:
        self.calls.append((tenant_id, user_id, session_id))
        return 2


class FakeMiddleware:
    def __init__(self) -> None:
        self.redis_stm = FakeSTM()
        self.milvus_ltm = FakeLTM()


def test_clear_conversation_memories_calls_stm_and_ltm(monkeypatch) -> None:
    middleware = FakeMiddleware()

    async def fake_get_container():
        return FakeContainer(middleware)

    monkeypatch.setattr(
        "app.platform.container.get_container",
        fake_get_container,
    )

    asyncio.run(
        service_module._clear_conversation_memories(user_id="7", session_id="42")
    )

    assert middleware.redis_stm.calls == [("default", "7", "42")]
    assert middleware.milvus_ltm.calls == [("default", "7", "42")]
