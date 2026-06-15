"""会话管理接口。

职责：
- 暴露会话创建、列表查询、删除、重命名接口
- 只做 HTTP 参数接收与响应转换
- 通过统一 helper 包装 Service 调用，避免每个 handler 重复样板代码
"""

from app.api.route_utils import run_route_action
from app.chat.application.conversation_service import (
    create_conversation as create_conversation_service,
)
from app.chat.application.conversation_service import (
    delete_conversation as delete_conversation_service,
)
from app.chat.application.conversation_service import (
    get_user_conversations as get_user_conversations_service,
)
from app.chat.application.conversation_service import (
    update_conversation_name as update_conversation_name_service,
)
from app.shared.core.logger import get_logger
from fastapi import APIRouter, Body

logger = get_logger(__name__)


router = APIRouter(tags=["conversations"])


@router.post("/conversations")
async def create_conversation(
    user_id: int = Body(..., embed=True),
) -> dict[str, int]:
    """创建新会话并返回会话 ID。"""
    conversation_id = await run_route_action(
        "create_conversation",
        create_conversation_service(user_id),
        logger=logger,
        user_id=user_id,
    )
    return {"conversation_id": conversation_id}


@router.get("/conversations/user/{user_id}")
async def get_user_conversations(user_id: int) -> list[dict[str, object]]:
    """查询指定用户的会话列表。"""
    return await run_route_action(
        "get_user_conversations",
        get_user_conversations_service(user_id),
        logger=logger,
        user_id=user_id,
    )


@router.delete("/conversations/{conversation_id}")
async def delete_conversation(conversation_id: int) -> dict[str, str]:
    """删除指定会话。"""
    await run_route_action(
        "delete_conversation",
        delete_conversation_service(conversation_id),
        logger=logger,
        conversation_id=conversation_id,
    )
    return {"message": "会话已删除"}


@router.put("/conversations/{conversation_id}/name")
async def update_conversation_name(
    conversation_id: int,
    name: str = Body(..., embed=True),
) -> dict[str, str]:
    """更新指定会话标题。"""
    await run_route_action(
        "update_conversation_name",
        update_conversation_name_service(conversation_id, name),
        logger=logger,
        conversation_id=conversation_id,
        name=name,
    )
    return {"message": "会话名称已更新"}
