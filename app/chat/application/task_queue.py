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

import asyncio
import json
import uuid
from collections.abc import Callable, Coroutine
from datetime import datetime
from typing import Any

import redis.asyncio as aioredis
from app.shared.core.logger import get_logger

logger = get_logger(__name__)
_runtime_instance: "_TaskManager | None" = None
_runtime_lock: asyncio.Lock = asyncio.Lock()


class _TaskManager:
    """基于 Redis 的异步任务管理器。"""

    def __init__(self, redis_client: Any) -> None:
        self._redis = redis_client
        # 保留后台任务引用，避免任务未完成前被垃圾回收后丢失异常信息。
        self._pending_tasks: set[asyncio.Task[Any]] = set()

    async def _write_task_status(
        self,
        task_id: str,
        status: str,
        *,
        result: Any = None,
        error: str | None = None,
    ) -> None:
        """构造统一状态 payload 并写入 Redis。"""
        payload: dict[str, Any] = {
            "task_id": task_id,
            "status": status,
            "updated_at": datetime.now().isoformat(),
        }
        if result is not None:
            payload["result"] = result
        if error is not None:
            payload["error"] = error

        await self._redis.set(
            f"task:doc_parse:{task_id}",
            json.dumps(payload, ensure_ascii=False, default=str),
            ex=3600 * 24,
        )

    async def submit(
        self,
        coro_func: Callable[..., Coroutine[Any, Any, Any]],
        *args: Any,
    ) -> str:
        """提交一个后台协程任务并返回 task_id。"""
        task_id = uuid.uuid4().hex[:12]
        await self._write_task_status(task_id, "pending")

        async def background_runner() -> None:
            await self._write_task_status(task_id, "running")
            try:
                result = await coro_func(*args)
                await self._write_task_status(
                    task_id,
                    "completed",
                    result=result,
                )
                logger.info("任务完成 | task_id=%s", task_id)
            except Exception as exc:
                await self._write_task_status(
                    task_id,
                    "failed",
                    error=str(exc),
                )
                logger.error("任务失败 | task_id=%s | %s", task_id, exc, exc_info=True)

        task = asyncio.create_task(
            background_runner(),
            name=f"task:{task_id}",
        )
        self._pending_tasks.add(task)
        task.add_done_callback(self._pending_tasks.discard)
        logger.info(
            "任务已提交 | task_id=%s | func=%s",
            task_id,
            getattr(coro_func, "__name__", coro_func.__class__.__name__),
        )
        return task_id

    async def get_status(self, task_id: str) -> dict[str, Any] | None:
        """读取任务状态，不存在时返回 None。"""
        raw = await self._redis.get(f"task:doc_parse:{task_id}")
        if raw is None:
            return None
        try:
            raw_payload = json.loads(raw)
        except (TypeError, ValueError):
            return None

        if not isinstance(raw_payload, dict):
            return None

        current_task_id = raw_payload.get("task_id")
        status = raw_payload.get("status")
        updated_at = raw_payload.get("updated_at")
        if not all(
            isinstance(value, str)
            for value in (current_task_id, status, updated_at)
        ):
            return None

        payload: dict[str, Any] = {
            "task_id": current_task_id,
            "status": status,
            "updated_at": updated_at,
        }
        if "result" in raw_payload:
            payload["result"] = raw_payload["result"]

        error = raw_payload.get("error")
        if isinstance(error, str):
            payload["error"] = error
        return payload

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
            manager = _TaskManager(
                aioredis.from_url(settings.REDIS_URL, decode_responses=True)
            )
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
