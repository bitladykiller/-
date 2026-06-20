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
import json
import uuid
from collections.abc import Callable
from collections.abc import Coroutine
from datetime import datetime
from enum import Enum
from typing import Any, Protocol, TypeAlias

from typing_extensions import TypedDict

import redis.asyncio as aioredis

from app.shared.core.logger import get_logger

logger = get_logger(__name__)
_runtime_instance: "_TaskManager | None" = None
_runtime_lock: asyncio.Lock = asyncio.Lock()
_TASK_KEY_PREFIX = "task:doc_parse:"
_TASK_TTL_SECONDS = 3600 * 24  # 任务状态保留 24 小时


class TaskStatus(str, Enum):
    """任务状态枚举。"""

    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"


class TaskStatusPayload(TypedDict, total=False):
    """Redis 中存储的任务状态结构。"""

    task_id: str
    status: str
    updated_at: str
    result: Any
    error: str

TaskResult: TypeAlias = Any
TaskCoroutine: TypeAlias = Coroutine[Any, Any, TaskResult]
TaskCallable: TypeAlias = Callable[..., TaskCoroutine]


class TaskLogger(Protocol):
    """任务队列日志对象的最小接口。"""

    def info(self, msg: str, *args: Any, **kwargs: Any) -> Any: ...

    def error(self, msg: str, *args: Any, **kwargs: Any) -> Any: ...


class TaskStore(Protocol):
    """任务状态存储后端的最小接口。"""

    async def set(self, key: str, value: str, ex: int | None = None) -> Any: ...

    async def get(self, key: str) -> str | None: ...

    async def close(self) -> Any: ...


def build_task_status_payload(
    task_id: str,
    status: TaskStatus,
    *,
    result: Any = None,
    error: str | None = None,
) -> TaskStatusPayload:
    """构造统一的任务状态负载。"""
    payload: TaskStatusPayload = {
        "task_id": task_id,
        "status": status.value,
        "updated_at": datetime.now().isoformat(),
    }
    if result is not None:
        payload["result"] = result
    if error is not None:
        payload["error"] = error
    return payload


def dump_task_status_payload(payload: TaskStatusPayload) -> str:
    """把任务状态序列化为可写入 Redis 的 JSON 字符串。"""
    return json.dumps(payload, ensure_ascii=False, default=str)

def load_task_status_payload(raw: str | None) -> TaskStatusPayload | None:
    """从 Redis 原始值解析任务状态。"""
    if raw is None:
        return None
    try:
        raw_payload = json.loads(raw)
    except (TypeError, ValueError):
        return None

    if not isinstance(raw_payload, dict):
        return None

    task_id = raw_payload.get("task_id")
    status = raw_payload.get("status")
    updated_at = raw_payload.get("updated_at")
    if not all(isinstance(value, str) for value in (task_id, status, updated_at)):
        return None

    payload: TaskStatusPayload = {
        "task_id": task_id,
        "status": status,
        "updated_at": updated_at,
    }
    if "result" in raw_payload:
        payload["result"] = raw_payload["result"]

    error = raw_payload.get("error")
    if isinstance(error, str):
        payload["error"] = error
    return payload


def create_redis_client(redis_url: str) -> TaskStore:
    """根据 Redis URL 创建异步 Redis 客户端。"""
    return aioredis.from_url(redis_url, decode_responses=True)


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
        f"{_TASK_KEY_PREFIX}{task_id}",
        dump_task_status_payload(payload),
        ex=_TASK_TTL_SECONDS,
    )


async def read_task_status(
    redis_client: TaskStore,
    task_id: str,
) -> TaskStatusPayload | None:
    """读取任务状态，不存在或格式异常时返回 None。"""
    raw = await redis_client.get(f"{_TASK_KEY_PREFIX}{task_id}")
    return load_task_status_payload(raw)


def spawn_tracked_task(
    pending_tasks: set[asyncio.Task[Any]],
    task_id: str,
    coro: Coroutine[Any, Any, Any],
) -> None:
    """创建后台任务、附加标准名称并登记引用。"""
    task = asyncio.create_task(
        coro,
        name=f"task:{task_id}",
    )
    pending_tasks.add(task)
    task.add_done_callback(pending_tasks.discard)


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


class _TaskManager:
    """基于 Redis 的异步任务管理器。"""

    def __init__(self, redis_client: TaskStore) -> None:
        self._redis = redis_client
        # 保留后台任务引用，避免任务未完成前被垃圾回收后丢失异常信息。
        self._pending_tasks: set[asyncio.Task[Any]] = set()

    async def submit(
        self,
        coro_func: TaskCallable,
        *args: Any,
        **kwargs: Any,
    ) -> str:
        """提交一个后台协程任务并返回 task_id。"""
        task_id = uuid.uuid4().hex[:12]
        await write_task_status(self._redis, task_id, TaskStatus.PENDING)
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
        logger.info(
            "任务已提交 | task_id=%s | func=%s",
            task_id,
            getattr(coro_func, "__name__", coro_func.__class__.__name__),
        )
        return task_id

    async def get_status(self, task_id: str) -> TaskStatusPayload | None:
        """读取任务状态，不存在时返回 None。"""
        return await read_task_status(self._redis, task_id)

    async def close(self) -> None:
        """关闭底层 Redis 连接。

        任务管理器不在这里取消后台任务，避免关闭动作和业务任务生命周期耦合。
        应用层如果需要优雅停机，应先阻止新任务进入，再决定是否等待现有任务收尾。
        """
        await self._redis.close()


async def get_task_manager() -> _TaskManager:
    """获取任务管理器单例。首次调用时创建 Redis 连接。"""
    global _runtime_instance
    manager = _runtime_instance
    if manager is not None:
        return manager

    from app.shared.core.config import settings

    async with _runtime_lock:
        manager = _runtime_instance
        if manager is None:
            manager = _TaskManager(create_redis_client(settings.REDIS_URL))
            _runtime_instance = manager
        return manager


async def close_task_manager() -> None:
    """关闭任务管理器的 Redis 连接。"""
    global _runtime_instance
    manager = _runtime_instance
    _runtime_instance = None
    if manager is None:
        return

    try:
        await manager.close()
    except Exception:
        return


__all__ = [
    "TaskStatus",
    "TaskStatusPayload",
    "build_task_status_payload",
    "close_task_manager",
    "dump_task_status_payload",
    "get_task_manager",
    "load_task_status_payload",
    "read_task_status",
    "run_task_with_status_updates",
    "spawn_tracked_task",
    "write_task_status",
]
