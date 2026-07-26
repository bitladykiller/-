"""文档上传接口。

落盘 + 写 MySQL 文档元数据 + 提交异步索引任务。
索引完成后由 document_indexing_job 回写 version/status。
"""
from __future__ import annotations

import hashlib
import uuid
from collections.abc import Awaitable
from datetime import datetime
from pathlib import Path
from typing import Annotated, cast

from app.api.common import run_api_action
from app.knowledge.application.document_indexing_job import run_document_indexing_job
from app.knowledge.application.document_service import document_service
from app.knowledge.application.indexing_service import (
    get_document_extension,
    normalize_upload_mode,
    supports_document_indexing,
)
from app.shared.background_tasks import TaskStatusPayload, get_task_manager
from app.shared.core.config import settings
from app.shared.core.logger import get_logger
from fastapi import APIRouter, File, Form, HTTPException, UploadFile
from typing_extensions import TypedDict

logger = get_logger(__name__)

router = APIRouter(tags=["upload"])

UPLOAD_DIR = Path("uploads")
MAX_UPLOAD_SIZE_MB = settings.app_config.upload.max_upload_size_mb
MAX_UPLOAD_SIZE_BYTES = settings.app_config.upload.max_upload_size_bytes
FILE_SIZE_EXCEEDED_DETAIL = f"文件大小超过限制 ({MAX_UPLOAD_SIZE_MB}MB)"
CONTENT_EXTENSION_MISMATCH_DETAIL = "文件内容与扩展名不匹配: {extension}"
_UNKNOWN_FILE_TYPE_DETAIL = "无法识别文件类型"
_UNSUPPORTED_FILE_TYPE_DETAIL = "不支持的文件类型: {extension}"
_TASK_NOT_FOUND_DETAIL = "任务不存在: {task_id}"
_UPLOAD_ACCEPTED_MESSAGE = "文件已上传，后台正在解析索引。请通过 task_id 查询进度。"
_UPLOAD_UNCHANGED_MESSAGE = "内容未变化（content_hash 一致），已跳过 reindex。"

_DOCUMENT_MAGIC_SIGNATURES: dict[str, tuple[bytes, ...]] = {
    ".pdf": (b"%PDF",),
    ".docx": (b"PK\x03\x04",),
}


def _document_magic_signatures(extension: str) -> tuple[bytes, ...]:
    return _DOCUMENT_MAGIC_SIGNATURES.get(extension, ())


class StoredUploadFileInfo(TypedDict, total=False):
    """上传成功后在 API 层和任务层共享的文件元信息。"""

    path: str
    user_id: int
    doc_id: str
    mode: str
    content_hash: str
    filename: str
    original_name: str | None
    size: int
    type: str | None
    user_uuid: str
    upload_time: str
    directory: str
    title: str
    unchanged: bool
    version: int
    chunk_count: int


class UploadAcceptedResponse(TypedDict, total=False):
    """上传接口的成功返回结构。"""

    path: str
    user_id: int
    doc_id: str
    mode: str
    content_hash: str
    filename: str
    original_name: str | None
    size: int
    type: str | None
    user_uuid: str
    upload_time: str
    directory: str
    title: str
    task_id: str
    message: str
    unchanged: bool
    skipped: bool
    version: int
    chunk_count: int


def _optional_str(value: object | None) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _require_doc_id(file_info: StoredUploadFileInfo) -> str:
    """从元信息取出 doc_id；缺失时抛业务错误。"""
    doc_id = _optional_str(file_info.get("doc_id"))
    if not doc_id:
        raise HTTPException(status_code=500, detail="内部错误：缺少 doc_id")
    return doc_id


def validate_upload(file: UploadFile) -> None:
    """校验扩展名（md/pdf/docx）与 content_type 是否存在。"""
    ext = get_document_extension(file.filename)
    if not supports_document_indexing(ext):
        raise HTTPException(
            status_code=400,
            detail=_UNSUPPORTED_FILE_TYPE_DETAIL.format(extension=ext),
        )
    if not file.content_type:
        raise HTTPException(status_code=400, detail=_UNKNOWN_FILE_TYPE_DETAIL)


async def read_upload_content(
    file: UploadFile,
    *,
    max_upload_size_bytes: int,
    file_size_exceeded_detail: str,
    content_extension_mismatch_detail: str,
) -> bytes:
    """读全文件并做大小/魔数校验。"""
    content = await file.read()
    if len(content) > max_upload_size_bytes:
        raise HTTPException(status_code=400, detail=file_size_exceeded_detail)

    extension = get_document_extension(file.filename)
    signatures = _document_magic_signatures(extension)
    if signatures and not any(content.startswith(signature) for signature in signatures):
        raise HTTPException(
            status_code=400,
            detail=content_extension_mismatch_detail.format(extension=extension),
        )
    return content


def _normalize_upload_mode(mode: str | None) -> str:
    """复用领域层的唯一校验实现，只负责把 ValueError 翻成 HTTP 400。"""
    try:
        return normalize_upload_mode(mode)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


