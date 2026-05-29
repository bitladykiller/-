from app.core.logger import get_logger

from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from typing import List, Dict

from app.services.llm_factory import LLMFactory

logger = get_logger(__name__)


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
            ),
            media_type="text/event-stream"
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.error(
            f"chat endpoint 异常 | user_id={request.user_id} "
            f"conversation_id={request.conversation_id} | {e}",
            exc_info=True,
        )
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
        logger.error(
            f"reason endpoint 异常 | user_id={request.user_id} | {e}",
            exc_info=True,
        )
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
        logger.error(
            f"search endpoint 异常 | user_id={request.user_id} "
            f"conversation_id={request.conversation_id} | {e}",
            exc_info=True,
        )
        raise HTTPException(status_code=500, detail="Internal server error")
