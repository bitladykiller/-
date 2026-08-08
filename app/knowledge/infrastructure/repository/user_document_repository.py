"""用户文档元信息 Repository。

多租户纪律：所有查询强制 tenant_id。
- 业务路径（list/get/delete）用 (tenant_id, user_id, doc_id) 三要素校验归属
- worker 回写路径（get_by_doc_id）也带 tenant_id——doc_id 已升级为租户内
  唯一，不带租户的查询要么歧义、要么泄漏
"""

from __future__ import annotations

from typing import Any

from app.knowledge.infrastructure.models.user_document import UserDocument
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession


class UserDocumentRepository:
    """user_documents 表数据访问。"""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def create(
        self,
        *,
        tenant_id: str,
        user_id: int,
        doc_id: str,
        title: str,
        original_name: str,
        source_path: str,
        content_hash: str,
        status: str = "pending",
        last_task_id: str = "",
    ) -> UserDocument:
        row = UserDocument(
            tenant_id=tenant_id,
            user_id=user_id,
            doc_id=doc_id,
            title=title,
            original_name=original_name,
            source_path=source_path,
            content_hash=content_hash,
            status=status,
            last_task_id=last_task_id,
        )
        self._session.add(row)
        await self._session.commit()
        await self._session.refresh(row)
        return row

    async def get_by_doc_id(
        self,
        tenant_id: str,
        doc_id: str,
    ) -> UserDocument | None:
        """按租户 + doc_id 查询（worker 任务回写路径）。"""
        result = await self._session.execute(
            select(UserDocument).where(
                UserDocument.tenant_id == tenant_id,
                UserDocument.doc_id == doc_id,
            )
        )
        return result.scalar_one_or_none()

    async def get_owned(
        self,
        tenant_id: str,
        user_id: int,
        doc_id: str,
    ) -> UserDocument | None:
        result = await self._session.execute(
            select(UserDocument).where(
                UserDocument.tenant_id == tenant_id,
                UserDocument.user_id == user_id,
                UserDocument.doc_id == doc_id,
            )
        )
        return result.scalar_one_or_none()

    async def list_by_user(
        self,
        tenant_id: str,
        user_id: int,
    ) -> list[UserDocument]:
        result = await self._session.execute(
            select(UserDocument)
            .where(
                UserDocument.tenant_id == tenant_id,
                UserDocument.user_id == user_id,
            )
            .order_by(UserDocument.updated_at.desc())
        )
        return list(result.scalars().all())

    async def delete_owned(
        self,
        tenant_id: str,
        user_id: int,
        doc_id: str,
    ) -> UserDocument | None:
        """删除指定租户+用户名下的文档元信息行，返回被删行；归属不符返回 None。"""
        row = await self.get_owned(tenant_id, user_id, doc_id)
        if row is None:
            return None
        await self._session.delete(row)
        await self._session.commit()
        return row

    async def mark_indexing(
        self,
        row: UserDocument,
        *,
        source_path: str,
        content_hash: str,
        original_name: str,
        title: str | None = None,
        last_task_id: str = "",
    ) -> UserDocument:
        row.status = "indexing"
        row.source_path = source_path
        row.content_hash = content_hash
        row.original_name = original_name
        if title is not None:
            row.title = title
        if last_task_id:
            row.last_task_id = last_task_id
        row.error_message = ""
        await self._session.commit()
        await self._session.refresh(row)
        return row

    async def apply_index_result(
        self,
        row: UserDocument,
        *,
        status: str,
        version: int | None = None,
        chunk_count: int | None = None,
        error_message: str = "",
        last_task_id: str | None = None,
    ) -> UserDocument:
        row.status = status
        if version is not None:
            row.version = int(version)
        if chunk_count is not None:
            row.chunk_count = int(chunk_count)
        row.error_message = error_message or ""
        if last_task_id is not None:
            row.last_task_id = last_task_id
        await self._session.commit()
        await self._session.refresh(row)
        return row

    @staticmethod
    def to_summary(row: UserDocument) -> dict[str, Any]:
        return {
            "id": row.id,
            "tenant_id": row.tenant_id,
            "user_id": row.user_id,
            "doc_id": row.doc_id,
            "title": row.title,
            "original_name": row.original_name,
            "source_path": row.source_path,
            "content_hash": row.content_hash,
            "status": row.status,
            "version": row.version,
            "chunk_count": row.chunk_count,
            "last_task_id": row.last_task_id,
            "error_message": row.error_message,
            "created_at": row.created_at.isoformat() if row.created_at else "",
            "updated_at": row.updated_at.isoformat() if row.updated_at else "",
        }


__all__ = ["UserDocumentRepository"]
