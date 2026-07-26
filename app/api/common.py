"""API 薄层共享约定。

这个模块只放路由层都能复用的轻量能力：
- 统一简单消息响应结构
- 统一 500 错误文案
- 统一“记录上下文后转成 HTTP 500”的包装逻辑

它不负责业务规则，也不应该演变成新的 Service 层。
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable
from typing import TypeVar

from app.shared.core.errors import ResourceNotFoundError
from app.shared.core.logger import format_log_context
from fastapi import HTTPException
from typing_extensions import TypedDict

INTERNAL_SERVER_ERROR_DETAIL = "Internal server error"
ApiResult = TypeVar("ApiResult")


class MessageResponse(TypedDict):
    """简单消息响应。"""

    message: str


def build_message_response(message: str) -> MessageResponse:
    """统一构造简单消息响应。"""
    return {"message": message}


async def run_api_action(
    action_name: str,
    operation: Awaitable[ApiResult],
    *,
    logger: logging.Logger,
    **context: object,
) -> ApiResult:
    """统一执行 API 层异步动作，并做异常 → HTTP 状态码映射。

    - `HTTPException`：原样透传（handler 自己定的语义）
    - `ResourceNotFoundError`：404 —— 业务层表达"资源不存在/不属于你"，
      不再被误翻成 500
    - 其余异常：记录上下文与堆栈后统一 500
    """
    try:
        return await operation
    except HTTPException:
        raise
    except ResourceNotFoundError as exc:
        raise HTTPException(status_code=404, detail=exc.message) from exc
    except Exception as exc:
        log_context = format_log_context(**context)
        logger.error(
            f"{action_name} 异常 | {log_context} | {exc}"
            if log_context
            else f"{action_name} 异常 | {exc}",
            exc_info=True,
        )
        raise HTTPException(status_code=500, detail=INTERNAL_SERVER_ERROR_DETAIL) from exc
