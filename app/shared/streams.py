"""Redis Streams 事件管线 — 持久化任务执行的最小内核。

这个模块负责：
- 事件发布（XADD）
- 消费组循环（XREADGROUP → 处理 → XACK）
- 故障恢复（XAUTOCLAIM 认领超时未 ACK 的消息）
- 超过重试上限的消息进入死信流

这个模块不负责：
- 具体事件的业务处理（handlers 由调用方注册）
- Redis 连接生命周期（客户端由容器/worker 注入）

WHY 用 Redis Streams 取代进程内 asyncio 任务：
`background_tasks` 的任务只活在进程内存里——进程重启任务即蒸发，只能靠
`interrupted` 状态让失败"可见"。Streams 把任务本体落到 Redis：
- 进程崩溃 → 消息停留在 PEL（pending entries list）→ 重启后 XAUTOCLAIM
  认领并**重新执行**，从"可见的失败"升级为"自动续跑"
- 多副本消费同一 group → 任务天然被分担
- delivery count 内建 → 重试上限与死信不需要自己记账

不引入新依赖：redis.asyncio 已在依赖清单里。
"""

from __future__ import annotations

import asyncio
import json
import uuid
from collections.abc import Awaitable, Callable
from typing import Any

from app.shared.core.degradation import log_degradation
from app.shared.core.logger import get_logger

logger = get_logger(__name__)

EventHandler = Callable[[dict[str, Any]], Awaitable[None]]

#: 事件负载在 stream entry 里的字段名
_FIELD_TYPE = "type"
_FIELD_DATA = "data"

DEFAULT_BLOCK_MS = 5000
DEFAULT_CLAIM_IDLE_MS = 60_000
DEFAULT_MAX_DELIVERIES = 3
DEFAULT_BATCH_COUNT = 16


def encode_event(event_type: str, payload: dict[str, Any]) -> dict[str, str]:
    """把事件编码为 stream entry 字段。"""
    return {_FIELD_TYPE: event_type, _FIELD_DATA: json.dumps(payload, ensure_ascii=False)}


def decode_event(fields: dict[Any, Any]) -> tuple[str, dict[str, Any]] | None:
    """从 entry 字段还原事件；格式异常返回 None（调用方 ACK 后丢弃）。"""

    def _text(value: Any) -> str:
        return value.decode("utf-8") if isinstance(value, bytes) else str(value)

    raw_type = fields.get(_FIELD_TYPE) or fields.get(_FIELD_TYPE.encode())
    raw_data = fields.get(_FIELD_DATA) or fields.get(_FIELD_DATA.encode())
    if raw_type is None or raw_data is None:
        return None
    try:
        payload = json.loads(_text(raw_data))
    except (TypeError, ValueError):
        return None
    if not isinstance(payload, dict):
        return None
    return _text(raw_type), payload


