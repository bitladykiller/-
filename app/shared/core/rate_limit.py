"""SSE 并发限流。

这个模块负责：
- 按用户限制同时进行的流式问答数（Redis 计数器）

WHY 需要它：
一次问答最多串起 6+ 次 LLM 调用。没有并发上限时，单个用户开 N 条 SSE
就能把 LLM 配额与后端事件循环一起榨干——这是最便宜的资源护栏。

实现说明：
- INCR 后为槽位 key 续 TTL：进程崩溃没走到 release 时，槽位最多泄漏
  `slot_ttl_seconds` 秒，之后自动归零，不会永久卡死用户
- 计数器整体过期而非逐槽过期：实现简单，代价是"崩溃后最多多放行一轮"，
  对护栏场景可接受
"""

from __future__ import annotations

from typing import Any

from app.shared.core.logger import get_logger

logger = get_logger(__name__)

_KEY_PREFIX = "ratelimit:sse:"


class ConcurrencyLimitExceededError(Exception):
    """并发槽位已满。API 层映射 429。"""

    def __init__(self, limit: int) -> None:
        super().__init__(f"并发上限 {limit}")
        self.limit = limit


class SseConcurrencyLimiter:
    """基于 Redis 计数器的每用户并发限制。"""

    def __init__(
        self,
        redis_client: Any,
        *,
        max_concurrent: int,
        slot_ttl_seconds: int,
    ) -> None:
        self._redis = redis_client
        self._max_concurrent = max_concurrent
        self._slot_ttl_seconds = slot_ttl_seconds

    def _key(self, user_id: int) -> str:
        return f"{_KEY_PREFIX}{user_id}"

    async def acquire(self, user_id: int) -> None:
        """占用一个并发槽位；超限抛 ConcurrencyLimitExceededError。

        限流器自身故障（Redis 不可用）按放行处理：
        护栏挂了不应该把主功能一起带走。
        """
        try:
            key = self._key(user_id)
            current = await self._redis.incr(key)
            await self._redis.expire(key, self._slot_ttl_seconds)
            if int(current) > self._max_concurrent:
                await self._redis.decr(key)
                raise ConcurrencyLimitExceededError(self._max_concurrent)
        except ConcurrencyLimitExceededError:
            raise
        except Exception as exc:
            logger.warning("并发限流器不可用，放行请求 | user=%s | %s", user_id, exc)

    async def release(self, user_id: int) -> None:
        """释放槽位；失败静默（TTL 会兜底回收）。"""
        try:
            key = self._key(user_id)
            remaining = await self._redis.decr(key)
            if int(remaining) < 0:
                # 防御：release 多于 acquire（如崩溃后 TTL 已清零）时归零
                await self._redis.set(key, 0, ex=self._slot_ttl_seconds)
        except Exception:
            logger.debug("释放并发槽位失败（TTL 将兜底）", exc_info=True)


__all__ = ["ConcurrencyLimitExceededError", "SseConcurrencyLimiter"]
