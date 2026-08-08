"""SSE 并发限流（租户级 + 用户级双层）。

这个模块负责：
- 按租户限制同时进行的流式问答数（企业级配额护栏）
- 按用户限制同时进行的流式问答数（用户级护栏）

WHY 需要它：
一次问答最多串起 6+ 次 LLM 调用。没有并发上限时，单个用户开 N 条 SSE
就能把 LLM 配额与后端事件循环一起榨干——这是最便宜的资源护栏。
SaaS 化后限流主体是租户（真正计费/配额单位），用户级限流是租户内部
的第二道护栏。

实现说明：
- INCR 后为槽位 key 续 TTL：进程崩溃没走到 release 时，槽位最多泄漏
  `slot_ttl_seconds` 秒，之后自动归零，不会永久卡死用户
- 计数器整体过期而非逐槽过期：实现简单，代价是"崩溃后最多多放行一轮"，
  对护栏场景可接受
- Redis key：`ratelimit:sse:{tenant_id}`（租户级）、
  `ratelimit:sse:{tenant_id}:user:{user_id}`（用户级）
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
    """基于 Redis 计数器的租户 + 用户双层并发限制。"""

    def __init__(
        self,
        redis_client: Any,
        *,
        max_concurrent: int,
        slot_ttl_seconds: int,
        max_concurrent_per_tenant: int = 0,
    ) -> None:
        self._redis = redis_client
        self._max_concurrent = max_concurrent
        self._slot_ttl_seconds = slot_ttl_seconds
        # 0 表示不启用租户级限制（单租户部署兼容）
        self._max_concurrent_per_tenant = int(max_concurrent_per_tenant or 0)

    @staticmethod
    def _user_key(tenant_id: str, user_id: int) -> str:
        return f"{_KEY_PREFIX}{tenant_id}:user:{user_id}"

    @staticmethod
    def _tenant_key(tenant_id: str) -> str:
        return f"{_KEY_PREFIX}{tenant_id}"

    async def _acquire_one(self, key: str, limit: int) -> None:
        """占用一个槽位；超限抛 ConcurrencyLimitExceededError。"""
        current = await self._redis.incr(key)
        await self._redis.expire(key, self._slot_ttl_seconds)
        if int(current) > limit:
            await self._redis.decr(key)
            raise ConcurrencyLimitExceededError(limit)

    async def acquire(self, tenant_id: str, user_id: int) -> None:
        """占用租户 + 用户各一个并发槽位；任一超限抛错。

        限流器自身故障（Redis 不可用）按放行处理：
        护栏挂了不应该把主功能一起带走。
        """
        try:
            if self._max_concurrent_per_tenant > 0:
                await self._acquire_one(
                    self._tenant_key(tenant_id),
                    self._max_concurrent_per_tenant,
                )
            try:
                await self._acquire_one(
                    self._user_key(tenant_id, user_id),
                    self._max_concurrent,
                )
            except ConcurrencyLimitExceededError:
                # 用户级失败：回收刚占用的租户槽位，保持计数一致。
                # 用户槽位由 _acquire_one 自回滚，无需再减。
                if self._max_concurrent_per_tenant > 0:
                    await self._release_one(self._tenant_key(tenant_id))
                raise
        except ConcurrencyLimitExceededError:
            raise
        except Exception as exc:
            logger.warning(
                "并发限流器不可用，放行请求 | tenant=%s user=%s | %s",
                tenant_id,
                user_id,
                exc,
            )

    async def release(self, tenant_id: str, user_id: int) -> None:
        """释放槽位；失败静默（TTL 会兜底回收）。"""
        try:
            if self._max_concurrent_per_tenant > 0:
                await self._release_one(self._tenant_key(tenant_id))
            await self._release_one(self._user_key(tenant_id, user_id))
        except Exception:
            logger.debug("释放并发槽位失败（TTL 将兜底）", exc_info=True)

    async def _release_one(self, key: str) -> None:
        remaining = await self._redis.decr(key)
        if int(remaining) < 0:
            # 防御：release 多于 acquire（如崩溃后 TTL 已清零）时归零
            await self._redis.set(key, 0, ex=self._slot_ttl_seconds)


__all__ = ["ConcurrencyLimitExceededError", "SseConcurrencyLimiter"]
