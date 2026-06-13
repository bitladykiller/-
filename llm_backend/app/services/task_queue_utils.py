"""任务队列共享辅助逻辑。

这里只放 `task_queue.py` 会反复使用、且不依赖 Redis 客户端实例的纯逻辑：
- 任务状态枚举与载荷结构
- task key / task id 构造
- 状态载荷的 JSON 编解码

这样 `TaskManager` 主文件可以更聚焦在“提交 / 运行 / 查询 / 关闭”流程。
"""
from __future__ import annotations

import json
import uuid
from datetime import datetime
from enum import Enum
from typing import Any, TypedDict

TASK_KEY_PREFIX = "task:doc_parse:"
TASK_TTL_SECONDS = 3600 * 24  # 任务状态保留 24 小时


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


def build_task_key(task_id: str) -> str:
    """构造任务在 Redis 中的存储 key。"""
    return f"{TASK_KEY_PREFIX}{task_id}"


def new_task_id() -> str:
    """生成短任务 ID，便于接口返回和人工排查。"""
    return uuid.uuid4().hex[:12]


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


def _coerce_task_status_payload(raw_payload: Any) -> TaskStatusPayload | None:
    """把任意 JSON 值收口成稳定的任务状态结构。"""
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


def load_task_status_payload(raw: str | None) -> TaskStatusPayload | None:
    """从 Redis 原始值解析任务状态。"""
    if raw is None:
        return None
    try:
        return _coerce_task_status_payload(json.loads(raw))
    except (TypeError, ValueError):
        return None


__all__ = [
    "TASK_TTL_SECONDS",
    "TaskStatus",
    "TaskStatusPayload",
    "build_task_key",
    "new_task_id",
    "build_task_status_payload",
    "dump_task_status_payload",
    "load_task_status_payload",
]
