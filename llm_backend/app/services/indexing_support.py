"""文档索引服务的 support helper。

职责：
- 定义索引服务共享的轻量类型契约
- 负责上传文件源信息的规范化与基础校验
- 负责 doc_id、结果字典和依赖加载等支撑逻辑

边界：
- 不负责真正的“解析文档 -> 写入索引”业务编排
- 不负责上传接口的文件落盘
"""

from __future__ import annotations

import uuid
from collections.abc import Callable
from pathlib import Path
from typing import Any, Protocol, TypeAlias, TypedDict

from app.services.document_formats import (
    get_document_extension,
    supports_document_indexing,
)

STATUS_SUCCESS = "success"
STATUS_ERROR = "error"
STATUS_WARNING = "warning"
FILE_NOT_FOUND_MESSAGE = "文件不存在"
EMPTY_DOCUMENT_MESSAGE = "文档无有效内容"
MISSING_DEPENDENCY_MESSAGE = "rag_doc_parser 模块未安装，文档已保存但未索引"

ChunkRecord: TypeAlias = dict[str, Any]
ParsedChunks: TypeAlias = list[ChunkRecord]


class UploadFileInfo(TypedDict, total=False):
    """上传接口传给索引服务的最小字段契约。"""

    path: str
    user_id: int


class ResolvedUploadSource(TypedDict):
    """索引服务内部使用的规范化源文件信息。"""

    path: Path
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


def build_result(status: str, **payload: Any) -> IndexingResult:
    """统一构造索引结果，避免主流程反复手写状态字典。"""
    return {"status": status, **payload}


def normalize_optional_path(value: Any) -> Path | None:
    """把路径参数统一收口成非空 Path。"""
    if isinstance(value, Path):
        return value
    if not isinstance(value, str):
        return None

    normalized = value.strip()
    if not normalized:
        return None
    return Path(normalized)


def coerce_user_id(raw_user_id: Any) -> int:
    """把用户 id 统一收口为 int，非法值回退到 0。"""
    if isinstance(raw_user_id, int) and not isinstance(raw_user_id, bool):
        return raw_user_id
    if isinstance(raw_user_id, str) and raw_user_id.isdigit():
        return int(raw_user_id)
    return 0


def resolve_source(file_info: UploadFileInfo) -> ResolvedUploadSource | None:
    """从上传元信息中提取文件路径和用户 id。"""
    file_path = normalize_optional_path(file_info.get("path"))
    if file_path is None:
        return None
    return {
        "path": file_path,
        "user_id": coerce_user_id(file_info.get("user_id", 0)),
    }


def build_invalid_source_result(
    source: ResolvedUploadSource | None,
) -> IndexingResult | None:
    """校验源文件是否存在且类型受支持，不合法时直接返回错误结果。"""
    if source is None or not source["path"].exists():
        return build_result(STATUS_ERROR, message=FILE_NOT_FOUND_MESSAGE)

    ext = get_document_extension(source["path"])
    if not supports_document_indexing(ext):
        return build_result(STATUS_ERROR, message=f"不支持的文件类型: {ext}")
    return None


def resolve_source_or_error(
    file_info: UploadFileInfo,
) -> tuple[ResolvedUploadSource | None, IndexingResult | None]:
    """统一执行源文件解析和基础校验。"""
    source = resolve_source(file_info)
    return source, build_invalid_source_result(source)


def build_doc_id(user_id: int) -> str:
    """为上传文档生成稳定前缀的临时 doc_id。"""
    return f"upload_{user_id}_{uuid.uuid4().hex[:8]}"


def load_pipeline_dependencies() -> tuple[DocumentParser, ChunkIndexer]:
    """延迟导入解析函数和检索索引器，降低模块 import 成本。"""
    from rag_doc_parser.pipeline import parse_document
    from rag_doc_parser.retrieval.config import RetrievalConfig
    from rag_doc_parser.retrieval.hybrid_search import HybridSearcher

    return parse_document, HybridSearcher(RetrievalConfig())


def build_empty_document_result() -> IndexingResult:
    """统一构造“文档无可索引内容”的成功响应。"""
    return build_result(
        STATUS_SUCCESS,
        chunks=0,
        message=EMPTY_DOCUMENT_MESSAGE,
    )


def build_missing_dependency_result(file_info: UploadFileInfo) -> IndexingResult:
    """统一构造缺少 rag_doc_parser 时的降级结果。"""
    return build_result(
        STATUS_WARNING,
        message=MISSING_DEPENDENCY_MESSAGE,
        file_info=file_info,
    )