async def _store_upload(
    file: UploadFile,
    user_id: int,
    *,
    doc_id: str | None = None,
    mode: str = "create",
) -> StoredUploadFileInfo:
    """落盘并组装 file_info。"""
    user_uuid = str(uuid.uuid5(uuid.NAMESPACE_DNS, f"user_{user_id}"))
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    upload_dir = UPLOAD_DIR / user_uuid / timestamp
    upload_dir.mkdir(parents=True, exist_ok=True)

    original_filename = file.filename or "upload"
    original_stem = Path(original_filename).stem
    file_path = upload_dir / (
        f"{original_stem}_{timestamp}{get_document_extension(original_filename)}"
    )
    content = await read_upload_content(
        file,
        max_upload_size_bytes=MAX_UPLOAD_SIZE_BYTES,
        file_size_exceeded_detail=FILE_SIZE_EXCEEDED_DETAIL,
        content_extension_mismatch_detail=CONTENT_EXTENSION_MISMATCH_DETAIL,
    )
    file_path.write_bytes(content)
    content_hash = hashlib.sha256(content).hexdigest()
    payload: StoredUploadFileInfo = {
        "filename": file_path.name,
        "original_name": original_filename,
        "title": original_filename,
        "size": len(content),
        "type": file.content_type,
        "path": file_path.as_posix(),
        "user_id": user_id,
        "user_uuid": user_uuid,
        "upload_time": timestamp,
        "directory": upload_dir.as_posix(),
        "mode": mode,
        "content_hash": content_hash,
    }
    cleaned_doc_id = _optional_str(doc_id)
    if cleaned_doc_id:
        payload["doc_id"] = cleaned_doc_id
    return payload


async def _register_document_metadata(file_info: StoredUploadFileInfo) -> StoredUploadFileInfo:
    """在提交索引前写入/更新 MySQL，保证 doc_id 与文件名绑定。"""
    user_id = int(file_info.get("user_id") or 0)
    mode = str(file_info.get("mode") or "create")
    original_name = str(
        file_info.get("original_name") or file_info.get("filename") or "document"
    )
    source_path = str(file_info.get("path") or "")
    content_hash = str(file_info.get("content_hash") or "")
    incoming_doc_id = _optional_str(file_info.get("doc_id"))

    try:
        if mode == "replace":
            meta = await document_service.prepare_replace(
                user_id=user_id,
                doc_id=incoming_doc_id or "",
                original_name=original_name,
                source_path=source_path,
                content_hash=content_hash,
            )
        else:
            meta = await document_service.prepare_create(
                user_id=user_id,
                title=str(file_info.get("title") or original_name),
                original_name=original_name,
                source_path=source_path,
                content_hash=content_hash,
                doc_id=incoming_doc_id,
            )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    updated = cast(StoredUploadFileInfo, {**file_info})
    updated["doc_id"] = str(meta.get("doc_id") or "")
    updated["title"] = str(meta.get("title") or original_name)
    if bool(meta.get("unchanged")):
        updated["unchanged"] = True
        updated["version"] = int(meta.get("version") or 0)
        updated["chunk_count"] = int(meta.get("chunk_count") or 0)
    else:
        updated["unchanged"] = False
    return updated


async def _run_upload(
    file: UploadFile,
    user_id: int,
    doc_id: str | None,
    mode: str,
) -> UploadAcceptedResponse:
    """上传主流程（显式 async，便于类型检查）。"""
    validate_upload(file)
    normalized_mode = _normalize_upload_mode(mode)
    if normalized_mode == "replace" and not _optional_str(doc_id):
        raise HTTPException(
            status_code=400,
            detail="replace 模式必须提供 doc_id（与文档列表中的稳定文档 ID 一致）",
        )

    file_info = await _store_upload(
        file,
        user_id,
        doc_id=doc_id,
        mode=normalized_mode,
    )
    file_info = await _register_document_metadata(file_info)
    resolved_doc_id = _require_doc_id(file_info)

    if file_info.get("unchanged"):
        response: UploadAcceptedResponse = {
            **file_info,
            "doc_id": resolved_doc_id,
            "task_id": "",
            "skipped": True,
            "unchanged": True,
            "message": _UPLOAD_UNCHANGED_MESSAGE,
        }
        return response

    task_manager = await get_task_manager()
    task_id = await task_manager.submit(run_document_indexing_job, file_info)
    await document_service.bind_task_id(resolved_doc_id, task_id)
    return {
        **file_info,
        "doc_id": resolved_doc_id,
        "task_id": task_id,
        "skipped": False,
        "unchanged": False,
        "message": _UPLOAD_ACCEPTED_MESSAGE,
    }


@router.post("/upload")
async def upload_file(
    file: UploadFile = File(...),
    user_id: int = Form(...),
    doc_id: Annotated[str | None, Form()] = None,
    mode: Annotated[str, Form()] = "create",
) -> UploadAcceptedResponse:
    """上传并异步索引。

    - mode=create：新建，服务端生成或使用传入 doc_id，写入 MySQL
    - mode=replace：必须传已有 doc_id；hash 相同则跳过，否则软删+新 version
    """
    operation: Awaitable[UploadAcceptedResponse] = _run_upload(
        file,
        user_id,
        doc_id,
        mode,
    )
    return await run_api_action(
        "upload_file",
        operation,
        logger=logger,
        user_id=user_id,
        filename=file.filename,
    )


@router.get("/upload/status/{task_id}")
async def get_upload_status(task_id: str) -> TaskStatusPayload:
    """查询文档解析任务状态。"""
    operation: Awaitable[TaskStatusPayload] = _run_get_upload_status(task_id)
    return await run_api_action(
        "get_upload_status",
        operation,
        logger=logger,
        task_id=task_id,
    )


async def _run_get_upload_status(task_id: str) -> TaskStatusPayload:
    task_manager = await get_task_manager()
    status = await task_manager.get_status(task_id)
    if status is None:
        raise HTTPException(
            status_code=404,
            detail=_TASK_NOT_FOUND_DETAIL.format(task_id=task_id),
        )
    return status
