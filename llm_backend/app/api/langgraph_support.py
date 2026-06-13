"""LangGraph 查询接口 support helper。

职责：
- 定义 LangGraph API 共享的轻量类型契约
- 负责 thread config / input state 构造
- 负责 SSE chunk 过滤、序列化与响应包装

边界：
- 不直接调用 LangGraph graph
- 不负责路由注册
- 不负责节点执行逻辑
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator, Mapping
from typing import Any, TypeAlias, TypedDict

from fastapi.responses import StreamingResponse
from langchain_core.messages import HumanMessage

from app.lg_agent.graph.state import InputState
from app.lg_agent.utils import new_uuid

ChunkMetadata: TypeAlias = Mapping[str, Any]
GraphStreamChunk: TypeAlias = tuple[Any, ChunkMetadata]
GraphStream: TypeAlias = AsyncIterator[GraphStreamChunk]
SSE_MEDIA_TYPE = "text/event-stream"
CONVERSATION_ID_HEADER = "X-Conversation-ID"
RESEARCH_PLAN_TAG = "research_plan"
STREAM_MODE_MESSAGES = "messages"
SSE_DATA_PREFIX = "data: "


class ThreadConfigPayload(TypedDict):
    """LangGraph `configurable` 配置字段。"""

    thread_id: str
    user_id: str


class ThreadConfig(TypedDict):
    """LangGraph 调用配置。"""

    configurable: ThreadConfigPayload


def build_thread_config(thread_id: str, user_id: int) -> ThreadConfig:
    """构造 LangGraph `configurable` 配置。"""
    return {
        "configurable": {
            "thread_id": thread_id,
            "user_id": str(user_id),
        }
    }


def build_input_state(query: str) -> InputState:
    """把原始 query 显式包装为 HumanMessage 列表。"""
    return InputState(messages=[HumanMessage(content=query)])


def resolve_thread_id(conversation_id: str | None) -> str:
    """优先复用现有会话 ID，没有时再创建新线程。"""
    return conversation_id or new_uuid()


def build_streaming_response(
    stream_iterator: AsyncIterator[str],
    thread_id: str,
) -> StreamingResponse:
    """统一构造 SSE 响应，并回写会话 ID。"""
    response = StreamingResponse(
        stream_iterator,
        media_type=SSE_MEDIA_TYPE,
    )
    response.headers[CONVERSATION_ID_HEADER] = thread_id
    return response


def chunk_tags(metadata: ChunkMetadata) -> list[str]:
    """从 metadata 中提取标签列表，非法值统一回退为空。"""
    raw_tags = metadata.get("tags", [])
    if not isinstance(raw_tags, list):
        return []
    return [tag for tag in raw_tags if isinstance(tag, str)]


def coerce_additional_kwargs(chunk: Any) -> Mapping[str, Any]:
    """把 chunk.additional_kwargs 收口为稳定的 Mapping。"""
    additional_kwargs = getattr(chunk, "additional_kwargs", {}) or {}
    if isinstance(additional_kwargs, Mapping):
        return additional_kwargs
    return {}


def has_tool_calls(additional_kwargs: Mapping[str, Any]) -> bool:
    """判断当前 chunk 是否只是在传递 tool call 元数据。"""
    return bool(additional_kwargs.get("tool_calls"))


def has_research_plan_tag(metadata: ChunkMetadata) -> bool:
    """判断当前 chunk 是否属于不应直传前端的研究计划标签。"""
    return RESEARCH_PLAN_TAG in chunk_tags(metadata)


def should_skip_chunk(
    *,
    content: Any,
    additional_kwargs: Mapping[str, Any],
    metadata: ChunkMetadata,
) -> bool:
    """过滤工具调用、研究计划等不应该直接下发给前端的 chunk。"""
    if not content:
        return True
    if has_tool_calls(additional_kwargs):
        return True
    if has_research_plan_tag(metadata):
        return True
    return False


def serialize_stream_chunk(chunk: Any, metadata: ChunkMetadata) -> str | None:
    """把 LangGraph message chunk 转成可下发的 SSE payload。"""
    content = getattr(chunk, "content", None)
    additional_kwargs = coerce_additional_kwargs(chunk)
    if should_skip_chunk(
        content=content,
        additional_kwargs=additional_kwargs,
        metadata=metadata,
    ):
        return None
    return json.dumps(content, ensure_ascii=False)


def build_sse_payload(payload: str) -> str:
    """把 JSON payload 包装成标准 SSE 数据帧。"""
    return f"{SSE_DATA_PREFIX}{payload}\n\n"
