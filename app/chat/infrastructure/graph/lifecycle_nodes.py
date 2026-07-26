"""主图中的响应后处理节点实现。

职责：
- 将本轮对话写入 Redis STM，并触发 LTM 抽取
- 通过 AppContainer 获取 MemoryMiddleware

设计要点：记忆写入是**后台任务**，不在 SSE 关键路径上。
"""

from __future__ import annotations

import asyncio

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


async def _write_turn_memory(
    *,
    tenant_id: str,
    user_id: str,
    session_id: str,
    user_message: str,
    assistant_message: str,
) -> None:
    """实际执行记忆写入；失败只打 warning，绝不上抛。"""
    middleware = await _get_memory_middleware()
    if middleware is None:
        return
    try:
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


async def after_response(
    state: AgentState, *, config: RunnableConfig
) -> dict[str, object]:
    """响应后处理：把本轮对话的记忆写入调度为后台任务。

    WHY fire-and-forget 而不是 await：
    本节点跑在 `graph.astream` 内部，是 SSE 流的最后一站。记忆写入在触发
    压缩的轮次要跑"摘要 LLM + 抽取 LLM + 去重 embedding"，动辄数秒——
    同步 await 意味着用户看完答案后，连接还要挂着转圈等记忆落盘。
    答案已经发出，记忆是旁路产物，不该让用户为它等待。

    WHY 失败只打 warning：用户答案已通过 SSE 发出，记忆失败不得反向变成 500。

    代价（接受）：极端情况下用户在写入完成前发下一条消息，before_agent
    可能读不到上一轮。这个窗口在同步版本里同样存在（网络间隙），
    且 STM 压缩持有分布式锁，不会出现并发写坏数据。
    """
    tenant_id, user_id, session_id = configurable_scope(config)
    user_message = find_last_user_message(state.messages)
    assistant_message = find_last_assistant_message(state.messages)
    # 拒答/异常路径可能缺一侧消息，跳过写入避免脏会话
    if not (user_message and assistant_message):
        return {}

    task = asyncio.create_task(
        _write_turn_memory(
            tenant_id=tenant_id,
            user_id=user_id,
            session_id=session_id,
            user_message=user_message,
            assistant_message=assistant_message,
        ),
        name=f"memory_write:{session_id}",
    )
    _pending_memory_writes.add(task)
    task.add_done_callback(_pending_memory_writes.discard)
    return {}


__all__ = ["after_response", "flush_pending_memory_writes"]
