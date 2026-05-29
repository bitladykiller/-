"""
文档索引服务（已从 GraphRAG 迁移到 rag_doc_parser）。

原 GraphRAG 已删除。文件上传后通过 rag_doc_parser 解析并存入 Milvus。
"""
import os
from pathlib import Path
from typing import Optional, Dict, Any
import uuid

from app.core.config import settings


class IndexingService:
    """文档索引服务。

    v3.1: GraphRAG 已替换为 rag_doc_parser。
    文件解析通过 rag_doc_parser.pipeline.parse_document() 完成。
    """

    async def process_file(self, file_info: Dict[str, Any]) -> Dict[str, Any]:
        """处理上传的文件。

        使用 rag_doc_parser 解析文件并生成 DocumentChunk，
        然后通过 HybridSearcher 写入 Milvus + BM25 索引。

        Args:
            file_info: 包含 filename, path, user_id 等字段的字典。

        Returns:
            索引结果字典。
        """
        file_path = file_info.get("path", "")
        user_id = file_info.get("user_id", 0)

        if not file_path or not Path(file_path).exists():
            return {"status": "error", "message": "文件不存在"}

        ext = Path(file_path).suffix.lower()
        if ext not in (".pdf", ".docx", ".txt", ".md", ".csv", ".json"):
            return {"status": "error", "message": f"不支持的文件类型: {ext}"}

        try:
            from rag_doc_parser.pipeline import parse_document
            from rag_doc_parser.retrieval.hybrid_search import HybridSearcher
            from rag_doc_parser.retrieval.config import RetrievalConfig

            doc_id = f"upload_{user_id}_{uuid.uuid4().hex[:8]}"
            chunks = parse_document(file_path, doc_id=doc_id)

            if not chunks:
                return {"status": "success", "chunks": 0, "message": "文档无有效内容"}

            config = RetrievalConfig()
            searcher = HybridSearcher(config)
            count = await searcher.index(chunks)

            return {
                "status": "success",
                "chunks": count,
                "doc_id": doc_id,
                "source_file": file_path,
            }
        except ImportError:
            return {
                "status": "warning",
                "message": "rag_doc_parser 模块未安装，文档已保存但未索引",
                "file_info": file_info,
            }
        except Exception as e:
            return {"status": "error", "message": str(e)}
