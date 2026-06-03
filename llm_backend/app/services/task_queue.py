"""
异步任务队列 — 基于 Redis 的轻量级后台任务管理。

设计思路（WHY）：
- 文档解析（PDF/DOCX）是 CPU 密集型操作，在 HTTP 请求中同步执行会阻塞 worker。
- 项目已有 Redis，无需引入 Celery 等重型框架。
- 使用 asyncio.create_task + Redis 状态存储实现轻量级异步任务。

使用方式：
1. 调用 TaskManager.submit() 提交任务，获得 task_id
2. 前端通过 task_id 轮询 TaskManager.get_status()
3. 任务完成后 status 包含 result 或 error

任务状态生命周期：pending → running → completed / failed
"""
from __future__ import annotations

import asyncio
import json
import logging
import uuid
from datetime import datetime
from enum import Enum
from typing import Any, Callable, Coroutine, Optional

import redis.asyncio as aioredis

logger = logging.getLogger(__name__)


class TaskStatus(str, Enum):
    """任务状态枚举。"""
    PENDING = "pending"        # 已提交，等待执行
    RUNNING = "running"        # 正在执行
    COMPLETED = "completed"    # 执行成功
    FAILED = "failed"          # 执行失败


# Redis key 模板
_TASK_KEY_PREFIX = "task:doc_parse:"
_TASK_TTL = 3600 * 24  # 任务状态保留 24 小时


class TaskManager:
    """基于 Redis 的异步任务管理器。

    职责：
    - 提交异步任务并分配 task_id
    - 在 Redis 中维护任务状态（pending/running/completed/failed）
    - 提供状态查询接口

    使用方式：
        manager = TaskManager(redis_client)
        task_id = await manager.submit(my_coroutine_func, arg1, arg2)
        status = await manager.get_status(task_id)
    """

    def __init__(self, redis_client: aioredis.Redis) -> None:
        self._redis = redis_client

    def _task_key(self, task_id: str) -> str:
        """生成 Redis key。"""
        return f"{_TASK_KEY_PREFIX}{task_id}"

    async def _set_status(
        self,
        task_id: str,
        status: TaskStatus,
        *,
        result: Any = None,
        error: Optional[str] = None,
    ) -> None:
        """更新任务状态到 Redis。"""
        data = {
            "task_id": task_id,
            "status": status.value,
            "updated_at": datetime.now().isoformat(),
        }
        if result is not None:
            data["result"] = result
        if error is not None:
            data["error"] = error

        key = self._task_key(task_id)
        await self._redis.set(key, json.dumps(data, ensure_ascii=False, default=str), ex=_TASK_TTL)

    async def submit(
        self,
        coro_func: Callable[..., Coroutine],
        *args: Any,
        **kwargs: Any,
    ) -> str:
        """提交一个异步任务。

        Args:
            coro_func: 异步函数（协程函数）。
            *args, **kwargs: 传递给 coro_func 的参数。

        Returns:
            task_id: 任务唯一标识，用于后续查询状态。
        """
        task_id = uuid.uuid4().hex[:12]

        # 初始状态：pending
        await self._set_status(task_id, TaskStatus.PENDING)

        # 启动后台任务
        asyncio.create_task(self._run_task(task_id, coro_func, *args, **kwargs))

        logger.info("任务已提交 | task_id=%s | func=%s", task_id, coro_func.__name__)
        return task_id

    async def _run_task(
        self,
        task_id: str,
        coro_func: Callable[..., Coroutine],
        *args: Any,
        **kwargs: Any,
    ) -> None:
        """后台执行任务并更新状态。"""
        await self._set_status(task_id, TaskStatus.RUNNING)
        try:
            result = await coro_func(*args, **kwargs)
            await self._set_status(task_id, TaskStatus.COMPLETED, result=result)
            logger.info("任务完成 | task_id=%s", task_id)
        except Exception as exc:
            await self._set_status(task_id, TaskStatus.FAILED, error=str(exc))
            logger.error("任务失败 | task_id=%s | %s", task_id, exc, exc_info=True)

    async def get_status(self, task_id: str) -> Optional[dict]:
        """查询任务状态。

        Args:
            task_id: 任务唯一标识。

        Returns:
            任务状态字典，包含 task_id/status/updated_at/result/error。
            任务不存在时返回 None。
        """
        key = self._task_key(task_id)
        raw = await self._redis.get(key)
        if raw is None:
            return None
        return json.loads(raw)


# ================================================================== #
# 模块级单例
# ================================================================== #

_task_manager_instance: Optional[TaskManager] = None


async def get_task_manager() -> TaskManager:
    """获取 TaskManager 单例。首次调用时创建 Redis 连接。"""
    global _task_manager_instance
    if _task_manager_instance is not None:
        return _task_manager_instance

    from app.core.config import settings
    redis_client = aioredis.from_url(settings.REDIS_URL, decode_responses=True)
    _task_manager_instance = TaskManager(redis_client)
    return _task_manager_instance
