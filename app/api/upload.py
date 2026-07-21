"""文档上传接口。

这个模块只处理上传入口和任务提交，不承担文档索引细节。
上传校验、目标路径构造和响应组装都保持为本模块私有 helper，
避免再拆出只服务单一入口文件的 support 壳层。
"""
from __future__ import annotations

import hashlib
import uuid
from datetime import datetime
from pathlib import Path
from typing import Annotated

from app.api.common import run_api_action
from app.knowledge.application.indexing_contracts import UploadFileInfo
from app.knowledge.application.indexing_service import (
    IndexingService,
    get_document_extension,
    supports_document_indexing,
)
from app.shared.core.config import settings
from app.shared.core.logger import get_logger
from app.shared.task_queue import TaskStatusPayload, get_task_manager
from fastapi import APIRouter, File, Form, HTTPException, UploadFile

logger = get_logger(__name__)

router = APIRouter(tags=["upload"])

UPLOAD_DIR = Path("uploads")
# 上传大小限制从统一配置读取
MAX_UPLOAD_SIZE_MB = settings.app_config.upload.max_upload_size_mb
MAX_UPLOAD_SIZE_BYTES = settings.app_config.upload.max_upload_size_bytes
FILE_SIZE_EXCEEDED_DETAIL = f"文件大小超过限制 ({MAX_UPLOAD_SIZE_MB}MB)"
CONTENT_EXTENSION_MISMATCH_DETAIL = "文件内容与扩展名不匹配: {extension}"
_UNKNOWN_FILE_TYPE_DETAIL = "无法识别文件类型"
_UNSUPPORTED_FILE_TYPE_DETAIL = "不支持的文件类型: {extension}"
_TASK_NOT_FOUND_DETAIL = "任务不存在: {task_id}"
_UPLOAD_ACCEPTED_MESSAGE = "文件已上传，后台正在解析索引。请通过 task_id 查询进度。"

# 与 knowledge.indexing_service 保持一致：可上传 = 可索引。
# Markdown 为纯文本无魔数；仅对 PDF / DOCX 做内容签名校验。
_DOCUMENT_MAGIC_SIGNATURES: dict[str, tuple[bytes, ...]] = {
    ".pdf": (b"%PDF",),
    ".docx": (b"PK\x03\x04",),
}


def _document_magic_signatures(extension: str) -> tuple[bytes, ...]:
    """返回扩展名对应的魔数签名；无定义时返回空元组。"""
    return _DOCUMENT_MAGIC_SIGNATURES.get(extension, ())


class StoredUploadFileInfo(UploadFileInfo, total=False):
    """上传成功后在 API 层和任务层共享的文件元信息。"""

    filename: str
    original_name: str | None
    size: int
    type: str | None
    user_uuid: str
    upload_time: str
    directory: str


class UploadAcceptedResponse(StoredUploadFileInfo, total=False):
    """上传接口的成功返回结构。"""

    task_id: str
    message: str


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
    """读全文件并做大小/魔数校验。

    Markdown 无稳定魔数，signatures 为空时跳过内容签名检查。
    """
    content = await file.read()
    if len(content) > max_upload_size_bytes:
        raise HTTPException(status_code=400, detail=file_size_exceeded_detail)

    extension = get_document_extension(file.filename)
    signatures = _document_magic_signatures(extension)
    # 仅 pdf/docx 有签名；伪造扩展名在此拦截
    if signatures and not any(content.startswith(signature) for signature in signatures):
        raise HTTPException(
            status_code=400,
            detail=content_extension_mismatch_detail.format(extension=extension),
        )
    return content


def _normalize_upload_mode(mode: str | None) -> str:
    """规范化上传索引模式：create | replace。"""
    value = (mode or "create").strip().lower()
    if value not in {"create", "replace"}:
        raise HTTPException(status_code=400, detail="mode 仅支持 create 或 replace")
    return value


async def _store_upload(
    file: UploadFile,
    user_id: int,
    *,
    doc_id: str | None = None,
    mode: str = "create",
) -> StoredUploadFileInfo:
    """落盘并组装 file_info（供 IndexingService.process_file 使用）。"""
    # uuid5：同一 user_id 稳定目录前缀，避免可枚举的递增路径
    user_uuid = str(uuid.uuid5(uuid.NAMESPACE_DNS, f"user_{user_id}"))
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    upload_dir = UPLOAD_DIR / user_uuid / timestamp
    upload_dir.mkdir(parents=True, exist_ok=True)

    original_name = Path(file.filename or "upload").stem
    file_path = upload_dir / f"{original_name}_{timestamp}{get_document_extension(file.filename)}"
    content = await read_upload_content(
        file,
        max_upload_size_bytes=MAX_UPLOAD_SIZE_BYTES,
        file_size_exceeded_detail=FILE_SIZE_EXCEEDED_DETAIL,
        content_extension_mismatch_detail=CONTENT_EXTENSION_MISMATCH_DETAIL,
    )
    file_path.write_bytes(content)
    # content_hash：便于审计与后续幂等；不做强一致去重（可后续接 MySQL 文档表）
    content_hash = hashlib.sha256(content).hexdigest()
    payload: StoredUploadFileInfo = {
        "filename": file_path.name,
        "original_name": file.filename,
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
    if doc_id and doc_id.strip():
        payload["doc_id"] = doc_id.strip()
    return payload


@router.post("/upload")
async def upload_file(
    file: UploadFile = File(...),
    user_id: int = Form(...),
    # Annotated 保证单元测试直接调函数时拿到真实默认值，而不是 Form 对象
    doc_id: Annotated[str | None, Form()] = None,
    mode: Annotated[str, Form()] = "create",
) -> UploadAcceptedResponse:
    """上传并异步索引：先落盘立刻返回 task_id，解析在后台跑。

    - mode=create：新建文档（可省略 doc_id，服务端自动生成）
    - mode=replace：动态更新（**必须**传稳定 doc_id；软删旧版 + 写新 version）
    """
    async def operation() -> UploadAcceptedResponse:
        validate_upload(file)
        normalized_mode = _normalize_upload_mode(mode)
        if normalized_mode == "replace" and not (doc_id and doc_id.strip()):
            raise HTTPException(
                status_code=400,
                detail="replace 模式必须提供 doc_id（与首次入库相同的稳定文档 ID）",
            )
        file_info = await _store_upload(
            file,
            user_id,
            doc_id=doc_id,
            mode=normalized_mode,
        )
        task_manager = await get_task_manager()
        # 勿在请求内同步 parse_document：大 PDF 会堵 worker
        task_id = await task_manager.submit(IndexingService().process_file, file_info)
        return {
            **file_info,
            "task_id": task_id,
            "message": _UPLOAD_ACCEPTED_MESSAGE,
        }

    return await run_api_action(
        "upload_file",
        operation(),
        logger=logger,
        user_id=user_id,
        filename=file.filename,
    )


@router.get("/upload/status/{task_id}")
async def get_upload_status(task_id: str) -> TaskStatusPayload:
    """查询文档解析任务状态。"""
    async def operation() -> TaskStatusPayload:
        task_manager = await get_task_manager()
        status = await task_manager.get_status(task_id)
        if status is None:
            raise HTTPException(
                status_code=404,
                detail=_TASK_NOT_FOUND_DETAIL.format(task_id=task_id),
            )
        return status

    return await run_api_action(
        "get_upload_status",
        operation(),
        logger=logger,
        task_id=task_id,
    )
