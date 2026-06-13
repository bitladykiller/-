"""Redis 短期记忆模块（ZSET + MsgPack + Zstd）。

STM = Short-Term Memory，短期记忆。
使用 Redis ZSET 保存最近消息，`stm_compressor.py` 负责消息压缩格式。

本文件主要职责：
- 管理 session 级 messages / summary / meta / lock 四类 key
- 维护消息滑动窗口
- 在需要时触发对话压缩
"""
from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable
from typing import TypeAlias

import redis.asyncio as redis

from app.core.logger import get_logger
from app.memory.config import (
    ShortTermMemoryConfig,
    short_term_compression_config,
    short_term_config,
    short_term_redis_config,
    short_term_window_config,
)
from app.memory.schemas import MessageRecord, SessionMeta, SessionSummary
from app.memory.stm_store_utils import (
    SessionKeys,
    build_session_keys,
    decode_messages,
    decode_model,
    extract_summary_from_response,
    message_score,
    split_messages_for_compression,
)
from app.memory.stm_compressor import compress_message

logger = get_logger(__name__)

SummaryCompressor: TypeAlias = Callable[[str, list[MessageRecord]], Awaitable[str]]
COMPRESS_FETCH_LIMIT = 100


class RedisShortTermMemory:
    """Redis 短期记忆存储层。"""

    def __init__(self, redis_client: redis.Redis):
        self.redis = redis_client
        self.config: ShortTermMemoryConfig = short_term_config()

        redis_config = short_term_redis_config()
        window_config = short_term_window_config()
        compression_config = short_term_compression_config()

        self.key_prefix = redis_config["key_prefix"]
        self.ttl_seconds = redis_config["ttl_seconds"]
        self.lock_ttl_seconds = redis_config["lock_ttl_seconds"]
        self.max_messages = window_config["max_messages"]
        self.compression_enabled = compression_config["enabled"]
        self.trigger_rounds = compression_config["trigger_rounds"]
        self.trigger_messages = compression_config["trigger_messages"]
        self.keep_recent_rounds = compression_config["keep_recent_rounds"]
        self.time_window_seconds = self.config["time_window_seconds"]

    def _build_session_keys(
        self,
        tenant_id: str,
        user_id: str,
        session_id: str,
    ) -> SessionKeys:
        """一次性返回当前 session 会用到的所有 key。"""
        return build_session_keys(
            self.key_prefix,
            tenant_id,
            user_id,
            session_id,
        )

    async def _read_model(
        self,
        key: str,
        model_cls: type[SessionMeta] | type[SessionSummary],
    ) -> SessionMeta | SessionSummary | None:
        """从 Redis 读取并解码指定模型。"""
        return decode_model(await self.redis.get(key), model_cls)

    async def _write_model(
        self,
        key: str,
        model: SessionMeta | SessionSummary,
    ) -> None:
        """把 Pydantic 模型序列化后写回 Redis。"""
        await self.redis.set(key, model.model_dump_json(), ex=self.ttl_seconds)

    def _split_messages_for_compression(
        self,
        messages: list[MessageRecord],
    ) -> tuple[list[MessageRecord], list[MessageRecord]]:
        """把消息切成“需要压缩的旧消息”和“需要保留的最近消息”。"""
        return split_messages_for_compression(
            messages,
            self.keep_recent_rounds,
        )

    async def _prune_message_window(self, key: str) -> None:
        """维护消息滑动窗口：同时控制条数、时间窗口和 TTL。"""
        await self.redis.zremrangebyrank(key, 0, -self.max_messages - 1)
        cutoff = int(time.time() * 1000) - self.time_window_seconds * 1000
        await self.redis.zremrangebyscore(key, 0, cutoff)
        await self.redis.expire(key, self.ttl_seconds)

    async def _rewrite_recent_messages(
        self,
        *,
        key: str,
        tenant_id: str,
        user_id: str,
        session_id: str,
        messages: list[MessageRecord],
    ) -> None:
        """用压缩后保留的最近消息重建消息窗口。"""
        if not messages:
            return
        await self.redis.delete(key)
        for message in messages:
            await self.append_message(tenant_id, user_id, session_id, message)

    async def _update_summary_from_messages(
        self,
        *,
        tenant_id: str,
        user_id: str,
        session_id: str,
        old_summary_str: str,
        messages_to_compress: list[MessageRecord],
        llm_compress_func: SummaryCompressor,
    ) -> None:
        """调用摘要压缩函数，并在成功时写回新的 session summary。"""
        if not messages_to_compress:
            return
        new_summary_str = await llm_compress_func(old_summary_str, messages_to_compress)
        new_summary = extract_summary_from_response(new_summary_str)
        if new_summary:
            await self.save_summary(tenant_id, user_id, session_id, new_summary)

    async def _prepare_compression_context(
        self,
        tenant_id: str,
        user_id: str,
        session_id: str,
    ) -> tuple[SessionKeys, SessionMeta, str, list[MessageRecord], list[MessageRecord]] | None:
        """读取压缩所需上下文，并在不满足条件时直接返回 None。"""
        meta = await self.get_meta(tenant_id, user_id, session_id)
        msg_count = await self.get_message_count(tenant_id, user_id, session_id)
        if not self.should_compress(
            meta.total_turns,
            meta.last_compressed_turn,
            msg_count,
        ):
            return None

        keys = self._build_session_keys(tenant_id, user_id, session_id)
        old_summary = await self.get_summary(tenant_id, user_id, session_id)
        all_messages = await self.get_recent_messages(
            tenant_id,
            user_id,
            session_id,
            limit=COMPRESS_FETCH_LIMIT,
        )
        old_summary_str = old_summary.model_dump_json() if old_summary else ""
        messages_to_compress, messages_to_keep = self._split_messages_for_compression(
            all_messages
        )
        return keys, meta, old_summary_str, messages_to_compress, messages_to_keep

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
            await self.redis.zadd(
                key,
                {compress_message(message): message_score(message)},
            )
            await self._prune_message_window(key)
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
            return decode_messages(raw)
        except Exception:
            return []

    async def get_message_count(self, tenant_id: str, user_id: str, session_id: str) -> int:
        """返回当前 session 的消息条数。"""
        try:
            key = self._build_session_keys(tenant_id, user_id, session_id)["messages"]
            return await self.redis.zcard(key)
        except Exception:
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
            return await self._read_model(key, SessionSummary)
        except Exception:
            return None

    async def save_summary(
        self,
        tenant_id: str,
        user_id: str,
        session_id: str,
        summary: SessionSummary,
    ) -> None:
        """保存会话摘要。"""
        try:
            key = self._build_session_keys(tenant_id, user_id, session_id)["summary"]
            await self._write_model(key, summary)
        except Exception:
            pass

    async def get_meta(self, tenant_id: str, user_id: str, session_id: str) -> SessionMeta:
        """读取会话元信息，不存在时返回默认对象。"""
        try:
            key = self._build_session_keys(tenant_id, user_id, session_id)["meta"]
            meta = await self._read_model(key, SessionMeta)
            if meta:
                return meta
        except Exception:
            pass
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
            await self._write_model(key, meta)
        except Exception:
            pass

    def should_compress(
        self,
        total_turns: int,
        last_compressed_turn: int,
        message_count: int,
    ) -> bool:
        """根据轮次和消息数判断是否触发压缩。"""
        if not self.compression_enabled:
            return False
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
        llm_compress_func: SummaryCompressor,
    ) -> bool:
        """压缩旧消息，并保留最近若干轮原始消息。"""
        try:
            context = await self._prepare_compression_context(
                tenant_id,
                user_id,
                session_id,
            )
            if context is None:
                return False

            keys, meta, old_summary_str, messages_to_compress, messages_to_keep = context
            acquired = await self.redis.set(
                keys["lock"],
                "1",
                ex=self.lock_ttl_seconds,
                nx=True,
            )
            if not acquired:
                return False

            try:
                await self._update_summary_from_messages(
                    tenant_id=tenant_id,
                    user_id=user_id,
                    session_id=session_id,
                    old_summary_str=old_summary_str,
                    messages_to_compress=messages_to_compress,
                    llm_compress_func=llm_compress_func,
                )
                await self._rewrite_recent_messages(
                    key=keys["messages"],
                    tenant_id=tenant_id,
                    user_id=user_id,
                    session_id=session_id,
                    messages=messages_to_keep,
                )

                meta.last_compressed_turn = meta.total_turns
                await self.save_meta(tenant_id, user_id, session_id, meta)
                return True
            finally:
                await self.redis.delete(keys["lock"])
        except Exception:
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
        except Exception:
            pass

    async def clear_session(self, tenant_id: str, user_id: str, session_id: str) -> None:
        """清理当前 session 的全部短期记忆数据。"""
        try:
            keys = self._build_session_keys(tenant_id, user_id, session_id)
            await self.redis.delete(*keys.values())
        except Exception:
            pass
