"""API 薄层共享约定。

这个模块只放路由层都能复用的轻量能力：
- 统一简单消息响应结构
- 统一 500 错误文案
- 统一“记录上下文后转成 HTTP 500”的包装逻辑
- 请求层幂等（X-Request-ID）的统一包装

它不负责业务规则，也不应该演变成新的 Service 层。
"""

from __future__ import annotations

import json
import logging
from collections.abc import Awaitable
from typing import Any, TypeVar

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


async def run_idempotent_action(
    action_name: str,
    operation: Awaitable[ApiResult],
    *,
    logger: logging.Logger,
    user_id: int,
    request_id: str,
    endpoint: str,
    **context: object,
) -> ApiResult:
    """执行带请求层幂等的 API 动作（X-Request-ID 防客户端重复提交）。

    行为：
    - 无 request_id：放行（兼容老客户端），等同 run_api_action
    - 首次请求：执行并记录 completed + 响应快照；重复请求返回缓存响应
    - 重复且仍在处理/已失败：409（客户端应换新 request_id）
    - 业务失败：记录 failed（重复请求 409，不重放失败结果）

    ⚠️ 响应必须可 JSON 序列化（TypedDict/dict）；SSE 等不可重放响应
    请走 `begin_idempotency` + 流结束时 `complete` 的手动路径。
    """
    from app.platform.idempotency import (
        IdempotencyDecision,
        RequestIdempotencyError,
        idempotency_service,
    )

    try:
        decision: IdempotencyDecision = await idempotency_service.begin(
            user_id=user_id,
            request_id=request_id,
            endpoint=endpoint,
        )
    except RequestIdempotencyError as exc:
        raise HTTPException(status_code=409, detail=exc.message) from exc

    if decision.is_new is False:
        # 重复请求：completed 且有快照 → 返回缓存；无快照（超限）→ 409
        if decision.cached_status and decision.cached_body:
            try:
                return json.loads(decision.cached_body)
            except (TypeError, ValueError):
                pass
        raise HTTPException(
            status_code=409,
            detail="该请求已处理过，请使用新的 request_id 发起新操作",
        )
    if decision.is_new is None:
        return await run_api_action(action_name, operation, logger=logger, **context)

    try:
        result = await run_api_action(action_name, operation, logger=logger, **context)
    except HTTPException as exc:
        await idempotency_service.mark_failed(
            user_id=user_id,
            request_id=request_id,
            error=str(exc.detail),
        )
        raise
    except Exception as exc:
        await idempotency_service.mark_failed(
            user_id=user_id,
            request_id=request_id,
            error=str(exc),
        )
        raise

    try:
        body = json.dumps(result, ensure_ascii=False, default=str)
    except (TypeError, ValueError):
        body = ""
    await idempotency_service.complete(
        user_id=user_id,
        request_id=request_id,
        endpoint=endpoint,
        response_status=200,
        response_body=body,
    )
    return result


async def begin_idempotency(
    *,
    user_id: int,
    request_id: str,
    endpoint: str,
) -> Any | None:
    """手动路径：SSE 等流式响应在流开始前认领幂等。

    Returns:
        IdempotencyDecision；无 request_id 时返回 None（放行）。
        重复请求抛 RequestIdempotencyError（调用方映射 409）。
    """
    from app.platform.idempotency import idempotency_service

    if not (request_id or "").strip():
        return None
    return await idempotency_service.begin(
        user_id=user_id,
        request_id=request_id,
        endpoint=endpoint,
    )


def complete_idempotency(
    *,
    user_id: int,
    request_id: str,
    endpoint: str,
    response_status: int = 200,
) -> Awaitable[None]:
    """手动路径：流结束后标记完成。"""
    from app.platform.idempotency import idempotency_service

    return idempotency_service.complete(
        user_id=user_id,
        request_id=request_id,
        endpoint=endpoint,
        response_status=response_status,
    )
