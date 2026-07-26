"""进程内后台任务 + Redis 状态上报。

职责：
- 为文档解析等长耗时任务生成 task_id
- 用 `asyncio.create_task` 在**当前进程**执行任务协程
- 用 Redis 保存任务状态，供轮询接口读取
- 启动时把上一代进程遗留的"运行中"任务标记为 interrupted

边界：
- 只负责"提交 / 状态流转 / 结果持久化"，不负责具体文档解析业务
- 关闭时只释放 Redis 连接，不主动改写后台任务生命周期

⚠️ 这**不是**分布式任务队列，别把它当 Celery / ARQ 用：

- 任务协程跑在当前进程的事件循环里，Redis 只存状态，不存待执行的任务
- 进程重启 / 崩溃 → 正在执行的任务直接消失，不会自动续跑，也没有重试
- 多副本部署时任务不会被分担，谁接到 HTTP 请求就在谁那儿执行

模块原名 `task_queue.py`，容易让人以为有队列的投递与持久化保证，故改名。
真需要"重启后继续执行 / 跨副本分担 / 自动重试"，要换成真正的队列
（Redis Stream、ARQ、Celery），而不是在这里打补丁。

作为退而求其次的可观测性兜底：每个状态记录会带上写入它的进程 id
（`worker_id`）。进程启动时调用 `reconcile_orphaned_tasks()`，把其它
worker 留下的 pending/running 记录改成 `interrupted`——这样孤儿任务
至少是"可见的失败"，而不是永远停在 running 让前端一直转圈。
"""

from __future__ import annotations

import asyncio
import json
import uuid
from collections.abc import AsyncIterator, Callable, Coroutine
from datetime import datetime
from enum import Enum
from typing import Any, Protocol, TypeAlias

import redis.asyncio as aioredis
from app.shared.core.config import settings
from app.shared.core.degradation import log_degradation
from app.shared.core.logger import get_logger
from typing_extensions import TypedDict

logger = get_logger(__name__)

_TASK_CFG = settings.app_config.task_queue


class TaskStatus(str, Enum):
    """任务状态枚举。"""

    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    #: 执行该任务的进程已消失（重启/崩溃），任务不会自动续跑
    INTERRUPTED = "interrupted"


#: 尚未终结的状态。进程重启后这些记录若属于旧 worker，即为孤儿。
UNFINISHED_STATUSES = frozenset({TaskStatus.PENDING.value, TaskStatus.RUNNING.value})

INTERRUPTED_ERROR_MESSAGE = "执行该任务的进程已重启，任务未完成且不会自动续跑，请重新提交。"

#: 当前进程标识。用于区分"我正在跑的任务"和"上一代进程遗留的任务"。
WORKER_ID = uuid.uuid4().hex[:12]


class TaskStatusPayload(TypedDict, total=False):
    """Redis 中存储的任务状态结构。"""

    task_id: str
    status: str
    updated_at: str
    worker_id: str
    #: 执行通道："stream" 表示经 Redis Streams 投递（崩溃后会被自动认领重跑）
    origin: str
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

    def scan_iter(self, match: str) -> AsyncIterator[str]: ...

    async def close(self) -> Any: ...


def build_task_status_payload(
    task_id: str,
    status: TaskStatus,
    *,
    result: Any = None,
    error: str | None = None,
    worker_id: str = WORKER_ID,
    origin: str | None = None,
) -> TaskStatusPayload:
    """构造统一的任务状态负载。"""
    payload: TaskStatusPayload = {
        "task_id": task_id,
        "status": status.value,
        "updated_at": datetime.now().isoformat(),
        "worker_id": worker_id,
    }
    if origin is not None:
        payload["origin"] = origin
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
    if not (
        isinstance(task_id, str)
        and isinstance(status, str)
        and isinstance(updated_at, str)
    ):
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

    # worker_id 是后加的字段：历史记录没有，按"归属未知的旧进程"处理
    worker_id = raw_payload.get("worker_id")
    if isinstance(worker_id, str):
        payload["worker_id"] = worker_id
    origin = raw_payload.get("origin")
    if isinstance(origin, str):
        payload["origin"] = origin
    return payload


def is_orphaned_task(payload: TaskStatusPayload, *, current_worker_id: str) -> bool:
    """判断这条状态是否属于"上一代进程遗留的未完成任务"。

    满足两个条件才算孤儿：状态尚未终结，且写入它的不是当前进程。
    缺少 `worker_id` 的历史记录一律视为旧进程留下的。

    经 Redis Streams 投递的任务（origin="stream"）**不算孤儿**：
    消息还在 stream 的 PEL 里，会被 XAUTOCLAIM 认领并自动重跑，
    把它标成 interrupted 反而是误报。
    """
    if payload.get("status") not in UNFINISHED_STATUSES:
        return False
    if payload.get("origin") == "stream":
        return False
    return payload.get("worker_id") != current_worker_id


