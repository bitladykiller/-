"""STM Redis 往返合并的回归测试。

写入路径以前是"每条消息 4 次 Redis 往返"，压缩重写窗口更是 4N 次。
这些断言把"合并成一次 pipeline"钉住，避免以后有人改回逐条 await。
"""

from __future__ import annotations

import asyncio
from typing import Any

from app.knowledge.domain.schemas import MessageRecord, SessionMeta
from app.knowledge.infrastructure.stm.redis_short_term_memory import (
    RedisShortTermMemory,
    rewrite_recent_messages,
)


class FakePipeline:
    def __init__(self, recorder: FakeRedis) -> None:
        self._recorder = recorder
        self.commands: list[tuple[str, tuple[Any, ...]]] = []

    async def __aenter__(self) -> FakePipeline:
        return self

    async def __aexit__(self, *exc_info: object) -> None:
        return None

    def _record(self, name: str, *args: Any) -> FakePipeline:
        self.commands.append((name, args))
        return self

    def zadd(self, *args: Any) -> FakePipeline:
        return self._record("zadd", *args)

    def delete(self, *args: Any) -> FakePipeline:
        return self._record("delete", *args)

    def zremrangebyrank(self, *args: Any) -> FakePipeline:
        return self._record("zremrangebyrank", *args)

    def zremrangebyscore(self, *args: Any) -> FakePipeline:
        return self._record("zremrangebyscore", *args)

    def expire(self, *args: Any) -> FakePipeline:
        return self._record("expire", *args)

    async def execute(self) -> list[Any]:
        self._recorder.executed_batches.append(list(self.commands))
        return []


class FakeRedis:
    """只实现 pipeline；任何直连命令都会 AttributeError，从而暴露未合并的调用。"""

    def __init__(self) -> None:
        self.executed_batches: list[list[tuple[str, tuple[Any, ...]]]] = []
        self.pipeline_calls = 0

    def pipeline(self, transaction: bool = True) -> FakePipeline:
        self.pipeline_calls += 1
        return FakePipeline(self)


def _message(message_id: str, turn_index: int = 1) -> MessageRecord:
    return MessageRecord(
        message_id=message_id,
        role="user",
        content=f"内容 {message_id}",
        created_at=1_700_000_000_000 + turn_index,
        turn_index=turn_index,
    )


def _command_names(batch: list[tuple[str, tuple[Any, ...]]]) -> list[str]:
    return [name for name, _args in batch]


async def test_append_message_uses_single_round_trip() -> None:
    redis = FakeRedis()
    stm = RedisShortTermMemory(redis)  # type: ignore[arg-type]

    await stm.append_message("tenant-1", "user-1", "session-1", _message("m1"))

    assert redis.pipeline_calls == 1
    assert len(redis.executed_batches) == 1
    assert _command_names(redis.executed_batches[0]) == [
        "zadd",
        "zremrangebyrank",
        "zremrangebyscore",
        "expire",
    ]


async def test_append_messages_batches_a_whole_turn() -> None:
    redis = FakeRedis()
    stm = RedisShortTermMemory(redis)  # type: ignore[arg-type]

    await stm.append_messages(
        "tenant-1",
        "user-1",
        "session-1",
        [_message("u1", 1), _message("a1", 1)],
    )

    # 一轮两条消息仍然只有一次往返，且一次 zadd 带两个成员
    assert redis.pipeline_calls == 1
    zadd_name, zadd_args = redis.executed_batches[0][0]
    assert zadd_name == "zadd"
    assert len(zadd_args[1]) == 2


async def test_append_messages_skips_empty_input() -> None:
    redis = FakeRedis()
    stm = RedisShortTermMemory(redis)  # type: ignore[arg-type]

    await stm.append_messages("tenant-1", "user-1", "session-1", [])

    assert redis.pipeline_calls == 0


async def test_rewrite_recent_messages_batches_delete_and_readd() -> None:
    redis = FakeRedis()
    stm = RedisShortTermMemory(redis)  # type: ignore[arg-type]
    messages = [_message(f"m{index}", index) for index in range(1, 6)]

    await rewrite_recent_messages(
        redis_client=redis,
        key="agent:stm:t:u:s:messages",
        messages=messages,
        runtime=stm.settings,
    )

    # 保留 5 条消息：以前是 delete + 5×(zadd+3 条修剪) = 21 次往返
    assert redis.pipeline_calls == 1
    assert _command_names(redis.executed_batches[0]) == [
        "delete",
        "zadd",
        "zremrangebyrank",
        "zremrangebyscore",
        "expire",
    ]
    _zadd_name, zadd_args = redis.executed_batches[0][1]
    assert len(zadd_args[1]) == 5


async def test_rewrite_recent_messages_noop_on_empty() -> None:
    redis = FakeRedis()
    stm = RedisShortTermMemory(redis)  # type: ignore[arg-type]

    await rewrite_recent_messages(
        redis_client=redis,
        key="k",
        messages=[],
        runtime=stm.settings,
    )

    assert redis.pipeline_calls == 0


class RecordingRedis(FakeRedis):
    """在 pipeline 之外还支持 get/set/delete，用于压缩全流程。"""

    def __init__(self) -> None:
        super().__init__()
        self.store: dict[str, Any] = {}
        self.zset: dict[str, dict[bytes, int]] = {}
        self.lock_acquired = False

    async def get(self, key: str) -> Any:
        return self.store.get(key)

    async def set(self, key: str, value: Any, ex: int | None = None, nx: bool = False) -> Any:
        if nx and key in self.store:
            return None
        if nx:
            self.lock_acquired = True
        self.store[key] = value
        return True

    async def delete(self, *keys: str) -> int:
        return sum(1 for key in keys if self.store.pop(key, None) is not None)

    async def zcard(self, key: str) -> int:
        return len(self.zset.get(key, {}))

    async def zrevrange(self, key: str, start: int, end: int) -> list[bytes]:
        members = list(self.zset.get(key, {}))
        return members[start : end + 1]

    async def expire(self, key: str, ttl: int) -> bool:
        return True


async def test_compress_session_memory_reads_state_concurrently() -> None:
    """meta / 计数 / 摘要 / 消息四路读取互不依赖，必须并发。"""
    redis = RecordingRedis()
    stm = RedisShortTermMemory(redis)  # type: ignore[arg-type]

    arrived = 0
    all_arrived = asyncio.Event()

    async def rendezvous() -> None:
        nonlocal arrived
        arrived += 1
        if arrived == 4:
            all_arrived.set()
        await asyncio.wait_for(all_arrived.wait(), timeout=2)

    async def slow_meta(*_args, **_kwargs):
        await rendezvous()
        return SessionMeta(total_turns=0, last_updated_at=0, last_compressed_turn=0)

    async def slow_count(*_args, **_kwargs) -> int:
        await rendezvous()
        return 0

    async def slow_summary(*_args, **_kwargs):
        await rendezvous()
        return None

    async def slow_messages(*_args, **_kwargs) -> list[MessageRecord]:
        await rendezvous()
        return []

    stm.get_meta = slow_meta  # type: ignore[assignment]
    stm.get_message_count = slow_count  # type: ignore[assignment]
    stm.get_summary = slow_summary  # type: ignore[assignment]
    stm.get_recent_messages = slow_messages  # type: ignore[assignment]

    async def never_called(_old: str, _messages: list[MessageRecord]) -> str:
        raise AssertionError("未达阈值不该调用 LLM 压缩")

    # 轮次与消息数都为 0，build_compression_context 返回 None → 不触发压缩
    assert await stm.compress_session_memory("t", "u", "s", never_called) is False
    assert arrived == 4
