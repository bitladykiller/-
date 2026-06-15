"""文档上传接口。

这个模块只处理上传入口和任务提交，不承担文档索引细节。
"""
from __future__ import annotations

import uuid
from datetime import datetime
from pathlib import Path

from fastapi import APIRouter, File, Form, HTTPException, UploadFile

from app.api.common import run_api_action
from app.shared.core.logger import get_logger
from app.chat.application.document_formats import (
    DOCUMENT_MAGIC_SIGNATURES,
    get_document_extension,
    supports_document_indexing,
)
from app.knowledge.application.indexing_service import process_file
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


@router.post("/upload")
async def upload_file(
    file: UploadFile = File(...),
    user_id: int = Form(...),
) -> dict[str, object]:
    """上传文档并异步解析索引。"""
    async def operation() -> dict[str, object]:
        extension = get_document_extension(file.filename)
        if not supports_document_indexing(extension):
            raise HTTPException(
                status_code=400,
                detail=_UNSUPPORTED_FILE_TYPE_DETAIL.format(extension=extension),
            )
        if not file.content_type:
            raise HTTPException(status_code=400, detail=_UNKNOWN_FILE_TYPE_DETAIL)

        user_uuid = str(uuid.uuid5(uuid.NAMESPACE_DNS, f"user_{user_id}"))
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        upload_dir = UPLOAD_DIR / user_uuid / timestamp
        upload_dir.mkdir(parents=True, exist_ok=True)

        original_name = Path(file.filename or "upload").stem
        file_path = upload_dir / (
            f"{original_name}_{timestamp}{extension}"
        )
        content = await file.read()
        if len(content) > MAX_UPLOAD_SIZE_BYTES:
            raise HTTPException(status_code=400, detail=FILE_SIZE_EXCEEDED_DETAIL)

        signatures = DOCUMENT_MAGIC_SIGNATURES.get(extension, ())
        if signatures and not any(content.startswith(signature) for signature in signatures):
            raise HTTPException(
                status_code=400,
                detail=CONTENT_EXTENSION_MISMATCH_DETAIL.format(extension=extension),
            )

        file_path.write_bytes(content)
        file_info = {
            "filename": file_path.name,
            "original_name": file.filename,
            "size": len(content),
            "type": file.content_type,
            "path": file_path.as_posix(),
            "user_id": user_id,
            "user_uuid": user_uuid,
            "upload_time": timestamp,
            "directory": upload_dir.as_posix(),
        }
        task_manager = await get_task_manager()
        task_id = await task_manager.submit(process_file, file_info)
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
