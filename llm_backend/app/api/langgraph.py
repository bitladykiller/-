import json
import os
import re
from datetime import datetime
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, HTTPException, UploadFile, File, Form
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from langgraph.types import Command

from app.lg_agent.lg_states import InputState
from app.lg_agent.utils import new_uuid
from app.lg_agent.lg_builder import graph


router = APIRouter(tags=["langgraph"])


class LangGraphResumeRequest(BaseModel):
    query: str
    user_id: int
    conversation_id: str


def _sanitize_path_component(name: str) -> str:
    return re.sub(r'[^a-zA-Z0-9_-]', '_', name or "unknown")


async def _stream_graph_response(graph_stream, thread_config):
    async for c, metadata in graph_stream:
        if c.content and "research_plan" not in metadata.get("tags", []) and not c.additional_kwargs.get("tool_calls"):
            content_json = json.dumps(c.content, ensure_ascii=False)
            yield f"data: {content_json}\n\n"
        elif c.additional_kwargs.get("tool_calls"):
            tool_data = c.additional_kwargs.get("tool_calls")[0]["function"].get("arguments")

    state = graph.get_state(thread_config)
    if len(state) > 0 and len(state[-1]) > 0:
        if len(state[-1][0].interrupts) > 0:
            interrupt_json = json.dumps({"interruption": True, "conversation_id": thread_config["configurable"]["thread_id"]})
            yield f"data: {interrupt_json}\n\n"


@router.post("/langgraph/query")
async def langgraph_query(
    query: str = Form(...),
    user_id: int = Form(...),
    conversation_id: Optional[str] = Form(None),
    image: Optional[UploadFile] = File(None)
):
    try:

        image_path = None
        if image:
            image_dir = Path("uploads/images")
            if conversation_id:
                image_dir = image_dir / _sanitize_path_component(conversation_id)
            image_dir.mkdir(parents=True, exist_ok=True)

            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            original_name, ext = os.path.splitext(image.filename)
            new_filename = f"{original_name}_{timestamp}{ext}"
            image_path = image_dir / new_filename

            content = await image.read()
            with open(image_path, "wb") as f:
                f.write(content)


        thread_id = conversation_id if conversation_id else new_uuid()
        thread_config = {
            "configurable": {
                "thread_id": thread_id,
                "user_id": user_id,
                "image_path": str(image_path) if image_path else None
            }
        }

        state_history = None
        try:
            if thread_id:
                state_history = graph.get_state(thread_config)
                if state_history:
                    pass
        except Exception as e:
            pass
        if state_history and len(state_history) > 0 and len(state_history[-1]) > 0:
            graph_stream = graph.astream(Command(resume=query), stream_mode="messages", config=thread_config)
        else:
            input_state = InputState(messages=query)
            graph_stream = graph.astream(input=input_state, stream_mode="messages", config=thread_config)

        response = StreamingResponse(
            _stream_graph_response(graph_stream, thread_config),
            media_type="text/event-stream"
        )
        response.headers["X-Conversation-ID"] = thread_id
        return response

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail="Internal server error")


@router.post("/langgraph/resume")
async def langgraph_resume(request: LangGraphResumeRequest):
    try:

        thread_config = {"configurable": {"thread_id": request.conversation_id}}

        graph_stream = graph.astream(Command(resume=request.query), stream_mode="messages", config=thread_config)

        return StreamingResponse(
            _stream_graph_response(graph_stream, thread_config),
            media_type="text/event-stream"
        )

    except Exception as e:
        raise HTTPException(status_code=500, detail="Internal server error")
