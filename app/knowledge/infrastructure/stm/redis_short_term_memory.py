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

import redis.asyncio as redis
from app.knowledge.domain.schemas import MessageRecord, SessionMeta, SessionSummary
from app.knowledge.infrastructure.stm.stm_compressor import (
    compress_message,
    decompress_message,
)
from app.shared.core.app_config import (
    STMCompressionConfig,
    STMConfig,
    STMRedisConfig,
    STMWindowConfig,
)
from app.shared.core.config import settings
from app.shared.core.degradation import log_degradation
from app.shared.core.json_utils import extract_first_json_object
from app.shared.core.logger import get_logger
from pydantic import BaseModel
from typing_extensions import TypedDict

logger = get_logger(__name__)
COMPRESS_FETCH_LIMIT = 100
SummaryCompressor: TypeAlias = Callable[[str, list[MessageRecord]], Awaitable[str]]
RedisModel = TypeVar("RedisModel", bound=BaseModel)


def create_stm_redis_client(redis_url: str) -> redis.Redis:
    """创建 STM 专用的**二进制安全** Redis 客户端。

    ⚠️ 必须 `decode_responses=False`，这是硬约束不是偏好：

    STM 消息经 `compress_message` 压缩成二进制 MsgPack/Zstd（首字节 `\\x00`-`\\x03`），
    不是合法 UTF-8。若客户端开启 `decode_responses=True`，写入没问题（bytes 原样落库），
    但 `zrevrange` 读取时 redis-py 会对每个成员做**严格 UTF-8 解码**，直接抛
    `UnicodeDecodeError`——再被上层降级逻辑吞掉，表现为"短期记忆写得进、读不出，
    每轮对话都拿到空的最近消息"。这是项目真实踩过的静默故障。

    本模块的读路径（`decode_model` / `decode_messages`）全部原生支持 bytes；
    画像缓存复用此客户端时存的是 JSON 文本，`json.loads` 同样接受 bytes。
    """
    return redis.from_url(redis_url, decode_responses=False)


class SessionKeys(TypedDict):
    """单个 session 会使用到的全部 Redis key。"""

    messages: str
    summary: str
    meta: str
    lock: str
    turns: str
    turn_lock: str


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
        "turns": f"{key_prefix}:{tenant_id}:{user_id}:{session_id}:turns",
        "turn_lock": f"{key_prefix}:{tenant_id}:{user_id}:{session_id}:turn_lock",
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
            if isinstance(raw_message, str):
                data = raw_message.encode("latin-1")
            else:
                data = raw_message
            messages.append(decompress_message(data))
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
    config: STMConfig,
    redis_config: STMRedisConfig,
    window_config: STMWindowConfig,
    compression_config: STMCompressionConfig,
) -> ShortTermMemoryRuntimeSettings:
    """从配置模块收口出存储层运行时参数。"""
    return ShortTermMemoryRuntimeSettings(
        key_prefix=redis_config.key_prefix,
        ttl_seconds=redis_config.ttl_seconds,
        lock_ttl_seconds=redis_config.lock_ttl_seconds,
        max_messages=window_config.max_messages,
        compression_enabled=compression_config.enabled,
        trigger_rounds=compression_config.trigger_rounds,
        trigger_messages=compression_config.trigger_messages,
        keep_recent_rounds=compression_config.keep_recent_rounds,
        time_window_seconds=config.time_window_seconds,
    )


def _build_compression_task_recorder(
    compression_id: str,
    session_id: str,
    tenant_id: str,
    user_id: str,
) -> Callable[[str], Awaitable[None]]:
    """构造压缩任务完成回调。

    v3.36+: 压缩 pipeline 成功完成后，记录到 compression_tasks 表。
    ON DUPLICATE KEY UPDATE 语义——无论重放多少次，只留一条 completed 记录。
    """

    async def _record(cid: str) -> None:
        try:
            from app.shared.core.database import AsyncSessionLocal
            from sqlalchemy import text

            async with AsyncSessionLocal() as db:
                await db.execute(
                    text(
                        "INSERT INTO compression_tasks "
                        "(compression_id, session_id, tenant_id, user_id, "
                        "from_turn, to_turn, status, completed_at) "
                        "VALUES (:cid, :sid, :tid, :uid, 0, 0, 'completed', NOW()) "
                        "ON DUPLICATE KEY UPDATE "
                        "status = 'completed', completed_at = NOW()"
                    ),
                    {
                        "cid": cid,
                        "sid": session_id,
                        "tid": tenant_id,
                        "uid": user_id,
                    },
                )
                await db.commit()
        except Exception:
            logger.warning(
                "记录压缩任���完成状态失败 | compression_id=%s session=%s",
                cid,
                session_id,
                exc_info=True,
            )

    return _record


