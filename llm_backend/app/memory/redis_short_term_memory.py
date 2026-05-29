"""
Redis 短期记忆模块（ZSET + MsgPack + Zstd 优化版）。

STM = Short-Term Memory，短期记忆。
使用 Redis ZSET 替代 List 实现滑动窗口。
Score = 时间戳，Value = MsgPack + Zstd 压缩后的消息体。
支持按时间和按条数两种滑动方式。
"""
import json
import time
import asyncio
from typing import List, Optional, Dict, Any
import redis.asyncio as redis
import msgpack
import zstandard as zstd
from app.memory.config import SHORT_TERM_MEMORY_CONFIG
from app.memory.schemas import MessageRecord, SessionMeta, SessionSummary

# 多级压缩器：按消息大小选择压缩级别，平衡 CPU 和存储
_zstd_fast   = zstd.ZstdCompressor(level=1)   # 500MB/s,  2-4KB 中型消息
_zstd_normal = zstd.ZstdCompressor(level=3)   # 200MB/s, 4-16KB 大型消息
_zstd_high   = zstd.ZstdCompressor(level=9)   #  50MB/s, >16KB 超大型消息，存储优先
_zstd_decompressor = zstd.ZstdDecompressor()   # 解压器不关心压缩级别，全兼容


class RedisShortTermMemory:
    """优化版短期记忆模块（ZSET + MsgPack + Zstd 压缩）。

    STM = Short-Term Memory，短期记忆。
    使用 Redis ZSET 保存当前 session 的对话消息。
    Score = 毫秒时间戳，天然按时间排序。
    支持按消息条数和时间窗口两种滑动方式。
    """

    def __init__(self, redis_client: redis.Redis):
        self.redis = redis_client
        self.config = SHORT_TERM_MEMORY_CONFIG
        self.key_prefix = self.config["redis"]["key_prefix"]
        self.ttl_seconds = self.config["redis"]["ttl_seconds"]
        self.lock_ttl_seconds = self.config["redis"]["lock_ttl_seconds"]
        self.max_messages = self.config["window"]["max_messages"]
        self.max_rounds = self.config["window"]["max_rounds"]
        self.compression_enabled = self.config["compression"]["enabled"]
        self.trigger_rounds = self.config["compression"]["trigger_rounds"]
        self.trigger_messages = self.config["compression"]["trigger_messages"]
        self.keep_recent_rounds = self.config["compression"]["keep_recent_rounds"]
        self.summary_max_chars = self.config["compression"]["summary_max_chars"]
        self.time_window_seconds = self.config.get("time_window_seconds", self.ttl_seconds)

    # ------------------------------------------------------------------ #
    # 压缩与解压
    # ------------------------------------------------------------------ #

    def _compress(self, message: MessageRecord) -> bytes:
        """MsgPack + 多级 Zstd 压缩。

        ≤2KB   → 仅 MsgPack（\x00 前缀）
        2-4KB  → MsgPack + Zstd level=1（\x01 前缀，快速）
        4-16KB → MsgPack + Zstd level=3（\x02 前缀，平衡）
        >16KB  → MsgPack + Zstd level=9（\x03 前缀，高压缩比）
        """
        packed = msgpack.packb(message.model_dump(), use_bin_type=True)

        if len(packed) <= 2048:
            return b'\x00' + packed
        elif len(packed) <= 4096:
            return b'\x01' + _zstd_fast.compress(packed)
        elif len(packed) <= 16384:
            return b'\x02' + _zstd_normal.compress(packed)
        else:
            return b'\x03' + _zstd_high.compress(packed)

    def _decompress(self, data: bytes) -> MessageRecord:
        """解压：解压器不关心压缩级别，flag >= 0x01 均走 zstd 解压。"""
        flag, payload = data[0], data[1:]
        if flag in (0x01, 0x02, 0x03):
            unpacked = msgpack.unpackb(_zstd_decompressor.decompress(payload), raw=False)
        else:
            unpacked = msgpack.unpackb(payload, raw=False)
        return MessageRecord(**unpacked)

    # ------------------------------------------------------------------ #
    # Redis Key 构造
    # ------------------------------------------------------------------ #

    def build_messages_key(self, t: str, u: str, s: str) -> str:
        return f"{self.key_prefix}:{t}:{u}:{s}:messages"

    def build_summary_key(self, t: str, u: str, s: str) -> str:
        return f"{self.key_prefix}:{t}:{u}:{s}:summary"

    def build_meta_key(self, t: str, u: str, s: str) -> str:
        return f"{self.key_prefix}:{t}:{u}:{s}:meta"

    def build_lock_key(self, t: str, u: str, s: str) -> str:
        return f"{self.key_prefix}:{t}:{u}:{s}:lock"

    # ------------------------------------------------------------------ #
    # 消息操作（ZSET 版本）
    # ------------------------------------------------------------------ #

    async def append_message(
        self, tenant_id: str, user_id: str, session_id: str, message: MessageRecord,
    ) -> None:
        try:
            key = self.build_messages_key(tenant_id, user_id, session_id)
            score = message.created_at if message.created_at > 1000000000000 else int(time.time() * 1000)
            compressed = self._compress(message)
            await self.redis.zadd(key, {compressed: score})
            await self.redis.zremrangebyrank(key, 0, -self.max_messages - 1)
            cutoff = int(time.time() * 1000) - self.time_window_seconds * 1000
            await self.redis.zremrangebyscore(key, 0, cutoff)
            await self.redis.expire(key, self.ttl_seconds)
        except Exception:
            pass

    async def get_recent_messages(
        self, tenant_id: str, user_id: str, session_id: str, limit: int = None,
    ) -> List[MessageRecord]:
        try:
            key = self.build_messages_key(tenant_id, user_id, session_id)
            limit = limit or self.max_messages
            raw = await self.redis.zrevrange(key, 0, limit - 1)
            result = []
            for data in raw:
                try:
                    result.append(self._decompress(data))
                except Exception:
                    pass
            result.reverse()
            return result
        except Exception:
            return []

    async def get_message_count(self, tenant_id: str, user_id: str, session_id: str) -> int:
        try:
            key = self.build_messages_key(tenant_id, user_id, session_id)
            return await self.redis.zcard(key)
        except Exception:
            return 0

    # ------------------------------------------------------------------ #
    # 摘要操作
    # ------------------------------------------------------------------ #

    async def get_summary(self, tenant_id: str, user_id: str, session_id: str) -> Optional[SessionSummary]:
        try:
            key = self.build_summary_key(tenant_id, user_id, session_id)
            raw = await self.redis.get(key)
            if raw:
                data = json.loads(raw.decode('utf-8') if isinstance(raw, bytes) else raw)
                return SessionSummary(**data)
        except Exception:
            pass
        return None

    async def save_summary(self, tenant_id: str, user_id: str, session_id: str, summary: SessionSummary) -> None:
        try:
            key = self.build_summary_key(tenant_id, user_id, session_id)
            await self.redis.set(key, summary.model_dump_json(), ex=self.ttl_seconds)
        except Exception:
            pass

    # ------------------------------------------------------------------ #
    # 元信息操作
    # ------------------------------------------------------------------ #

    async def get_meta(self, tenant_id: str, user_id: str, session_id: str) -> SessionMeta:
        try:
            key = self.build_meta_key(tenant_id, user_id, session_id)
            raw = await self.redis.get(key)
            if raw:
                data = json.loads(raw.decode('utf-8') if isinstance(raw, bytes) else raw)
                return SessionMeta(**data)
        except Exception:
            pass
        return SessionMeta()

    async def save_meta(self, tenant_id: str, user_id: str, session_id: str, meta: SessionMeta) -> None:
        try:
            key = self.build_meta_key(tenant_id, user_id, session_id)
            await self.redis.set(key, meta.model_dump_json(), ex=self.ttl_seconds)
        except Exception:
            pass

    # ------------------------------------------------------------------ #
    # 压缩判断与执行
    # ------------------------------------------------------------------ #

    def should_compress(self, total_turns: int, last_compressed_turn: int, message_count: int) -> bool:
        if not self.compression_enabled:
            return False
        if total_turns - last_compressed_turn >= self.trigger_rounds:
            return True
        if message_count >= self.trigger_messages:
            return True
        return False

    async def compress_session_memory(
        self, tenant_id: str, user_id: str, session_id: str, llm_compress_func,
    ) -> bool:
        try:
            meta = await self.get_meta(tenant_id, user_id, session_id)
            msg_count = await self.get_message_count(tenant_id, user_id, session_id)
            if not self.should_compress(meta.total_turns, meta.last_compressed_turn, msg_count):
                return False

            lock_key = self.build_lock_key(tenant_id, user_id, session_id)
            acquired = await self.redis.set(lock_key, "1", ex=self.lock_ttl_seconds, nx=True)
            if not acquired:
                return False

            try:
                old_summary = await self.get_summary(tenant_id, user_id, session_id)
                all_messages = await self.get_recent_messages(tenant_id, user_id, session_id, limit=100)
                old_summary_str = old_summary.model_dump_json() if old_summary else ""

                recent_count = self.keep_recent_rounds * 2
                messages_to_keep = all_messages[-recent_count:]
                messages_to_compress = all_messages[:-recent_count] if len(all_messages) > recent_count else []

                if messages_to_compress:
                    new_summary_str = await llm_compress_func(old_summary_str, messages_to_compress)
                    import re as _re
                    match = _re.search(r"\{.*\}", new_summary_str, _re.DOTALL)
                    if match:
                        data = json.loads(match.group())
                        new_summary = SessionSummary(**data)
                        await self.save_summary(tenant_id, user_id, session_id, new_summary)

                if messages_to_keep:
                    msg_key = self.build_messages_key(tenant_id, user_id, session_id)
                    await self.redis.delete(msg_key)
                    for msg in messages_to_keep:
                        await self.append_message(tenant_id, user_id, session_id, msg)

                meta.last_compressed_turn = meta.total_turns
                await self.save_meta(tenant_id, user_id, session_id, meta)
                return True
            finally:
                await self.redis.delete(lock_key)
        except Exception:
            return False

    # ------------------------------------------------------------------ #
    # 辅助方法
    # ------------------------------------------------------------------ #

    async def refresh_ttl(self, tenant_id: str, user_id: str, session_id: str) -> None:
        try:
            await asyncio.gather(
                self.redis.expire(self.build_messages_key(tenant_id, user_id, session_id), self.ttl_seconds),
                self.redis.expire(self.build_summary_key(tenant_id, user_id, session_id), self.ttl_seconds),
                self.redis.expire(self.build_meta_key(tenant_id, user_id, session_id), self.ttl_seconds),
            )
        except Exception:
            pass

    async def clear_session(self, tenant_id: str, user_id: str, session_id: str) -> None:
        try:
            keys = [
                self.build_messages_key(tenant_id, user_id, session_id),
                self.build_summary_key(tenant_id, user_id, session_id),
                self.build_meta_key(tenant_id, user_id, session_id),
                self.build_lock_key(tenant_id, user_id, session_id),
            ]
            await self.redis.delete(*keys)
        except Exception:
            pass
