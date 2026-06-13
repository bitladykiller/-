"""TaskQueue 主流程共享 helper。

职责：
- 定义 TaskStore 最小协议，隔离具体 Redis 客户端类型
- 承接后台任务命名、函数名提取等纯 helper
- 统一读写任务状态 payload，避免主流程散落 Redis key / JSON 细节
- 承接后台任务执行时的状态流转样板

边界：
- 不负责 TaskManager 运行时单例生命周期
- 不负责任务状态 JSON 编解码规则本身
- 不负责任务提交和后台协程执行编排
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable, Coroutine
from typing import Any, Protocol, TypeAlias

import redis.asyncio as aioredis

from app.services.task_queue_utils import (
    TASK_TTL_SECONDS,
    TaskStatus,
    TaskStatusPayload,
    build_task_key,
    build_task_status_payload,
    dump_task_status_payload,
    load_task_status_payload,
)

TaskResult: TypeAlias = Any
TaskCoroutine: TypeAlias = Coroutine[Any, Any, TaskResult]
TaskCallable: TypeAlias = Callable[..., TaskCoroutine]


class TaskStore(Protocol):
    """任务状态存储后端的最小接口。"""

    async def set(self, key: str, value: str, ex: int | None = None) -> Any: ...

    async def get(self, key: str) -> str | None: ...

    async def close(self) -> Any: ...


class TaskLogger(Protocol):
    """任务队列日志对象的最小接口。"""

    def info(self, msg: str, *args: Any, **kwargs: Any) -> Any: ...

    def error(self, msg: str, *args: Any, **kwargs: Any) -> Any: ...


def task_callable_name(coro_func: TaskCallable) -> str:
    """提取后台任务函数名，用于日志输出。"""
    return getattr(coro_func, "__name__", coro_func.__class__.__name__)


def create_redis_client(redis_url: str) -> TaskStore:
    """根据 Redis URL 创建异步 Redis 客户端。"""
    return aioredis.from_url(redis_url, decode_responses=True)


def build_background_task_name(task_id: str) -> str:
    """构造 asyncio 后台任务名，便于调试和问题排查。"""
    return f"task:{task_id}"


def register_pending_task(
    pending_tasks: set[asyncio.Task[Any]],
    task: asyncio.Task[Any],
) -> None:
    """登记后台任务引用，并在结束时自动移除。"""
    pending_tasks.add(task)
    task.add_done_callback(pending_tasks.discard)


def spawn_tracked_task(
    pending_tasks: set[asyncio.Task[Any]],
    task_id: str,
    coro: Coroutine[Any, Any, Any],
) -> None:
    """创建后台任务、附加标准名称并登记引用。"""
    task = asyncio.create_task(
        coro,
        name=build_background_task_name(task_id),
    )
    register_pending_task(pending_tasks, task)


async def write_task_status(
    redis_client: TaskStore,
    task_id: str,
    status: TaskStatus,
    *,
    result: Any = None,
    error: str | None = None,
) -> None:
    """构造统一状态 payload 并写入 Redis。"""
    payload = build_task_status_payload(
        task_id,
        status,
        result=result,
        error=error,
    )
    await redis_client.set(
        build_task_key(task_id),
        dump_task_status_payload(payload),
        ex=TASK_TTL_SECONDS,
    )


async def read_task_status(
    redis_client: TaskStore,
    task_id: str,
) -> TaskStatusPayload | None:
    """读取任务状态，不存在或格式异常时返回 None。"""
    raw = await redis_client.get(build_task_key(task_id))
    return load_task_status_payload(raw)


async def run_task_with_status_updates(
    redis_client: TaskStore,
    logger: TaskLogger,
    task_id: str,
    coro_func: TaskCallable,
    *args: Any,
    **kwargs: Any,
) -> None:
    """执行后台任务，并统一维护 Redis 状态流转与日志。"""
    await write_task_status(redis_client, task_id, TaskStatus.RUNNING)
    try:
        result = await coro_func(*args, **kwargs)
        await write_task_status(
            redis_client,
            task_id,
            TaskStatus.COMPLETED,
            result=result,
        )
        logger.info("任务完成 | task_id=%s", task_id)
    except Exception as exc:
        await write_task_status(
            redis_client,
            task_id,
            TaskStatus.FAILED,
            error=str(exc),
        )
        logger.error("任务失败 | task_id=%s | %s", task_id, exc, exc_info=True)


__all__ = [
    "TaskCallable",
    "TaskLogger",
    "TaskStore",
    "build_background_task_name",
    "create_redis_client",
    "read_task_status",
    "register_pending_task",
    "run_task_with_status_updates",
    "spawn_tracked_task",
    "task_callable_name",
    "write_task_status",
]
