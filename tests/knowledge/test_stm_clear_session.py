"""Redis STM clear_session 测试。"""

from __future__ import annotations

import asyncio

from app.knowledge.infrastructure.stm.redis_short_term_memory import RedisShortTermMemory


class FakeRedis:
    def __init__(self) -> None:
        self.deleted_keys: list[str] = []

    async def delete(self, *keys: str) -> int:
        self.deleted_keys.extend(keys)
        return len(keys)


def test_clear_session_deletes_all_session_keys() -> None:
    redis_client = FakeRedis()
    stm = RedisShortTermMemory(redis_client)  # type: ignore[arg-type]

    deleted = asyncio.run(stm.clear_session("default", "7", "42"))

    assert deleted == 4
    assert redis_client.deleted_keys == [
        "agent:stm:default:7:42:messages",
        "agent:stm:default:7:42:summary",
        "agent:stm:default:7:42:meta",
        "agent:stm:default:7:42:lock",
    ]