# NOTE: 下面这些函数的运行时参数统一叫 `runtime`，不叫 `settings`——
# 本模块同时在用全局 `app.shared.core.config.settings`，同名参数会遮蔽它，
# 读者很难判断某处的 `settings` 到底指哪个。


def should_compress_session(
    runtime: ShortTermMemoryRuntimeSettings,
    *,
    total_turns: int,
    last_compressed_turn: int,
    message_count: int,
) -> bool:
    """根据配置、轮次和消息数判断是否触发压缩。"""
    if not runtime.compression_enabled:
        return False
    if total_turns - last_compressed_turn >= runtime.trigger_rounds:
        return True
    return message_count >= runtime.trigger_messages


def build_compression_context(
    *,
    runtime: ShortTermMemoryRuntimeSettings,
    keys: SessionKeys,
    meta: SessionMeta,
    message_count: int,
    old_summary: SessionSummary | None,
    all_messages: list[MessageRecord],
) -> CompressionContext | None:
    """基于当前 session 状态准备压缩上下文；未达阈值返回 None。"""
    if not should_compress_session(
        runtime,
        total_turns=meta.total_turns,
        last_compressed_turn=meta.last_compressed_turn,
        message_count=message_count,
    ):
        return None

    messages_to_compress, messages_to_keep = split_messages_for_compression(
        all_messages,
        runtime.keep_recent_rounds,
    )
    return CompressionContext(
        keys=keys,
        meta=meta,
        old_summary_str=old_summary.model_dump_json() if old_summary else "",
        messages_to_compress=messages_to_compress,
        messages_to_keep=messages_to_keep,
    )


def queue_window_pruning(
    pipe: Any,
    key: str,
    runtime: ShortTermMemoryRuntimeSettings,
) -> None:
    """把"滑动窗口修剪"的三条命令排入 pipeline。

    修剪包含三件事：截断超出条数上限的旧消息、丢弃超出时间窗口的消息、
    续期 TTL。三者都是 fire-and-forget，没有读依赖，适合合并成一次往返。
    """
    pipe.zremrangebyrank(key, 0, -runtime.max_messages - 1)
    cutoff = int(time.time() * 1000) - runtime.time_window_seconds * 1000
    pipe.zremrangebyscore(key, 0, cutoff)
    pipe.expire(key, runtime.ttl_seconds)


async def prune_message_window(
    *,
    redis_client: Any,
    key: str,
    runtime: ShortTermMemoryRuntimeSettings,
) -> None:
    """维护消息滑动窗口：同时控制条数、时间窗口和 TTL。"""
    async with redis_client.pipeline(transaction=False) as pipe:
        queue_window_pruning(pipe, key, runtime)
        await pipe.execute()


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
    redis_client: Any,
    key: str,
    messages: Sequence[MessageRecord],
    runtime: ShortTermMemoryRuntimeSettings,
) -> None:
    """用压缩后保留的最近消息重建消息窗口。

    delete + 批量 zadd + 窗口修剪合并成一次往返。
    之前是"删一次 + 每条消息各 4 次命令"，保留 5 条就是 21 次 Redis 往返。
    """
    if not messages:
        return

    scored = {compress_message(message): message_score(message) for message in messages}
    async with redis_client.pipeline(transaction=False) as pipe:
        pipe.delete(key)
        pipe.zadd(key, scored)
        queue_window_pruning(pipe, key, runtime)
        await pipe.execute()


