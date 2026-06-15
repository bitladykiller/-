"""文档上传接口。

这个模块只处理上传入口和任务提交，不承担文档索引细节。
"""

import uuid
from datetime import datetime
from pathlib import Path
from typing import Any

from app.api.route_utils import run_route_action
from app.chat.application.task_queue import get_task_manager
from app.knowledge.application.indexing_service import (
    DOCUMENT_MAGIC_SIGNATURES,
    INDEXABLE_DOCUMENT_EXTENSIONS,
    process_file,
)
from app.shared.core.logger import get_logger
from fastapi import APIRouter, File, Form, HTTPException, UploadFile

logger = get_logger(__name__)

router = APIRouter(tags=["upload"])

UPLOAD_DIR = Path("uploads")
MAX_UPLOAD_SIZE_MB = 50


@router.post("/upload")
async def upload_file(
    file: UploadFile = File(...),
    user_id: int = Form(...),
) -> dict[str, object]:
    """上传文档并异步解析索引。"""
    async def operation() -> dict[str, object]:
        extension = Path(file.filename or "").suffix.lower()
        if extension not in INDEXABLE_DOCUMENT_EXTENSIONS:
            raise HTTPException(
                status_code=400,
                detail=f"不支持的文件类型: {extension}",
            )
        if not file.content_type:
            raise HTTPException(status_code=400, detail="无法识别文件类型")

        user_uuid = str(uuid.uuid5(uuid.NAMESPACE_DNS, f"user_{user_id}"))
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        upload_dir = UPLOAD_DIR / user_uuid / timestamp
        upload_dir.mkdir(parents=True, exist_ok=True)

        original_name = Path(file.filename or "upload").stem
        file_path = upload_dir / (
            f"{original_name}_{timestamp}{extension}"
        )
        content = await file.read()
        if len(content) > MAX_UPLOAD_SIZE_MB * 1024 * 1024:
            raise HTTPException(
                status_code=400,
                detail=f"文件大小超过限制 ({MAX_UPLOAD_SIZE_MB}MB)",
            )

        signatures = DOCUMENT_MAGIC_SIGNATURES.get(extension, ())
        if signatures and not any(content.startswith(signature) for signature in signatures):
            raise HTTPException(
                status_code=400,
                detail=f"文件内容与扩展名不匹配: {extension}",
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
            "message": "文件已上传，后台正在解析索引。请通过 task_id 查询进度。",
        }

    return await run_route_action(
        "upload_file",
        operation(),
        logger=logger,
        user_id=user_id,
        filename=file.filename,
    )


@router.get("/upload/status/{task_id}")
async def get_upload_status(task_id: str) -> dict[str, Any]:
    """查询文档解析任务状态。"""
    async def operation() -> dict[str, Any]:
        task_manager = await get_task_manager()
        status = await task_manager.get_status(task_id)
        if status is None:
            raise HTTPException(
                status_code=404,
                detail=f"任务不存在: {task_id}",
            )
        return status

    return await run_route_action(
        "get_upload_status",
        operation(),
        logger=logger,
        task_id=task_id,
    )
