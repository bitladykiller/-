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

from fastapi import APIRouter, Form, HTTPException
from fastapi.responses import StreamingResponse

from app.api.common import INTERNAL_SERVER_ERROR_DETAIL
from app.chat.application.agent_query_service import stream_agent_query
from app.shared.core.logger import get_logger

logger = get_logger(__name__)

router = APIRouter(tags=["langgraph"])

_SSE_MEDIA_TYPE = "text/event-stream"
_CONVERSATION_ID_HEADER = "X-Conversation-ID"
_RESEARCH_PLAN_TAG = "research_plan"
_SSE_DATA_PREFIX = "data: "


@router.post("/langgraph/query")
async def langgraph_query(
    query: str = Form(...),
    user_id: int = Form(...),
    conversation_id: str | None = Form(None),
) -> StreamingResponse:
    """LangGraph Agent 查询接口。"""
    try:
        thread_id = conversation_id or str(uuid.uuid4())
        graph_stream = stream_agent_query(
            query=query,
            user_id=user_id,
            thread_id=thread_id,
        )

        async def response_stream():
            async for chunk, metadata in graph_stream:
                content = getattr(chunk, "content", None)
                additional_kwargs = getattr(chunk, "additional_kwargs", {}) or {}
                if not isinstance(additional_kwargs, Mapping):
                    additional_kwargs = {}

                raw_tags = metadata.get("tags", [])
                tags = (
                    [tag for tag in raw_tags if isinstance(tag, str)]
                    if isinstance(raw_tags, list)
                    else []
                )
                if (
                    not content
                    or additional_kwargs.get("tool_calls")
                    or _RESEARCH_PLAN_TAG in tags
                ):
                    continue
                yield f"{_SSE_DATA_PREFIX}{json.dumps(content, ensure_ascii=False)}\n\n"

        response = StreamingResponse(response_stream(), media_type=_SSE_MEDIA_TYPE)
        response.headers[_CONVERSATION_ID_HEADER] = thread_id
        return response
    except Exception:
        logger.exception("[api] SSE 流处理异常")
        raise HTTPException(status_code=500, detail=INTERNAL_SERVER_ERROR_DETAIL)
