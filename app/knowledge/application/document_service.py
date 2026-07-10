"""用户知识文档元信息服务。

MySQL 保存 doc_id ↔ 文件名/版本/状态，保证前端「更新文档」
能把展示名与后端稳定 doc_id 对齐。
"""

from __future__ import annotations

from collections.abc import Callable
from contextlib import AbstractAsyncContextManager
from typing import Any, TypeAlias

from app.knowledge.application.indexing_service import build_doc_id
from app.knowledge.infrastructure.doc_parser.retrieval.doc_lifecycle import (
    validate_doc_id,
)
from app.knowledge.infrastructure.repository.user_document_repository import (
    UserDocumentRepository,
)
from app.shared.core.database import AsyncSessionLocal
from app.shared.core.degradation import log_degradation
from app.shared.core.errors import ResourceNotFoundError
from app.shared.core.logger import get_logger
from sqlalchemy.ext.asyncio import AsyncSession

logger = get_logger(__name__)

_SessionFactory: TypeAlias = Callable[[], AbstractAsyncContextManager[AsyncSession]]

DocumentSummary = dict[str, Any]


class DocumentService:
    """文档元数据 CRUD + 索引结果回写。"""

    def __init__(self, session_factory: _SessionFactory = AsyncSessionLocal) -> None:
        self._session_factory = session_factory

    async def list_user_documents(
        self,
        tenant_id: str,
        user_id: int,
    ) -> list[DocumentSummary]:
        async with self._session_factory() as db:
            repo = UserDocumentRepository(db)
            rows = await repo.list_by_user(tenant_id, user_id)
            return [repo.to_summary(r) for r in rows]

    async def get_user_document(
        self,
        tenant_id: str,
        user_id: int,
        doc_id: str,
    ) -> DocumentSummary | None:
        safe = validate_doc_id(doc_id)
        async with self._session_factory() as db:
            repo = UserDocumentRepository(db)
            row = await repo.get_owned(tenant_id, user_id, safe)
            return repo.to_summary(row) if row else None

    async def delete_document(
        self,
        tenant_id: str,
        user_id: int,
        doc_id: str,
    ) -> dict[str, Any]:
        """删除文档：MySQL 删元信息行 + Milvus 软删全部 chunk。

        WHY 顺序是"先 MySQL 后 Milvus"：
        元信息行是归属与展示的权威来源，先删它，用户列表立即看不到；
        chunk 软删失败时文档虽仍可被检索到，但已无法通过界面管理——
        这种残留由检索侧的 is_deleted 过滤 + 定期硬清理兜底，不阻塞删除操作。

        Returns:
            {"doc_id": ..., "soft_deleted_chunks": n}

        Raises:
            ResourceNotFoundError: 文档不存在或不属于该用户（API 层映射 404）。
        """
        safe = validate_doc_id(doc_id)
        async with self._session_factory() as db:
            repo = UserDocumentRepository(db)
            row = await repo.delete_owned(tenant_id, user_id, safe)
        if row is None:
            raise ResourceNotFoundError(f"文档不存在或不属于当前用户: {safe}")

        soft_deleted = 0
        try:
            from app.knowledge.infrastructure.doc_parser.retrieval.hybrid_search import (
                get_shared_searcher,
            )

            result = await get_shared_searcher().soft_delete_document(
                safe,
                tenant_id=tenant_id,
            )
            soft_deleted = int(result.get("soft_deleted") or 0)
        except Exception as exc:
            # 软删 chunk 失败不回滚元信息删除；chunk 残留由 is_deleted 过滤兜底
            log_degradation(logger, "document.delete_chunks", exc, doc_id=safe)

        logger.info(
            "文档已删除 | user=%s doc_id=%s soft_deleted_chunks=%s",
            user_id,
            safe,
            soft_deleted,
        )
        return {"doc_id": safe, "soft_deleted_chunks": soft_deleted}

    async def prepare_create(
        self,
        *,
        tenant_id: str,
        user_id: int,
        title: str,
        original_name: str,
        source_path: str,
        content_hash: str,
        doc_id: str | None = None,
        last_task_id: str = "",
    ) -> DocumentSummary:
        """首次上传：分配/校验 doc_id 并写入 pending 记录。"""
        resolved = (
            validate_doc_id(doc_id) if doc_id and str(doc_id).strip() else build_doc_id(user_id)
        )
        display = (title or original_name or resolved).strip() or resolved
        async with self._session_factory() as db:
            repo = UserDocumentRepository(db)
            existing = await repo.get_by_doc_id(tenant_id, resolved)
            if existing is not None:
                raise ValueError(f"doc_id 已存在: {resolved}")
            row = await repo.create(
                tenant_id=tenant_id,
                user_id=user_id,
                doc_id=resolved,
                title=display[:255],
                original_name=(original_name or "")[:255],
                source_path=source_path or "",
                content_hash=content_hash or "",
                status="pending",
                last_task_id=last_task_id,
            )
            return repo.to_summary(row)

    async def prepare_replace(
        self,
        *,
        tenant_id: str,
        user_id: int,
        doc_id: str,
        original_name: str,
        source_path: str,
        content_hash: str,
        last_task_id: str = "",
        update_title: bool = True,
    ) -> DocumentSummary:
        """替换更新：校验归属；hash 一致则跳过，否则标记 indexing。

        返回 dict 额外字段：
        - unchanged=True：content_hash 与库中一致，调用方勿 reindex
        """
        safe = validate_doc_id(doc_id)
        new_hash = (content_hash or "").strip()
        async with self._session_factory() as db:
            repo = UserDocumentRepository(db)
            row = await repo.get_owned(tenant_id, user_id, safe)
            if row is None:
                raise ValueError(f"文档不存在或不属于当前用户: {safe}")

            old_hash = (row.content_hash or "").strip()
            # 两边都有非空 hash 且相等 → 内容未变，跳过 reindex
            if old_hash and new_hash and old_hash == new_hash:
                summary = repo.to_summary(row)
                summary["unchanged"] = True
                summary["skipped_reason"] = "content_hash_match"
                return summary

            title = (original_name or row.title)[:255] if update_title else None
            row = await repo.mark_indexing(
                row,
                source_path=source_path,
                content_hash=new_hash,
                original_name=(original_name or row.original_name)[:255],
                title=title,
                last_task_id=last_task_id,
            )
            summary = repo.to_summary(row)
            summary["unchanged"] = False
            return summary

    async def bind_task_id(
        self,
        tenant_id: str,
        doc_id: str,
        task_id: str,
    ) -> None:
        async with self._session_factory() as db:
            repo = UserDocumentRepository(db)
            row = await repo.get_by_doc_id(tenant_id, validate_doc_id(doc_id))
            if row is None:
                return
            row.last_task_id = task_id
            row.status = "indexing"
            await db.commit()

    async def apply_indexing_result(
        self,
        *,
        tenant_id: str,
        doc_id: str,
        indexing_result: dict[str, Any],
        task_id: str = "",
    ) -> None:
        """索引任务结束后回写 version / chunks / status。"""
        try:
            safe = validate_doc_id(doc_id)
        except ValueError:
            logger.warning("apply_indexing_result 跳过非法 doc_id=%s", doc_id)
            return

        status_raw = str(indexing_result.get("status") or "")
        if status_raw == "success":
            mysql_status = "ready"
            err = ""
        else:
            mysql_status = "failed"
            err = str(indexing_result.get("message") or "索引失败")

        version = indexing_result.get("version")
        chunks = indexing_result.get("chunks")
        try:
            version_i = int(version) if version is not None else None
        except (TypeError, ValueError):
            version_i = None
        try:
            chunks_i = int(chunks) if chunks is not None else None
        except (TypeError, ValueError):
            chunks_i = None

        try:
            async with self._session_factory() as db:
                repo = UserDocumentRepository(db)
                row = await repo.get_by_doc_id(tenant_id, safe)
                if row is None:
                    logger.warning("apply_indexing_result 未找到 doc_id=%s", safe)
                    return
                if task_id and row.last_task_id and row.last_task_id != task_id:
                    logger.info(
                        "跳过过期索引任务结果 | doc_id=%s task_id=%s current_task_id=%s",
                        safe,
                        task_id,
                        row.last_task_id,
                    )
                    return
                await repo.apply_index_result(
                    row,
                    status=mysql_status,
                    version=version_i,
                    chunk_count=chunks_i,
                    error_message=err,
                    last_task_id=task_id or row.last_task_id,
                )
        except Exception as exc:
            logger.error(
                "apply_indexing_result 失败 | doc_id=%s | %s",
                safe,
                exc,
                exc_info=True,
            )


document_service = DocumentService()

__all__ = ["DocumentService", "DocumentSummary", "document_service"]
