"""会话管理接口。

身份一律来自访问令牌（`CurrentUser`），路径与请求体不再接受自报 user_id。

职责：
- 暴露会话创建、列表查询、删除、重命名、历史消息接口
- 只做 HTTP 参数接收与响应转换
- 通过统一 helper 包装 Service 调用，避免每个 handler 重复样板代码
"""

from __future__ import annotations

from app.api.common import (
    MessageResponse,
    build_message_response,
    run_api_action,
    run_idempotent_action,
)
from app.api.deps import CurrentUser
from app.chat.application.conversation_service import ConversationSummary, conversation_service
from app.platform.idempotency import REQUEST_ID_HEADER
from app.shared.core.logger import get_logger
from fastapi import APIRouter, Request
from pydantic import BaseModel
from typing_extensions import TypedDict

logger = get_logger(__name__)


router = APIRouter(tags=["conversations"])
DELETE_SUCCESS_MESSAGE = "会话已删除"
UPDATE_NAME_SUCCESS_MESSAGE = "会话名称已更新"


class ConversationCreatedResponse(TypedDict):
    """创建会话成功响应。"""

    conversation_id: int


class UpdateConversationNameRequest(BaseModel):
    """修改会话名称请求体。"""

    name: str


@router.post("/conversations")
async def create_conversation(
    request: Request,
    current_user: CurrentUser,
) -> ConversationCreatedResponse:
    """为当前用户创建新会话并返回会话 ID。

    X-Request-ID 幂等：前端在"创建"动作生成一次 request_id，网络重试
    复用同一值——重复请求直接返回首次创建的会话 ID，不产生第二个会话。
    """
    conversation_id = await run_idempotent_action(
        "create_conversation",
        conversation_service.create_conversation(current_user.tenant_id, current_user.id),
        logger=logger,
        user_id=current_user.id,
        request_id=request.headers.get(REQUEST_ID_HEADER, ""),
        endpoint="create_conversation",
    )
    return {"conversation_id": conversation_id}


@router.get("/conversations")
async def get_my_conversations(current_user: CurrentUser) -> list[ConversationSummary]:
    """查询当前用户的会话列表。"""
    return await run_api_action(
        "get_my_conversations",
        conversation_service.get_user_conversations(current_user.tenant_id, current_user.id),
        logger=logger,
        user_id=current_user.id,
    )


@router.get("/conversations/{conversation_id}/messages")
async def get_conversation_messages(
    conversation_id: int,
    current_user: CurrentUser,
) -> list[dict[str, str]]:
    """查询会话的持久化历史消息（归属校验，非本人 404）。

    数据来自 MySQL messages 表（append-only），与 Redis STM 的
    推理上下文互相独立——STM 过期不影响这里的可见历史。
    """
    return await run_api_action(
        "get_conversation_messages",
        conversation_service.list_messages(
            current_user.tenant_id,
            conversation_id,
            current_user.id,
        ),
        logger=logger,
        conversation_id=conversation_id,
        user_id=current_user.id,
    )


@router.delete("/conversations/{conversation_id}")
async def delete_conversation(
    conversation_id: int,
    current_user: CurrentUser,
) -> MessageResponse:
    """删除当前用户名下的会话及其关联记忆（非本人 404）。

    会清理：
    - MySQL 会话元信息与 messages 历史
    - Redis STM 该会话短期记忆
    - Milvus LTM 中带 session_id 的长期记忆（软删除）
    """
    await run_api_action(
        "delete_conversation",
        conversation_service.delete_conversation(
            current_user.tenant_id,
            conversation_id,
            current_user.id,
        ),
        logger=logger,
        conversation_id=conversation_id,
        user_id=current_user.id,
    )
    return build_message_response(DELETE_SUCCESS_MESSAGE)


@router.put("/conversations/{conversation_id}/name")
async def update_conversation_name(
    conversation_id: int,
    request: UpdateConversationNameRequest,
    current_user: CurrentUser,
) -> MessageResponse:
    """更新当前用户名下的会话标题（非本人 404）。"""
    await run_api_action(
        "update_conversation_name",
        conversation_service.update_conversation_name(
            current_user.tenant_id,
            conversation_id,
            current_user.id,
            request.name,
        ),
        logger=logger,
        conversation_id=conversation_id,
        user_id=current_user.id,
        name=request.name,
    )
    return build_message_response(UPDATE_NAME_SUCCESS_MESSAGE)
