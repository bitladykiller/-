from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

from app.services.conversation_service import ConversationService


router = APIRouter(tags=["conversations"])


class CreateConversationRequest(BaseModel):
    user_id: int


class UpdateConversationNameRequest(BaseModel):
    name: str


@router.post("/conversations")
async def create_conversation(request: CreateConversationRequest):
    try:
        conversation_id = await ConversationService.create_conversation(request.user_id)
        return {"conversation_id": conversation_id}
    except Exception as e:
        raise HTTPException(status_code=500, detail="Internal server error")


@router.get("/conversations/user/{user_id}")
async def get_user_conversations(user_id: int):
    try:
        conversations = await ConversationService.get_user_conversations(user_id)
        return conversations
    except Exception as e:
        raise HTTPException(status_code=500, detail="Internal server error")


@router.get("/conversations/{conversation_id}/messages")
async def get_conversation_messages(
    conversation_id: int,
    user_id: int = Query(...)
):
    try:
        messages = await ConversationService.get_conversation_messages(conversation_id, user_id)
        return messages
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail="Internal server error")


@router.delete("/conversations/{conversation_id}")
async def delete_conversation(conversation_id: int):
    try:
        conversation_service = ConversationService()
        await conversation_service.delete_conversation(conversation_id)
        return {"message": "会话已删除"}
    except Exception as e:
        raise HTTPException(status_code=500, detail="Internal server error")


@router.put("/conversations/{conversation_id}/name")
async def update_conversation_name(
    conversation_id: int,
    request: UpdateConversationNameRequest
):
    try:
        conversation_service = ConversationService()
        await conversation_service.update_conversation_name(conversation_id, request.name)
        return {"message": "会话名称已更新"}
    except Exception as e:
        raise HTTPException(status_code=500, detail="Internal server error")
