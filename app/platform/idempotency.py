"""请求层幂等（request_id / X-Request-ID）。

解决"客户端把同一操作发了两次"的重复提交问题：
- HTTP 网络抖动导致前端重试 → 每次都是新回合、新 turn_id，事件层幂等拦不住
- 客户端在"用户点击"时生成一次 X-Request-ID，重试时复用同一个值
- 服务端以 (user_id, request_id) 唯一键去重：首次执行并缓存响应，
  重复请求直接返回缓存结果（或 409）

与事件层幂等（processed_events / turn_id）的分工：
- 事件层：防"同一事件的投递重放"（Stream 至少一次、崩溃续跑）
- 请求层：防"同一操作的重复提交"（前端重试、连点）

状态机：
    processing ──业务成功──> completed（缓存响应快照）
        │                        │
        └──业务失败──> failed    └──重复请求──> 返回缓存 / 409（SSE）
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from app.shared.core.database import AsyncSessionLocal, Base
from app.shared.core.logger import get_logger
from sqlalchemy import DateTime, Integer, String, Text, UniqueConstraint, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import async_sessionmaker
from sqlalchemy.orm import Mapped, mapped_column

logger = get_logger(__name__)

REQUEST_ID_HEADER = "X-Request-ID"

IDEMPOTENCY_STATUS_PROCESSING = "processing"
IDEMPOTENCY_STATUS_COMPLETED = "completed"
IDEMPOTENCY_STATUS_FAILED = "failed"

# 响应快照上限：超出则只记状态不缓存 body（重复请求返回 409）
RESPONSE_SNAPSHOT_LIMIT = 16 * 1024


@dataclass(frozen=True)
class IdempotencyDecision:
    """单次幂等检查结果。"""

    # None = 无 request_id（放行，不参与幂等）
    # True = 首次请求，执行并记录
    # False = 重复请求
    is_new: bool | None
    request_id: str = ""
    cached_status: int = 0
    cached_body: str = ""


class RequestIdempotency(Base):
    """request_idempotency 表：请求层幂等收件箱。"""

    __tablename__ = "request_idempotency"
    __table_args__ = (UniqueConstraint("user_id", "request_id", name="uk_request_idempotency"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    request_id: Mapped[str] = mapped_column(String(64), nullable=False)
    endpoint: Mapped[str] = mapped_column(String(128), nullable=False, default="")
    status: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
        default=IDEMPOTENCY_STATUS_PROCESSING,
        index=True,
    )
    response_status: Mapped[int | None] = mapped_column(Integer, nullable=True)
    response_body: Mapped[str] = mapped_column(Text, nullable=False, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime,
        default=datetime.utcnow,
        onupdate=datetime.utcnow,
    )


class RequestIdempotencyError(Exception):
    """幂等冲突（重复提交进行中 / 响应不可重放）。API 层映射 409。"""

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


class IdempotencyService:
    """请求层幂等控制器。"""

    def __init__(self, session_factory: async_sessionmaker | None = None) -> None:
        if session_factory is None:
            session_factory = AsyncSessionLocal
        self._session_factory = session_factory

    async def begin(
        self,
        *,
        user_id: int,
        request_id: str,
        endpoint: str,
    ) -> IdempotencyDecision:
        """认领一次幂等操作。

        返回决策：
        - is_new=True：首次到达，调用方执行业务并调 complete()
        - is_new=False：重复请求（completed → 带缓存；processing/failed → 409）
        - is_new=None：request_id 为空，放行
        """
        request_id = (request_id or "").strip()
        if not request_id:
            return IdempotencyDecision(is_new=None)

        async with self._session_factory() as db:
            row = RequestIdempotency(
                user_id=user_id,
                request_id=request_id[:64],
                endpoint=endpoint,
                status=IDEMPOTENCY_STATUS_PROCESSING,
            )
            db.add(row)
            try:
                await db.commit()
                return IdempotencyDecision(is_new=True, request_id=request_id)
            except IntegrityError:
                await db.rollback()

            result = await db.execute(
                select(RequestIdempotency).where(
                    RequestIdempotency.user_id == user_id,
                    RequestIdempotency.request_id == request_id,
                )
            )
            existing = result.scalar_one_or_none()
            if existing is None:
                raise RuntimeError(f"幂等记录消失: user={user_id} request_id={request_id}")

            if existing.status == IDEMPOTENCY_STATUS_COMPLETED:
                return IdempotencyDecision(
                    is_new=False,
                    request_id=request_id,
                    cached_status=int(existing.response_status or 0),
                    cached_body=existing.response_body or "",
                )
            # processing（并发重复提交）或 failed：不允许重放，客户端应换新 request_id
            raise RequestIdempotencyError(
                "该请求已提交且仍在处理中，请勿重复提交（或使用新的 request_id）"
            )

    async def complete(
        self,
        *,
        user_id: int,
        request_id: str,
        endpoint: str,
        response_status: int,
        response_body: str = "",
    ) -> None:
        """业务成功后记录完成状态与响应快照（供重复请求返回）。"""
        request_id = (request_id or "").strip()
        if not request_id:
            return
        try:
            snapshot = response_body or ""
            if len(snapshot) > RESPONSE_SNAPSHOT_LIMIT:
                snapshot = ""
            async with self._session_factory() as db:
                result = await db.execute(
                    select(RequestIdempotency).where(
                        RequestIdempotency.user_id == user_id,
                        RequestIdempotency.request_id == request_id,
                    )
                )
                row = result.scalar_one_or_none()
                if row is None:
                    return
                row.status = IDEMPOTENCY_STATUS_COMPLETED
                row.response_status = int(response_status)
                row.response_body = snapshot
                row.endpoint = endpoint
                await db.commit()
        except Exception as exc:
            # 幂等记录失败不阻断业务本身（业务落点有自己的幂等）
            logger.warning(
                "记录幂等完成状态失败 | user=%s request_id=%s | %s",
                user_id,
                request_id,
                exc,
            )

    async def mark_failed(
        self,
        *,
        user_id: int,
        request_id: str,
        error: str = "",
    ) -> None:
        """业务失败时标记 failed（重复请求将 409，不重放失败结果）。"""
        request_id = (request_id or "").strip()
        if not request_id:
            return
        try:
            async with self._session_factory() as db:
                result = await db.execute(
                    select(RequestIdempotency).where(
                        RequestIdempotency.user_id == user_id,
                        RequestIdempotency.request_id == request_id,
                    )
                )
                row = result.scalar_one_or_none()
                if row is None:
                    return
                row.status = IDEMPOTENCY_STATUS_FAILED
                row.response_body = (error or "")[:2000]
                await db.commit()
        except Exception:
            logger.warning("标记幂等失败状态失败", exc_info=True)


idempotency_service = IdempotencyService()

__all__ = [
    "IDEMPOTENCY_STATUS_COMPLETED",
    "IDEMPOTENCY_STATUS_FAILED",
    "IDEMPOTENCY_STATUS_PROCESSING",
    "REQUEST_ID_HEADER",
    "RequestIdempotency",
    "RequestIdempotencyError",
    "IdempotencyDecision",
    "IdempotencyService",
    "idempotency_service",
]
