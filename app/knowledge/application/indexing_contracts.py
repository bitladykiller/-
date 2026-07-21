"""文档索引服务共享契约。"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any, TypeAlias

from typing_extensions import TypedDict


class UploadFileInfo(TypedDict, total=False):
    """上传接口传给索引服务的最小字段契约。"""

    path: str
    user_id: int
    # 稳定文档 ID；replace 模式必填；create 可省略（自动生成）
    doc_id: str
    # create = 首次入库；replace = 软删旧版 + 写新 version
    mode: str
    # 可选内容哈希，用于审计/后续幂等
    content_hash: str


class IndexingResult(TypedDict, total=False):
    """索引服务对外返回的统一结果结构。"""

    status: str
    message: str
    chunks: int
    doc_id: str
    source_file: str
    file_info: UploadFileInfo
    mode: str
    version: int
    soft_deleted: int


# (parse_document, HybridSearcher) — 延迟加载，具体类型由实现侧保证
PipelineLoader: TypeAlias = Callable[[], tuple[Any, Any]]
DocIDFactory: TypeAlias = Callable[[int], str]

__all__ = [
    "DocIDFactory",
    "IndexingResult",
    "PipelineLoader",
    "UploadFileInfo",
]
