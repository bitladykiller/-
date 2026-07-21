"""用户知识文档元信息 API。

列表 / 详情；替换更新仍走 /api/upload（mode=replace + doc_id）。
"""

from __future__ import annotations

from typing import Any

from app.api.common import run_api_action
from app.knowledge.application.document_service import document_service
from app.shared.core.logger import get_logger
from fastapi import APIRouter, HTTPException

logger = get_logger(__name__)
router = APIRouter(tags=["documents"])


@router.get("/documents/user/{user_id}")
async def list_user_documents(user_id: int) -> list[dict[str, Any]]:
    """列出用户文档（doc_id / 文件名 / version / status）。"""

    async def operation() -> list[dict[str, Any]]:
        return await document_service.list_user_documents(user_id)

    return await run_api_action(
        "list_user_documents",
        operation(),
        logger=logger,
        user_id=user_id,
    )


@router.get("/documents/user/{user_id}/{doc_id}")
async def get_user_document(user_id: int, doc_id: str) -> dict[str, Any]:
    """查询单条文档元信息。"""

    async def operation() -> dict[str, Any]:
        row = await document_service.get_user_document(user_id, doc_id)
        if row is None:
            raise HTTPException(status_code=404, detail=f"文档不存在: {doc_id}")
        return row

    return await run_api_action(
        "get_user_document",
        operation(),
        logger=logger,
        user_id=user_id,
        doc_id=doc_id,
    )