async def run_compression_pipeline(
    *,
    redis_client: Any,
    context: CompressionContext,
    lock_ttl_seconds: int,
    update_summary: Callable[[CompressionContext], Awaitable[None]],
    rewrite_messages: Callable[[CompressionContext], Awaitable[None]],
    save_meta: Callable[[SessionMeta], Awaitable[None]],
    compression_id: str = "",
    on_complete: Callable[[str], Awaitable[None]] | None = None,
) -> bool:
    """执行一次带分布式锁保护的压缩流程。

    v3.36+: compression_id 提供显式幂等键；on_complete 回调在流程
    成功完成后调用（用于记录 compression_tasks 表）。
    """
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
        if on_complete and compression_id:
            await on_complete(compression_id)
        return True
    finally:
        await redis_client.delete(context.keys["lock"])


class RedisShortTermMemory:
    """Redis 短期记忆存储层。"""

    def __init__(self, redis_client: redis.Redis):
        self.redis = redis_client
        stm_cfg = settings.app_config.memory.stm
        self.config: STMConfig = stm_cfg
        self.settings = build_runtime_settings(
            config=stm_cfg,
            redis_config=stm_cfg.redis,
            window_config=stm_cfg.window,
            compression_config=stm_cfg.compression,
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
        """写入一条短期消息，并维护滑动窗口。

        zadd 与三条窗口修剪命令合并成一次往返（原本是 4 次）。
        """
        try:
            key = self._build_session_keys(tenant_id, user_id, session_id)["messages"]
            async with self.redis.pipeline(transaction=False) as pipe:
                pipe.zadd(key, {compress_message(message): message_score(message)})
                queue_window_pruning(pipe, key, self.settings)
                await pipe.execute()
        except Exception as exc:
            log_degradation(logger, "stm.append_message", exc, session=session_id)

    async def append_messages(
        self,
        tenant_id: str,
        user_id: str,
        session_id: str,
        messages: Sequence[MessageRecord],
    ) -> None:
        """批量写入短期消息（一轮对话的 user + assistant 合并成一次往返）。"""
        if not messages:
            return
        try:
            key = self._build_session_keys(tenant_id, user_id, session_id)["messages"]
            scored = {compress_message(message): message_score(message) for message in messages}
            async with self.redis.pipeline(transaction=False) as pipe:
                pipe.zadd(key, scored)
                queue_window_pruning(pipe, key, self.settings)
                await pipe.execute()
        except Exception as exc:
            log_degradation(logger, "stm.append_messages", exc, session=session_id)

    async def append_turn_once(
        self,
        tenant_id: str,
        user_id: str,
        session_id: str,
        *,
        turn_id: str,
        user_message: str,
        assistant_message: str,
        created_at: int,
    ) -> tuple[bool, SessionMeta]:
        """原子地追加一个回合；同一 turn_id 重放时不重复写入。

        先获取 session 级短锁，再用 Redis transaction 一次提交消息、meta 和
        已处理回合标记。这样进程在 EXEC 前退出不会留下半标记，EXEC 后重放则能
        看到完整的 turn_id 记录。
        """
        keys = self._build_session_keys(tenant_id, user_id, session_id)
        token = f"turn-{time.time_ns()}"
        acquired = await self.redis.set(
            keys["turn_lock"],
            token,
            ex=self.settings.lock_ttl_seconds,
            nx=True,
        )
        if not acquired:
            # 与同 session 的在途回合同步时不抢写；Stream 会在 PEL 中重试。
            raise RuntimeError(f"STM 回合锁忙: {session_id}")

        try:
            existing = await self.redis.hget(keys["turns"], turn_id)  # type: ignore[misc]
            meta = await self.get_meta(tenant_id, user_id, session_id)
            if existing is not None:
                return False, meta

            meta.total_turns += 1
            meta.last_updated_at = created_at
            messages = [
                MessageRecord(
                    message_id=f"msg_u_{turn_id}",
                    role="user",
                    content=user_message,
                    created_at=created_at,
                    turn_index=meta.total_turns,
                ),
                MessageRecord(
                    message_id=f"msg_a_{turn_id}",
                    role="assistant",
                    content=assistant_message,
                    created_at=created_at,
                    turn_index=meta.total_turns,
                ),
            ]
            scored = {compress_message(message): message_score(message) for message in messages}
            async with self.redis.pipeline(transaction=True) as pipe:
                pipe.zadd(keys["messages"], scored)
                queue_window_pruning(pipe, keys["messages"], self.settings)
                pipe.set(keys["meta"], meta.model_dump_json(), ex=self.settings.ttl_seconds)
                pipe.hset(keys["turns"], turn_id, str(meta.total_turns))
                pipe.expire(keys["turns"], self.settings.ttl_seconds)
                await pipe.execute()
            return True, meta
        except Exception as exc:
            log_degradation(logger, "stm.append_turn_once", exc, session=session_id)
            raise
        finally:
            await self.redis.delete(keys["turn_lock"])

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
            log_degradation(logger, "stm.get_recent_messages", exc, session=session_id)
            return []

    async def get_message_count(self, tenant_id: str, user_id: str, session_id: str) -> int:
        """返回当前 session 的消息条数。"""
        try:
            key = self._build_session_keys(tenant_id, user_id, session_id)["messages"]
            return await self.redis.zcard(key)
        except Exception as exc:
            log_degradation(logger, "stm.get_message_count", exc, session=session_id)
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
            log_degradation(logger, "stm.get_summary", exc, session=session_id)
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
            log_degradation(logger, "stm.save_summary", exc, session=session_id)

    async def get_meta(self, tenant_id: str, user_id: str, session_id: str) -> SessionMeta:
        """读取会话元信息,不存在时返回默认对象。"""
        try:
            key = self._build_session_keys(tenant_id, user_id, session_id)["meta"]
            meta = decode_model(await self.redis.get(key), SessionMeta)
            if meta:
                return meta
        except Exception as exc:
            log_degradation(logger, "stm.get_meta", exc, session=session_id)
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
            log_degradation(logger, "stm.save_meta", exc, session=session_id)

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
        *,
        compression_id: str = "",
    ) -> bool:
        """压缩旧消息，并保留最近若干轮原始消息。

        四路状态读取（meta / 计数 / 摘要 / 消息）互不依赖，并发发起。

        v3.36+: compression_id 非空时，压缩完成后记录到 compression_tasks
        表，实现显式幂等——即使进程在 save_summary → rewrite_recent_messages
        → save_meta 之间崩���，重放时可凭 compression_id 判断状态。
        """
        try:
            keys = self._build_session_keys(tenant_id, user_id, session_id)
            meta, msg_count, old_summary, all_messages = await asyncio.gather(
                self.get_meta(tenant_id, user_id, session_id),
                self.get_message_count(tenant_id, user_id, session_id),
                self.get_summary(tenant_id, user_id, session_id),
                self.get_recent_messages(
                    tenant_id,
                    user_id,
                    session_id,
                    limit=COMPRESS_FETCH_LIMIT,
                ),
            )
            context = build_compression_context(
                runtime=self.settings,
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
                    redis_client=self.redis,
                    key=current.keys["messages"],
                    messages=current.messages_to_keep,
                    runtime=self.settings,
                ),
                save_meta=lambda meta: self.save_meta(tenant_id, user_id, session_id, meta),
                compression_id=compression_id,
                on_complete=_build_compression_task_recorder(
                    compression_id, session_id, tenant_id, user_id
                ) if compression_id else None,
            )
        except Exception as exc:
            log_degradation(logger, "stm.compress_session_memory", exc, session=session_id)
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
            log_degradation(logger, "stm.refresh_ttl", exc, session=session_id)

    async def clear_session(
        self,
        tenant_id: str,
        user_id: str,
        session_id: str,
    ) -> int:
        """删除指定 session 的全部短期记忆 key。

        会清理 messages / summary / meta / lock，返回实际删除的 key 数量。
        """
        try:
            keys = self._build_session_keys(tenant_id, user_id, session_id)
            deleted = await self.redis.delete(
                keys["messages"],
                keys["summary"],
                keys["meta"],
                keys["lock"],
                keys["turns"],
                keys["turn_lock"],
            )
            return int(deleted or 0)
        except Exception as exc:
            log_degradation(
                logger,
                "stm.clear_session",
                exc,
                tenant=tenant_id,
                user=user_id,
                session=session_id,
            )
            return 0
