"""文档索引服务共享契约。"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any, Protocol, TypeAlias, TypedDict

ChunkRecord: TypeAlias = dict[str, Any]
ParsedChunks: TypeAlias = list[ChunkRecord]


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


class DocumentParser(Protocol):
    """文档解析函数的最小调用契约。"""

    def __call__(self, path: str, *, doc_id: str) -> ParsedChunks: ...


class ChunkIndexer(Protocol):
    """索引写入器的最小调用契约。"""

    async def index(self, chunks: list[ChunkRecord]) -> int: ...


PipelineLoader: TypeAlias = Callable[[], tuple[DocumentParser, ChunkIndexer]]
DocIDFactory: TypeAlias = Callable[[int], str]

__all__ = [
    "ChunkIndexer",
    "DocIDFactory",
    "DocumentParser",
    "IndexingResult",
    "ParsedChunks",
    "PipelineLoader",
    "UploadFileInfo",
]