class RedisStreamQueue:
    """单 stream + 单消费组的事件队列。"""

    def __init__(
        self,
        redis_client: Any,
        *,
        stream: str,
        group: str,
        consumer_name: str | None = None,
        block_ms: int = DEFAULT_BLOCK_MS,
        claim_idle_ms: int = DEFAULT_CLAIM_IDLE_MS,
        max_deliveries: int = DEFAULT_MAX_DELIVERIES,
        event_inbox: Any | None = None,
    ) -> None:
        self._redis = redis_client
        self.stream = stream
        self.group = group
        # 消费者名带随机后缀：多副本共享 group 时互不覆盖
        self.consumer_name = consumer_name or f"consumer-{uuid.uuid4().hex[:8]}"
        self._block_ms = block_ms
        self._claim_idle_ms = claim_idle_ms
        self._max_deliveries = max_deliveries
        # Inbox 由容器注入；单元测试和独立使用者可以保持原有的纯 Stream 行为。
        self._event_inbox = event_inbox
        self.dead_letter_stream = f"{stream}:dead"

    # ------------------------------------------------------------------ #
    # 发布
    # ------------------------------------------------------------------ #

    async def publish(self, event_type: str, payload: dict[str, Any]) -> str:
        """发布事件，返回 stream entry id。"""
        entry_id = await self._redis.xadd(self.stream, encode_event(event_type, payload))
        return entry_id.decode() if isinstance(entry_id, bytes) else str(entry_id)

    # ------------------------------------------------------------------ #
    # 消费
    # ------------------------------------------------------------------ #

    async def ensure_group(self) -> None:
        """确保消费组存在（幂等）。"""
        try:
            await self._redis.xgroup_create(self.stream, self.group, id="0", mkstream=True)
        except Exception as exc:
            # BUSYGROUP = 已存在，属正常路径
            if "BUSYGROUP" not in str(exc):
                raise

    async def _handle_entry(
        self,
        handlers: dict[str, EventHandler],
        entry_id: Any,
        fields: dict[Any, Any],
        *,
        deliveries: int,
    ) -> None:
        """处理单条消息：成功 ACK；失败留在 PEL 等待重试或转死信。"""
        eid = entry_id.decode() if isinstance(entry_id, bytes) else str(entry_id)
        decoded = decode_event(fields)
        if decoded is None:
            logger.warning("丢弃格式异常的事件 | stream=%s id=%s", self.stream, eid)
            await self._redis.xack(self.stream, self.group, entry_id)
            return

        event_type, payload = decoded
        handler = handlers.get(event_type)
        if handler is None:
            logger.warning("无处理器的事件类型 | type=%s id=%s", event_type, eid)
            await self._redis.xack(self.stream, self.group, entry_id)
            return

        event_id = ""
        claim_owner = ""
        payload_conflict = False
        if self._event_inbox is not None:
            from app.platform.event_inbox import (
                InboxClaimAction,
                resolve_event_id,
                stable_payload_hash,
            )

            event_id = resolve_event_id(
                event_type=event_type,
                payload=payload,
                stream=self.stream,
                entry_id=eid,
            )
            # 把解析结果交给业务 handler：旧 PEL 同样能获得稳定的派生 event_id。
            payload = {**payload, "event_id": event_id}
            claim = await self._event_inbox.claim(
                event_type=event_type,
                event_id=event_id,
                payload_hash=stable_payload_hash(payload),
                stream=self.stream,
                entry_id=eid,
            )
            claim_owner = claim.owner
            if claim.action is InboxClaimAction.SKIP_COMPLETED:
                logger.info(
                    "幂等跳过已完成事件 | type=%s event_id=%s stream_id=%s",
                    event_type,
                    event_id,
                    eid,
                )
                await self._redis.xack(self.stream, self.group, entry_id)
                return
            if claim.action is InboxClaimAction.BUSY:
                logger.info(
                    "事件仍由其他消费者处理，保留 PEL | type=%s event_id=%s",
                    event_type,
                    event_id,
                )
                return
            if claim.action is InboxClaimAction.PAYLOAD_CONFLICT:
                payload_conflict = True

        try:
            if payload_conflict:
                raise ValueError(f"event_id payload 冲突: {event_id}")
            await handler(payload)
            if self._event_inbox is not None:
                # 必须先落 completed 再 ACK。此处失败时保留 PEL，重放由业务侧
                # event_id 幂等保障收敛。
                await self._event_inbox.mark_completed(
                    event_type=event_type,
                    event_id=event_id,
                    owner=claim_owner,
                )
        except Exception as exc:
            if self._event_inbox is not None and event_id:
                try:
                    await self._event_inbox.mark_failed(
                        event_type=event_type,
                        event_id=event_id,
                        owner=claim_owner,
                        error=str(exc),
                        dead_lettered=deliveries >= self._max_deliveries,
                    )
                except Exception:
                    logger.warning(
                        "Inbox 失败状态回写失败 | type=%s event_id=%s",
                        event_type,
                        event_id,
                        exc_info=True,
                    )
            log_degradation(
                logger,
                "streams.handle_event",
                exc,
                stream=self.stream,
                type=event_type,
                id=eid,
                deliveries=deliveries,
            )
            if deliveries >= self._max_deliveries:
                # 转死信 + ACK：不让毒消息永远阻塞重试循环
                await self._redis.xadd(
                    self.dead_letter_stream,
                    {**encode_event(event_type, payload), "error": str(exc)[:500]},
                )
                await self._redis.xack(self.stream, self.group, entry_id)
                logger.error(
                    "事件超过重试上限，已转死信 | type=%s id=%s deliveries=%s",
                    event_type,
                    eid,
                    deliveries,
                )
            return

        await self._redis.xack(self.stream, self.group, entry_id)

    async def _claim_stale(self, handlers: dict[str, EventHandler]) -> None:
        """认领超时未 ACK 的消息（上一代进程崩溃遗留），重新执行。"""
        try:
            result = await self._redis.xautoclaim(
                self.stream,
                self.group,
                self.consumer_name,
                min_idle_time=self._claim_idle_ms,
                start_id="0-0",
                count=DEFAULT_BATCH_COUNT,
            )
        except Exception as exc:
            log_degradation(logger, "streams.autoclaim", exc, stream=self.stream)
            return

        # redis-py 返回 (next_start_id, [(id, fields), ...]) 或三元组（含 deleted）
        entries = result[1] if isinstance(result, (list, tuple)) and len(result) >= 2 else []
        for entry_id, fields in entries:
            deliveries = await self._delivery_count(entry_id)
            await self._handle_entry(handlers, entry_id, fields, deliveries=deliveries)

    async def _delivery_count(self, entry_id: Any) -> int:
        """查询消息的投递次数；失败按 1 处理。"""
        try:
            eid = entry_id.decode() if isinstance(entry_id, bytes) else str(entry_id)
            pending = await self._redis.xpending_range(
                self.stream, self.group, min=eid, max=eid, count=1
            )
            if pending:
                first = pending[0]
                count = (
                    first.get("times_delivered")
                    if isinstance(first, dict)
                    else getattr(first, "times_delivered", None)
                )
                if isinstance(count, int):
                    return count
        except Exception:
            logger.debug("读取 delivery count 失败", exc_info=True)
        return 1

    async def run_consumer(
        self,
        handlers: dict[str, EventHandler],
        stop_event: asyncio.Event,
    ) -> None:
        """消费主循环：先认领遗留，再阻塞读新消息，直到 stop_event。"""
        await self.ensure_group()
        logger.info(
            "事件消费者启动 | stream=%s group=%s consumer=%s",
            self.stream,
            self.group,
            self.consumer_name,
        )
        while not stop_event.is_set():
            await self._claim_stale(handlers)
            try:
                batches = await self._redis.xreadgroup(
                    groupname=self.group,
                    consumername=self.consumer_name,
                    streams={self.stream: ">"},
                    count=DEFAULT_BATCH_COUNT,
                    block=self._block_ms,
                )
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                log_degradation(logger, "streams.read", exc, stream=self.stream)
                # 读失败退避，避免 Redis 故障时空转刷日志
                await asyncio.sleep(1)
                continue

            for _stream_name, entries in batches or []:
                for entry_id, fields in entries:
                    await self._handle_entry(handlers, entry_id, fields, deliveries=1)

        logger.info("事件消费者退出 | stream=%s consumer=%s", self.stream, self.consumer_name)


__all__ = [
    "DEFAULT_MAX_DELIVERIES",
    "EventHandler",
    "RedisStreamQueue",
    "decode_event",
    "encode_event",
]
