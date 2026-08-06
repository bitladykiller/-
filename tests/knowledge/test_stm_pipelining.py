"""STM Redis 往返合并的回归测试。

写入路径以前是"每条消息 4 次 Redis 往返"，压缩重写窗口更是 4N 次。
这些断言把"合并成一次 pipeline"钉住，避免以后有人改回逐条 await。
"""

from __future__ import annotations

import asyncio
from typing import Any

import redis.exceptions as redis_exceptions
from app.knowledge.domain.schemas import MessageRecord, SessionMeta, SessionSummary
from app.knowledge.infrastructure.stm.redis_short_term_memory import (
    RedisShortTermMemory,
    rewrite_recent_messages,
)
from app.knowledge.infrastructure.stm.stm_compressor import compress_message


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


# ---------------------------------------------------------------------- #
# 读写方法与降级路径
# ---------------------------------------------------------------------- #


class FullRedis(FakeRedis):
    """实现 STM 用到的全部 Redis 命令。"""

    def __init__(self) -> None:
        super().__init__()
        self.store: dict[str, str] = {}
        self.zsets: dict[str, list[bytes]] = {}
        self.expire_calls: list[str] = []

    async def get(self, key: str) -> str | None:
        return self.store.get(key)

    async def set(self, key: str, value: str, ex: int | None = None, nx: bool = False):
        if nx and key in self.store:
            return None
        self.store[key] = value
        return True

    async def delete(self, *keys: str) -> int:
        removed = 0
        for key in keys:
            removed += 1 if self.store.pop(key, None) is not None else 0
            removed += 1 if self.zsets.pop(key, None) is not None else 0
        return removed

    async def zcard(self, key: str) -> int:
        return len(self.zsets.get(key, []))

    async def zrevrange(self, key: str, start: int, end: int) -> list[bytes]:
        return self.zsets.get(key, [])[start : end + 1]

    async def expire(self, key: str, ttl: int) -> bool:
        self.expire_calls.append(key)
        return True


class BrokenRedis(FullRedis):
    """所有命令都抛外部依赖故障，用于验证降级不炸。"""

    async def get(self, key: str):
        raise redis_exceptions.ConnectionError("down")

    async def set(self, key: str, value: str, ex: int | None = None, nx: bool = False):
        raise redis_exceptions.ConnectionError("down")

    async def zcard(self, key: str) -> int:
        raise redis_exceptions.ConnectionError("down")

    async def zrevrange(self, key: str, start: int, end: int):
        raise redis_exceptions.ConnectionError("down")

    async def expire(self, key: str, ttl: int):
        raise redis_exceptions.ConnectionError("down")

    async def delete(self, *keys: str) -> int:
        raise redis_exceptions.ConnectionError("down")

    def pipeline(self, transaction: bool = True):
        raise redis_exceptions.ConnectionError("down")


def _stm(redis) -> RedisShortTermMemory:
    return RedisShortTermMemory(redis)  # type: ignore[arg-type]


async def test_summary_round_trip() -> None:
    stm = _stm(FullRedis())
    summary = SessionSummary(content="摘要", compressed_at=1, compressed_round=2)

    await stm.save_summary("t", "u", "s", summary)

    assert await stm.get_summary("t", "u", "s") == summary


async def test_meta_round_trip_and_default() -> None:
    stm = _stm(FullRedis())

    assert await stm.get_meta("t", "u", "s") == SessionMeta()

    meta = SessionMeta(total_turns=4, last_updated_at=9, last_compressed_turn=2)
    await stm.save_meta("t", "u", "s", meta)

    assert await stm.get_meta("t", "u", "s") == meta


async def test_message_count_and_recent_messages() -> None:
    redis = FullRedis()
    stm = _stm(redis)
    key = "agent:stm:t:u:s:messages"
    redis.zsets[key] = [compress_message(_message("m1", 1)), compress_message(_message("m2", 2))]

    assert await stm.get_message_count("t", "u", "s") == 2
    messages = await stm.get_recent_messages("t", "u", "s")
    assert [m.message_id for m in messages] == ["m2", "m1"]


async def test_refresh_ttl_touches_all_session_keys() -> None:
    redis = FullRedis()
    stm = _stm(redis)

    await stm.refresh_ttl("t", "u", "s")

    assert sorted(key.rsplit(":", 1)[-1] for key in redis.expire_calls) == [
        "messages",
        "meta",
        "summary",
    ]


