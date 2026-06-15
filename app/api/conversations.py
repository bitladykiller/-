"""会话管理接口。

职责：
- 暴露会话创建、列表查询、删除、重命名接口
- 只做 HTTP 参数接收与响应转换
- 通过统一 helper 包装 Service 调用，避免每个 handler 重复样板代码
"""

from __future__ import annotations

from fastapi import APIRouter
from pydantic import BaseModel

from app.api.common import run_api_action
from app.shared.core.logger import get_logger
from app.chat.application.conversation_service import (
    ConversationSummary,
    create_conversation as create_conversation_service,
    delete_conversation as delete_conversation_service,
    get_user_conversations as get_user_conversations_service,
    update_conversation_name as update_conversation_name_service,
)

logger = get_logger(__name__)


router = APIRouter(tags=["conversations"])
DELETE_SUCCESS_MESSAGE = "会话已删除"
UPDATE_NAME_SUCCESS_MESSAGE = "会话名称已更新"


class CreateConversationRequest(BaseModel):
    """创建会话请求体。"""

    user_id: int


class UpdateConversationNameRequest(BaseModel):
    """修改会话名称请求体。"""

    name: str


@router.post("/conversations")
async def create_conversation(
    request: CreateConversationRequest,
) -> dict[str, int]:
    """创建新会话并返回会话 ID。"""
    conversation_id = await run_api_action(
        "create_conversation",
        create_conversation_service(request.user_id),
        logger=logger,
        user_id=request.user_id,
    )
    return {"conversation_id": conversation_id}


@router.get("/conversations/user/{user_id}")
async def get_user_conversations(user_id: int) -> list[ConversationSummary]:
    """查询指定用户的会话列表。"""
    return await run_api_action(
        "get_user_conversations",
        get_user_conversations_service(user_id),
        logger=logger,
        user_id=user_id,
    )


@router.delete("/conversations/{conversation_id}")
async def delete_conversation(conversation_id: int) -> dict[str, str]:
    """删除指定会话。"""
    await run_api_action(
        "delete_conversation",
        delete_conversation_service(conversation_id),
        logger=logger,
        conversation_id=conversation_id,
    )
    return {"message": DELETE_SUCCESS_MESSAGE}


@router.put("/conversations/{conversation_id}/name")
async def update_conversation_name(
    conversation_id: int,
    request: UpdateConversationNameRequest,
) -> dict[str, str]:
    """更新指定会话标题。"""
    await run_api_action(
        "update_conversation_name",
        update_conversation_name_service(conversation_id, request.name),
        logger=logger,
        conversation_id=conversation_id,
        name=request.name,
    )
    return {"message": UPDATE_NAME_SUCCESS_MESSAGE}
