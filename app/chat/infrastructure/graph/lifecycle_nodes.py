"""主图中的响应后处理节点实现。

职责：
- 将本轮对话写入 Redis STM，并触发 LTM 抽取
- 通过 AppContainer 获取 MemoryMiddleware

设计要点：记忆写入是**后台任务**，不在 SSE 关键路径上。

v3.36+:
- 透传 long_term_memories 到事件 payload，打通 LTM 读→写链路
- fire-and-forget 回退路径增加监控计数器
"""

from __future__ import annotations

import asyncio
import time
import uuid

from app.chat.infrastructure.graph.memory_context import _get_memory_middleware, configurable_scope
from app.chat.infrastructure.graph.message_utils import (
    find_last_assistant_message,
    find_last_user_message,
)
from app.chat.infrastructure.graph.state import AgentState
from app.shared.core.logger import get_logger
from langchain_core.runnables import RunnableConfig

logger = get_logger(__name__)

# 持有后台写任务的引用：asyncio.create_task 的返回值若不被引用，
# 任务可能在完成前被垃圾回收，异常也会静默丢失
_pending_memory_writes: set[asyncio.Task[None]] = set()


def _extract_long_term_memory_ids(memory_state: object | None) -> list[str]:
    """从 memory_state 中提取 LTM 记忆 ID 列表，用于透传到事件 payload。

    memory_state 是 AgentMemoryState 实例，含 long_term_memories 字段。
    只传 memory_id（不传完整 content）以控制事件 payload 大小。
    """
    if memory_state is None:
        return []
    try:
        ltm_list = getattr(memory_state, "long_term_memories", None)
        if not ltm_list:
            return []
        return [
            result.memory.memory_id
            for result in ltm_list
            if hasattr(result, "memory")
            and bool(getattr(result.memory, "memory_id", ""))
        ]
    except Exception:
        return []


async def _write_turn_memory(
    *,
    tenant_id: str,
    user_id: str,
    session_id: str,
    user_message: str,
    assistant_message: str,
    turn_id: str,
    event_created_at: int,
    long_term_memories: list | None = None,
) -> None:
    """实际执行记忆写入；失败只打 warning，绝不上抛。

    v3.36+: persist_view_status=False 因为 MySQL 可能是回退原因；
    turn_id 仍在，STM/LTM 自带幂等保护。
    """
    middleware = await _get_memory_middleware()
    if middleware is None:
        return
    try:
        memory_kwargs: dict = {
            "tenant_id": tenant_id,
            "user_id": user_id,
            "session_id": session_id,
            "user_message": user_message,
            "assistant_message": assistant_message,
            "turn_id": turn_id,
            "event_created_at": event_created_at,
            "long_term_memories": long_term_memories,
            "persist_view_status": False,
        }
        try:
            await middleware.after_agent(**memory_kwargs)
        except TypeError as exc:
            if "turn_id" not in str(exc) and "event_created_at" not in str(exc):
                raise
            await middleware.after_agent(
                tenant_id=tenant_id,
                user_id=user_id,
                session_id=session_id,
                user_message=user_message,
                assistant_message=assistant_message,
            )
    except Exception:
        logger.warning("[memory] after_response 记忆写入失败，本轮对话可能丢失", exc_info=True)


async def flush_pending_memory_writes() -> None:
    """等待所有在途记忆写任务完成（测试与优雅停机使用）。"""
    pending = list(_pending_memory_writes)
    if pending:
        await asyncio.gather(*pending, return_exceptions=True)


async def _publish_turn_completed(payload: dict[str, str]) -> bool:
    """尝试把回合完成事件发到 Redis Streams。

    Returns:
        True 表示已投递（消费者负责历史落库 + 记忆写入，且具备
        崩溃后认领重放能力）；False 表示事件基础设施不可用，调用方
        应回退到进程内直写。
    """
    from app.platform.container import get_container_if_initialized

    container = get_container_if_initialized()
    queue = getattr(container, "event_queue", None) if container else None
    if queue is None:
        return False
    try:
        from app.platform.events import EVENT_TURN_COMPLETED

        await queue.publish(EVENT_TURN_COMPLETED, payload)
        return True
    except Exception:
        logger.warning("turn_completed 事件投递失败，回退进程内写入", exc_info=True)
        return False


