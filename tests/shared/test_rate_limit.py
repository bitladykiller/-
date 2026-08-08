"""SSE 并发限流器单测（租户级 + 用户级双层）。"""

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


def _limiter(
    redis,
    max_concurrent: int = 2,
    max_concurrent_per_tenant: int = 0,
) -> SseConcurrencyLimiter:
    return SseConcurrencyLimiter(
        redis,
        max_concurrent=max_concurrent,
        slot_ttl_seconds=60,
        max_concurrent_per_tenant=max_concurrent_per_tenant,
    )


async def test_acquire_within_limit_and_release() -> None:
    redis = FakeCounterRedis()
    limiter = _limiter(redis, max_concurrent=2)

    await limiter.acquire("t_1", 7)
    await limiter.acquire("t_1", 7)
    await limiter.release("t_1", 7)
    await limiter.release("t_1", 7)

    assert redis.counters["ratelimit:sse:t_1:user:7"] == 0
    # 每次 acquire 都续 TTL，崩溃后槽位可自动回收
    assert len(redis.expire_calls) == 2


async def test_acquire_beyond_limit_raises_429_semantics() -> None:
    limiter = _limiter(FakeCounterRedis(), max_concurrent=1)

    await limiter.acquire("t_1", 7)
    with pytest.raises(ConcurrencyLimitExceededError):
        await limiter.acquire("t_1", 7)


async def test_limits_are_per_user() -> None:
    limiter = _limiter(FakeCounterRedis(), max_concurrent=1)

    await limiter.acquire("t_1", 7)
    await limiter.acquire("t_1", 8)  # 同一租户另一个用户不受影响


async def test_limits_are_per_tenant() -> None:
    limiter = _limiter(FakeCounterRedis(), max_concurrent=1)

    await limiter.acquire("t_1", 7)
    await limiter.acquire("t_2", 7)  # 另一租户同名用户不受影响
    assert limiter._user_key("t_1", 7) != limiter._user_key("t_2", 7)


async def test_tenant_level_limit_blocks_beyond_capacity() -> None:
    """租户级配额：同一租户并发满后第 N+1 个请求 429。"""
    redis = FakeCounterRedis()
    limiter = _limiter(redis, max_concurrent=3, max_concurrent_per_tenant=2)

    await limiter.acquire("t_1", 7)
    await limiter.acquire("t_1", 8)
    with pytest.raises(ConcurrencyLimitExceededError):
        await limiter.acquire("t_1", 9)

    # 用户级失败后回收租户槽位，计数保持一致
    assert redis.counters["ratelimit:sse:t_1"] == 2
    # 租户级失败时用户槽位根本未被占用
    assert redis.counters.get("ratelimit:sse:t_1:user:9", 0) == 0


async def test_tenant_limit_failure_self_rolls_back_tenant_slot() -> None:
    """租户级满员：第 N+1 个请求的租户槽位由 _acquire_one 自回滚。"""
    redis = FakeCounterRedis()
    limiter = _limiter(redis, max_concurrent=3, max_concurrent_per_tenant=1)

    await limiter.acquire("t_1", 7)
    with pytest.raises(ConcurrencyLimitExceededError):
        await limiter.acquire("t_1", 8)

    assert redis.counters["ratelimit:sse:t_1"] == 1
    assert redis.counters.get("ratelimit:sse:t_1:user:8", 0) == 0


async def test_tenant_limit_zero_disables_tenant_slots() -> None:
    """max_concurrent_per_tenant=0（默认）时不产生租户级 key。"""
    redis = FakeCounterRedis()
    limiter = _limiter(redis, max_concurrent=3)

    await limiter.acquire("t_1", 7)
    await limiter.release("t_1", 7)

    assert redis.counters == {"ratelimit:sse:t_1:user:7": 0}


async def test_limiter_outage_fails_open() -> None:
    """护栏自身故障必须放行，不能把主功能带走。"""
    limiter = _limiter(BrokenRedis())

    await limiter.acquire("t_1", 7)  # 不抛即通过


async def test_release_clamps_negative_counter() -> None:
    redis = FakeCounterRedis()
    limiter = _limiter(redis)

    await limiter.release("t_1", 7)  # 未 acquire 直接 release（崩溃后 TTL 清零场景）

    assert redis.counters["ratelimit:sse:t_1:user:7"] == 0