async def test_clear_session_removes_every_key() -> None:
    redis = FullRedis()
    stm = _stm(redis)
    redis.store["agent:stm:t:u:s:summary"] = "{}"
    redis.store["agent:stm:t:u:s:meta"] = "{}"
    redis.zsets["agent:stm:t:u:s:messages"] = [b"x"]

    assert await stm.clear_session("t", "u", "s") == 3


async def test_all_reads_degrade_gracefully_on_redis_outage() -> None:
    """Redis 全挂时每个读接口都要给出兜底值，而不是抛异常打断对话。"""
    stm = _stm(BrokenRedis())

    assert await stm.get_summary("t", "u", "s") is None
    assert await stm.get_meta("t", "u", "s") == SessionMeta()
    assert await stm.get_recent_messages("t", "u", "s") == []
    assert await stm.get_message_count("t", "u", "s") == 0
    assert await stm.clear_session("t", "u", "s") == 0


async def test_all_writes_degrade_gracefully_on_redis_outage() -> None:
    stm = _stm(BrokenRedis())

    # 不抛异常即为通过
    await stm.append_message("t", "u", "s", _message("m1"))
    await stm.append_messages("t", "u", "s", [_message("m1")])
    await stm.save_summary("t", "u", "s", SessionSummary(content="x", compressed_at=1, compressed_round=1))
    await stm.save_meta("t", "u", "s", SessionMeta())
    await stm.refresh_ttl("t", "u", "s")


async def test_should_compress_delegates_to_runtime_settings() -> None:
    stm = _stm(FullRedis())

    assert stm.should_compress(total_turns=100, last_compressed_turn=0, message_count=0) is True
    assert stm.should_compress(total_turns=1, last_compressed_turn=1, message_count=0) is False


# ---------------------------------------------------------------------- #
# 二进制安全回归：STM 消息是 MsgPack/Zstd 字节，客户端绝不能 decode_responses
# ---------------------------------------------------------------------- #


def test_create_stm_redis_client_is_binary_safe(monkeypatch) -> None:
    """STM 客户端必须 decode_responses=False。

    压缩消息不是合法 UTF-8：decode_responses=True 的客户端写入正常，
    但 zrevrange 读取时对成员做严格 UTF-8 解码直接抛 UnicodeDecodeError，
    被上层降级吞掉后表现为"短期记忆永远读到空"——真实踩过的静默故障。
    """
    import redis.asyncio as aioredis
    from app.knowledge.infrastructure.stm.redis_short_term_memory import (
        create_stm_redis_client,
    )

    captured: dict[str, object] = {}

    def fake_from_url(url: str, **kwargs):
        captured["url"] = url
        captured.update(kwargs)
        return object()

    monkeypatch.setattr(aioredis.Redis, "from_url", staticmethod(fake_from_url), raising=False)
    monkeypatch.setattr(
        "app.knowledge.infrastructure.stm.redis_short_term_memory.redis.from_url",
        fake_from_url,
    )

    create_stm_redis_client("redis://example:6379/0")

    assert captured["url"] == "redis://example:6379/0"
    assert captured["decode_responses"] is False


def test_compressed_message_is_not_valid_utf8() -> None:
    """钉死前提：压缩消息确实不是 UTF-8，decode_responses 客户端必然读崩。"""
    import pytest as _pytest

    blob = compress_message(_message("m1"))

    assert isinstance(blob, bytes)
    with _pytest.raises(UnicodeDecodeError):
        blob.decode("utf-8")


async def test_append_and_read_round_trip_with_binary_client() -> None:
    """端到端回归：二进制客户端下写入的消息必须能原样读回。"""

    class BinaryRedis(FullRedis):
        """模拟 decode_responses=False：zrevrange 原样返回 bytes 成员。"""

        async def zadd(self, key: str, mapping) -> int:
            self.zsets.setdefault(key, [])
            # 新消息排前面，模拟 zrevrange 的时间倒序
            for member in mapping:
                self.zsets[key].insert(0, member)
            return len(mapping)

        def pipeline(self, transaction: bool = True):
            outer = self

            class PassthroughPipe(FakePipeline):
                async def execute(self) -> list:
                    for name, args in self.commands:
                        if name == "zadd":
                            await outer.zadd(*args)
                    return []

            return PassthroughPipe(outer)

    redis = BinaryRedis()
    stm = _stm(redis)
    original = _message("m1")

    await stm.append_message("t", "u", "s", original)
    restored = await stm.get_recent_messages("t", "u", "s")

    assert [m.message_id for m in restored] == ["m1"]
    assert restored[0].content == original.content
