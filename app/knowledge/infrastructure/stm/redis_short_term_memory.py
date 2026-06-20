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
import json
import time
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from typing import Any, TypeAlias, TypeVar

from typing_extensions import TypedDict

import redis.asyncio as redis
from pydantic import BaseModel

from app.shared.core.json_utils import extract_first_json_object
from app.shared.core.logger import get_logger
from app.knowledge.infrastructure.config import (
    ShortTermMemoryConfig,
    short_term_compression_config,
    short_term_config,
    short_term_redis_config,
    short_term_window_config,
)
from app.knowledge.domain.schemas import MessageRecord, SessionMeta, SessionSummary
from app.knowledge.infrastructure.stm.stm_compressor import (
    compress_message,
    decompress_message,
)

logger = get_logger(__name__)
COMPRESS_FETCH_LIMIT = 100
SummaryCompressor: TypeAlias = Callable[[str, list[MessageRecord]], Awaitable[str]]
RedisModel = TypeVar("RedisModel", bound=BaseModel)


class SessionKeys(TypedDict):
    """单个 session 会使用到的全部 Redis key。"""

    messages: str
    summary: str
    meta: str
    lock: str


def build_session_keys(
    key_prefix: str,
    tenant_id: str,
    user_id: str,
    session_id: str,
) -> SessionKeys:
    """一次性返回当前 session 会用到的所有 key。"""
    return {
        "messages": f"{key_prefix}:{tenant_id}:{user_id}:{session_id}:messages",
        "summary": f"{key_prefix}:{tenant_id}:{user_id}:{session_id}:summary",
        "meta": f"{key_prefix}:{tenant_id}:{user_id}:{session_id}:meta",
        "lock": f"{key_prefix}:{tenant_id}:{user_id}:{session_id}:lock",
    }


def decode_model(
    raw: bytes | str | None,
    model_cls: type[RedisModel],
) -> RedisModel | None:
    """把 Redis JSON 文本解码成指定 Pydantic 模型。"""
    if not raw:
        return None
    text = raw.decode("utf-8") if isinstance(raw, bytes) else raw
    data = json.loads(text)
    if not isinstance(data, dict):
        return None
    return model_cls(**data)


def message_score(message: MessageRecord) -> int:
    """把消息时间统一转换成 Redis ZSET 所需的毫秒 score。"""
    if message.created_at > 1000000000000:
        return message.created_at
    return int(time.time() * 1000)


def extract_summary_from_response(response: str) -> SessionSummary | None:
    """从 LLM 压缩结果中提取 SessionSummary JSON。"""
    payload = extract_first_json_object(response)
    if payload is None:
        return None
    data = json.loads(payload)
    return SessionSummary(**data)


def decode_messages(raw_messages: list[bytes | str]) -> list[MessageRecord]:
    """批量解压 Redis 中保存的消息记录。"""
    messages: list[MessageRecord] = []
    for raw_message in raw_messages:
        try:
            messages.append(decompress_message(raw_message))
        except Exception as exc:
            logger.debug("[stm] 解压消息失败: %s", exc)
            continue

    messages.reverse()
    return messages


def split_messages_for_compression(
    messages: list[MessageRecord],
    keep_recent_rounds: int,
) -> tuple[list[MessageRecord], list[MessageRecord]]:
    """把消息切成“需要压缩的旧消息”和“需要保留的最近消息”。

    当前默认一轮对话按 user + assistant 两条消息估算，因此
    `keep_recent_rounds` 会换算成 `keep_recent_rounds * 2` 条消息。
    """
    recent_count = keep_recent_rounds * 2
    messages_to_keep = messages[-recent_count:]
    messages_to_compress = messages[:-recent_count] if len(messages) > recent_count else []
    return messages_to_compress, messages_to_keep


@dataclass(frozen=True)
class ShortTermMemoryRuntimeSettings:
    """短期记忆存储层运行时配置。"""

    key_prefix: str
    ttl_seconds: int
    lock_ttl_seconds: int
    max_messages: int
    compression_enabled: bool
    trigger_rounds: int
    trigger_messages: int
    keep_recent_rounds: int
    time_window_seconds: int


@dataclass(frozen=True)
class CompressionContext:
    """一次压缩执行所需的上下文。"""

    keys: SessionKeys
    meta: SessionMeta
    old_summary_str: str
    messages_to_compress: list[MessageRecord]
    messages_to_keep: list[MessageRecord]


