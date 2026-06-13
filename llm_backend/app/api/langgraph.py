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

from collections.abc import AsyncIterator

from fastapi import APIRouter, HTTPException, Form
from fastapi.responses import StreamingResponse

from app.api.common import INTERNAL_SERVER_ERROR_DETAIL
from app.api.langgraph_support import (
    GraphStream,
    STREAM_MODE_MESSAGES,
    build_input_state,
    build_sse_payload,
    build_streaming_response,
    build_thread_config,
    resolve_thread_id,
    serialize_stream_chunk,
)
from app.lg_agent.graph.builder import graph

router = APIRouter(tags=["langgraph"])


def _build_graph_stream(
    *,
    query: str,
    thread_id: str,
    user_id: int,
) -> GraphStream:
    """统一组装 LangGraph 流式执行输入。"""
    return graph.astream(
        input=build_input_state(query),
        stream_mode=STREAM_MODE_MESSAGES,
        config=build_thread_config(thread_id, user_id),
    )


async def _stream_graph_response(
    graph_stream: GraphStream,
) -> AsyncIterator[str]:
    """SSE 流式输出 Agent 消息。"""
    async for chunk, metadata in graph_stream:
        payload = serialize_stream_chunk(chunk, metadata)
        if payload is not None:
            yield build_sse_payload(payload)


@router.post("/langgraph/query")
async def langgraph_query(
    query: str = Form(...),
    user_id: int = Form(...),
    conversation_id: str | None = Form(None),
) -> StreamingResponse:
    """LangGraph Agent 查询接口。"""
    try:
        thread_id = resolve_thread_id(conversation_id)
        graph_stream = _build_graph_stream(
            query=query,
            thread_id=thread_id,
            user_id=user_id,
        )
        return build_streaming_response(_stream_graph_response(graph_stream), thread_id)
    except Exception:
        raise HTTPException(status_code=500, detail=INTERNAL_SERVER_ERROR_DETAIL)
