"""Redis Stream 消费端 MySQL Inbox。

Inbox 负责把 Redis Streams 的“至少一次投递”收敛成业务层可控的幂等状态：
同一 (event_type, event_id) 只有一个消费者能进入业务处理；已完成事件重放时
直接 ACK；payload hash 冲突时拒绝执行业务副作用。
"""

from __future__ import annotations

import hashlib
import json
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import Enum
from typing import Any

from app.shared.core.database import AsyncSessionLocal, Base
from app.shared.core.logger import get_logger
from sqlalchemy import DateTime, Integer, String, Text, UniqueConstraint, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import async_sessionmaker
from sqlalchemy.orm import Mapped, mapped_column

logger = get_logger(__name__)

EVENT_STATUS_PROCESSING = "processing"
EVENT_STATUS_COMPLETED = "completed"
EVENT_STATUS_FAILED = "failed"

DEFAULT_EVENT_LEASE_SECONDS = 120


class InboxClaimAction(str, Enum):
    """Inbox 认领结果。"""

    PROCESS = "process"
    SKIP_COMPLETED = "skip_completed"
    BUSY = "busy"
    PAYLOAD_CONFLICT = "payload_conflict"


@dataclass(frozen=True)
class InboxClaim:
    """单次 Inbox 认领结果。"""

    action: InboxClaimAction
    event_id: str
    owner: str
    attempts: int = 0