async def after_response(state: AgentState, *, config: RunnableConfig) -> dict[str, object]:
    """响应后处理：发布回合完成事件（首选）或调度进程内写入（回退）。

    WHY 不在本节点同步 await 记忆写入：
    本节点跑在 `graph.astream` 内部，是 SSE 流的最后一站。记忆写入在触发
    压缩的轮次要跑"摘要 LLM + 抽取 LLM + 去重 embedding"，动辄数秒——
    答案已经发出，记忆是旁路产物，不该让用户为它等待。

    WHY 首选事件流：fire-and-forget 协程随进程��存亡且无重试；
    事件在 Redis PEL 里有崩溃认领与死信兜底（见 app.platform.events）。
    进程内直写仅作为事件基础设施不可用时的降级路径。

    v3.36+: 透传 long_term_memory_ids 到事件 payload，打通 LTM
    before_agent→after_agent 的命中统计链路。
    """
    tenant_id, user_id, session_id = configurable_scope(config)
    user_message = find_last_user_message(state.messages)
    assistant_message = find_last_assistant_message(state.messages)
    # 拒答/异常路径可能缺一侧消息，跳过写入避免脏会话
    if not (user_message and assistant_message):
        return {}

    turn_id = f"turn_{uuid.uuid4().hex}"
    event_created_at = int(time.time())

    # v3.36+: 提取 LTM 记忆 ID 列表用于透传
    ltm_memory_ids = _extract_long_term_memory_ids(state.memory_state)

    published = await _publish_turn_completed(
        {
            "event_id": turn_id,
            "turn_id": turn_id,
            "event_created_at": str(event_created_at),
            "tenant_id": tenant_id,
            "user_id": user_id,
            "session_id": session_id,
            "user_message": user_message,
            "assistant_message": assistant_message,
            "long_term_memory_ids": ",".join(ltm_memory_ids) if ltm_memory_ids else "",
        }
    )
    if published:
        return {}

    # ---- fire-and-forget 回退路径 ----
    _record_fallback(session_id)

    # 恢复 LTM 对象列表用于 memory_middleware 的 hit update
    long_term_memories = _restore_ltm_memories(state.memory_state)

    task = asyncio.create_task(
        _write_turn_memory(
            tenant_id=tenant_id,
            user_id=user_id,
            session_id=session_id,
            user_message=user_message,
            assistant_message=assistant_message,
            turn_id=turn_id,
            event_created_at=event_created_at,
            long_term_memories=long_term_memories,
        ),
        name=f"memory_write:{session_id}",
    )
    _pending_memory_writes.add(task)
    task.add_done_callback(_pending_memory_writes.discard)
    return {}


def _record_fallback(session_id: str) -> None:
    """记录 fire-and-forget 回退路径使用事件。

    生产上应监控此计数器的增长速率——回退比例升高说明
    Redis Stream 基础设施不稳定，需排查。
    """
    try:
        from app.platform.events import _fallback_counter
        _fallback_counter["total"] += 1
        logger.warning(
            "[memory] after_response 使用 fire-and-forget 回退路径 | session=%s count=%d",
            session_id,
            _fallback_counter["total"],
        )
    except Exception:
        pass


def _restore_ltm_memories(memory_state: object | None) -> list:
    """从 memory_state 恢复 LTM MemorySearchResult 列表。"""
    if memory_state is None:
        return []
    try:
        ltm_list = getattr(memory_state, "long_term_memories", None)
        if ltm_list is None:
            return []
        return list(ltm_list)
    except Exception:
        return []


__all__ = ["after_response", "flush_pending_memory_writes"]
