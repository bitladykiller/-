"""文档索引服务。

上传后的文件通过 `app.knowledge.infrastructure.doc_parser` 解析，再写入检索索引。
本文件只保留"校验输入文件 + 调用解析索引管道"这一层，不承载上传或任务编排逻辑。

支持策略 2 文档动态更新：
- mode=create：insert version=1
- mode=replace：soft_delete 旧 chunk → insert 新 version
"""
from __future__ import annotations

import uuid
from collections.abc import Awaitable, Sequence
from pathlib import Path
from typing import Any, cast

from app.knowledge.application.indexing_contracts import (
    ChunkIndexer,
    DocIDFactory,
    FullIndexFn,
    IndexingResult,
    ParseDocumentFn,
    PipelineLoader,
    ReindexFn,
    ReindexResult,
    UploadFileInfo,
)
from app.knowledge.infrastructure.doc_parser.retrieval.doc_lifecycle import (
    validate_doc_id,
)

_DOCUMENT_EXTENSIONS = frozenset({".pdf", ".docx", ".md", ".markdown"})

_MODE_CREATE = "create"
_MODE_REPLACE = "replace"
_ALLOWED_MODES = frozenset({_MODE_CREATE, _MODE_REPLACE})


def get_document_extension(path: str | Path | None) -> str:
    """返回文件的小写扩展名。"""
    return Path(path or "").suffix.lower()


def supports_document_indexing(extension: str) -> bool:
    """判断扩展名是否属于可索引的文档格式。"""
    return extension in _DOCUMENT_EXTENSIONS


STATUS_SUCCESS = "success"
STATUS_ERROR = "error"
_STATUS_WARNING = "warning"
FILE_NOT_FOUND_MESSAGE = "文件不存在"
_EMPTY_DOCUMENT_MESSAGE = "文档无有效内容"
_MISSING_DEPENDENCY_MESSAGE = (
    "app.knowledge.infrastructure.doc_parser 模块未安装，文档已保存但未索引"
)
_INVALID_MODE_MESSAGE = "mode 仅支持 create 或 replace"
_REPLACE_REQUIRES_DOC_ID = "replace 模式必须提供合法 doc_id"


def load_pipeline_dependencies() -> tuple[ParseDocumentFn, ChunkIndexer]:
    """延迟导入解析函数和检索索引器，降低模块 import 成本。

    索引器走 `get_shared_searcher()` 的进程内单例：以前每处理一个上传文件
    都会新建一个 `HybridSearcher`（新 Milvus 连接 + 重新加载 embedding 模型），
    白白付出重复的构造成本。
    """
    from app.knowledge.infrastructure.doc_parser.pipeline import parse_document
    from app.knowledge.infrastructure.doc_parser.retrieval.hybrid_search import (
        get_shared_searcher,
    )

    return cast(ParseDocumentFn, parse_document), cast(ChunkIndexer, get_shared_searcher())


def build_doc_id(user_id: int) -> str:
    """为上传文档生成稳定前缀的临时 doc_id。"""
    return f"upload_{user_id}_{uuid.uuid4().hex[:8]}"


def _normalize_mode(raw: object) -> str:
    if raw is None or raw == "":
        return _MODE_CREATE
    mode = str(raw).strip().lower()
    if mode not in _ALLOWED_MODES:
        raise ValueError(_INVALID_MODE_MESSAGE)
    return mode


def _resolve_doc_id(
    file_info: UploadFileInfo,
    *,
    user_id: int,
    mode: str,
    doc_id_factory: DocIDFactory,
) -> str:
    raw = file_info.get("doc_id")
    if raw is not None and str(raw).strip():
        return validate_doc_id(str(raw))
    if mode == _MODE_REPLACE:
        raise ValueError(_REPLACE_REQUIRES_DOC_ID)
    return validate_doc_id(doc_id_factory(user_id))


def _as_reindex_fn(searcher: object) -> ReindexFn | None:
    """取出 searcher.reindex 并标成可 await 的协程函数。"""
    reindex = getattr(searcher, "reindex", None)
    if reindex is None or not callable(reindex):
        return None
    return cast(ReindexFn, reindex)


def _as_index_fn(searcher: object) -> FullIndexFn:
    index_fn = getattr(searcher, "index", None)
    if index_fn is None or not callable(index_fn):
        raise TypeError("indexer 缺少 index 方法")
    return cast(FullIndexFn, index_fn)