def build_runtime_settings(
    *,
    config: ShortTermMemoryConfig,
    redis_config: dict[str, Any],
    window_config: dict[str, Any],
    compression_config: dict[str, Any],
) -> ShortTermMemoryRuntimeSettings:
    """从配置模块收口出存储层运行时参数。"""
    return ShortTermMemoryRuntimeSettings(
        key_prefix=redis_config["key_prefix"],
        ttl_seconds=redis_config["ttl_seconds"],
        lock_ttl_seconds=redis_config["lock_ttl_seconds"],
        max_messages=window_config["max_messages"],
        compression_enabled=compression_config["enabled"],
        trigger_rounds=compression_config["trigger_rounds"],
        trigger_messages=compression_config["trigger_messages"],
        keep_recent_rounds=compression_config["keep_recent_rounds"],
        time_window_seconds=config["time_window_seconds"],
    )


def should_compress_session(
    settings: ShortTermMemoryRuntimeSettings,
    *,
    total_turns: int,
    last_compressed_turn: int,
    message_count: int,
) -> bool:
    """根据配置、轮次和消息数判断是否触发压缩。"""
    if not settings.compression_enabled:
        return False
    if total_turns - last_compressed_turn >= settings.trigger_rounds:
        return True
    if message_count >= settings.trigger_messages:
        return True
    return False


def build_compression_context(
    *,
    settings: ShortTermMemoryRuntimeSettings,
    keys: SessionKeys,
    meta: SessionMeta,
    message_count: int,
    old_summary: SessionSummary | None,
    all_messages: list[MessageRecord],
) -> CompressionContext | None:
    """基于当前 session 状态准备压缩上下文。"""
    if not should_compress_session(
        settings,
        total_turns=meta.total_turns,
        last_compressed_turn=meta.last_compressed_turn,
        message_count=message_count,
    ):
        return None

    messages_to_compress, messages_to_keep = split_messages_for_compression(
        all_messages,
        settings.keep_recent_rounds,
    )
    return CompressionContext(
        keys=keys,
        meta=meta,
        old_summary_str=old_summary.model_dump_json() if old_summary else "",
        messages_to_compress=messages_to_compress,
        messages_to_keep=messages_to_keep,
    )


async def prune_message_window(
    *,
    redis_client: Any,
    key: str,
    settings: ShortTermMemoryRuntimeSettings,
) -> None:
    """维护消息滑动窗口：同时控制条数、时间窗口和 TTL。"""
    await redis_client.zremrangebyrank(key, 0, -settings.max_messages - 1)
    cutoff = int(time.time() * 1000) - settings.time_window_seconds * 1000
    await redis_client.zremrangebyscore(key, 0, cutoff)
    await redis_client.expire(key, settings.ttl_seconds)


async def persist_summary_from_messages(
    *,
    tenant_id: str,
    user_id: str,
    session_id: str,
    old_summary_str: str,
    messages_to_compress: list[MessageRecord],
    llm_compress_func: SummaryCompressor,
    extract_summary_from_response: Callable[[str], SessionSummary | None],
    save_summary: Callable[[str, str, str, SessionSummary], Awaitable[None]],
) -> None:
    """调用摘要压缩函数，并在成功时写回新的 session summary。"""
    if not messages_to_compress:
        return

    new_summary_str = await llm_compress_func(old_summary_str, messages_to_compress)
    new_summary = extract_summary_from_response(new_summary_str)
    if new_summary:
        await save_summary(tenant_id, user_id, session_id, new_summary)


async def rewrite_recent_messages(
    *,
    key: str,
    messages: Sequence[MessageRecord],
    reset_messages: Callable[[str], Awaitable[None]],
    append_message: Callable[[MessageRecord], Awaitable[None]],
) -> None:
    """用压缩后保留的最近消息重建消息窗口。"""
    if not messages:
        return
    await reset_messages(key)
    for message in messages:
        await append_message(message)


async def run_compression_pipeline(
    *,
    redis_client: Any,
    context: CompressionContext,
    lock_ttl_seconds: int,
    update_summary: Callable[[CompressionContext], Awaitable[None]],
    rewrite_messages: Callable[[CompressionContext], Awaitable[None]],
    save_meta: Callable[[SessionMeta], Awaitable[None]],
) -> bool:
    """执行一次带分布式锁保护的压缩流程。"""
    acquired = await redis_client.set(
        context.keys["lock"],
        "1",
        ex=lock_ttl_seconds,
        nx=True,
    )
    if not acquired:
        return False

    try:
        await update_summary(context)
        await rewrite_messages(context)
        meta = context.meta.model_copy(deep=True)
        meta.last_compressed_turn = meta.total_turns
        await save_meta(meta)
        return True
    finally:
        await redis_client.delete(context.keys["lock"])


