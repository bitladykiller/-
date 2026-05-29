import os
import uuid
import re
from datetime import datetime
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, HTTPException, UploadFile, File, Form

from app.services.indexing_service import IndexingService
from app.core.logger import get_logger

logger = get_logger(__name__)


router = APIRouter(tags=["upload"])

UPLOAD_DIR = Path("uploads")
UPLOAD_DIR.mkdir(exist_ok=True)

ALLOWED_EXTENSIONS = {'.pdf', '.txt', '.csv', '.json', '.docx', '.doc', '.xlsx', '.xls', '.pptx', '.ppt'}
MAX_UPLOAD_SIZE = 50 * 1024 * 1024  # 50MB

# 常见文件类型的魔数签名，用于验证文件真实类型
MAGIC_SIGNATURES: dict[str, list[bytes]] = {
    '.pdf': [b'%PDF'],
    '.docx': [b'PK\x03\x04'],  # OOXML 格式
    '.doc': [b'\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1'],  # OLE 格式
    '.xlsx': [b'PK\x03\x04'],
    '.xls': [b'\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1'],
    '.pptx': [b'PK\x03\x04'],
    '.ppt': [b'\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1'],
    '.png': [b'\x89PNG\r\n\x1a\n'],
    '.jpg': [b'\xff\xd8\xff'],
    '.jpeg': [b'\xff\xd8\xff'],
    '.gif': [b'GIF89a', b'GIF87a'],
    '.webp': [b'RIFF'],
}


def _validate_upload(file: UploadFile):
    """验证上传文件的扩展名和魔数签名。"""
    ext = os.path.splitext(file.filename or "")[1].lower()
    if ext not in ALLOWED_EXTENSIONS:
        raise HTTPException(status_code=400, detail=f"不支持的文件类型: {ext}")
    if not file.content_type:
        raise HTTPException(status_code=400, detail="无法识别文件类型")


def _validate_magic_bytes(filename: str, content: bytes) -> bool:
    """通过魔数签名验证文件内容是否与扩展名匹配。"""
    ext = os.path.splitext(filename)[1].lower()
    signatures = MAGIC_SIGNATURES.get(ext)
    if not signatures:
        # 无魔数定义的类型（如 .txt, .csv, .json），跳过验证
        return True
    return any(content.startswith(sig) for sig in signatures)


@router.post("/upload")
async def upload_file(
    file: UploadFile = File(...),
    user_id: int = Form(...)
):
    try:
        _validate_upload(file)

        user_uuid = str(uuid.uuid5(uuid.NAMESPACE_DNS, f"user_{user_id}"))
        first_level_dir = UPLOAD_DIR / user_uuid

        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        second_level_dir = first_level_dir / timestamp
        second_level_dir.mkdir(parents=True, exist_ok=True)

        original_name, ext = os.path.splitext(file.filename)
        new_filename = f"{original_name}_{timestamp}{ext}"
        file_path = second_level_dir / new_filename

        content = await file.read()
        if len(content) > MAX_UPLOAD_SIZE:
            raise HTTPException(status_code=400, detail="文件大小超过限制 (50MB)")

        # 验证魔数签名
        if not _validate_magic_bytes(file.filename or "", content):
            raise HTTPException(status_code=400, detail=f"文件内容与扩展名不匹配: {ext}")

        with open(file_path, "wb") as f:
            f.write(content)

        file_info = {
            "filename": new_filename,
            "original_name": file.filename,
            "size": len(content),
            "type": file.content_type,
            "path": str(file_path).replace('\\', '/'),
            "user_id": user_id,
            "user_uuid": user_uuid,
            "upload_time": timestamp,
            "directory": str(second_level_dir)
        }

        indexing_service = IndexingService()
        index_result = await indexing_service.process_file(file_info)

        result = {**file_info, "index_result": index_result}

        return result

    except HTTPException:
        raise
    except Exception as e:
        logger.error(
            f"upload_file 异常 | user_id={user_id} "
            f"filename={file.filename} | {e}",
            exc_info=True,
        )
        raise HTTPException(status_code=500, detail="Internal server error")


def _sanitize_path_component(name: str) -> str:
    return re.sub(r'[^a-zA-Z0-9_-]', '_', name or "unknown")


@router.post("/upload/image")
async def upload_image(
    image: UploadFile = File(...),
    user_id: int = Form(...),
    conversation_id: Optional[str] = Form(None)
):
    try:
        _validate_upload(image)
        image_dir = Path("uploads/images")
        if conversation_id:
            conversation_id = _sanitize_path_component(conversation_id)
            image_dir = image_dir / conversation_id
        image_dir.mkdir(parents=True, exist_ok=True)

        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        original_name, ext = os.path.splitext(image.filename)
        new_filename = f"{original_name}_{timestamp}{ext}"
        image_path = image_dir / new_filename

        content = await image.read()
        if len(content) > MAX_UPLOAD_SIZE:
            raise HTTPException(status_code=400, detail="文件大小超过限制 (50MB)")

        with open(image_path, "wb") as f:
            f.write(content)

        image_info = {
            "filename": new_filename,
            "original_name": image.filename,
            "size": len(content),
            "type": image.content_type,
            "path": str(image_path).replace('\\', '/'),
            "user_id": user_id,
            "conversation_id": conversation_id,
            "upload_time": timestamp
        }

        return image_info

    except HTTPException:
        raise
    except Exception as e:
        logger.error(
            f"upload_image 异常 | user_id={user_id} "
            f"filename={image.filename} | {e}",
            exc_info=True,
        )
        raise HTTPException(status_code=500, detail="Internal server error")
