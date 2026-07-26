"""文档索引服务。

上传后的文件通过 `app.knowledge.infrastructure.doc_parser` 解析，再写入检索索引。
本文件只保留"校验输入文件 + 调用解析索引管道"这一层，不承载上传或任务编排逻辑。

支持策略 2 文档动态更新：
- mode=create：insert version=1
- mode=replace：soft_delete 旧 chunk → insert 新 version
"""
from __future__ import annotations

import uuid
from pathlib import Path
from typing import cast

from app.knowledge.application.indexing_contracts import (
    ChunkIndexer,
    DocIDFactory,
    IndexingResult,
    ParseDocumentFn,
    PipelineLoader,
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


def normalize_upload_mode(raw: object) -> str:
    """把上传模式归一化成 `create` / `replace`，非法值抛 ValueError。

    这是**唯一**的上传模式校验实现：API 层（`app.api.upload`）与索引层
    都从这里取，避免两处各写一份、日后只改一处而行为分叉。
    API 层负责把 ValueError 翻译成 HTTP 400。
    """
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


# NOTE: 索引器统一遵循 `ChunkIndexer` 协议（index + reindex，签名固定）。
# 这里曾经有一层"兼容层"：用 `except TypeError` 嗅探 index 的签名、
# 用 getattr 探测 reindex 是否存在，并在缺失时退化成全量 index。
# 那些分支只为早期的测试替身而存在，生产实现从来都两个方法齐全——
# 生产代码不该为测试替身让路，该改的是替身。已删除，契约以 Protocol 为准。


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
            mode = normalize_upload_mode(file_info.get("mode"))
            doc_id = _resolve_doc_id(
                file_info,
                user_id=user_id,
                mode=mode,
                doc_id_factory=self._doc_id_factory,
            )
        except ValueError as exc:
            return {"status": STATUS_ERROR, "message": str(exc)}

        content_hash = str(file_info.get("content_hash") or "")
        owner_id = str(file_info.get("owner_id") or "global")

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
                result = await searcher.reindex(
                    doc_id, chunks, content_hash=content_hash, owner_id=owner_id
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

            count = int(
                await searcher.index(
                    chunks, version=1, content_hash=content_hash, owner_id=owner_id
                )
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


__all__ = [
    "IndexingService",
    "load_pipeline_dependencies",
    "normalize_upload_mode",
]
