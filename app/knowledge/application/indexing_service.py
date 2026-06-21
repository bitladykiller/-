"""文档索引服务。

上传后的文件通过 `rag_doc_parser` 解析，再写入 Milvus / BM25 检索索引。
本文件只保留"校验输入文件 + 调用解析索引管道"这一层，不承载上传或任务编排逻辑。

重构后:
- 文档格式定义从 chat/ 收拢到 knowledge/ 自身，消除 knowledge -> chat 依赖
"""
from __future__ import annotations

from pathlib import Path
import uuid

from app.knowledge.application.indexing_contracts import (
    DocIDFactory,
    IndexingResult,
    PipelineLoader,
    UploadFileInfo,
)

# 知识域自行维护可索引的文档格式，不依赖 chat 域
_DOCUMENT_EXTENSIONS = frozenset({".pdf", ".docx"})
_DOCUMENT_MAGIC_SIGNATURES: dict[str, tuple[bytes, ...]] = {
    ".pdf": (b"%PDF",),
    ".docx": (b"PK\x03\x04",),
}


def get_document_extension(path: Path) -> str:
    """返回文件的小写扩展名。"""
    return path.suffix.lower()


def supports_document_indexing(extension: str) -> bool:
    """判断扩展名是否属于可索引的文档格式。"""
    return extension in _DOCUMENT_EXTENSIONS

STATUS_SUCCESS = "success"
STATUS_ERROR = "error"
_STATUS_WARNING = "warning"
FILE_NOT_FOUND_MESSAGE = "文件不存在"
_EMPTY_DOCUMENT_MESSAGE = "文档无有效内容"
_MISSING_DEPENDENCY_MESSAGE = "rag_doc_parser 模块未安装，文档已保存但未索引"


def load_pipeline_dependencies() -> tuple:
    """延迟导入解析函数和检索索引器，降低模块 import 成本。"""
    from rag_doc_parser.pipeline import parse_document
    from rag_doc_parser.retrieval.config import RetrievalConfig
    from rag_doc_parser.retrieval.hybrid_search import HybridSearcher

    return parse_document, HybridSearcher(RetrievalConfig())


def build_doc_id(user_id: int) -> str:
    """为上传文档生成稳定前缀的临时 doc_id。"""
    return f"upload_{user_id}_{uuid.uuid4().hex[:8]}"


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
            parse_document, searcher = self._pipeline_loader()
            doc_id = self._doc_id_factory(user_id)
            chunks = parse_document(str(path), doc_id=doc_id)
            if not chunks:
                return {
                    "status": STATUS_SUCCESS,
                    "chunks": 0,
                    "message": _EMPTY_DOCUMENT_MESSAGE,
                }

            count = await searcher.index(chunks)
            return {
                "status": STATUS_SUCCESS,
                "chunks": count,
                "doc_id": doc_id,
                "source_file": str(path),
            }
        except ImportError:
            return {
                "status": _STATUS_WARNING,
                "message": _MISSING_DEPENDENCY_MESSAGE,
                "file_info": file_info,
            }
        except Exception as exc:
            return {"status": STATUS_ERROR, "message": str(exc)}


__all__ = ["IndexingService"]
