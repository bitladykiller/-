"""
Milvus 向量存储 — Collection 管理与向量检索。

支持创建 Collection、批量插入 DocumentChunk、
向量相似度检索。
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from pymilvus import MilvusClient, DataType

from rag_doc_parser.retrieval.config import RetrievalConfig

logger = logging.getLogger(__name__)


class MilvusStore:
    """Milvus 向量存储。

    管理 RAG 文档的向量索引和相似度检索。
    """

    def __init__(self, config: RetrievalConfig, embedding_model=None):
        """初始化 Milvus 连接和 Collection。

        Args:
            config: 检索配置。
            embedding_model: Embedding 模型，需要有 embed_query(text) -> List[float] 方法。
        """
        self.config = config
        self.embedding_model = embedding_model
        self.client = MilvusClient(
            uri=f"http://{config.milvus_host}:{config.milvus_port}"
        )
        self._create_collection_if_not_exists()

    # ------------------------------------------------------------------ #
    # Collection 管理
    # ------------------------------------------------------------------ #

    def _create_collection_if_not_exists(self):
        """创建 Collection（如果不存在）。

        Schema:
        - chunk_id: VARCHAR(64) PRIMARY KEY
        - doc_id: VARCHAR(64)
        - source_file: VARCHAR(512)
        - chunk_type: VARCHAR(32)
        - section_path: VARCHAR(512)
        - raw_text: VARCHAR(8192)
        - embedding_text: VARCHAR(8192)
        - embedding: FLOAT_VECTOR(1024) → bge-m3
        """
        name = self.config.milvus_collection_name
        if self.client.has_collection(name):
            logger.info(f"Collection {name} 已存在")
            return

        schema = self.client.create_schema(auto_id=False, enable_dynamic_field=True)

        schema.add_field("chunk_id", DataType.VARCHAR, is_primary=True, max_length=64)
        schema.add_field("doc_id", DataType.VARCHAR, max_length=64)
        schema.add_field("source_file", DataType.VARCHAR, max_length=512)
        schema.add_field("chunk_type", DataType.VARCHAR, max_length=32)
        schema.add_field("section_path", DataType.VARCHAR, max_length=512)
        schema.add_field("raw_text", DataType.VARCHAR, max_length=8192)
        schema.add_field("embedding_text", DataType.VARCHAR, max_length=8192)
        schema.add_field(
            "embedding",
            DataType.FLOAT_VECTOR,
            dim=self.config.milvus_embedding_dim,
        )

        index_params = self.client.prepare_index_params()
        index_params.add_index(
            field_name="embedding",
            index_type=self.config.milvus_index_type,
            metric_type=self.config.milvus_metric_type,
            params={"nlist": self.config.milvus_nlist},
        )

        self.client.create_collection(
            collection_name=name,
            schema=schema,
            index_params=index_params,
        )
        logger.info(f"Collection {name} 创建成功")

    # ------------------------------------------------------------------ #
    # 数据操作
    # ------------------------------------------------------------------ #

    async def _get_embedding(self, text: str) -> List[float]:
        """获取文本的 embedding 向量。

        Args:
            text: 输入文本。

        Returns:
            1024 维浮点数列表。
        """
        if self.embedding_model is None:
            raise RuntimeError("embedding_model 未设置，无法生成向量")
        return self.embedding_model.embed_query(text)

    async def insert_chunks(self, chunks: List[Any]) -> int:
        """批量插入 DocumentChunk 到 Milvus。

        Args:
            chunks: DocumentChunk 列表。

        Returns:
            成功插入的数量。
        """
        if not chunks:
            return 0

        data = []
        for chunk in chunks:
            text = chunk.embedding_text or chunk.raw_text
            vector = await self._get_embedding(text)

            data.append({
                "chunk_id": chunk.chunk_id,
                "doc_id": chunk.doc_id,
                "source_file": chunk.source_file,
                "chunk_type": chunk.chunk_type,
                "section_path": chunk.section_path,
                "raw_text": chunk.raw_text,
                "embedding_text": chunk.embedding_text,
                "embedding": vector,
            })

        result = self.client.insert(
            collection_name=self.config.milvus_collection_name,
            data=data,
        )
        count = result.get("insert_count", 0)
        logger.info(f"插入 {count} 条记录到 Milvus")
        return count

    # ------------------------------------------------------------------ #
    # 向量检索
    # ------------------------------------------------------------------ #

    async def search(
        self,
        query: str,
        top_k: Optional[int] = None,
        filter_expr: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """向量相似度检索。

        Args:
            query: 查询文本。
            top_k: 返回条数（默认使用 config.vector_top_k）。
            filter_expr: Milvus 过滤表达式（如 'chunk_type == "table"'）。

        Returns:
            检索结果列表，每项包含 chunk 信息 + score。
        """
        top_k = top_k or self.config.vector_top_k
        query_vector = await self._get_embedding(query)

        results = self.client.search(
            collection_name=self.config.milvus_collection_name,
            data=[query_vector],
            filter=filter_expr,
            limit=top_k,
            output_fields=[
                "chunk_id", "doc_id", "source_file", "chunk_type",
                "section_path", "raw_text", "embedding_text",
            ],
        )

        if not results or not results[0]:
            return []

        # 格式化输出
        formatted = []
        for item in results[0]:
            entity = item.get("entity", {})
            formatted.append({
                "chunk_id": entity.get("chunk_id", ""),
                "doc_id": entity.get("doc_id", ""),
                "source_file": entity.get("source_file", ""),
                "chunk_type": entity.get("chunk_type", ""),
                "section_path": entity.get("section_path", ""),
                "raw_text": entity.get("raw_text", ""),
                "embedding_text": entity.get("embedding_text", ""),
                "vector_score": item.get("distance", 0.0),
            })

        return formatted
