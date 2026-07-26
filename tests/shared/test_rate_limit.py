"""SSE 并发限流器单测。"""

from __future__ import annotations

import pytest
from app.shared.core.rate_limit import ConcurrencyLimitExceededError, SseConcurrencyLimiter


class FakeCounterRedis:
    def __init__(self) -> None:
        self.counters: dict[str, int] = {}
        self.expire_calls: list[tuple[str, int]] = []

    async def incr(self, key: str) -> int:
        self.counters[key] = self.counters.get(key, 0) + 1
        return self.counters[key]

    async def decr(self, key: str) -> int:
        self.counters[key] = self.counters.get(key, 0) - 1
        return self.counters[key]

    async def expire(self, key: str, ttl: int) -> bool:
        self.expire_calls.append((key, ttl))
        return True

    async def set(self, key: str, value: int, ex: int | None = None) -> bool:
        self.counters[key] = int(value)
        return True


class BrokenRedis(FakeCounterRedis):
    async def incr(self, key: str) -> int:
        raise ConnectionError("redis down")


def _limiter(redis, max_concurrent: int = 2) -> SseConcurrencyLimiter:
    return SseConcurrencyLimiter(redis, max_concurrent=max_concurrent, slot_ttl_seconds=60)


async def test_acquire_within_limit_and_release() -> None:
    redis = FakeCounterRedis()
    limiter = _limiter(redis, max_concurrent=2)

    await limiter.acquire(7)
    await limiter.acquire(7)
    await limiter.release(7)
    await limiter.release(7)

    assert redis.counters["ratelimit:sse:7"] == 0
    # 每次 acquire 都续 TTL，崩溃后槽位可自动回收
    assert len(redis.expire_calls) == 2


async def test_acquire_beyond_limit_raises_429_semantics() -> None:
    limiter = _limiter(FakeCounterRedis(), max_concurrent=1)

    await limiter.acquire(7)
    with pytest.raises(ConcurrencyLimitExceededError):
        await limiter.acquire(7)


async def test_limits_are_per_user() -> None:
    limiter = _limiter(FakeCounterRedis(), max_concurrent=1)

    await limiter.acquire(7)
    await limiter.acquire(8)  # 另一个用户不受影响


async def test_limiter_outage_fails_open() -> None:
    """护栏自身故障必须放行，不能把主功能带走。"""
    limiter = _limiter(BrokenRedis())

    await limiter.acquire(7)  # 不抛即通过


async def test_release_clamps_negative_counter() -> None:
    redis = FakeCounterRedis()
    limiter = _limiter(redis)

    await limiter.release(7)  # 未 acquire 直接 release（崩溃后 TTL 清零场景）

    assert redis.counters["ratelimit:sse:7"] == 0
