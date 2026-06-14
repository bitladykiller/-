"""文档上传接口。

这个模块只处理上传入口和任务提交，不承担文档索引细节。
上传校验、目标路径构造和响应组装都保持为本模块私有 helper，
避免再拆出只服务单一入口文件的 support 壳层。
"""
from __future__ import annotations

import uuid
from datetime import datetime
from pathlib import Path
from typing import Any, TypedDict

from fastapi import APIRouter, File, Form, HTTPException, UploadFile

from app.api.common import run_api_action
from app.shared.core.logger import get_logger
from app.chat.application.document_formats import (
    document_magic_signatures,
    get_document_extension,
    supports_document_indexing,
)
from app.knowledge.application.indexing_service import (
    IndexingService,
)
from app.knowledge.application.indexing_contracts import UploadFileInfo
from app.chat.application.task_queue import TaskStatusPayload, get_task_manager

logger = get_logger(__name__)

router = APIRouter(tags=["upload"])

UPLOAD_DIR = Path("uploads")
MAX_UPLOAD_SIZE_MB = 50
MAX_UPLOAD_SIZE_BYTES = MAX_UPLOAD_SIZE_MB * 1024 * 1024
FILE_SIZE_EXCEEDED_DETAIL = f"文件大小超过限制 ({MAX_UPLOAD_SIZE_MB}MB)"
CONTENT_EXTENSION_MISMATCH_DETAIL = "文件内容与扩展名不匹配: {extension}"
_UNKNOWN_FILE_TYPE_DETAIL = "无法识别文件类型"
_UNSUPPORTED_FILE_TYPE_DETAIL = "不支持的文件类型: {extension}"
_TASK_NOT_FOUND_DETAIL = "任务不存在: {task_id}"
_UPLOAD_ACCEPTED_MESSAGE = "文件已上传，后台正在解析索引。请通过 task_id 查询进度。"


class StoredUploadFileInfo(UploadFileInfo, total=False):
    """上传成功后在 API 层和任务层共享的文件元信息。"""

    filename: str
    original_name: str | None
    size: int
    type: str | None
    user_uuid: str
    upload_time: str
    directory: str


class UploadTarget(TypedDict):
    """上传落盘目标。"""

    user_uuid: str
    timestamp: str
    upload_dir: Path
    file_path: Path


class UploadAcceptedResponse(StoredUploadFileInfo, total=False):
    """上传接口的成功返回结构。"""

    task_id: str
    message: str


def validate_upload(file: UploadFile) -> None:
    """验证上传文件的扩展名和 MIME 基本信息。"""
    ext = get_document_extension(file.filename)
    if not supports_document_indexing(ext):
        raise HTTPException(
            status_code=400,
            detail=_UNSUPPORTED_FILE_TYPE_DETAIL.format(extension=ext),
        )
    if not file.content_type:
        raise HTTPException(status_code=400, detail=_UNKNOWN_FILE_TYPE_DETAIL)


def build_upload_target(
    user_id: int,
    filename: str | None,
    *,
    upload_dir_root: Path,
    uuid_module: Any,
    clock: type[datetime],
) -> UploadTarget:
    """构造用户目录、时间目录和最终保存路径。"""
    user_uuid = str(uuid_module.uuid5(uuid_module.NAMESPACE_DNS, f"user_{user_id}"))
    timestamp = clock.now().strftime("%Y%m%d_%H%M%S")
    upload_dir = upload_dir_root / user_uuid / timestamp
    upload_dir.mkdir(parents=True, exist_ok=True)

    original_name = Path(filename or "upload").stem
    ext = get_document_extension(filename)
    new_filename = f"{original_name}_{timestamp}{ext}"
    return {
        "user_uuid": user_uuid,
        "timestamp": timestamp,
        "upload_dir": upload_dir,
        "file_path": upload_dir / new_filename,
    }


def build_file_info(
    *,
    file: UploadFile,
    user_id: int,
    user_uuid: str,
    timestamp: str,
    file_path: Path,
    directory: Path,
    size: int,
) -> StoredUploadFileInfo:
    """组装统一的上传文件元信息。"""
    return {
        "filename": file_path.name,
        "original_name": file.filename,
        "size": size,
        "type": file.content_type,
        "path": file_path.as_posix(),
        "user_id": user_id,
        "user_uuid": user_uuid,
        "upload_time": timestamp,
        "directory": directory.as_posix(),
    }


def _validate_magic_bytes(filename: str, content: bytes) -> bool:
    """通过魔数签名验证文件内容是否与扩展名匹配。"""
    signatures = document_magic_signatures(get_document_extension(filename))
    if not signatures:
        return True
    return any(content.startswith(signature) for signature in signatures)


async def read_upload_content(
    file: UploadFile,
    *,
    max_upload_size_bytes: int,
    file_size_exceeded_detail: str,
    content_extension_mismatch_detail: str,
) -> bytes:
    """读取上传内容并执行大小/魔数校验。"""
    content = await file.read()
    if len(content) > max_upload_size_bytes:
        raise HTTPException(status_code=400, detail=file_size_exceeded_detail)

    if not _validate_magic_bytes(file.filename or "", content):
        raise HTTPException(
            status_code=400,
            detail=content_extension_mismatch_detail.format(
                extension=get_document_extension(file.filename),
            ),
        )
    return content


def build_upload_accepted_response(
    file_info: StoredUploadFileInfo,
    task_id: str,
) -> UploadAcceptedResponse:
    """统一构造上传受理响应。"""
    return {
        **file_info,
        "task_id": task_id,
        "message": _UPLOAD_ACCEPTED_MESSAGE,
    }


async def _store_upload(file: UploadFile, user_id: int) -> StoredUploadFileInfo:
    """完成上传文件的校验、落盘与元信息组装。"""
    target = build_upload_target(
        user_id,
        file.filename,
        upload_dir_root=UPLOAD_DIR,
        uuid_module=uuid,
        clock=datetime,
    )
    content = await read_upload_content(
        file,
        max_upload_size_bytes=MAX_UPLOAD_SIZE_BYTES,
        file_size_exceeded_detail=FILE_SIZE_EXCEEDED_DETAIL,
        content_extension_mismatch_detail=CONTENT_EXTENSION_MISMATCH_DETAIL,
    )
    target["file_path"].write_bytes(content)
    return build_file_info(
        file=file,
        user_id=user_id,
        user_uuid=target["user_uuid"],
        timestamp=target["timestamp"],
        file_path=target["file_path"],
        directory=target["upload_dir"],
        size=len(content),
    )


@router.post("/upload")
async def upload_file(
    file: UploadFile = File(...),
    user_id: int = Form(...),
) -> UploadAcceptedResponse:
    """上传文档并异步解析索引。"""
    async def operation() -> UploadAcceptedResponse:
        validate_upload(file)
        file_info = await _store_upload(file, user_id)
        task_manager = await get_task_manager()
        task_id = await task_manager.submit(IndexingService().process_file, file_info)
        return build_upload_accepted_response(file_info, task_id)

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
