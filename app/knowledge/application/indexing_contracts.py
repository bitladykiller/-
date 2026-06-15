"""文档索引服务共享契约。"""

from __future__ import annotations

from typing import TypedDict


class UploadFileInfo(TypedDict, total=False):
    """上传接口传给索引服务的最小字段契约。"""

    path: str
    user_id: int


class IndexingResult(TypedDict, total=False):
    """索引服务对外返回的统一结果结构。"""

    status: str
    message: str
    chunks: int
    doc_id: str
    source_file: str
    file_info: UploadFileInfo


__all__ = [
    "IndexingResult",
    "UploadFileInfo",
]