class ProcessedEvent(Base):
    """processed_events 表：Stream 消费幂等收件箱。"""

    __tablename__ = "processed_events"
    __table_args__ = (UniqueConstraint("event_type", "event_id", name="uk_processed_event"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    event_type: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    event_id: Mapped[str] = mapped_column(String(128), nullable=False)
    stream_name: Mapped[str] = mapped_column(String(128), nullable=False, default="")
    stream_entry_id: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    payload_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
        default=EVENT_STATUS_PROCESSING,
        index=True,
    )
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    lease_owner: Mapped[str] = mapped_column(String(128), nullable=False, default="")
    lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    last_error: Mapped[str] = mapped_column(Text, nullable=False, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime,
        default=datetime.utcnow,
        onupdate=datetime.utcnow,
    )
    completed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    dead_lettered_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


def stable_payload_hash(payload: dict[str, Any]) -> str:
    """对事件 payload 生成稳定 hash，用于发现 event_id 被错误复用。"""
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def resolve_event_id(
    *,
    event_type: str,
    payload: dict[str, Any],
    stream: str,
    entry_id: str,
) -> str:
    """解析事件幂等 ID；兼容部署前遗留的无 ID 消息。"""
    candidates = (
        payload.get("event_id"),
        payload.get("turn_id"),
        payload.get("task_id"),
    )
    for candidate in candidates:
        value = str(candidate or "").strip()
        if value:
            return value[:128]
    raw = f"{stream}:{entry_id}:{event_type}".encode()
    digest = hashlib.sha256(raw).hexdigest()
    return f"legacy_{digest[:32]}"


class EventInbox:
    """MySQL-backed Stream 消费幂等控制器。"""

    def __init__(
        self,
        session_factory: async_sessionmaker[Any] = AsyncSessionLocal,
        *,
        lease_seconds: int = DEFAULT_EVENT_LEASE_SECONDS,
    ) -> None:
        self._session_factory = session_factory
        self._lease_seconds = max(1, int(lease_seconds))

    def _new_owner(self) -> str:
        return f"inbox-{uuid.uuid4().hex[:12]}"

    def _lease_deadline(self) -> datetime:
        return datetime.utcnow() + timedelta(seconds=self._lease_seconds)

    async def claim(
        self,
        *,
        event_type: str,
        event_id: str,
        payload_hash: str,
        stream: str,
        entry_id: str,
    ) -> InboxClaim:
        """尝试认领事件；返回 PROCESS 才允许执行业务 handler。"""
        owner = self._new_owner()
        now = datetime.utcnow()
        async with self._session_factory() as db:
            row = ProcessedEvent(
                event_type=event_type,
                event_id=event_id,
                stream_name=stream,
                stream_entry_id=entry_id,
                payload_hash=payload_hash,
                status=EVENT_STATUS_PROCESSING,
                attempts=1,
                lease_owner=owner,
                lease_expires_at=self._lease_deadline(),
                last_error="",
            )
            db.add(row)
            try:
                await db.commit()
                return InboxClaim(
                    InboxClaimAction.PROCESS,
                    event_id=event_id,
                    owner=owner,
                    attempts=1,
                )
            except IntegrityError:
                await db.rollback()

            result = await db.execute(
                select(ProcessedEvent).where(
                    ProcessedEvent.event_type == event_type,
                    ProcessedEvent.event_id == event_id,
                )
            )
            existing = result.scalar_one_or_none()
            if existing is None:
                raise RuntimeError(f"processed_event disappeared: {event_type}/{event_id}")

            if existing.payload_hash != payload_hash:
                existing.status = EVENT_STATUS_FAILED
                existing.last_error = "payload_hash_conflict"
                existing.updated_at = now
                await db.commit()
                return InboxClaim(
                    InboxClaimAction.PAYLOAD_CONFLICT,
                    event_id=event_id,
                    owner=owner,
                    attempts=existing.attempts,
                )

            if existing.status == EVENT_STATUS_COMPLETED:
                return InboxClaim(
                    InboxClaimAction.SKIP_COMPLETED,
                    event_id=event_id,
                    owner=owner,
                    attempts=existing.attempts,
                )

            lease_active = (
                existing.status == EVENT_STATUS_PROCESSING
                and existing.lease_expires_at is not None
                and existing.lease_expires_at > now
            )
            if lease_active:
                return InboxClaim(
                    InboxClaimAction.BUSY,
                    event_id=event_id,
                    owner=owner,
                    attempts=existing.attempts,
                )

            existing.status = EVENT_STATUS_PROCESSING
            existing.attempts = int(existing.attempts or 0) + 1
            existing.lease_owner = owner
            existing.lease_expires_at = self._lease_deadline()
            existing.last_error = ""
            existing.stream_name = stream
            existing.stream_entry_id = entry_id
            existing.updated_at = now
            await db.commit()
            return InboxClaim(
                InboxClaimAction.PROCESS,
                event_id=event_id,
                owner=owner,
                attempts=existing.attempts,
            )

    async def refresh_lease(
        self,
        *,
        event_type: str,
        event_id: str,
        owner: str,
    ) -> None:
        """延长处理中事件的租约；长文档索引用它避免并发接管。"""
        async with self._session_factory() as db:
            result = await db.execute(
                select(ProcessedEvent).where(
                    ProcessedEvent.event_type == event_type,
                    ProcessedEvent.event_id == event_id,
                )
            )
            row = result.scalar_one_or_none()
            if row is None or row.lease_owner != owner:
                return
            if row.status != EVENT_STATUS_PROCESSING:
                return
            row.lease_expires_at = self._lease_deadline()
            row.updated_at = datetime.utcnow()
            await db.commit()

    async def mark_completed(
        self,
        *,
        event_type: str,
        event_id: str,
        owner: str,
    ) -> None:
        """标记事件处理完成。"""
        async with self._session_factory() as db:
            result = await db.execute(
                select(ProcessedEvent).where(
                    ProcessedEvent.event_type == event_type,
                    ProcessedEvent.event_id == event_id,
                )
            )
            row = result.scalar_one_or_none()
            if row is None:
                raise RuntimeError(f"processed_event not found: {event_type}/{event_id}")
            if row.lease_owner != owner and row.status != EVENT_STATUS_COMPLETED:
                raise RuntimeError(f"processed_event lease owner changed: {event_type}/{event_id}")
            now = datetime.utcnow()
            row.status = EVENT_STATUS_COMPLETED
            row.completed_at = now
            row.lease_expires_at = None
            row.last_error = ""
            row.updated_at = now
            await db.commit()

    async def mark_failed(
        self,
        *,
        event_type: str,
        event_id: str,
        owner: str,
        error: str,
        dead_lettered: bool = False,
    ) -> None:
        """记录失败原因；默认仍允许后续租约过期后重试。"""
        async with self._session_factory() as db:
            result = await db.execute(
                select(ProcessedEvent).where(
                    ProcessedEvent.event_type == event_type,
                    ProcessedEvent.event_id == event_id,
                )
            )
            row = result.scalar_one_or_none()
            if row is None:
                return
            if row.lease_owner != owner and not dead_lettered:
                return
            now = datetime.utcnow()
            row.status = EVENT_STATUS_FAILED
            row.last_error = (error or "")[:1000]
            row.lease_expires_at = None if dead_lettered else now
            row.dead_lettered_at = now if dead_lettered else row.dead_lettered_at
            row.updated_at = now
            await db.commit()


__all__ = [
    "DEFAULT_EVENT_LEASE_SECONDS",
    "EVENT_STATUS_COMPLETED",
    "EVENT_STATUS_FAILED",
    "EVENT_STATUS_PROCESSING",
    "EventInbox",
    "InboxClaim",
    "InboxClaimAction",
    "ProcessedEvent",
    "resolve_event_id",
    "stable_payload_hash",
]
