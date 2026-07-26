"""Redis Streams 事件队列单测（内存 fake 实现消费组语义）。"""

from __future__ import annotations

import asyncio
from typing import Any

from app.shared.streams import RedisStreamQueue, decode_event, encode_event


class FakeStreamRedis:
    """最小消费组语义：xadd / xreadgroup(>) / xack / xautoclaim / xpending_range。"""

    def __init__(self) -> None:
        self.entries: dict[str, list[tuple[str, dict[str, str]]]] = {}
        self.groups: dict[tuple[str, str], dict[str, Any]] = {}
        self._seq = 0

    def _group(self, stream: str, group: str) -> dict[str, Any]:
        return self.groups[(stream, group)]

    async def xadd(self, stream: str, fields: dict[str, str]) -> str:
        self._seq += 1
        entry_id = f"{self._seq}-0"
        self.entries.setdefault(stream, []).append((entry_id, dict(fields)))
        return entry_id

    async def xgroup_create(self, stream: str, group: str, id: str, mkstream: bool):
        key = (stream, group)
        if key in self.groups:
            raise RuntimeError("BUSYGROUP Consumer Group name already exists")
        self.entries.setdefault(stream, [])
        # pending: entry_id -> {"deliveries": n, "consumer": name}
        self.groups[key] = {"cursor": 0, "pending": {}}

    async def xreadgroup(self, *, groupname, consumername, streams, count, block):
        results = []
        for stream, marker in streams.items():
            assert marker == ">"
            g = self._group(stream, groupname)
            available = self.entries.get(stream, [])[g["cursor"] :]
            batch = available[:count]
            if not batch:
                continue
            g["cursor"] += len(batch)
            for entry_id, _fields in batch:
                g["pending"][entry_id] = {"deliveries": 1, "consumer": consumername}
            results.append((stream.encode(), [(eid.encode(), f) for eid, f in batch]))
        if not results:
            await asyncio.sleep(0)  # 模拟 block 超时
        return results

    async def xack(self, stream: str, group: str, entry_id) -> int:
        eid = entry_id.decode() if isinstance(entry_id, bytes) else str(entry_id)
        return 1 if self._group(stream, group)["pending"].pop(eid, None) else 0

    async def xautoclaim(self, stream, group, consumer, *, min_idle_time, start_id, count):
        g = self._group(stream, group)
        claimed = []
        lookup = dict(self.entries.get(stream, []))
        for eid, meta in list(g["pending"].items()):
            # fake 里所有 pending 都视为超时可认领
            meta["deliveries"] += 1
            meta["consumer"] = consumer
            claimed.append((eid.encode(), lookup[eid]))
            if len(claimed) >= count:
                break
        return (b"0-0", claimed)

    async def xpending_range(self, stream, group, *, min, max, count):
        g = self._group(stream, group)
        meta = g["pending"].get(min)
        if meta is None:
            return []
        return [{"message_id": min, "times_delivered": meta["deliveries"]}]


def _queue(redis: FakeStreamRedis, **kwargs) -> RedisStreamQueue:
    return RedisStreamQueue(
        redis,
        stream="agent:events",
        group="core",
        consumer_name="test-consumer",
        block_ms=1,
        max_deliveries=kwargs.pop("max_deliveries", 3),
        **kwargs,
    )


async def _run_one_cycle(queue: RedisStreamQueue, handlers) -> None:
    """跑一轮消费循环后停止。"""
    stop = asyncio.Event()

    async def stopper():
        await asyncio.sleep(0.05)
        stop.set()

    await asyncio.gather(queue.run_consumer(handlers, stop), stopper())


def test_event_encoding_round_trip() -> None:
    fields = encode_event("turn_completed", {"user_id": "7", "内容": "中文"})

    decoded = decode_event(fields)

    assert decoded == ("turn_completed", {"user_id": "7", "内容": "中文"})


def test_decode_event_rejects_malformed_fields() -> None:
    assert decode_event({}) is None
    assert decode_event({"type": "x", "data": "not-json"}) is None
    assert decode_event({"type": "x", "data": "[1,2]"}) is None


async def test_consumer_processes_and_acks_published_events() -> None:
    redis = FakeStreamRedis()
    queue = _queue(redis)
    handled: list[dict] = []

    async def handler(payload: dict) -> None:
        handled.append(payload)

    await queue.publish("turn_completed", {"session_id": "11"})
    await _run_one_cycle(queue, {"turn_completed": handler})

    assert handled == [{"session_id": "11"}]
    # 已 ACK：pending 清空
    assert redis.groups[("agent:events", "core")]["pending"] == {}


async def test_failed_event_stays_pending_then_reclaimed_and_retried() -> None:
    """核心保证：处理失败/进程崩溃的消息会被认领重放，而不是丢失。"""
    redis = FakeStreamRedis()
    queue = _queue(redis)
    attempts: list[int] = []

    async def flaky(payload: dict) -> None:
        attempts.append(1)
        if len(attempts) == 1:
            raise RuntimeError("first attempt boom")

    await queue.publish("turn_completed", {"session_id": "11"})
    # 首次处理失败不 ACK（留在 PEL）；下一迭代 XAUTOCLAIM 认领重放并成功。
    # 断言最终不变量：恰好重试到成功（2 次），且 PEL 清空。
    await _run_one_cycle(queue, {"turn_completed": flaky})

    assert len(attempts) == 2
    assert redis.groups[("agent:events", "core")]["pending"] == {}


async def test_poison_event_goes_to_dead_letter_after_max_deliveries() -> None:
    redis = FakeStreamRedis()
    queue = _queue(redis, max_deliveries=2)

    async def always_fail(payload: dict) -> None:
        raise RuntimeError("poison")

    await queue.publish("turn_completed", {"session_id": "bad"})
    for _ in range(4):
        await _run_one_cycle(queue, {"turn_completed": always_fail})

    # 毒消息最终 ACK 并转入死信流，不再阻塞循环
    assert redis.groups[("agent:events", "core")]["pending"] == {}
    dead = redis.entries.get("agent:events:dead", [])
    assert len(dead) == 1
    assert decode_event(dead[0][1]) == ("turn_completed", {"session_id": "bad"})


async def test_unknown_event_type_is_acked_and_skipped() -> None:
    redis = FakeStreamRedis()
    queue = _queue(redis)

    await queue.publish("no_such_type", {"x": 1})
    await _run_one_cycle(queue, {})

    assert redis.groups[("agent:events", "core")]["pending"] == {}


async def test_ensure_group_is_idempotent() -> None:
    queue = _queue(FakeStreamRedis())

    await queue.ensure_group()
    await queue.ensure_group()  # BUSYGROUP 被吞掉，不抛
