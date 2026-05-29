from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from typing import List, Dict

from app.services.llm_factory import LLMFactory
from app.services.conversation_service import ConversationService


router = APIRouter(tags=["chat"])


class ChatMessage(BaseModel):
    messages: List[Dict[str, str]]
    user_id: int
    conversation_id: int


class ReasonRequest(BaseModel):
    messages: List[Dict[str, str]]
    user_id: int


@router.post("/chat")
async def chat_endpoint(request: ChatMessage):
    try:

        chat_service = LLMFactory.create_chat_service()

        return StreamingResponse(
            chat_service.generate_stream(
                messages=request.messages,
                user_id=request.user_id,
                conversation_id=request.conversation_id,
                on_complete=ConversationService.save_message
            ),
            media_type="text/event-stream"
        )
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail="Internal server error")


@router.post("/reason")
async def reason_endpoint(request: ReasonRequest):
    try:
        reasoner = LLMFactory.create_reasoner_service()

        return StreamingResponse(
            reasoner.generate_stream(request.messages),
            media_type="text/event-stream"
        )

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail="Internal server error")


@router.post("/search")
async def search_endpoint(request: ChatMessage):
    try:

        search_service = LLMFactory.create_search_service()
        return StreamingResponse(
            search_service.generate_stream(
                query=request.messages[0]["content"],
                user_id=request.user_id,
                conversation_id=request.conversation_id,
            ),
            media_type="text/event-stream"
        )

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail="Internal server error")
