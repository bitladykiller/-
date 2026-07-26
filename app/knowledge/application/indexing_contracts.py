"""文档索引服务共享契约。"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Sequence
from typing import Any, Protocol, TypeAlias, runtime_checkable

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


class ReindexResult(TypedDict, total=False):
    """HybridSearcher.reindex 返回结构。"""

    soft_deleted: int
    version: int
    chunks: int


@runtime_checkable
class ChunkIndexer(Protocol):
    """索引器最小协议：create / replace。"""

    async def index(
        self,
        chunks: Sequence[Any],
        *,
        version: int = 1,
        content_hash: str = "",
        owner_id: str = "global",
    ) -> int: ...

    async def reindex(
        self,
        doc_id: str,
        chunks: Sequence[Any],
        *,
        content_hash: str = "",
        owner_id: str = "global",
    ) -> ReindexResult: ...


# parse_document(path, *, doc_id) -> chunks
ParseDocumentFn: TypeAlias = Callable[..., Sequence[Any]]
# (parse_document, indexer)
PipelineLoader: TypeAlias = Callable[[], tuple[ParseDocumentFn, ChunkIndexer]]
DocIDFactory: TypeAlias = Callable[[int], str]

# 兼容仅同步/简化签名的 Fake indexer
SimpleIndexFn: TypeAlias = Callable[[Sequence[Any]], Awaitable[int]]
FullIndexFn: TypeAlias = Callable[..., Awaitable[int]]
ReindexFn: TypeAlias = Callable[..., Awaitable[ReindexResult | dict[str, Any]]]

__all__ = [
    "ChunkIndexer",
    "DocIDFactory",
    "FullIndexFn",
    "IndexingResult",
    "ParseDocumentFn",
    "PipelineLoader",
    "ReindexFn",
    "ReindexResult",
    "SimpleIndexFn",
    "UploadFileInfo",
]
