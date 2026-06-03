"""
LangGraph Agent API。
v3.7: 图片在 API 层解析为文本上下文，注入 query。无 checkpointer。
v3.17: 移除图片上传接口。图片解析功能在实际使用中调用率低且维护成本高，
        删除后简化 API 和维护负担。
"""
from __future__ import annotations

import json
from typing import Optional

from fastapi import APIRouter, HTTPException, Form
from fastapi.responses import StreamingResponse

from app.lg_agent.lg_states import InputState
from app.lg_agent.utils import new_uuid
from app.lg_agent.lg_builder import graph
from app.core.logger import get_logger

logger = get_logger(__name__)

router = APIRouter(tags=["langgraph"])


async def _stream_graph_response(graph_stream):
    """SSE 流式输出 Agent 消息。"""
    async for c, metadata in graph_stream:
        if c.content and not c.additional_kwargs.get("tool_calls") \
                and "research_plan" not in metadata.get("tags", []):
            content_json = json.dumps(c.content, ensure_ascii=False)
            yield f"data: {content_json}\n\n"


@router.post("/langgraph/query")
async def langgraph_query(
    query: str = Form(...),
    user_id: int = Form(...),
    conversation_id: Optional[str] = Form(None),
):
    """LangGraph Agent 查询接口。

    Args:
        query: 用户输入问题。
        user_id: 用户 ID。
        conversation_id: 会话 ID（可选，不传则创建新会话）。
    """
    try:
        thread_id = conversation_id if conversation_id else new_uuid()
        thread_config = {
            "configurable": {
                "thread_id": thread_id,
                "user_id": user_id,
            }
        }

        input_state = InputState(messages=query)
        graph_stream = graph.astream(
            input=input_state, stream_mode="messages", config=thread_config
        )

        response = StreamingResponse(
            _stream_graph_response(graph_stream),
            media_type="text/event-stream",
        )
        response.headers["X-Conversation-ID"] = thread_id
        return response

    except HTTPException:
        raise
    except Exception:
        raise HTTPException(status_code=500, detail="Internal server error")
