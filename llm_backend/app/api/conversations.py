from app.core.logger import get_logger

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app.services.conversation_service import ConversationService

logger = get_logger(__name__)


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
        logger.error(
            f"create_conversation 异常 | user_id={request.user_id} | {e}",
            exc_info=True,
        )
        raise HTTPException(status_code=500, detail="Internal server error")


@router.get("/conversations/user/{user_id}")
async def get_user_conversations(user_id: int):
    try:
        conversations = await ConversationService.get_user_conversations(user_id)
        return conversations
    except Exception as e:
        logger.error(
            f"get_user_conversations 异常 | user_id={user_id} | {e}",
            exc_info=True,
        )
        raise HTTPException(status_code=500, detail="Internal server error")


@router.delete("/conversations/{conversation_id}")
async def delete_conversation(conversation_id: int):
    try:
        await ConversationService.delete_conversation(conversation_id)
        return {"message": "会话已删除"}
    except Exception as e:
        logger.error(
            f"delete_conversation 异常 | conversation_id={conversation_id} | {e}",
            exc_info=True,
        )
        raise HTTPException(status_code=500, detail="Internal server error")


@router.put("/conversations/{conversation_id}/name")
async def update_conversation_name(
    conversation_id: int,
    request: UpdateConversationNameRequest
):
    try:
        await ConversationService.update_conversation_name(
            conversation_id, request.name
        )
        return {"message": "会话名称已更新"}
    except Exception as e:
        logger.error(
            f"update_conversation_name 异常 | conversation_id={conversation_id} "
            f"name={request.name} | {e}",
            exc_info=True,
        )
        raise HTTPException(status_code=500, detail="Internal server error")
