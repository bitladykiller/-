"""应用事件定义与处理器注册。

事件流拓扑（单 stream 单消费组，默认在 app 进程内消费，
也可用 `python -m app.worker` 拆成独立 worker）：

    agent:events / group "core"
    ├── turn_completed            对话回合完成
    │     ├── MySQL messages 表落一轮历史（给人看的记录）
    │     └── MemoryMiddleware.after_agent（STM/压缩/LTM/画像）
    └── document_index_requested  文档索引请求
          └── run_document_indexing_job + Redis 任务状态流转

WHY 记忆写入走事件而不是 fire-and-forget 协程：
协程随进程共存亡且没有重试；事件在 Redis 里有 PEL 兜底——
进程崩溃后由 XAUTOCLAIM 认领重放，失败有次数上限和死信可查。
"""

from __future__ import annotations

from typing import Any

from app.shared.core.degradation import log_degradation
from app.shared.core.logger import get_logger
from app.shared.streams import EventHandler

logger = get_logger(__name__)

EVENT_STREAM = "agent:events"
EVENT_GROUP = "core"

EVENT_TURN_COMPLETED = "turn_completed"
EVENT_DOCUMENT_INDEX_REQUESTED = "document_index_requested"


async def handle_turn_completed(payload: dict[str, Any]) -> None:
    """对话回合完成：持久化历史 + 写记忆。

    两步各自容错：历史落库失败不阻断记忆写入，反之亦然。
    整个 handler 抛出才会触发 stream 重试，因此只有"两步都想重试"
    的系统性故障（如 Redis/DB 全挂）才向上抛。
    """
    tenant_id = str(payload.get("tenant_id") or "default")
    user_id = str(payload.get("user_id") or "")
    session_id = str(payload.get("session_id") or "")
    user_message = str(payload.get("user_message") or "")
    assistant_message = str(payload.get("assistant_message") or "")
    turn_id = str(payload.get("turn_id") or payload.get("event_id") or "")
    if not (user_id and session_id and user_message and assistant_message):
        logger.warning("turn_completed 载荷不完整，丢弃 | payload_keys=%s", sorted(payload))
        return

    if turn_id:
        await _persist_turn_history(
            session_id,
            user_message,
            assistant_message,
            turn_event_id=turn_id,
        )
    else:
        await _persist_turn_history(session_id, user_message, assistant_message)

    from app.platform.container import get_container

    container = await get_container()
    middleware = container.memory_middleware
    if middleware is None:
        logger.warning("记忆中间件未初始化，跳过记忆写入 | session=%s", session_id)
        return
    memory_kwargs: dict[str, Any] = {
        "tenant_id": tenant_id,
        "user_id": user_id,
        "session_id": session_id,
        "user_message": user_message,
        "assistant_message": assistant_message,
    }
    if turn_id:
        memory_kwargs["turn_id"] = turn_id
    event_created_at = payload.get("event_created_at")
    if isinstance(event_created_at, (int, str)) and str(event_created_at).isdigit():
        memory_kwargs["event_created_at"] = int(event_created_at)
    await middleware.after_agent(**memory_kwargs)


async def _persist_turn_history(
    session_id: str,
    user_message: str,
    assistant_message: str,
    *,
    turn_event_id: str | None = None,
) -> None:
    """把一轮对话写入 MySQL messages（失败降级，不阻断记忆写入）。

    session_id 即会话主键（v3.35.0 起服务端保证一致）；
    历史遗留的非数字 session 直接跳过。
    """
    if not session_id.isdigit():
        return
    try:
        from app.chat.infrastructure.repository.message_repository import (
            MessageRepository,
        )
        from app.shared.core.database import AsyncSessionLocal

        async with AsyncSessionLocal() as db:
            await MessageRepository(db).add_turn(
                int(session_id),
                user_message,
                assistant_message,
                turn_event_id=turn_event_id,
            )
    except Exception as exc:
        log_degradation(logger, "events.persist_turn_history", exc, session=session_id)


async def handle_document_index_requested(payload: dict[str, Any]) -> None:
    """文档索引请求：执行索引任务并维护 Redis 任务状态。

    任务状态复用 background_tasks 的读写协议，前端轮询接口不感知
    执行通道的变化。handler 自身不抛业务异常（状态里已带 failed），
    只有基础设施故障才向上抛触发重试。
    """
    task_id = str(payload.get("task_id") or "")
    file_info = payload.get("file_info")
    if not task_id or not isinstance(file_info, dict):
        logger.warning("document_index_requested 载荷不完整，丢弃")
        return

    from app.knowledge.application.document_indexing_job import (
        run_document_indexing_job_with_task,
    )
    from app.platform.container import get_container
    from app.shared.background_tasks import run_task_with_status_updates

    container = await get_container()
    manager = container.task_manager
    if manager is None:
        raise RuntimeError("task_manager 未初始化，无法回写任务状态")

    indexed_file_info = {**file_info, "event_id": str(payload.get("event_id") or task_id)}
    await run_task_with_status_updates(
        manager._redis,  # noqa: SLF001 — 平台层与任务层同属基础设施内核
        logger,
        task_id,
        run_document_indexing_job_with_task,
        indexed_file_info,
        task_id,
        origin="stream",
    )


def build_core_handlers() -> dict[str, EventHandler]:
    """核心消费组的事件路由表。"""
    return {
        EVENT_TURN_COMPLETED: handle_turn_completed,
        EVENT_DOCUMENT_INDEX_REQUESTED: handle_document_index_requested,
    }


__all__ = [
    "EVENT_DOCUMENT_INDEX_REQUESTED",
    "EVENT_GROUP",
    "EVENT_STREAM",
    "EVENT_TURN_COMPLETED",
    "build_core_handlers",
    "handle_document_index_requested",
    "handle_turn_completed",
]
