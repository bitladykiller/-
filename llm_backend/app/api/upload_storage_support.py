"""上传接口的文件存储 helper。

职责：
- 负责上传文件的内容校验、落盘目标构造和元信息组装
- 收敛上传目录、大小限制和魔数签名相关样板

边界：
- 不负责后台索引任务提交
- 不负责任务状态查询
- 不负责上传成功响应构造
"""

from __future__ import annotations

import uuid
from datetime import datetime
from pathlib import Path
from typing import TypedDict

from fastapi import HTTPException, UploadFile

from app.services.document_formats import (
    document_magic_signatures,
    get_document_extension,
)
from app.services.indexing_service import UploadFileInfo

UPLOAD_DIR = Path("uploads")
MAX_UPLOAD_SIZE_MB = 50
MAX_UPLOAD_SIZE_BYTES = MAX_UPLOAD_SIZE_MB * 1024 * 1024
FILE_SIZE_EXCEEDED_DETAIL = f"文件大小超过限制 ({MAX_UPLOAD_SIZE_MB}MB)"
CONTENT_EXTENSION_MISMATCH_DETAIL = "文件内容与扩展名不匹配: {extension}"


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


def validate_magic_bytes(filename: str, content: bytes) -> bool:
    """通过魔数签名验证文件内容是否与扩展名匹配。"""
    signatures = document_magic_signatures(get_document_extension(filename))
    if not signatures:
        # 当前链路无额外魔数约束的类型，默认跳过内容签名检查。
        return True
    return any(content.startswith(signature) for signature in signatures)


def build_upload_target(user_id: int, filename: str | None) -> UploadTarget:
    """构造用户目录、时间目录和最终保存路径。"""
    user_uuid = str(uuid.uuid5(uuid.NAMESPACE_DNS, f"user_{user_id}"))
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    upload_dir = UPLOAD_DIR / user_uuid / timestamp
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


async def read_upload_content(file: UploadFile) -> bytes:
    """读取上传内容并执行大小/魔数校验。"""
    content = await file.read()
    if len(content) > MAX_UPLOAD_SIZE_BYTES:
        raise HTTPException(status_code=400, detail=FILE_SIZE_EXCEEDED_DETAIL)

    if not validate_magic_bytes(file.filename or "", content):
        raise HTTPException(
            status_code=400,
            detail=CONTENT_EXTENSION_MISMATCH_DETAIL.format(
                extension=get_document_extension(file.filename),
            ),
        )
    return content


def save_uploaded_file(file_path: Path, content: bytes) -> None:
    """把上传内容落盘到目标路径。"""
    file_path.write_bytes(content)


async def store_upload(
    file: UploadFile,
    user_id: int,
) -> StoredUploadFileInfo:
    """完成上传文件的校验、落盘和元信息组装。"""
    target = build_upload_target(user_id, file.filename)
    content = await read_upload_content(file)
    save_uploaded_file(target["file_path"], content)

    return build_file_info(
        file=file,
        user_id=user_id,
        user_uuid=target["user_uuid"],
        timestamp=target["timestamp"],
        file_path=target["file_path"],
        directory=target["upload_dir"],
        size=len(content),
    )


__all__ = [
    "CONTENT_EXTENSION_MISMATCH_DETAIL",
    "FILE_SIZE_EXCEEDED_DETAIL",
    "MAX_UPLOAD_SIZE_BYTES",
    "MAX_UPLOAD_SIZE_MB",
    "StoredUploadFileInfo",
    "UPLOAD_DIR",
    "UploadTarget",
    "build_file_info",
    "build_upload_target",
    "read_upload_content",
    "save_uploaded_file",
    "store_upload",
    "validate_magic_bytes",
]