async def _call_index(
    index_fn: FullIndexFn,
    chunks: Sequence[Any],
    *,
    version: int = 1,
    content_hash: str = "",
) -> int:
    """兼容 (chunks) 与 (chunks, version=, content_hash=) 两种签名。"""
    try:
        result = index_fn(chunks, version=version, content_hash=content_hash)
    except TypeError:
        result = index_fn(chunks)
    count = await cast(Awaitable[int], result)
    return int(count)


async def _call_reindex(
    reindex_fn: ReindexFn,
    doc_id: str,
    chunks: Sequence[Any],
    *,
    content_hash: str,
) -> ReindexResult:
    raw = await cast(
        Awaitable[ReindexResult | dict[str, Any]],
        reindex_fn(doc_id, chunks, content_hash=content_hash),
    )
    return cast(ReindexResult, raw)


class IndexingService:
    """文档索引服务。"""

    def __init__(
        self,
        *,
        pipeline_loader: PipelineLoader | None = None,
        doc_id_factory: DocIDFactory | None = None,
    ) -> None:
        self._pipeline_loader = pipeline_loader or load_pipeline_dependencies
        self._doc_id_factory = doc_id_factory or build_doc_id

    async def process_file(self, file_info: UploadFileInfo) -> IndexingResult:
        """处理上传文件并写入检索索引。"""
        raw_path = file_info.get("path")
        if isinstance(raw_path, Path):
            path = raw_path
        elif isinstance(raw_path, str) and raw_path.strip():
            path = Path(raw_path.strip())
        else:
            path = None

        raw_user_id = file_info.get("user_id", 0)
        if isinstance(raw_user_id, int) and not isinstance(raw_user_id, bool):
            user_id = raw_user_id
        elif isinstance(raw_user_id, str) and raw_user_id.isdigit():
            user_id = int(raw_user_id)
        else:
            user_id = 0

        if path is None or not path.exists():
            return {"status": STATUS_ERROR, "message": FILE_NOT_FOUND_MESSAGE}

        ext = get_document_extension(path)
        if not supports_document_indexing(ext):
            return {"status": STATUS_ERROR, "message": f"不支持的文件类型: {ext}"}

        try:
            mode = _normalize_mode(file_info.get("mode"))
            doc_id = _resolve_doc_id(
                file_info,
                user_id=user_id,
                mode=mode,
                doc_id_factory=self._doc_id_factory,
            )
        except ValueError as exc:
            return {"status": STATUS_ERROR, "message": str(exc)}

        content_hash = str(file_info.get("content_hash") or "")

        try:
            parse_document, searcher = self._pipeline_loader()
            chunks = parse_document(str(path), doc_id=doc_id)
            if not chunks:
                return {
                    "status": STATUS_SUCCESS,
                    "chunks": 0,
                    "message": _EMPTY_DOCUMENT_MESSAGE,
                    "doc_id": doc_id,
                    "mode": mode,
                }

            if mode == _MODE_REPLACE:
                reindex_fn = _as_reindex_fn(searcher)
                if reindex_fn is not None:
                    result = await _call_reindex(
                        reindex_fn,
                        doc_id,
                        chunks,
                        content_hash=content_hash,
                    )
                    return {
                        "status": STATUS_SUCCESS,
                        "chunks": int(result.get("chunks") or 0),
                        "doc_id": doc_id,
                        "source_file": str(path),
                        "mode": mode,
                        "version": int(result.get("version") or 0),
                        "soft_deleted": int(result.get("soft_deleted") or 0),
                    }
                # Fake / 旧 indexer 无 reindex：退化为直接 index
                count = await _call_index(_as_index_fn(searcher), chunks)
                return {
                    "status": STATUS_SUCCESS,
                    "chunks": count,
                    "doc_id": doc_id,
                    "source_file": str(path),
                    "mode": mode,
                    "version": 0,
                    "soft_deleted": 0,
                }

            count = await _call_index(
                _as_index_fn(searcher),
                chunks,
                version=1,
                content_hash=content_hash,
            )
            return {
                "status": STATUS_SUCCESS,
                "chunks": count,
                "doc_id": doc_id,
                "source_file": str(path),
                "mode": mode,
                "version": 1,
                "soft_deleted": 0,
            }
        except ImportError:
            return {
                "status": _STATUS_WARNING,
                "message": _MISSING_DEPENDENCY_MESSAGE,
                "file_info": file_info,
            }
        except Exception as exc:
            return {"status": STATUS_ERROR, "message": str(exc)}


__all__ = ["IndexingService", "load_pipeline_dependencies"]
