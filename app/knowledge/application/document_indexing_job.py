"""文档索引后台任务：跑 IndexingService 后回写 MySQL 元数据。"""

from __future__ import annotations

from typing import Any

from app.knowledge.application.document_service import document_service
from app.knowledge.application.indexing_contracts import IndexingResult, UploadFileInfo
from app.knowledge.application.indexing_service import IndexingService
from app.shared.core.logger import get_logger

logger = get_logger(__name__)


async def run_document_indexing_job(
    file_info: UploadFileInfo,
    *,
    task_id: str = "",
) -> IndexingResult:
    """供 task_queue 提交的统一入口。

    1. IndexingService.process_file（create / replace 策略 2）
    2. DocumentService.apply_indexing_result 同步 MySQL
    """
    result = await IndexingService().process_file(file_info)
    doc_id = str(file_info.get("doc_id") or result.get("doc_id") or "")
    if doc_id:
        # 把任务结果里的 doc_id 写回，便于前端轮询看到
        if not result.get("doc_id"):
            result["doc_id"] = doc_id
        await document_service.apply_indexing_result(
            doc_id=doc_id,
            indexing_result=dict(result),
            task_id=task_id,
        )
    else:
        logger.warning("索引完成但无 doc_id，跳过 MySQL 回写 | result=%s", result)
    return result


async def run_document_indexing_job_with_task(
    file_info: UploadFileInfo,
    task_id: str,
) -> IndexingResult:
    """显式带 task_id 的包装（upload 提交时 bind）。"""
    info: dict[str, Any] = dict(file_info)
    return await run_document_indexing_job(info, task_id=task_id)  # type: ignore[arg-type]


__all__ = [
    "run_document_indexing_job",
    "run_document_indexing_job_with_task",
]
