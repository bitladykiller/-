"""知识文档元信息 API。

身份来自访问令牌；`user_id` 不再出现在路径里。
替换更新仍走 /api/upload（mode=replace + doc_id）。
"""

from __future__ import annotations

from typing import Any

from app.api.common import run_api_action
from app.api.deps import CurrentUser
from app.knowledge.application.document_service import document_service
from app.shared.core.logger import get_logger
from fastapi import APIRouter, HTTPException

logger = get_logger(__name__)
router = APIRouter(tags=["documents"])


@router.get("/documents")
async def list_my_documents(current_user: CurrentUser) -> list[dict[str, Any]]:
    """列出当前用户上传的文档（doc_id / 文件名 / version / status）。

    注意：这是**上传管理视角**的列表。知识库检索是全局共享的，
    见 05 文档「知识库的隔离语义」。
    """

    async def operation() -> list[dict[str, Any]]:
        return await document_service.list_user_documents(
            current_user.tenant_id,
            current_user.id,
        )

    return await run_api_action(
        "list_my_documents",
        operation(),
        logger=logger,
        user_id=current_user.id,
    )


@router.get("/documents/{doc_id}")
async def get_my_document(doc_id: str, current_user: CurrentUser) -> dict[str, Any]:
    """查询单条文档元信息（归属校验）。"""

    async def operation() -> dict[str, Any]:
        row = await document_service.get_user_document(
            current_user.tenant_id,
            current_user.id,
            doc_id,
        )
        if row is None:
            raise HTTPException(status_code=404, detail=f"文档不存在: {doc_id}")
        return row

    return await run_api_action(
        "get_my_document",
        operation(),
        logger=logger,
        user_id=current_user.id,
        doc_id=doc_id,
    )


@router.delete("/documents/{doc_id}")
async def delete_my_document(doc_id: str, current_user: CurrentUser) -> dict[str, Any]:
    """删除当前用户名下的知识文档。

    - MySQL：删除 user_documents 元信息行（列表立即不可见）
    - Milvus：软删除该 doc_id 全部 chunk（检索立即排除）
    - 归属不符或不存在 → 404
    """
    result = await run_api_action(
        "delete_my_document",
        document_service.delete_document(
            current_user.tenant_id,
            current_user.id,
            doc_id,
        ),
        logger=logger,
        user_id=current_user.id,
        doc_id=doc_id,
    )
    return {**result, "message": "文档已删除"}