def create_redis_client(redis_url: str) -> TaskStore:
    """根据 Redis URL 创建异步 Redis 客户端。"""
    client = aioredis.from_url(redis_url, decode_responses=True)
    return client  # type: ignore[return-value]  # pyright: ignore[reportReturnType]


async def write_task_status(
    redis_client: TaskStore,
    task_id: str,
    status: TaskStatus,
    *,
    result: Any = None,
    error: str | None = None,
    origin: str | None = None,
) -> None:
    """构造统一状态 payload 并写入 Redis。"""
    payload = build_task_status_payload(
        task_id,
        status,
        result=result,
        error=error,
        origin=origin,
    )
    await redis_client.set(
        f"{_TASK_CFG.task_key_prefix}{task_id}",
        dump_task_status_payload(payload),
        ex=_TASK_CFG.task_ttl_seconds,
    )


async def read_task_status(
    redis_client: TaskStore,
    task_id: str,
) -> TaskStatusPayload | None:
    """读取任务状态，不存在或格式异常时返回 None。"""
    raw = await redis_client.get(f"{_TASK_CFG.task_key_prefix}{task_id}")
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
    origin: str | None = None,
    **kwargs: Any,
) -> None:
    """执行后台任务，并统一维护 Redis 状态流转与日志。"""
    await write_task_status(redis_client, task_id, TaskStatus.RUNNING, origin=origin)
    try:
        result = await coro_func(*args, **kwargs)
        await write_task_status(
            redis_client,
            task_id,
            TaskStatus.COMPLETED,
            result=result,
            origin=origin,
        )
        logger.info("任务完成 | task_id=%s", task_id)
    except Exception as exc:
        await write_task_status(
            redis_client,
            task_id,
            TaskStatus.FAILED,
            error=str(exc),
            origin=origin,
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

    async def reconcile_orphaned_tasks(self) -> int:
        """把上一代进程遗留的未完成任务标记为 interrupted。

        任务协程只活在进程内存里，进程没了任务就没了，但 Redis 里的状态还停在
        `running`——前端会一直转圈等一个永远不会来的结果。启动时扫一遍，
        把不属于当前 worker 的 pending/running 记录改成 `interrupted`，
        让"任务丢了"变成一个明确、可展示的终态。

        Returns:
            被标记的任务数；扫描失败返回 0（不阻断启动）。
        """
        pattern = f"{_TASK_CFG.task_key_prefix}*"
        reconciled = 0
        try:
            async for key in self._redis.scan_iter(match=pattern):
                payload = load_task_status_payload(await self._redis.get(key))
                if payload is None:
                    continue
                if not is_orphaned_task(payload, current_worker_id=WORKER_ID):
                    continue
                await write_task_status(
                    self._redis,
                    payload["task_id"],
                    TaskStatus.INTERRUPTED,
                    error=INTERRUPTED_ERROR_MESSAGE,
                )
                reconciled += 1
        except Exception as exc:
            log_degradation(logger, "background_tasks.reconcile_orphaned_tasks", exc)
            return 0

        if reconciled:
            logger.warning(
                "已将 %s 个上一代进程遗留的任务标记为 interrupted | worker=%s",
                reconciled,
                WORKER_ID,
            )
        return reconciled

    async def close(self) -> None:
        """关闭底层 Redis 连接。

        任务管理器不在这里取消后台任务，避免关闭动作和业务任务生命周期耦合。
        应用层如果需要优雅停机，应先阻止新任务进入，再决定是否等待现有任务收尾。
        """
        await self._redis.close()


async def get_task_manager() -> _TaskManager:
    """获取任务管理器单例（通过 AppContainer 统一管理）。"""
    from app.platform.container import get_container

    container = await get_container()
    manager = container.task_manager
    if manager is None:
        from app.shared.core.config import settings

        manager = _TaskManager(create_redis_client(settings.REDIS_URL))
        container.task_manager = manager
    return manager


# NOTE: 关闭动作只有 `AppContainer.close()` 一个入口。
# 这里曾有一个 `close_task_manager()` 与之重复实现，但从没有被生产代码调用过，
# 两份同义逻辑并存只会让"到底谁负责关连接"变得含糊，已删除。


__all__ = [
    "INTERRUPTED_ERROR_MESSAGE",
    "UNFINISHED_STATUSES",
    "WORKER_ID",
    "TaskStatus",
    "TaskStatusPayload",
    "build_task_status_payload",
    "dump_task_status_payload",
    "get_task_manager",
    "is_orphaned_task",
    "load_task_status_payload",
    "read_task_status",
    "run_task_with_status_updates",
    "spawn_tracked_task",
    "write_task_status",
]
