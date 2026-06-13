"""异步任务队列。

职责：
- 为文档解析等长耗时任务生成 task_id
- 用 Redis 保存任务状态，供轮询接口读取
- 用 `asyncio.create_task` 托管后台协程

边界：
- 这里只负责“提交 / 状态流转 / 结果持久化”
- 不负责具体的文档解析业务
- 关闭时只释放 Redis 连接，不主动改写后台任务生命周期
"""
from __future__ import annotations

import asyncio
from typing import Any, cast

from app.core.logger import get_logger
from app.services.task_queue_runtime import (
    close_runtime_safely,
    get_or_create_runtime,
    reset_runtime,
)
from app.services.task_queue_support import (
    TaskCallable,
    TaskStore,
    create_redis_client,
    read_task_status,
    run_task_with_status_updates,
    spawn_tracked_task,
    task_callable_name,
    write_task_status,
)
from app.services.task_queue_utils import (
    TaskStatus,
    TaskStatusPayload,
    new_task_id,
)

logger = get_logger(__name__)


def _build_task_manager(redis_url: str) -> "TaskManager":
    """根据 Redis URL 构造 TaskManager 实例。"""
    return TaskManager(create_redis_client(redis_url))


class TaskManager:
    """基于 Redis 的异步任务管理器。"""

    def __init__(self, redis_client: TaskStore) -> None:
        self._redis = redis_client
        # 保留后台任务引用，避免任务未完成前被垃圾回收后丢失异常信息。
        self._pending_tasks: set[asyncio.Task[Any]] = set()

    async def _save_status(
        self,
        task_id: str,
        status: TaskStatus,
        *,
        result: Any = None,
        error: str | None = None,
    ) -> None:
        """统一写入任务状态，主流程只表达状态流转。"""
        await write_task_status(
            self._redis,
            task_id,
            status,
            result=result,
            error=error,
        )

    def _spawn_task(
        self,
        task_id: str,
        coro_func: TaskCallable,
        *args: Any,
        **kwargs: Any,
    ) -> None:
        """创建后台任务并保留引用，避免异常未消费警告。"""
        spawn_tracked_task(
            self._pending_tasks,
            task_id,
            run_task_with_status_updates(
                self._redis,
                logger,
                task_id,
                coro_func,
                *args,
                **kwargs,
            ),
        )

    async def submit(
        self,
        coro_func: TaskCallable,
        *args: Any,
        **kwargs: Any,
    ) -> str:
        """提交一个后台协程任务并返回 task_id。"""
        task_id = new_task_id()
        await self._save_status(task_id, TaskStatus.PENDING)

        self._spawn_task(task_id, coro_func, *args, **kwargs)
        logger.info(
            "任务已提交 | task_id=%s | func=%s",
            task_id,
            task_callable_name(coro_func),
        )
        return task_id

    async def get_status(self, task_id: str) -> TaskStatusPayload | None:
        """读取任务状态，不存在时返回 None。"""
        return await read_task_status(self._redis, task_id)

    async def close(self) -> None:
        """关闭底层 Redis 连接。

        TaskManager 不在这里取消后台任务，避免关闭动作和业务任务生命周期耦合。
        应用层如果需要优雅停机，应先阻止新任务进入，再决定是否等待现有任务收尾。
        """
        await self._redis.close()


async def get_task_manager() -> TaskManager:
    """获取 TaskManager 单例。首次调用时创建 Redis 连接。"""
    from app.core.config import settings

    return cast(
        TaskManager,
        await get_or_create_runtime(
            lambda: _build_task_manager(settings.REDIS_URL)
        ),
    )


async def close_task_manager() -> None:
    """关闭 TaskManager 的 Redis 连接。"""
    manager = reset_runtime()
    if manager is None:
        return

    await close_runtime_safely(manager)


__all__ = ["TaskStatusPayload", "TaskManager", "get_task_manager", "close_task_manager"]
