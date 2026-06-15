"""Redis 短期记忆模块（ZSET + MsgPack + Zstd）。

STM = Short-Term Memory，短期记忆。
使用 Redis ZSET 保存最近消息，`stm_compressor.py` 负责消息压缩格式。

本文件主要职责：
- 管理 session 级 messages / summary / meta / lock 四类 key
- 维护消息滑动窗口
- 在需要时触发对话压缩
"""

import asyncio
import json
import time
from collections.abc import Awaitable, Callable

import redis.asyncio as redis
from app.knowledge.domain.schemas import MessageRecord, SessionMeta, SessionSummary
from app.knowledge.infrastructure.config import (
    SHORT_TERM_MEMORY_CONFIG,
)
from app.knowledge.infrastructure.stm.stm_compressor import (
    compress_message,
    decompress_message,
)
from app.shared.core.json_utils import extract_first_json_object
from app.shared.core.logger import get_logger

logger = get_logger(__name__)

class RedisShortTermMemory:
    """Redis 短期记忆存储层。"""

    def __init__(self, redis_client: redis.Redis):
        self.redis = redis_client
        redis_config = SHORT_TERM_MEMORY_CONFIG["redis"]
        window_config = SHORT_TERM_MEMORY_CONFIG["window"]
        compression_config = SHORT_TERM_MEMORY_CONFIG["compression"]
        self.key_prefix = redis_config["key_prefix"]
        self.ttl_seconds = redis_config["ttl_seconds"]
        self.lock_ttl_seconds = redis_config["lock_ttl_seconds"]
        self.max_messages = window_config["max_messages"]
        self.trigger_rounds = compression_config["trigger_rounds"]
        self.trigger_messages = compression_config["trigger_messages"]
        self.keep_recent_rounds = compression_config["keep_recent_rounds"]
        self.time_window_seconds = SHORT_TERM_MEMORY_CONFIG["time_window_seconds"]

    def _build_session_keys(
        self,
        tenant_id: str,
        user_id: str,
        session_id: str,
    ) -> dict[str, str]:
        """返回当前 session 会用到的所有 Redis key。"""
        return {
            "messages": (
                f"{self.key_prefix}:{tenant_id}:{user_id}:{session_id}:messages"
            ),
            "summary": (
                f"{self.key_prefix}:{tenant_id}:{user_id}:{session_id}:summary"
            ),
            "meta": f"{self.key_prefix}:{tenant_id}:{user_id}:{session_id}:meta",
            "lock": f"{self.key_prefix}:{tenant_id}:{user_id}:{session_id}:lock",
        }

    async def append_message(
        self,
        tenant_id: str,
        user_id: str,
        session_id: str,
        message: MessageRecord,
    ) -> None:
        """写入一条短期消息，并维护滑动窗口。"""
        try:
            key = self._build_session_keys(tenant_id, user_id, session_id)["messages"]
            score = int(time.time() * 1000)
            await self.redis.zadd(
                key,
                {compress_message(message): score},
            )
            await self.redis.zremrangebyrank(key, 0, -self.max_messages - 1)
            cutoff = int(time.time() * 1000) - self.time_window_seconds * 1000
            await self.redis.zremrangebyscore(key, 0, cutoff)
            await self.redis.expire(key, self.ttl_seconds)
        except Exception:
            logger.warning("[stm] append_message 失败", exc_info=True)

    async def get_recent_messages(
        self,
        tenant_id: str,
        user_id: str,
        session_id: str,
        limit: int | None = None,
    ) -> list[MessageRecord]:
        """按时间顺序返回最近消息。"""
        try:
            key = self._build_session_keys(tenant_id, user_id, session_id)["messages"]
            limit = limit or self.max_messages
            raw = await self.redis.zrevrange(key, 0, limit - 1)
            messages: list[MessageRecord] = []
            for raw_message in raw:
                try:
                    messages.append(decompress_message(raw_message))
                except Exception as exc:
                    logger.debug("[stm] 解压消息失败: %s", exc)
                    continue

            messages.reverse()
            return messages
        except Exception as exc:
            logger.debug(f"[stm] 读取最近消息失败: {exc}")
            return []

    async def get_message_count(self, tenant_id: str, user_id: str, session_id: str) -> int:
        """返回当前 session 的消息条数。"""
        try:
            key = self._build_session_keys(tenant_id, user_id, session_id)["messages"]
            return await self.redis.zcard(key)
        except Exception as exc:
            logger.debug(f"[stm] 获取消息计数失败: {exc}")
            return 0

    async def get_summary(
        self,
        tenant_id: str,
        user_id: str,
        session_id: str,
    ) -> SessionSummary | None:
        """读取会话摘要。"""
        try:
            key = self._build_session_keys(tenant_id, user_id, session_id)["summary"]
            raw = await self.redis.get(key)
            if not raw:
                return None
            return SessionSummary(**json.loads(raw))
        except Exception as exc:
            logger.debug(f"[stm] 读取会话摘要失败: {exc}")
            return None

    async def get_meta(self, tenant_id: str, user_id: str, session_id: str) -> SessionMeta:
        """读取会话元信息,不存在时返回默认对象。"""
        try:
            key = self._build_session_keys(tenant_id, user_id, session_id)["meta"]
            raw = await self.redis.get(key)
            if not raw:
                return SessionMeta()
            return SessionMeta(**json.loads(raw))
        except Exception as exc:
            logger.debug(f"[stm] 读取会话元信息失败: {exc}")
        return SessionMeta()

    async def save_meta(
        self,
        tenant_id: str,
        user_id: str,
        session_id: str,
        meta: SessionMeta,
    ) -> None:
        """保存会话元信息。"""
        try:
            key = self._build_session_keys(tenant_id, user_id, session_id)["meta"]
            await self.redis.set(
                key,
                meta.model_dump_json(),
                ex=self.ttl_seconds,
            )
        except Exception as exc:
            logger.debug(f"[stm] 保存会话元信息失败: {exc}")

    def should_compress(
        self,
        total_turns: int,
        last_compressed_turn: int,
        message_count: int,
    ) -> bool:
        """根据轮次和消息数判断是否触发压缩。"""
        if total_turns - last_compressed_turn >= self.trigger_rounds:
            return True
        if message_count >= self.trigger_messages:
            return True
        return False

    async def compress_session_memory(
        self,
        tenant_id: str,
        user_id: str,
        session_id: str,
        llm_compress_func: Callable[[str, list[MessageRecord]], Awaitable[str]],
    ) -> bool:
        """压缩旧消息，并保留最近若干轮原始消息。"""
        try:
            meta = await self.get_meta(tenant_id, user_id, session_id)
            msg_count = await self.get_message_count(tenant_id, user_id, session_id)
            keys = self._build_session_keys(tenant_id, user_id, session_id)
            old_summary = await self.get_summary(tenant_id, user_id, session_id)
            all_messages = await self.get_recent_messages(
                tenant_id,
                user_id,
                session_id,
                limit=100,
            )
            if not self.should_compress(
                meta.total_turns,
                meta.last_compressed_turn,
                msg_count,
            ):
                return False
            recent_count = self.keep_recent_rounds * 2
            messages_to_keep = all_messages[-recent_count:]
            messages_to_compress = (
                all_messages[:-recent_count] if len(all_messages) > recent_count else []
            )
            old_summary_str = old_summary.model_dump_json() if old_summary else ""

            acquired = await self.redis.set(
                keys["lock"],
                "1",
                ex=self.lock_ttl_seconds,
                nx=True,
            )
            if not acquired:
                return False

            try:
                if messages_to_compress:
                    new_summary_str = await llm_compress_func(
                        old_summary_str,
                        messages_to_compress,
                    )
                    new_summary = None
                    try:
                        payload = extract_first_json_object(new_summary_str)
                        if payload is not None:
                            new_summary = SessionSummary(**json.loads(payload))
                    except Exception as exc:
                        logger.debug("[stm] 解析压缩摘要失败: %s", exc)
                    if new_summary:
                        try:
                            await self.redis.set(
                                keys["summary"],
                                new_summary.model_dump_json(),
                                ex=self.ttl_seconds,
                            )
                        except Exception as exc:
                            logger.debug(f"[stm] 保存会话摘要失败: {exc}")

                if messages_to_keep:
                    await self.redis.delete(keys["messages"])
                    for message in messages_to_keep:
                        await self.append_message(
                            tenant_id,
                            user_id,
                            session_id,
                            message,
                        )

                compressed_meta = meta.model_copy(deep=True)
                compressed_meta.last_compressed_turn = compressed_meta.total_turns
                await self.save_meta(
                    tenant_id,
                    user_id,
                    session_id,
                    compressed_meta,
                )
                return True
            finally:
                await self.redis.delete(keys["lock"])
        except Exception as exc:
            logger.debug(f"[stm] 压缩会话记忆失败: {exc}")
            return False

    async def refresh_ttl(self, tenant_id: str, user_id: str, session_id: str) -> None:
        """刷新当前 session 相关 key 的 TTL。"""
        try:
            keys = self._build_session_keys(tenant_id, user_id, session_id)
            await asyncio.gather(
                self.redis.expire(keys["messages"], self.ttl_seconds),
                self.redis.expire(keys["summary"], self.ttl_seconds),
                self.redis.expire(keys["meta"], self.ttl_seconds),
            )
        except Exception as exc:
            logger.debug(f"[stm] 刷新 TTL 失败: {exc}")
