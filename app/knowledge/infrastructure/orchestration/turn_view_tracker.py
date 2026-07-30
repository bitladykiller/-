"""回合视图写入状态追踪。

职责：
- 每个物化视图（history/stm/compression/ltm/profile/hits）独立跟踪状态
- after_agent 完成后返回 TurnMemoryReport，handler 据此决定补偿策略
- 支持失败视图单独重放，替代"全部成功才标记 completed"的粗粒度模式
"""

from __future__ import annotations

import hashlib
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any

from app.shared.core.database import AsyncSessionLocal, Base
from sqlalchemy import DateTime, Integer, String, Text, UniqueConstraint, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Mapped, mapped_column

logger = logging.getLogger(__name__)


class ViewStatus(str, Enum):
    """视图写入状态。"""

    PENDING = "pending"
    COMPLETED = "completed"
    FAILED = "failed"
    SKIPPED = "skipped"


class ViewName(str, Enum):
    """物化视图名称。"""

    HISTORY = "history"
    STM = "stm"
    COMPRESSION = "compression"
    LTM = "ltm"
    PROFILE = "profile"
    HITS = "hits"


@dataclass
class TurnMemoryReport:
    """after_agent 执行后返回的状态报告。

    handler 根据此报告决定：
    - 全部 completed → mark_completed + XACK
    - 部分 failed → mark_failed（允许整回合重放，各视图自带幂等）
    - 但对于非幂等危险视图（profile/hits），仅记录状态，不触发整回合重放
    """

    turn_id: str = ""
    views: dict[str, ViewStatus] = field(default_factory=dict)
    errors: dict[str, str] = field(default_factory=dict)

    @property
    def any_failed(self) -> bool:
        return any(s == ViewStatus.FAILED for s in self.views.values())

    @property
    def all_completed(self) -> bool:
        return bool(self.views) and all(
            s in (ViewStatus.COMPLETED, ViewStatus.SKIPPED)
            for s in self.views.values()
        )

    @property
    def failed_views(self) -> list[str]:
        return [v for v, s in self.views.items() if s == ViewStatus.FAILED]

    def record(self, view: str, status: ViewStatus, error: str = "") -> None:
        self.views[view] = status
        if error:
            self.errors[view] = error


class TurnViewStatus(Base):
    """turn_view_status 表 ORM 模型。"""

    __tablename__ = "turn_view_status"
    __table_args__ = (
        UniqueConstraint("turn_id", "view_name", name="uk_turn_view"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    turn_id: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    view_name: Mapped[str] = mapped_column(String(32), nullable=False)
    status: Mapped[str] = mapped_column(
        String(32), nullable=False, default=ViewStatus.PENDING.value
    )
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    last_error: Mapped[str] = mapped_column(Text, nullable=False, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(timezone.utc))
    updated_at: Mapped[datetime] = mapped_column(
        DateTime,
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )


class CompressionTask(Base):
    """compression_tasks 表 ORM 模型（压缩显式幂等键）。"""

    __tablename__ = "compression_tasks"
    __table_args__ = (
        UniqueConstraint("compression_id", name="uk_compression_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    compression_id: Mapped[str] = mapped_column(String(64), nullable=False)
    session_id: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    tenant_id: Mapped[str] = mapped_column(String(128), nullable=False, default="")
    user_id: Mapped[str] = mapped_column(String(128), nullable=False, default="")
    from_turn: Mapped[int] = mapped_column(Integer, nullable=False)
    to_turn: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="processing")
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    last_error: Mapped[str] = mapped_column(Text, nullable=False, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(timezone.utc))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class MemoryHitEvent(Base):
    """memory_hit_events 表 ORM 模型（LTM hit_count 去重）。"""

    __tablename__ = "memory_hit_events"
    __table_args__ = (
        UniqueConstraint("turn_id", "memory_id", name="uk_hit_event"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    turn_id: Mapped[str] = mapped_column(String(128), nullable=False)
    memory_id: Mapped[str] = mapped_column(String(128), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(timezone.utc))


def build_compression_id(session_id: str, from_turn: int, to_turn: int) -> str:
    """生成压缩任务幂等键：SHA256(session_id:from_turn:to_turn)。"""
    raw = f"{session_id}:{from_turn}:{to_turn}".encode()
    return hashlib.sha256(raw).hexdigest()


async def record_view_status(
    turn_id: str,
    view_name: str,
    status: str,
    error: str = "",
) -> None:
    """记录单个视图的写入状态到 turn_view_status 表。

    同 (turn_id, view_name) 已存在时，更新状态和错误（不增加 attempts，
    因为这是同一次 after_agent 调用内的最终结果，而非 Stream 层面的重试）。
    """
    if not turn_id:
        return
    try:
        async with AsyncSessionLocal() as db:
            await db.execute(
                text(
                    "INSERT INTO turn_view_status (turn_id, view_name, status, last_error) "
                    "VALUES (:tid, :vn, :st, :err) "
                    "ON DUPLICATE KEY UPDATE status = :st2, last_error = :err2"
                ),
                {
                    "tid": turn_id,
                    "vn": view_name,
                    "st": status,
                    "err": (error or "")[:1000],
                    "st2": status,
                    "err2": (error or "")[:1000],
                },
            )
            await db.commit()
    except Exception:
        logger.warning(
            "记录视图状态失败 | turn=%s view=%s",
            turn_id,
            view_name,
            exc_info=True,
        )


async def record_all_view_statuses(
    turn_id: str,
    report: TurnMemoryReport,
) -> None:
    """将 TurnMemoryReport 中的所有视图状态批量写入 turn_view_status。"""
    for view_name in ViewName:
        status = report.views.get(view_name.value, ViewStatus.SKIPPED.value)
        error = report.errors.get(view_name.value, "")
        await record_view_status(turn_id, view_name.value, status, error)


__all__ = [
    "CompressionTask",
    "MemoryHitEvent",
    "TurnMemoryReport",
    "TurnViewStatus",
    "ViewName",
    "ViewStatus",
    "build_compression_id",
    "record_all_view_statuses",
    "record_view_status",
]
