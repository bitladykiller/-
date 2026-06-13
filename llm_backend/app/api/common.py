"""API 薄层共享约定。

这个模块只放路由层都能复用的轻量能力：
- 统一简单消息响应结构
- 统一 500 错误文案
- 统一“记录上下文后转成 HTTP 500”的包装逻辑

它不负责业务规则，也不应该演变成新的 Service 层。
"""

from __future__ import annotations

from collections.abc import Awaitable
from typing import Protocol, TypeVar, TypedDict

from fastapi import HTTPException

from app.core.logger import format_log_context

INTERNAL_SERVER_ERROR_DETAIL = "Internal server error"
ApiResult = TypeVar("ApiResult")


class MessageResponse(TypedDict):
    """简单消息响应。"""

    message: str


class ErrorLogger(Protocol):
    """满足 API 包装器所需最小能力的日志接口。"""

    def error(self, msg: str, *args: object, **kwargs: object) -> object: ...


def build_message_response(message: str) -> MessageResponse:
    """统一构造简单消息响应。"""
    return {"message": message}


def _build_action_error_message(
    action_name: str,
    exc: Exception,
    **context: object,
) -> str:
    """按“动作 + 上下文 + 异常”格式拼装错误日志。"""
    log_context = format_log_context(**context)
    if log_context:
        return f"{action_name} 异常 | {log_context} | {exc}"
    return f"{action_name} 异常 | {exc}"


async def run_api_action(
    action_name: str,
    operation: Awaitable[ApiResult],
    *,
    logger: ErrorLogger,
    **context: object,
) -> ApiResult:
    """统一执行 API 层异步动作，并把未知异常转换成 HTTP 500。"""
    try:
        return await operation
    except HTTPException:
        raise
    except Exception as exc:
        logger.error(
            _build_action_error_message(action_name, exc, **context),
            exc_info=True,
        )
        raise HTTPException(status_code=500, detail=INTERNAL_SERVER_ERROR_DETAIL)
