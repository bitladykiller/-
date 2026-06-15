"""文档索引服务。

上传后的文件通过 `rag_doc_parser` 解析，再写入 Milvus / BM25 检索索引。
本文件只保留“校验输入文件 + 调用解析索引管道”这一层，不承载上传或任务编排逻辑。
"""

import uuid
from pathlib import Path
from typing import Any

INDEXABLE_DOCUMENT_EXTENSIONS = frozenset({".pdf", ".docx"})
DOCUMENT_MAGIC_SIGNATURES: dict[str, tuple[bytes, ...]] = {
    ".pdf": (b"%PDF",),
    ".docx": (b"PK\x03\x04",),
}


async def process_file(
    file_info: dict[str, Any],
) -> dict[str, Any]:
    """处理上传文件并写入检索索引。

    当前只接受上传路由生成的 `file_info` 契约：
    - `path`: 非空字符串
    - `user_id`: `int`
    """
    raw_path = file_info.get("path")
    if not isinstance(raw_path, str) or not raw_path:
        return {"status": "error", "message": "文件不存在"}
    path = Path(raw_path)

    user_id = file_info.get("user_id")
    if not isinstance(user_id, int) or isinstance(user_id, bool):
        return {"status": "error", "message": "非法用户标识"}

    if not path.exists():
        return {"status": "error", "message": "文件不存在"}

    ext = path.suffix.lower()
    if ext not in INDEXABLE_DOCUMENT_EXTENSIONS:
        return {"status": "error", "message": f"不支持的文件类型: {ext}"}

    try:
        from rag_doc_parser.pipeline import parse_document
        from rag_doc_parser.retrieval.config import RetrievalConfig
        from rag_doc_parser.retrieval.hybrid_search import HybridSearcher

        doc_id = f"upload_{user_id}_{uuid.uuid4().hex[:8]}"
        chunks = parse_document(str(path), doc_id=doc_id)
        if not chunks:
            return {
                "status": "success",
                "chunks": 0,
                "message": "文档无有效内容",
            }

        searcher = HybridSearcher(RetrievalConfig())
        count = await searcher.index(chunks)
        return {
            "status": "success",
            "chunks": count,
            "doc_id": doc_id,
            "source_file": str(path),
        }
    except ImportError:
        return {
            "status": "warning",
            "message": "rag_doc_parser 模块未安装，文档已保存但未索引",
            "file_info": file_info,
        }
    except Exception as exc:
        return {"status": "error", "message": str(exc)}
