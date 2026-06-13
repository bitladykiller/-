"""上传接口 support helper。

职责：
- 定义上传接口共享的响应契约和错误文案
- 负责上传入口的基础校验与受理响应构造

边界：
- 不负责后台索引任务提交
- 不负责任务状态查询
- 不负责文件落盘和内容校验
"""

from __future__ import annotations

from typing import TypedDict

from fastapi import HTTPException, UploadFile

UNKNOWN_FILE_TYPE_DETAIL = "无法识别文件类型"
UNSUPPORTED_FILE_TYPE_DETAIL = "不支持的文件类型: {extension}"
TASK_NOT_FOUND_DETAIL = "任务不存在: {task_id}"
UPLOAD_ACCEPTED_MESSAGE = "文件已上传，后台正在解析索引。请通过 task_id 查询进度。"

from app.api.upload_storage_support import StoredUploadFileInfo
from app.services.document_formats import (
    get_document_extension,
    supports_document_indexing,
)


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
            detail=UNSUPPORTED_FILE_TYPE_DETAIL.format(extension=ext),
        )
    if not file.content_type:
        raise HTTPException(status_code=400, detail=UNKNOWN_FILE_TYPE_DETAIL)


def build_upload_accepted_response(
    file_info: StoredUploadFileInfo,
    task_id: str,
) -> UploadAcceptedResponse:
    """统一构造上传受理响应。"""
    return {
        **file_info,
        "task_id": task_id,
        "message": UPLOAD_ACCEPTED_MESSAGE,
    }


__all__ = [
    "StoredUploadFileInfo",
    "TASK_NOT_FOUND_DETAIL",
    "UNKNOWN_FILE_TYPE_DETAIL",
    "UNSUPPORTED_FILE_TYPE_DETAIL",
    "UPLOAD_ACCEPTED_MESSAGE",
    "UploadAcceptedResponse",
    "build_upload_accepted_response",
    "validate_upload",
]
