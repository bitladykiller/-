"""会话管理接口。

职责：
- 暴露会话创建、列表查询、删除、重命名接口
- 只做 HTTP 参数接收与响应转换
- 通过统一 helper 包装 Service 调用，避免每个 handler 重复样板代码
"""

from __future__ import annotations

from typing_extensions import TypedDict

from fastapi import APIRouter
from pydantic import BaseModel

from app.api.common import MessageResponse, build_message_response, run_api_action
from app.shared.core.logger import get_logger
from app.chat.application.conversation_service import conversation_service, ConversationSummary

logger = get_logger(__name__)


router = APIRouter(tags=["conversations"])
DELETE_SUCCESS_MESSAGE = "会话已删除"
UPDATE_NAME_SUCCESS_MESSAGE = "会话名称已更新"


class ConversationCreatedResponse(TypedDict):
    """创建会话成功响应。"""

    conversation_id: int


class CreateConversationRequest(BaseModel):
    """创建会话请求体。"""

    user_id: int


class UpdateConversationNameRequest(BaseModel):
    """修改会话名称请求体。"""

    name: str


@router.post("/conversations")
async def create_conversation(
    request: CreateConversationRequest,
) -> ConversationCreatedResponse:
    """创建新会话并返回会话 ID。"""
    conversation_id = await run_api_action(
        "create_conversation",
        conversation_service.create_conversation(request.user_id),
        logger=logger,
        user_id=request.user_id,
    )
    return {"conversation_id": conversation_id}


@router.get("/conversations/user/{user_id}")
async def get_user_conversations(user_id: int) -> list[ConversationSummary]:
    """查询指定用户的会话列表。"""
    return await run_api_action(
        "get_user_conversations",
        conversation_service.get_user_conversations(user_id),
        logger=logger,
        user_id=user_id,
    )


@router.delete("/conversations/{conversation_id}")
async def delete_conversation(conversation_id: int) -> MessageResponse:
    """删除指定会话。"""
    await run_api_action(
        "delete_conversation",
        conversation_service.delete_conversation(conversation_id),
        logger=logger,
        conversation_id=conversation_id,
    )
    return build_message_response(DELETE_SUCCESS_MESSAGE)


@router.put("/conversations/{conversation_id}/name")
async def update_conversation_name(
    conversation_id: int,
    request: UpdateConversationNameRequest,
) -> MessageResponse:
    """更新指定会话标题。"""
    await run_api_action(
        "update_conversation_name",
        conversation_service.update_conversation_name(conversation_id, request.name),
        logger=logger,
        conversation_id=conversation_id,
        name=request.name,
    )
    return build_message_response(UPDATE_NAME_SUCCESS_MESSAGE)
