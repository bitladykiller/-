"""LangGraph 查询接口。

这个模块只负责：
- 接收 HTTP 表单参数
- 把用户输入包装成 LangGraph 期望的输入状态
- 把图执行流转换成 SSE 响应

不负责：
- LangGraph 节点编排
- 记忆读写
- 检索与工具执行细节
"""
from __future__ import annotations

import json
import uuid
from collections.abc import AsyncIterator, Mapping
from typing import Any, TypeAlias

from fastapi import APIRouter, HTTPException, Form
from fastapi.responses import StreamingResponse
from langchain_core.messages import HumanMessage

from app.api.common import INTERNAL_SERVER_ERROR_DETAIL
from app.chat.infrastructure.graph.builder import graph
from app.chat.infrastructure.graph.state import InputState

router = APIRouter(tags=["langgraph"])

_ChunkMetadata: TypeAlias = Mapping[str, Any]
_GraphStreamChunk: TypeAlias = tuple[Any, _ChunkMetadata]
GraphStream: TypeAlias = AsyncIterator[_GraphStreamChunk]
_SSE_MEDIA_TYPE = "text/event-stream"
_CONVERSATION_ID_HEADER = "X-Conversation-ID"
_RESEARCH_PLAN_TAG = "research_plan"
STREAM_MODE_MESSAGES = "messages"
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
        graph_stream: GraphStream = graph.astream(
            input=InputState(messages=[HumanMessage(content=query)]),
            stream_mode=STREAM_MODE_MESSAGES,
            config={
                "configurable": {
                    "thread_id": thread_id,
                    "user_id": str(user_id),
                }
            },
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
