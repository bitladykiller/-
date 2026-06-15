"""API 薄层共享约定。

这个模块只放路由层都能复用的轻量能力：
- 统一简单消息响应结构
- 统一 500 错误文案
- 统一“记录上下文后转成 HTTP 500”的包装逻辑

它不负责业务规则，也不应该演变成新的 Service 层。
"""

from __future__ import annotations

from collections.abc import Awaitable
from typing import Any, TypeVar

from fastapi import HTTPException

from app.shared.core.logger import format_log_context

INTERNAL_SERVER_ERROR_DETAIL = "Internal server error"
ApiResult = TypeVar("ApiResult")


async def run_api_action(
    action_name: str,
    operation: Awaitable[ApiResult],
    *,
    logger: Any,
    **context: object,
) -> ApiResult:
    """统一执行 API 层异步动作，并把未知异常转换成 HTTP 500。"""
    try:
        return await operation
    except HTTPException:
        raise
    except Exception as exc:
        log_context = format_log_context(**context)
        logger.error(
            f"{action_name} 异常 | {log_context} | {exc}"
            if log_context
            else f"{action_name} 异常 | {exc}",
            exc_info=True,
        )
        raise HTTPException(status_code=500, detail=INTERNAL_SERVER_ERROR_DETAIL)
