"""LangGraph 查询接口。

这个模块只负责：
- 接收 HTTP 表单参数（身份来自访问令牌）
- 解析/创建会话（thread_id 与 MySQL 会话主键强一致）
- 并发限流
- 调用 chat.application 查询门面，把图执行流转换成 SSE 响应

不负责：
- LangGraph 节点编排
- 记忆读写
- 检索与工具执行细节
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Annotated, Any

from app.api.common import INTERNAL_SERVER_ERROR_DETAIL
from app.api.deps import CurrentUser
from app.chat.application.agent_query_service import stream_agent_query
from app.chat.application.conversation_service import conversation_service
from app.platform.idempotency import REQUEST_ID_HEADER
from app.shared.core.errors import ResourceNotFoundError
from app.shared.core.logger import get_logger
from app.shared.core.rate_limit import ConcurrencyLimitExceededError
from fastapi import APIRouter, Form, HTTPException, Request
from fastapi.responses import StreamingResponse

logger = get_logger(__name__)

router = APIRouter(tags=["langgraph"])

_SSE_MEDIA_TYPE = "text/event-stream"
_CONVERSATION_ID_HEADER = "X-Conversation-ID"
_RESEARCH_PLAN_TAG = "research_plan"
_SSE_DATA_PREFIX = "data: "
_SSE_ERROR_EVENT = "event: error\n"
_STREAM_ERROR_MESSAGE = "生成过程中出现异常，请重试。"
_RATE_LIMIT_DETAIL = "并发对话数已达上限，请等待当前回答完成。"


def format_sse_data(payload: object) -> str:
    """把内容编码为一条 SSE data 帧。"""
    return f"{_SSE_DATA_PREFIX}{json.dumps(payload, ensure_ascii=False)}\n\n"


def format_sse_error(message: str) -> str:
    """把错误编码为命名 error 事件。

    WHY 需要显式错误帧：StreamingResponse 一旦开始，HTTP 状态码已经发出（200），
    流中途的异常无法再变成 4xx/5xx——不发这一帧，客户端只会看到连接
    无声断掉，无从区分"生成完了"和"后端炸了"。
    """
    return f"{_SSE_ERROR_EVENT}{_SSE_DATA_PREFIX}{json.dumps(message, ensure_ascii=False)}\n\n"


def _chunk_tags(metadata: Mapping[str, Any]) -> list[str]:
    """从 astream metadata 中提取字符串 tags。"""
    raw_tags = metadata.get("tags", [])
    if not isinstance(raw_tags, list):
        return []
    return [tag for tag in raw_tags if isinstance(tag, str)]


def _should_emit_sse_chunk(chunk: object, metadata: Mapping[str, Any]) -> bool:
    """判断 chunk 是否应推给前端。

    WHY 过滤：
    - 空 content：无展示价值
    - tool_calls：工具调用中间态，用户只关心最终自然语言
    - research_plan：内部规划标签，避免泄露到 SSE
    """
    content = getattr(chunk, "content", None)
    if not content:
        return False

    additional_kwargs = getattr(chunk, "additional_kwargs", None) or {}
    if not isinstance(additional_kwargs, Mapping):
        additional_kwargs = {}
    if additional_kwargs.get("tool_calls"):
        return False
    if _RESEARCH_PLAN_TAG in _chunk_tags(metadata):
        return False
    return True


def _merge_usage(total: dict[str, int], chunk: object) -> None:
    """累计 LLM token 用量（模型不上报时保持为空）。"""
    usage = getattr(chunk, "usage_metadata", None)
    if not isinstance(usage, Mapping):
        return
    for key in ("input_tokens", "output_tokens", "total_tokens"):
        value = usage.get(key)
        if isinstance(value, int):
            total[key] = total.get(key, 0) + value


async def _get_sse_limiter():
    """取容器上的并发限流器；未初始化时返回 None（放行）。"""
    from app.platform.container import get_container_if_initialized

    container = get_container_if_initialized()
    return getattr(container, "sse_limiter", None) if container else None


@router.post("/langgraph/query")
async def langgraph_query(
    request: Request,
    current_user: CurrentUser,
    query: str = Form(...),
    conversation_id: Annotated[int | None, Form()] = None,
) -> StreamingResponse:
    """LangGraph Agent 查询接口（SSE）。

    会话标识（v3.35.0 起服务端唯一化）：
    - 传 conversation_id：必须存在且属于当前用户，否则 404
    - 不传：服务端自动创建会话
    - thread_id（STM/LTM session 作用域）恒等于 str(conversation_id)，
      消除"uuid 孤儿线程的记忆无法被会话删除清理"的问题

    X-Request-ID 幂等（v3.37）：SSE 流无法重放，重复请求一律 409——
    前端在"发送"动作生成一次 request_id，网络重试复用同一值，
    避免同一提问被重复执行（重复扣 LLM 费用、重复写记忆）。
    """
    from app.api.common import begin_idempotency, complete_idempotency
    from app.platform.idempotency import RequestIdempotencyError

    request_id = request.headers.get(REQUEST_ID_HEADER, "")
    # 幂等认领在限流之前：重复请求直接 409，不占并发槽位
    idem_decision = None
    if request_id.strip():
        try:
            idem_decision = await begin_idempotency(
                user_id=current_user.id,
                request_id=request_id,
                endpoint="langgraph_query",
            )
        except RequestIdempotencyError as exc:
            raise HTTPException(status_code=409, detail=exc.message) from exc

    # 限流在会话解析之前：被 429 拒掉的请求不该每次都留下一个空会话行
    limiter = await _get_sse_limiter()
    if limiter is not None:
        try:
            await limiter.acquire(current_user.tenant_id, current_user.id)
        except ConcurrencyLimitExceededError as exc:
            raise HTTPException(status_code=429, detail=_RATE_LIMIT_DETAIL) from exc

    try:
        resolved_conversation_id = await conversation_service.ensure_conversation(
            current_user.tenant_id,
            current_user.id,
            conversation_id,
        )
    except ResourceNotFoundError as exc:
        if limiter is not None:
            await limiter.release(current_user.tenant_id, current_user.id)
        raise HTTPException(status_code=404, detail=exc.message) from exc
    except Exception:
        if limiter is not None:
            await limiter.release(current_user.tenant_id, current_user.id)
        raise

    thread_id = str(resolved_conversation_id)
    try:
        graph_stream = stream_agent_query(
            query=query,
            user_id=current_user.id,
            tenant_id=current_user.tenant_id,
            thread_id=thread_id,
        )

        async def response_stream():
            usage_total: dict[str, int] = {}
            # 外层 try/except 只保护"流创建之前"；流一旦开始，
            # 异常必须在这里捕获并转成 error 事件，否则客户端只看到静默断流
            try:
                async for chunk, metadata in graph_stream:
                    meta = metadata if isinstance(metadata, Mapping) else {}
                    _merge_usage(usage_total, chunk)
                    if not _should_emit_sse_chunk(chunk, meta):
                        continue
                    content = getattr(chunk, "content", None)
                    yield format_sse_data(content)
            except Exception:
                logger.exception("[api] SSE 流中途异常 | thread_id=%s", thread_id)
                yield format_sse_error(_STREAM_ERROR_MESSAGE)
            finally:
                if limiter is not None:
                    await limiter.release(current_user.tenant_id, current_user.id)
                if idem_decision is not None and idem_decision.is_new:
                    await complete_idempotency(
                        user_id=current_user.id,
                        request_id=request_id,
                        endpoint="langgraph_query",
                    )
                if usage_total:
                    logger.info(
                        "llm_usage | user=%s conversation=%s in=%s out=%s total=%s",
                        current_user.id,
                        thread_id,
                        usage_total.get("input_tokens", 0),
                        usage_total.get("output_tokens", 0),
                        usage_total.get("total_tokens", 0),
                    )

        response = StreamingResponse(response_stream(), media_type=_SSE_MEDIA_TYPE)
        response.headers[_CONVERSATION_ID_HEADER] = thread_id
        return response
    except HTTPException:
        raise
    except Exception as exc:
        if limiter is not None:
            await limiter.release(current_user.tenant_id, current_user.id)
        logger.exception("[api] SSE 流处理异常")
        raise HTTPException(status_code=500, detail=INTERNAL_SERVER_ERROR_DETAIL) from exc
