"""LangGraph 查询接口。

这个模块只负责：
- 接收 HTTP 表单参数
- 调用 chat.application 查询门面
- 把图执行流转换成 SSE 响应

不负责：
- LangGraph 节点编排
- 记忆读写
- 检索与工具执行细节
"""
from __future__ import annotations

import json
import uuid
from collections.abc import Mapping
from typing import Any

from app.api.common import INTERNAL_SERVER_ERROR_DETAIL
from app.chat.application.agent_query_service import stream_agent_query
from app.shared.core.logger import get_logger
from fastapi import APIRouter, Form, HTTPException
from fastapi.responses import StreamingResponse

logger = get_logger(__name__)

router = APIRouter(tags=["langgraph"])

_SSE_MEDIA_TYPE = "text/event-stream"
_CONVERSATION_ID_HEADER = "X-Conversation-ID"
_RESEARCH_PLAN_TAG = "research_plan"
_SSE_DATA_PREFIX = "data: "


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


@router.post("/langgraph/query")
async def langgraph_query(
    query: str = Form(...),
    user_id: int = Form(...),
    conversation_id: str | None = Form(None),
) -> StreamingResponse:
    """LangGraph Agent 查询接口（SSE）。

    - conversation_id 缺省时生成新 thread_id（与 STM session 对齐）
    - 响应头 X-Conversation-ID 在 StreamingResponse 创建时即写入，便于客户端续聊
    """
    try:
        # 续聊必须回传此 id；新建会话则用 uuid，与 MySQL conversation 主键可不同
        thread_id = conversation_id or str(uuid.uuid4())
        graph_stream = stream_agent_query(
            query=query,
            user_id=user_id,
            thread_id=thread_id,
        )

        async def response_stream():
            async for chunk, metadata in graph_stream:
                meta = metadata if isinstance(metadata, Mapping) else {}
                if not _should_emit_sse_chunk(chunk, meta):
                    continue
                content = getattr(chunk, "content", None)
                yield f"{_SSE_DATA_PREFIX}{json.dumps(content, ensure_ascii=False)}\n\n"

        response = StreamingResponse(response_stream(), media_type=_SSE_MEDIA_TYPE)
        response.headers[_CONVERSATION_ID_HEADER] = thread_id
        return response
    except Exception as exc:
        logger.exception("[api] SSE 流处理异常")
        raise HTTPException(status_code=500, detail=INTERNAL_SERVER_ERROR_DETAIL) from exc
