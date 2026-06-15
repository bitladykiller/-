"""API 路由层共享 helper。"""

from collections.abc import Awaitable
from typing import Any

from app.shared.core.logger import format_log_context
from fastapi import HTTPException


async def run_route_action(
    action_name: str,
    operation: Awaitable[Any],
    *,
    logger,
    **context: object,
) -> Any:
    """统一执行路由动作，并把未知异常转换成 HTTP 500。"""
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
        raise HTTPException(status_code=500, detail="Internal server error")
