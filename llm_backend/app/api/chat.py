from app.core.logger import get_logger
from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from typing import List, Dict
import json

from app.lg_agent.lg_states import InputState
from app.lg_agent.lg_builder import graph

logger = get_logger(__name__)

router = APIRouter(tags=["chat"])


class ChatMessage(BaseModel):
    messages: List[Dict[str, str]]
    user_id: int
    conversation_id: int


async def _stream_graph(graph_stream):
    async for c, metadata in graph_stream:
        if c.content and not c.additional_kwargs.get("tool_calls") \
                and "research_plan" not in metadata.get("tags", []):
            yield f"data: {json.dumps(c.content, ensure_ascii=False)}\n\n"


@router.post("/chat")
async def chat_endpoint(request: ChatMessage):
    """统一走 LangGraph 图 — Router + 记忆注入 + after_response。"""
    try:
        last_user = next(
            (m["content"] for m in reversed(request.messages) if m.get("role") == "user"),
            request.messages[-1]["content"]
        )
        thread_config = {
            "configurable": {
                "thread_id": str(request.conversation_id),
                "user_id": str(request.user_id),
            }
        }
        graph_stream = graph.astream(
            input=InputState(messages=last_user),
            stream_mode="messages",
            config=thread_config,
        )
        return StreamingResponse(
            _stream_graph(graph_stream),
            media_type="text/event-stream",
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"chat endpoint 异常 | user_id={request.user_id} | {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Internal server error")