class RedisShortTermMemory:
    """Redis 短期记忆存储层。"""

    def __init__(self, redis_client: redis.Redis):
        self.redis = redis_client
        self.config: ShortTermMemoryConfig = short_term_config()
        self.settings = build_runtime_settings(
            config=self.config,
            redis_config=short_term_redis_config(),
            window_config=short_term_window_config(),
            compression_config=short_term_compression_config(),
        )

    def _build_session_keys(
        self,
        tenant_id: str,
        user_id: str,
        session_id: str,
    ) -> SessionKeys:
        """一次性返回当前 session 会用到的所有 key。"""
        return build_session_keys(
            self.settings.key_prefix,
            tenant_id,
            user_id,
            session_id,
        )

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
            await prune_message_window(
                redis_client=self.redis,
                key=key,
                settings=self.settings,
            )
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
            limit = limit or self.settings.max_messages
            raw = await self.redis.zrevrange(key, 0, limit - 1)
            return decode_messages(raw)
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
            return decode_model(await self.redis.get(key), SessionSummary)
        except Exception as exc:
            logger.debug(f"[stm] 读取会话摘要失败: {exc}")
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
            await self.redis.set(
                key,
                summary.model_dump_json(),
                ex=self.settings.ttl_seconds,
            )
        except Exception as exc:
            logger.debug(f"[stm] 保存会话摘要失败: {exc}")

    async def get_meta(self, tenant_id: str, user_id: str, session_id: str) -> SessionMeta:
        """读取会话元信息,不存在时返回默认对象。"""
        try:
            key = self._build_session_keys(tenant_id, user_id, session_id)["meta"]
            meta = decode_model(await self.redis.get(key), SessionMeta)
            if meta:
                return meta
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
                ex=self.settings.ttl_seconds,
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
        return should_compress_session(
            self.settings,
            total_turns=total_turns,
            last_compressed_turn=last_compressed_turn,
            message_count=message_count,
        )

    async def compress_session_memory(
        self,
        tenant_id: str,
        user_id: str,
        session_id: str,
        llm_compress_func: SummaryCompressor,
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
                limit=COMPRESS_FETCH_LIMIT,
            )
            context = build_compression_context(
                settings=self.settings,
                keys=keys,
                meta=meta,
                message_count=msg_count,
                old_summary=old_summary,
                all_messages=all_messages,
            )
            if context is None:
                return False

            return await run_compression_pipeline(
                redis_client=self.redis,
                context=context,
                lock_ttl_seconds=self.settings.lock_ttl_seconds,
                update_summary=lambda current: persist_summary_from_messages(
                    tenant_id=tenant_id,
                    user_id=user_id,
                    session_id=session_id,
                    old_summary_str=current.old_summary_str,
                    messages_to_compress=current.messages_to_compress,
                    llm_compress_func=llm_compress_func,
                    extract_summary_from_response=extract_summary_from_response,
                    save_summary=self.save_summary,
                ),
                rewrite_messages=lambda current: rewrite_recent_messages(
                    key=current.keys["messages"],
                    messages=current.messages_to_keep,
                    reset_messages=self.redis.delete,
                    append_message=lambda message: self.append_message(
                        tenant_id,
                        user_id,
                        session_id,
                        message,
                    ),
                ),
                save_meta=lambda meta: self.save_meta(tenant_id, user_id, session_id, meta),
            )
        except Exception as exc:
            logger.debug(f"[stm] 压缩会话记忆失败: {exc}")
            return False

    async def refresh_ttl(self, tenant_id: str, user_id: str, session_id: str) -> None:
        """刷新当前 session 相关 key 的 TTL。"""
        try:
            keys = self._build_session_keys(tenant_id, user_id, session_id)
            await asyncio.gather(
                self.redis.expire(keys["messages"], self.settings.ttl_seconds),
                self.redis.expire(keys["summary"], self.settings.ttl_seconds),
                self.redis.expire(keys["meta"], self.settings.ttl_seconds),
            )
        except Exception as exc:
            logger.debug(f"[stm] 刷新 TTL 失败: {exc}")
