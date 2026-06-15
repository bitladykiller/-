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

import json
import uuid
from collections.abc import AsyncIterator
from typing import Any

from app.chat.infrastructure.graph.builder import graph
from app.chat.infrastructure.graph.state import InputState
from fastapi import APIRouter, Form, HTTPException
from fastapi.responses import StreamingResponse
from langchain_core.messages import HumanMessage

router = APIRouter(tags=["langgraph"])


@router.post("/langgraph/query")
async def langgraph_query(
    query: str = Form(...),
    user_id: int = Form(...),
    conversation_id: str | None = Form(None),
) -> StreamingResponse:
    """LangGraph Agent 查询接口。"""
    try:
        thread_id = conversation_id or str(uuid.uuid4())
        graph_stream: AsyncIterator[tuple[Any, dict[str, Any]]] = graph.astream(
            input=InputState(messages=[HumanMessage(content=query)]),
            stream_mode="messages",
            config={
                "configurable": {
                    "thread_id": thread_id,
                    "user_id": str(user_id),
                }
            },
        )

        async def response_stream():
            async for chunk, metadata in graph_stream:
                content = chunk.content
                additional_kwargs = chunk.additional_kwargs
                tags = metadata.get("tags", [])
                if (
                    not content
                    or additional_kwargs.get("tool_calls")
                    or "research_plan" in tags
                ):
                    continue
                yield f'data: {json.dumps(content, ensure_ascii=False)}\n\n'

        response = StreamingResponse(response_stream(), media_type="text/event-stream")
        response.headers["X-Conversation-ID"] = thread_id
        return response
    except Exception:
        raise HTTPException(status_code=500, detail="Internal server error")
